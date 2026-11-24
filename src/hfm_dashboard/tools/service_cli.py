"""Operator command ``hfm-service``: install, check, inspect and remove the monitor's Windows service.

    hfm-service check     --config-dir D:\\...\\config --data-dir ... --log-dir ...   # no admin rights needed
    hfm-service install   --config-dir ... --data-dir ... --log-dir ... --account <DOMAIN>\\<gmsa>$ [--start]
    hfm-service install   ... --account <DOMAIN>\\<user> --ask-password       # ordinary domain user (D33)
    hfm-service status
    hfm-service uninstall

``install`` and ``uninstall`` need an elevated (Run as administrator) prompt. ``install`` refuses
unless the configuration is valid and the exact service command line passes its self-check, then
registers the service (automatic, delayed start), sets Windows recovery actions from rules.yaml
``monitor_service`` and registers the Event Log source. It never touches an HFM service.

Accounts: a gMSA / sMSA (``DOMAIN\\name$``) has no password. An ordinary user account (interim, D33)
needs ``--ask-password``: the password is prompted for (not echoed), handed to the service manager and
never printed or written to a file. A named account is granted 'Log on as a service' on this computer.

Exit codes: 0 ok; 1 service manager error (or ``status``: installed but not running);
2 configuration / check problem; 3 ``status``: not installed.
"""

from __future__ import annotations

import argparse
import getpass
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from hfm_dashboard.app.service_host import (
    CHECK_FLAG, DESCRIPTION, DISPLAY_NAME, SERVICE_NAME, ServiceOptions, boot_script, command_line,
    interpreter_paths, load_service_rules, profile_problems, service_command, user_profiles_dir,
)
from hfm_dashboard.config.startup import ConfigurationError, load_configuration
from hfm_dashboard.ports.service_control import (
    BUILT_IN_PREFIXES, ServiceControl, ServiceControlError, ServiceSpec, needs_password,
)

# Waiting for a stop when the service's rules.yaml cannot be read (only then).
FALLBACK_STOP_SECONDS = 120

Runner = Callable[[list[str]], tuple[int, str]]
AskPassword = Callable[[str], str]


def run_command(args: list[str]) -> tuple[int, str]:
    result = subprocess.run(args, capture_output=True, text=True)
    return result.returncode, (result.stdout + result.stderr).strip()


def _default_control() -> ServiceControl:
    from hfm_dashboard.adapters.windows_service import PywinServiceControl
    return PywinServiceControl()


def _options(a: argparse.Namespace) -> ServiceOptions:
    return ServiceOptions(
        config_dir=a.config_dir.resolve(), data_dir=a.data_dir.resolve(), log_dir=a.log_dir.resolve(),
        ui_dir=a.ui_dir.resolve() if a.ui_dir else None, environments=tuple(a.env or ()), no_web=a.no_web)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hfm-service", description="Manage the monitor's Windows service.")
    sub = p.add_subparsers(dest="command", required=True)
    for name, text in (("check", "check what the service needs (no admin rights needed)"),
                       ("install", "register the service (elevated prompt)")):
        s = sub.add_parser(name, help=text)
        s.add_argument("--config-dir", type=Path, required=True)
        s.add_argument("--data-dir", type=Path, required=True)
        s.add_argument("--log-dir", type=Path, required=True)
        s.add_argument("--ui-dir", type=Path)
        s.add_argument("--env", action="append", help="only this environment (repeatable)")
        s.add_argument("--no-web", action="store_true")
        s.add_argument("--allow-user-profile", action="store_true",
                       help="testing on a workstation only: accept Python or folders inside a user profile")
        if name == "install":
            s.add_argument("--account", help=r"service account, e.g. a gMSA: DOMAIN\name$ (default LocalSystem)")
            s.add_argument("--ask-password", action="store_true",
                           help="prompt for the password of an ordinary user account (not for a gMSA / sMSA)")
            s.add_argument("--start", action="store_true", help="start the service after installing")
    sub.add_parser("status", help="show state, account and command line")
    sub.add_parser("uninstall", help="stop and remove the service (elevated prompt)")
    return p


def main(argv: list[str] | None = None, *, control: ServiceControl | None = None,
         runner: Runner = run_command, out: TextIO | None = None,
         ask_password: AskPassword = getpass.getpass,
         profiles_dir: Callable[[], Path | None] = user_profiles_dir) -> int:
    out = out or sys.stdout
    a = _parser().parse_args(argv)
    try:
        if a.command in ("check", "install"):
            try:
                options = _options(a)
            except ValueError as exc:
                print(f"PROBLEM: {exc}", file=out)
                return 2
            if a.command == "install":
                problem = _account_problem(a.account, a.ask_password)
                if problem:
                    print(f"PROBLEM: {problem}", file=out)
                    return 2
            ok = _check(options, runner, out,
                        profiles=None if a.allow_user_profile else profiles_dir())
            if a.command == "check" or not ok:
                return 0 if ok else 2
            password = None
            if a.ask_password:
                password = _read_password(a.account, ask_password, out)
                if password is None:
                    return 2
            return _install(options, a.account, password, a.start, control or _default_control(), out)
        control = control or _default_control()
        if a.command == "status":
            return _status(control, out)
        return _uninstall(control, out)
    except ServiceControlError as exc:
        print(f"ERROR: {exc}", file=out)
        if exc.access_denied:
            print("Run this from an elevated prompt (Run as administrator).", file=out)
        return 1


