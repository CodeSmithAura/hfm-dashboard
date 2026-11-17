"""Entry point: load configuration (refuse to start on any problem), then poll every environment.

    hfm-dashboard --config-dir config            # poll on the rules.yaml interval until stopped
    hfm-dashboard --config-dir config --once     # one poll per environment, print scores, exit

Until the dashboard exists, each poll is summarised in the log. Secrets come from the
environment-variable secret store (interim, Q55). Exit codes: 0 ok, 2 configuration problem.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from hfm_dashboard.adapters.env_secrets import EnvironmentSecretStore
from hfm_dashboard.app.poller import Collectors, Poller, PollSnapshot
from hfm_dashboard.app.scheduler import build_scheduler
from hfm_dashboard.app.wiring import build_collectors
from hfm_dashboard.config.startup import ConfigurationError, load_configuration
from hfm_dashboard.domain.environment import Environment
from hfm_dashboard.domain.rules import Rules
from hfm_dashboard.domain.status import Status

log = logging.getLogger("hfm_dashboard")

CollectorsFactory = Callable[[str, Environment, Rules], Collectors]


def _default_factory(name: str, env: Environment, rules: Rules) -> Collectors:
    return build_collectors(env, rules, EnvironmentSecretStore())


def summarise(s: PollSnapshot) -> list[str]:
    """Human-readable lines for one poll (node keys only, no hosts)."""
    pct = "n/a" if s.score.overall_pct is None else f"{s.score.overall_pct:.2f}%"
    head = f"{s.environment}: {pct} {s.score.band.value.upper()}"
    if s.score.capped_by:
        head += f" (capped: {', '.join(s.score.capped_by)} down)"
    head += f" - poll {s.duration_seconds:.1f} s"
    lines = [head]
    for (cid, node), st in sorted(s.node_states.items()):
        if st.status is not Status.UP:
            lines.append(f"  {cid}/{node}: {st.status.value.upper()} - {st.detail}")
    for r in s.load_balancer:
        if r.status is not Status.UP:
            lines.append(f"  load balancer {r.check}: {r.status.value.upper()} - {r.detail} (not scored, Q57)")
    if s.collector_errors:
        lines.append(f"  collector errors: {', '.join(s.collector_errors)}")
    return lines


def main(
    argv: list[str] | None = None,
    *,
    collectors_factory: CollectorsFactory = _default_factory,
    out: TextIO | None = None,
    stop: threading.Event | None = None,
) -> int:
    out = out or sys.stdout
    p = argparse.ArgumentParser(prog="hfm-dashboard")
    p.add_argument("--config-dir", type=Path, default=Path("config"))
    p.add_argument("--once", action="store_true", help="poll each environment once, print, exit")
    p.add_argument("--env", action="append", help="only this environment (repeatable)")
    args = p.parse_args(argv)

    try:
        config = load_configuration(args.config_dir)
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    rules = config.rules
    names = args.env or list(config.environments.environments)
    unknown = [n for n in names if n not in config.environments.environments]
    if unknown:
        print(f"unknown environment(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    pollers = [Poller(n, config.environments.environments[n], rules,
                      collectors_factory(n, config.environments.environments[n], rules)) for n in names]

    def report(snapshot: PollSnapshot) -> None:
        for line in summarise(snapshot):
            print(line, file=out)
        out.flush()

    if args.once:
        for poller in pollers:
            snapshot = poller.poll_once()
            if snapshot is not None:
                report(snapshot)
        return 0

    stop = stop or threading.Event()
    scheduler = build_scheduler(pollers, rules.collection.poll_interval_seconds, report)
    scheduler.start()
    log.info("polling %s every %s s", ", ".join(names), rules.collection.poll_interval_seconds)
    try:
        while not stop.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.shutdown(wait=True)
    return 0


NOISY_LOGGERS = ("pypsrp", "spnego", "requests", "urllib3", "httpx", "httpcore", "apscheduler")


def quiet_libraries() -> None:
    """Library INFO logs repeat every poll and include scripts and URLs with host names: keep only
    their warnings and errors."""
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.Formatter.converter = time.gmtime   # log timestamps in UTC (hard rule 5)
    quiet_libraries()
    raise SystemExit(main())


if __name__ == "__main__":
    run()
