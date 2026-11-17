"""Check Windows services for one environment from the command line, and optionally record
masked sample responses for the test fixtures.

    python -m hfm_dashboard.tools.check_services --env NONPROD
    python -m hfm_dashboard.tools.check_services --env NONPROD --record recordings
    python -m hfm_dashboard.tools.check_services --env NONPROD --username DOMAIN\\dev.user

Read-only: runs the same Get-Service query as the monitoring service. Only the chosen
environment's block in environments.yaml must be filled in. Output shows node keys, not host
names. ``--username`` (development testing only, decision D11) prompts for the password; it is
refused for PROD, where the service's gMSA identity is used.

Exit codes: 0 every service Up, 1 any service not Up, 2 configuration or usage problem.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from hfm_dashboard.adapters.winrm_services import Credential, WinRMServiceSource
from hfm_dashboard.app.service_collector import collect_windows_services
from hfm_dashboard.config.environment_loader import EnvironmentConfigError, load_environments
from hfm_dashboard.config.rules_loader import RulesError, load_rules
from hfm_dashboard.domain.status import CheckResult, Status
from hfm_dashboard.tools.masking import build_masks, leftovers, mask

PRODUCTION = "PROD"
MASKED_USER = "monitor-user"

SourceFactory = Callable[..., Any]


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="check_services", description=__doc__.split("\n\n")[0])
    p.add_argument("--env", required=True, help="environment name in environments.yaml, e.g. NONPROD")
    p.add_argument("--config-dir", default="config", type=Path,
                   help="folder holding rules.yaml and environments.yaml (default: config)")
    p.add_argument("--record", type=Path, metavar="DIR",
                   help="write masked raw responses and a summary to DIR")
    p.add_argument("--username", help="development testing only (D11); password is prompted")
    return p.parse_args(argv)


def _table(results: list[CheckResult]) -> list[str]:
    rows = [("COMPONENT", "NODE", "STATUS", "DETAIL")]
    rows += [(r.component, r.node, r.status.value.upper(), r.detail) for r in results]
    widths = [max(len(row[i]) for row in rows) for i in range(3)]
    return [f"{a:<{widths[0]}}  {b:<{widths[1]}}  {c:<{widths[2]}}  {d}" for a, b, c, d in rows]


def main(
    argv: list[str] | None = None,
    *,
    source_factory: SourceFactory = WinRMServiceSource,
    getpass_fn: Callable[[str], str] = getpass.getpass,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    args = _parse_args(argv)

    try:
        rules = load_rules(args.config_dir / "rules.yaml")
        env = load_environments(args.config_dir / "environments.yaml", rules,
                                only_environment=args.env).environments[args.env]
    except (RulesError, EnvironmentConfigError) as exc:
        print(f"configuration problem: {exc}", file=err)
        return 2

    credential = None
    extra_masks: list[tuple[str, str]] = []
    if args.username:
        if args.env.upper() == PRODUCTION:
            print("--username is not allowed for PROD (decision D11): the service's gMSA identity is "
                  "used there", file=err)
            return 2
        credential = Credential(args.username, getpass_fn(f"Password for {args.username}: "))
        extra_masks.append((args.username, MASKED_USER))
        if "\\" in args.username:
            domain, user = args.username.split("\\", 1)
            extra_masks += [(domain, "DOMAIN"), (user, MASKED_USER)]

    raw: dict[str, str] = {}
    source = source_factory(env.winrm, rules.checks.windows_service.timeout_seconds,
                            credential=credential, on_output=lambda host, text: raw.__setitem__(host, text))
    results = collect_windows_services(env, rules, source, now_fn())

    lines = _table(results)
    for line in lines:
        print(line, file=out)
    not_up = [r for r in results if r.status is not Status.UP]
    print(f"\n{len(results)} service check(s), {len(not_up)} not Up", file=out)

    if args.record:
        if not _record(args.record, args.env, env, raw, lines, extra_masks, out, err):
            return 2
    return 1 if not_up else 0


def _record(folder: Path, env_name: str, env, raw: dict[str, str], lines: list[str],
            extra_masks: list[tuple[str, str]], out: TextIO, err: TextIO) -> bool:
    pairs = build_masks(env, extra_masks)
    node_of_host = {node.host: key for key, node in env.nodes.items()}
    files = {f"{env_name.lower()}_{node_of_host.get(host, 'unknown_node')}.json": text
             for host, text in raw.items()}
    files[f"{env_name.lower()}_summary.txt"] = "\n".join(lines) + "\n"

    masked = {name: mask(text, pairs) for name, text in files.items()}
    unsafe = {name: leftovers(text, pairs) for name, text in masked.items()}
    unsafe = {name: kinds for name, kinds in unsafe.items() if kinds}
    if unsafe:
        for name, kinds in unsafe.items():
            print(f"not recorded: {name} still contains {', '.join(kinds)}", file=err)
        return False

    folder.mkdir(parents=True, exist_ok=True)
    for name, text in masked.items():
        (folder / name).write_text(text, encoding="utf-8")
    print(f"recorded {len(masked)} masked file(s) in {folder} - review them before adding to "
          f"tests/fixtures/winrm/", file=out)
    return True


if __name__ == "__main__":
    raise SystemExit(main())
