"""Run the monitor inside a Windows service - everything except the pywin32 calls, so it is testable.

The service starts ``<python> -I -S <service_boot.py> <site-packages> <options...>`` (command line
built by ``service_command``). The options carry absolute folders because a service starts in the
system folder. ``run_service`` then:

1. reads ``log_keep_days`` from rules.yaml (if rules.yaml cannot be read, keeps every log file so the
   reason is not lost) and logs to ``<log-dir>/hfm-dashboard.log`` (rotated 00:00 UTC);
2. sends anything printed (poll summaries, start-up refusals) to that log - a service has no console;
3. runs the normal ``hfm-dashboard`` loop until the stop event is set.

Outcome for the service manager (``ServiceOutcome``): STOPPED after a requested stop; REFUSED when
start-up was refused (exit 2: configuration, owners or history problem - a restart cannot fix it, so
the service stops with an error and Windows does not restart it); CRASHED otherwise (the process ends
abnormally and Windows' recovery actions restart it, rules.yaml ``monitor_service``).
"""

from __future__ import annotations

import argparse
import contextlib
import enum
import logging
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from hfm_dashboard.app.logs import LogWriter, configure_logging, remove_handlers
from hfm_dashboard.config.rules_loader import RulesError, load_rules
from hfm_dashboard.domain.rules import Rules

log = logging.getLogger("hfm_dashboard.service")

SERVICE_NAME = "HfmAvailabilityMonitor"
DISPLAY_NAME = "HFM Availability Monitor"
DESCRIPTION = ("Read-only availability monitor for Oracle HFM: polls the servers, scores stability, "
               "serves the dashboard and emails caution notices. Never starts or stops HFM services.")
RULES_FILE = "rules.yaml"
CHECK_FLAG = "--check"

MainFn = Callable[..., int]


class ServiceOutcome(enum.Enum):
    STOPPED = "stopped"     # stop requested, clean exit
    REFUSED = "refused"     # start-up refused (exit 2): stop with an error, no automatic restart
    CRASHED = "crashed"     # anything else: let Windows' recovery actions restart the service


@dataclass(frozen=True)
class ServiceOptions:
    config_dir: Path
    data_dir: Path
    log_dir: Path
    ui_dir: Path | None = None
    environments: tuple[str, ...] = field(default_factory=tuple)
    no_web: bool = False

    def __post_init__(self) -> None:
        for name in ("config_dir", "data_dir", "log_dir", "ui_dir"):
            value = getattr(self, name)
            if value is not None and not Path(value).is_absolute():
                raise ValueError(f"{name.replace('_', '-')} must be an absolute path (a service starts "
                                 "in the system folder)")

    def to_args(self) -> list[str]:
        args = ["--config-dir", str(self.config_dir), "--data-dir", str(self.data_dir),
                "--log-dir", str(self.log_dir)]
        if self.ui_dir is not None:
            args += ["--ui-dir", str(self.ui_dir)]
        for env in self.environments:
            args += ["--env", env]
        if self.no_web:
            args.append("--no-web")
        return args

    def main_argv(self) -> list[str]:
        """Arguments for ``hfm_dashboard.app.main.main`` (which has no ``--log-dir``)."""
        args = ["--config-dir", str(self.config_dir), "--data-dir", str(self.data_dir)]
        if self.ui_dir is not None:
            args += ["--ui-dir", str(self.ui_dir)]
        for env in self.environments:
            args += ["--env", env]
        if self.no_web:
            args.append("--no-web")
        return args

    @classmethod
    def parse(cls, argv: list[str]) -> ServiceOptions:
        p = argparse.ArgumentParser(prog="hfm-service", add_help=False)
        p.add_argument("--config-dir", type=Path, required=True)
        p.add_argument("--data-dir", type=Path, required=True)
        p.add_argument("--log-dir", type=Path, required=True)
        p.add_argument("--ui-dir", type=Path)
        p.add_argument("--env", action="append", default=[])
        p.add_argument("--no-web", action="store_true")
        a = p.parse_args(argv)
        return cls(a.config_dir, a.data_dir, a.log_dir, a.ui_dir, tuple(a.env), a.no_web)


