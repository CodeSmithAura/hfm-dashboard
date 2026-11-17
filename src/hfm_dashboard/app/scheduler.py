"""Schedule polls with APScheduler: one job per environment every ``poll_interval_seconds``.

``max_instances=1`` and ``coalesce=True`` mean a slow poll is never overlapped and missed runs are
not queued up; ``Poller.poll_once`` guards against overlap as well.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from hfm_dashboard.app.poller import Poller, PollSnapshot


def build_scheduler(
    pollers: Iterable[Poller],
    interval_seconds: int,
    on_snapshot: Callable[[PollSnapshot], None],
    first_run: datetime | None = None,
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=UTC)
    start = first_run or datetime.now(UTC)
    for poller in pollers:
        def run(p: Poller = poller) -> None:
            snapshot = p.poll_once()
            if snapshot is not None:
                on_snapshot(snapshot)

        scheduler.add_job(
            run,
            IntervalTrigger(seconds=interval_seconds, timezone=UTC),
            id=f"poll-{poller.name}",
            name=f"poll {poller.name}",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=interval_seconds,
            next_run_time=start,
        )
    return scheduler