def _account_problem(account: str | None, ask: bool) -> str | None:
    if ask and not needs_password(account):
        return ("--ask-password is only for an ordinary user account; a gMSA / sMSA (name ending in $), "
                "LocalSystem and NT AUTHORITY accounts have no password to give")
    if not ask and needs_password(account):
        return (f"{account} is an ordinary user account: add --ask-password (without its password the "
                "service cannot log on - error 1069 at start). A gMSA / sMSA name ends with $.")
    return None


def _read_password(account: str, ask_password: AskPassword, out: TextIO) -> str | None:
    try:
        first = ask_password(f"Password for {account}: ")
        second = ask_password("Again: ")
    except (EOFError, KeyboardInterrupt):
        print("PROBLEM: no password entered; nothing installed", file=out)
        return None
    if not first:
        print("PROBLEM: empty password; nothing installed", file=out)
        return None
    if first != second:
        print("PROBLEM: the two passwords differ; nothing installed", file=out)
        return None
    return first


def _check(options: ServiceOptions, runner: Runner, out: TextIO, profiles: Path | None = None) -> bool:
    try:
        load_configuration(options.config_dir)
    except ConfigurationError as exc:
        print(f"PROBLEM: {exc}", file=out)
        return False
    exe, site_dir = interpreter_paths()
    paths = {"interpreter": exe, "monitor code": site_dir, "config folder": options.config_dir,
             "data folder": options.data_dir, "log folder": options.log_dir}
    if options.ui_dir is not None:
        paths["ui folder"] = options.ui_dir
    problems = profile_problems(paths, profiles)
    for p in problems:
        print(f"PROBLEM: {p}", file=out)
    if problems:
        print("(only for a test on your own workstation: add --allow-user-profile)", file=out)
        return False
    args = service_command(exe, boot_script(), site_dir, options)
    check = [*args[:5], CHECK_FLAG, *args[5:]]
    code, text = runner(check)
    if text:
        print(text, file=out)
    if code != 0:
        print("PROBLEM: the service command line failed its self-check (output above)", file=out)
        return False
    return True


def _install(options: ServiceOptions, account: str | None, password: str | None, start: bool,
             control: ServiceControl, out: TextIO) -> int:
    if control.info(SERVICE_NAME) is not None:
        print(f"PROBLEM: {SERVICE_NAME} is already installed; run 'hfm-service uninstall' first", file=out)
        return 2
    rules, _ = load_service_rules(options)
    assert rules is not None          # _check loaded the configuration
    exe, site_dir = interpreter_paths()
    spec = ServiceSpec(SERVICE_NAME, DISPLAY_NAME, DESCRIPTION,
                       tuple(service_command(exe, boot_script(), site_dir, options)), account=account,
                       password=password)
    granted = account is not None and not account.upper().startswith(BUILT_IN_PREFIXES)
    if granted:
        control.grant_service_logon(account)      # before install: a mistyped account installs nothing
    control.install(spec)
    ms = rules.monitor_service
    control.set_recovery(SERVICE_NAME, ms.restart_after_failure_seconds, ms.reset_failure_count_after_hours * 3600)
    print(f"installed {SERVICE_NAME} (automatic, delayed start)", file=out)
    print(f"  command:  {command_line(list(spec.command))}", file=out)
    print(f"  account:  {account or 'LocalSystem'}"
          + (" ('Log on as a service' granted)" if granted else ""), file=out)
    print(f"  recovery: restart after {', '.join(f'{d} s' for d in ms.restart_after_failure_seconds)}; "
          f"count reset after {ms.reset_failure_count_after_hours} h", file=out)
    if account is None:
        print("WARNING: running as LocalSystem. Before shadow mode set the read-only gMSA (A07): "
              "reinstall with --account.", file=out)
    if password:
        print("NOTE: interim user account (D33); Windows keeps its password. When the password changes, "
              f"update it in services.msc (Log On tab of {SERVICE_NAME}) or the service will not start "
              "(error 1069).", file=out)
    if start:
        control.start(SERVICE_NAME)
        print(f"started; log: {options.log_dir}", file=out)
    else:
        print(f"start with: Start-Service {SERVICE_NAME}   (log: {options.log_dir})", file=out)
    return 0


def _status(control: ServiceControl, out: TextIO) -> int:
    info = control.info(SERVICE_NAME)
    if info is None:
        print(f"{SERVICE_NAME}: not installed", file=out)
        return 3
    print(f"{SERVICE_NAME}: {info.state}", file=out)
    print(f"  start type: {info.start_type}", file=out)
    print(f"  account:    {info.account}", file=out)
    print(f"  command:    {info.command_line}", file=out)
    return 0 if info.state == "running" else 1


def _stop_seconds(command_line_text: str) -> int:
    """stop_timeout_seconds from the rules.yaml the installed service uses."""
    import shlex
    try:
        parts = shlex.split(command_line_text, posix=False)
        args = [p.strip('"') for p in parts]
        idx = args.index("--config-dir")
        rules, _ = load_service_rules(ServiceOptions.parse(args[idx:]))
    except (ValueError, SystemExit):
        rules = None
    return rules.monitor_service.stop_timeout_seconds if rules else FALLBACK_STOP_SECONDS


def _uninstall(control: ServiceControl, out: TextIO) -> int:
    info = control.info(SERVICE_NAME)
    if info is None:
        print(f"{SERVICE_NAME}: not installed, nothing to do", file=out)
        return 0
    if info.state != "stopped":
        seconds = _stop_seconds(info.command_line)
        print(f"stopping {SERVICE_NAME} (up to {seconds} s) ...", file=out)
        control.stop(SERVICE_NAME, seconds)
    control.remove(SERVICE_NAME)
    print(f"removed {SERVICE_NAME}; log and data folders are kept", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
