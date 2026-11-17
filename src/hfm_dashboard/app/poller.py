"""Poll one environment: run every collector, fold results into node states, score.

The collectors run in parallel (each blocks up to its own timeout). A collector that raises
loses only its own checks: they are missing from the results and ``fold_poll`` counts them as
Unknown. ``poll_once`` never runs twice at the same time for one environment; a poll that would
overlap a running one is skipped and logged (rules.yaml url_probe timeout can equal the interval).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime

from hfm_dashboard.app.host_collector import collect_host_health
from hfm_dashboard.app.notice_tracker import NoticeTracker
from hfm_dashboard.app.probe_collector import collect_url_probes
from hfm_dashboard.app.service_collector import collect_windows_services
from hfm_dashboard.app.sql_collector import collect_sql_server
from hfm_dashboard.app.weblogic_collector import collect_weblogic
from hfm_dashboard.domain.environment import Environment
from hfm_dashboard.domain.notices import Notice
from hfm_dashboard.domain.rules import Rules
from hfm_dashboard.domain.scoring import StabilityScore, score
from hfm_dashboard.domain.snapshot import (
    NodeKey,
    Transition,
    component_nodes,
    expected_checks,
    fold_poll,
    node_statuses,
    transitions,
)
from hfm_dashboard.domain.status import CheckResult, NodeState
from hfm_dashboard.ports.history import HistoryRepository, PollRecord
from hfm_dashboard.ports.host_health import HostHealthSource
from hfm_dashboard.ports.sql_health import SqlHealthSource
from hfm_dashboard.ports.url_probe import UrlProber
from hfm_dashboard.ports.weblogic import WeblogicSource
from hfm_dashboard.ports.windows_services import WindowsServiceSource

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Collectors:
    """The sources one environment is polled with (real adapters or fakes)."""

    services: WindowsServiceSource
    weblogic: WeblogicSource
    prober: UrlProber
    sql: SqlHealthSource
    host: HostHealthSource          # disk space and event log


@dataclass(frozen=True)
class PollSnapshot:
    environment: str
    observed_at: datetime                       # UTC, start of the poll
    duration_seconds: float
    results: tuple[CheckResult, ...]            # per-node check results (scored)
    load_balancer: tuple[CheckResult, ...]      # probes through the LB (reported only, Q57)
    node_states: dict[NodeKey, NodeState]
    score: StabilityScore
    collector_errors: tuple[str, ...] = field(default=())   # collectors that raised (names only)
    transitions: tuple[Transition, ...] = field(default=())  # confirmed status changes this poll
    notices: tuple[Notice, ...] = field(default=())          # caution notices produced this poll


class Poller:
    def __init__(
        self,
        name: str,
        env: Environment,
        rules: Rules,
        collectors: Collectors,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        history: HistoryRepository | None = None,
        notices: NoticeTracker | None = None,
    ) -> None:
        self.name = name
        self._env = env
        self._rules = rules
        self._collectors = collectors
        self._clock = clock
        self._monotonic = monotonic
        self._expected = expected_checks(env, rules)
        self._scope = component_nodes(env, rules)
        self._history = history
        self._notices = notices
        self._states: dict[NodeKey, NodeState] = self._restore()
        self._running = threading.Lock()
        self.latest: PollSnapshot | None = None

    def _restore(self) -> dict[NodeKey, NodeState]:
        """Node states from the history store, limited to nodes still monitored."""
        if self._history is None:
            return {}
        try:
            saved = self._history.load_node_states(self.name)
        except Exception as exc:  # noqa: BLE001 - start without history rather than not at all
            log.error("%s: could not restore node states (%s); starting fresh", self.name, type(exc).__name__)
            return {}
        restored = {k: v for k, v in saved.items() if k in self._expected}
        if restored:
            log.info("%s: restored %d node state(s) from history", self.name, len(restored))
        return restored

    def poll_once(self) -> PollSnapshot | None:
        """Run one poll; returns None (and logs) if a poll for this environment is still running."""
        if not self._running.acquire(blocking=False):
            log.warning("%s: previous poll still running, this poll is skipped", self.name)
            return None
        try:
            return self._poll()
        finally:
            self._running.release()

    def _poll(self) -> PollSnapshot:
        observed_at = self._clock()
        start = self._monotonic()
        env, rules, c = self._env, self._rules, self._collectors
        jobs: dict[str, Callable[[], object]] = {
            "windows_services": lambda: collect_windows_services(env, rules, c.services, observed_at),
            "weblogic": lambda: collect_weblogic(env, rules, c.weblogic, observed_at),
            "url_probes": lambda: collect_url_probes(env, rules, c.prober, observed_at),
            "sql_server": lambda: collect_sql_server(env, rules, c.sql, observed_at),
            "host_health": lambda: collect_host_health(env, rules, c.host, observed_at),
        }
        outputs: dict[str, object] = {}
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = {name: pool.submit(job) for name, job in jobs.items()}
            for name, future in futures.items():
                try:
                    outputs[name] = future.result()
                except Exception as exc:  # noqa: BLE001 - one collector must not stop the poll
                    log.error("%s: collector %s failed (%s)", self.name, name, type(exc).__name__)
                    errors.append(name)

        results: list[CheckResult] = []
        lb: list[CheckResult] = []
        for name, out in outputs.items():
            if name == "url_probes":
                results += out.nodes            # type: ignore[attr-defined]
                lb += out.load_balancer         # type: ignore[attr-defined]
            else:
                results += out                  # type: ignore[arg-type]

        previous = self._states
        self._states = fold_poll(previous, results, self._expected, rules, observed_at)
        changes = transitions(previous, self._states, observed_at)
        stability = score(node_statuses(self._states), rules, self._scope)
        produced: list[Notice] = []
        if self._notices is not None:
            try:
                produced = self._notices.process(observed_at, self._states,
                                                 {cid: c.status for cid, c in stability.components.items()})
            except Exception as exc:  # noqa: BLE001 - notices must never stop monitoring
                log.error("%s: notice engine failed (%s)", self.name, type(exc).__name__)
        snapshot = PollSnapshot(
            environment=self.name,
            observed_at=observed_at,
            duration_seconds=round(self._monotonic() - start, 3),
            results=tuple(results),
            load_balancer=tuple(lb),
            node_states=dict(self._states),
            score=stability,
            collector_errors=tuple(errors),
            transitions=tuple(changes),
            notices=tuple(produced),
        )
        self.latest = snapshot
        self._save(snapshot)
        return snapshot

    def _save(self, s: PollSnapshot) -> None:
        if self._history is None:
            return
        record = PollRecord(s.environment, s.observed_at, s.score.overall_pct, s.score.uncapped_pct,
                            s.score.band.value, s.score.capped_by, s.duration_seconds, s.collector_errors)
        try:
            self._history.save_poll(record, s.node_states, list(s.transitions))
        except Exception as exc:  # noqa: BLE001 - history must never stop monitoring
            log.error("%s: could not save history (%s)", self.name, type(exc).__name__)