def service_command(python_exe: Path, boot_script: Path, site_dir: Path, options: ServiceOptions) -> list[str]:
    """The service's command line as an argument list (``subprocess.list2cmdline`` quotes it).

    ``-I -S``: isolated, no automatic site - the boot script adds exactly the monitor's
    site-packages, so nothing from another Python installation or user folder is picked up.
    """
    return [str(python_exe), "-I", "-S", str(boot_script), str(site_dir), *options.to_args()]


def command_line(args: list[str]) -> str:
    return subprocess.list2cmdline(args)


def interpreter_paths() -> tuple[Path, Path]:
    """(python.exe to register, site-packages to add). A venv's python.exe is only a launcher that
    starts the real interpreter as a child process - the service manager would be talking to the
    wrong process - so the base interpreter is registered and the venv's site-packages added."""
    import sysconfig
    exe = Path(getattr(sys, "_base_executable", "") or sys.executable)
    return exe, Path(sysconfig.get_paths()["purelib"])


def boot_script() -> Path:
    return Path(__file__).resolve().parents[1] / "service_boot.py"


def load_service_rules(options: ServiceOptions) -> tuple[Rules | None, str | None]:
    try:
        return load_rules(options.config_dir / RULES_FILE), None
    except RulesError as exc:
        return None, str(exc)
    except OSError as exc:
        return None, f"cannot read {RULES_FILE} ({type(exc).__name__})"


def run_service(
    options: ServiceOptions,
    stop: threading.Event,
    *,
    main_fn: MainFn | None = None,
    console: bool = False,
) -> ServiceOutcome:
    """Body of the service: log to file, run the monitor until ``stop`` is set, classify the end."""
    if main_fn is None:
        from hfm_dashboard.app.main import main as main_fn
    rules, problem = load_service_rules(options)
    keep = rules.monitor_service.log_keep_days if rules else None
    handlers = configure_logging(log_dir=options.log_dir, keep_days=keep, console=console)
    out = LogWriter(logging.getLogger("hfm_dashboard.poll"), logging.INFO)
    err = LogWriter(logging.getLogger("hfm_dashboard.service"), logging.ERROR)
    try:
        log.info("service starting (config %s, data %s)", options.config_dir, options.data_dir)
        if problem:
            log.error("rules.yaml problem - start-up will be refused: %s", problem)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main_fn(options.main_argv(), stop=stop, out=out)
        except Exception:  # noqa: BLE001 - logged with traceback, then Windows restarts the service
            log.exception("monitor crashed")
            return ServiceOutcome.CRASHED
        finally:
            out.flush()
            err.flush()
        if code == 2:
            log.error("start-up refused (see the messages above); the service stops and is not restarted")
            return ServiceOutcome.REFUSED
        if stop.is_set() and code == 0:
            log.info("service stopped")
            return ServiceOutcome.STOPPED
        log.error("monitor ended unexpectedly (exit code %s)", code)
        return ServiceOutcome.CRASHED
    finally:
        remove_handlers(handlers)


def self_check(options: ServiceOptions) -> int:
    """``--check``: what the service will need, without the service manager. Exit 0 ok, 2 problem."""
    from hfm_dashboard.config.startup import ConfigurationError, load_configuration
    from hfm_dashboard.adapters.file_maintenance import FileMaintenanceCalendar
    from hfm_dashboard.config.maintenance_loader import MaintenanceError
    from hfm_dashboard.config.owners_loader import OwnersError, load_owners
    problems: list[str] = []
    try:
        config = load_configuration(options.config_dir)
    except ConfigurationError as exc:
        problems.append(str(exc))
        config = None
    if config is not None:
        try:
            load_owners(options.config_dir, config.rules)
        except OwnersError as exc:
            problems.append(str(exc))
        try:
            FileMaintenanceCalendar(options.config_dir / config.rules.maintenance.windows_file, config.rules,
                                    set(config.environments.environments))
        except MaintenanceError as exc:
            problems.append(str(exc))
    for folder in (options.data_dir, options.log_dir):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / ".write-check"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            problems.append(f"cannot write to {folder} ({type(exc).__name__})")
    try:
        import servicemanager  # noqa: F401
        import win32service  # noqa: F401
    except ImportError:
        problems.append("pywin32 is not importable from the service's interpreter")
    for p in problems:
        print(f"PROBLEM: {p}")
    if not problems:
        print(f"OK: interpreter {sys.executable}, configuration valid, folders writable, pywin32 present")
    return 2 if problems else 0
