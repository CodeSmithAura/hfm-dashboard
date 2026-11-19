"""Entry point: load configuration (refuse to start on any problem), then poll every environment.

    hfm-dashboard --config-dir config            # poll on the rules.yaml interval until stopped
    hfm-dashboard --config-dir config --once     # one poll per environment, print scores, exit
    hfm-dashboard ... --data-dir <folder>         # where history.sqlite lives (default: data)
    hfm-dashboard ... --once --no-history         # dry run: read nothing from and write nothing to history

Until the dashboard exists, each poll is summarised in the log. Secrets come from the
environment-variable secret store (interim, Q55). Exit codes: 0 ok, 1 dashboard could not start,
2 configuration problem.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from datetime import UTC, datetime, timedelta

from hfm_dashboard.adapters.env_secrets import EnvironmentSecretStore
from hfm_dashboard.adapters.file_maintenance import FileMaintenanceCalendar
from hfm_dashboard.adapters.smtp_mail import SmtpMailSender
from hfm_dashboard.adapters.sqlite_history import SqliteHistory
from hfm_dashboard.app.dashboard_source import LiveDashboardSource
from hfm_dashboard.app.logs import NOISY_LOGGERS, configure_logging, quiet_libraries  # noqa: F401 - re-exported
from hfm_dashboard.app.notice_mailer import NoticeMailer
from hfm_dashboard.app.notice_tracker import NoticeTracker
from hfm_dashboard.app.poller import Collectors, Poller, PollSnapshot
from hfm_dashboard.app.scheduler import build_scheduler
from hfm_dashboard.app.web_server import WebServer
from hfm_dashboard.app.wiring import build_collectors
from hfm_dashboard.config.maintenance_loader import MaintenanceError
from hfm_dashboard.config.owners_loader import OwnersError, load_owners
from hfm_dashboard.config.startup import ConfigurationError, load_configuration
from hfm_dashboard.domain.environment import Environment
from hfm_dashboard.domain.rules import Rules
from hfm_dashboard.domain.notice_text import OutgoingMail
from hfm_dashboard.domain.owners import OwnersConfig
from hfm_dashboard.domain.status import Status
from hfm_dashboard.ports.history import HistoryError, HistoryRepository
from hfm_dashboard.ports.mail import DeliveryError, MailSender

log = logging.getLogger("hfm_dashboard")

CollectorsFactory = Callable[[str, Environment, Rules], Collectors]
SenderFactory = Callable[[Environment, Rules], MailSender]
HISTORY_FILE = "history.sqlite"


def _default_factory(name: str, env: Environment, rules: Rules) -> Collectors:
    return build_collectors(env, rules, EnvironmentSecretStore())


def _default_sender(env: Environment, rules: Rules) -> MailSender:
    n = env.notices
    return SmtpMailSender(n.smtp_relay, n.smtp_port, n.from_address, rules.notices.smtp_timeout_seconds,
                          starttls=n.starttls)


def delivery_check_mail(env_name: str, env: Environment, owners: OwnersConfig) -> OutgoingMail:
    """A delivery check sent to the monitoring team (``--test-email``)."""
    return OutgoingMail(
        to=tuple(owners.monitoring_team), cc=(),
        subject=f"[HFM {env_name}] TEST: notice delivery check",
        body=(f"HFM availability notice - {env.display_name}\n\n"
              "This is a test message from the HFM availability monitor. If you received it, notice\n"
              "emails can reach this address through the SMTP relay. No action is needed.\n"),
    )


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
    for n in s.notices:
        since = n.since.strftime("%Y-%m-%d %H:%M UTC") if n.since else "-"
        nodes = f"{len(n.affected_nodes)} of {n.total_nodes} node(s)" if n.affected_nodes else "all nodes"
        extra = f"; also affected: {', '.join(n.downstream)}" if n.downstream else ""
        lines.append(f"  NOTICE {n.kind.value}{' (+manager)' if n.escalate else ''}: {n.component} - "
                     f"{nodes}, since {since}{extra}")
    return lines


def main(
    argv: list[str] | None = None,
    *,
    collectors_factory: CollectorsFactory = _default_factory,
    out: TextIO | None = None,
    stop: threading.Event | None = None,
    history: HistoryRepository | None = None,
    sender_factory: SenderFactory = _default_sender,
) -> int:
    out = out or sys.stdout
    p = argparse.ArgumentParser(prog="hfm-dashboard")
    p.add_argument("--config-dir", type=Path, default=Path("config"))
    p.add_argument("--once", action="store_true", help="poll each environment once, print, exit")
    p.add_argument("--env", action="append", help="only this environment (repeatable)")
    p.add_argument("--data-dir", type=Path, default=Path("data"), help="folder for history.sqlite")
    p.add_argument("--no-history", action="store_true", help="do not read or write the history store")
    p.add_argument("--test-email", metavar="ENV", help="send one test email to the monitoring team, exit")
    p.add_argument("--no-web", action="store_true", help="poll without serving the dashboard")
    p.add_argument("--ui-dir", type=Path, help="folder with templates/ and static/ overriding the built-in UI")
    args = p.parse_args(argv)

    try:
        config = load_configuration(args.config_dir)
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    rules = config.rules
    try:
        owners = load_owners(args.config_dir, rules)
    except OwnersError as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 2
    if owners is None:
        log.warning("%s not found: notices are worked out and logged but not emailed", rules.notices.owners_file)
    try:
        maintenance = FileMaintenanceCalendar(args.config_dir / rules.maintenance.windows_file, rules,
                                              set(config.environments.environments))
    except MaintenanceError as exc:
        print(f"refusing to start: {exc}", file=sys.stderr)
        return 2

    if args.test_email:
        env = config.environments.environments.get(args.test_email)
        if env is None or owners is None:
            print("--test-email needs a known environment and an owners file", file=sys.stderr)
            return 2
        try:
            sender_factory(env, rules).send(delivery_check_mail(args.test_email, env, owners))
        except DeliveryError as exc:
            print(f"test email NOT sent: {exc}", file=out)
            return 1
        print(f"test email sent to {len(owners.monitoring_team)} monitoring address(es)", file=out)
        return 0
    names = args.env or list(config.environments.environments)
    unknown = [n for n in names if n not in config.environments.environments]
    if unknown:
        print(f"unknown environment(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    if args.no_history:
        history = None
    elif history is None:
        try:
            history = SqliteHistory(args.data_dir / HISTORY_FILE)
        except HistoryError as exc:
            print(f"refusing to start: {exc}", file=sys.stderr)
            return 2

    pollers = [Poller(n, config.environments.environments[n], rules,
                      collectors_factory(n, config.environments.environments[n], rules), history=history,
                      notices=NoticeTracker(n, rules, maintenance=maintenance, history=history, deliver=NoticeMailer(
                          config.environments.environments[n], rules, owners,
                          sender_factory(config.environments.environments[n], rules))))
               for n in names]

    retention = timedelta(days=rules.collection.history_retention_days)

    def purge() -> None:
        if history is None:
            return
        try:
            deleted = history.purge_before(datetime.now(UTC) - retention)
            if deleted:
                log.info("history: purged %d row(s) older than %d days", deleted, retention.days)
        except Exception as exc:  # noqa: BLE001 - purging must never stop monitoring
            log.error("history: purge failed (%s)", type(exc).__name__)

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
    scheduler = build_scheduler(pollers, rules.collection.poll_interval_seconds, report,
                                purge=purge if history is not None else None)
    web = None
    if not args.no_web:
        from hfm_dashboard.web.app import create_app
        envs = config.environments.environments
        source = LiveDashboardSource({p.name: p for p in pollers}, {n: envs[n] for n in names}, history,
                                     maintenance=maintenance)
        mon = config.environments.monitoring
        web = WebServer(create_app(source, rules, ui_dir=args.ui_dir), mon.dashboard_bind, mon.dashboard_port)
    if web is not None and not web.start():
        web.stop()
        return 1        # in the Windows service: a crash, so recovery retries (e.g. port still in use)
    scheduler.start()
    log.info("polling %s every %s s", ", ".join(names), rules.collection.poll_interval_seconds)
    try:
        while not stop.wait(1.0):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        if web is not None:
            web.stop()
        scheduler.shutdown(wait=True)
    return 0


def run() -> None:
    configure_logging()   # console, UTC timestamps (hard rule 5); the service adds a log file
    raise SystemExit(main())


if __name__ == "__main__":
    run()
