"""In-memory fake of the history port, for tests and offline runs."""

from __future__ import annotations

from datetime import datetime

from hfm_dashboard.domain.notices import ComponentNoticeState, Notice
from hfm_dashboard.domain.snapshot import NodeKey, Transition
from hfm_dashboard.domain.status import NodeState
from hfm_dashboard.ports.history import HistoryError, PollRecord


class FakeHistory:
    def __init__(self, fail_writes: bool = False) -> None:
        self.fail_writes = fail_writes
        self.poll_rows: list[PollRecord] = []
        self.states: dict[str, dict[NodeKey, NodeState]] = {}
        self.changes: dict[str, list[Transition]] = {}
        self.notice_states: dict[str, dict[str, ComponentNoticeState]] = {}
        self.notice_log: list[tuple[Notice, bool, str]] = []

    def save_poll(self, record: PollRecord, states: dict[NodeKey, NodeState],
                  changes: list[Transition]) -> None:
        if self.fail_writes:
            raise HistoryError("cannot write history (FakeError)")
        self.poll_rows.append(record)
        self.states.setdefault(record.environment, {}).update(states)
        self.changes.setdefault(record.environment, []).extend(changes)

    def load_node_states(self, environment: str) -> dict[NodeKey, NodeState]:
        return dict(self.states.get(environment, {}))

    def transitions(self, environment: str, since: datetime) -> list[Transition]:
        return [t for t in self.changes.get(environment, []) if t.at >= since]

    def polls(self, environment: str, since: datetime) -> list[PollRecord]:
        return [p for p in self.poll_rows if p.environment == environment and p.observed_at >= since]

    def purge_before(self, cutoff: datetime) -> int:
        before = len(self.poll_rows) + sum(len(v) for v in self.changes.values())
        self.poll_rows = [p for p in self.poll_rows if p.observed_at >= cutoff]
        self.changes = {e: [t for t in v if t.at >= cutoff] for e, v in self.changes.items()}
        return before - len(self.poll_rows) - sum(len(v) for v in self.changes.values())

    def save_notice_states(self, environment: str, states: dict[str, ComponentNoticeState]) -> None:
        if self.fail_writes:
            raise HistoryError("cannot write notice states (FakeError)")
        self.notice_states[environment] = dict(states)

    def load_notice_states(self, environment: str) -> dict[str, ComponentNoticeState]:
        return dict(self.notice_states.get(environment, {}))

    def record_notices(self, notices: list[Notice], delivered: bool, note: str = "") -> None:
        if self.fail_writes:
            raise HistoryError("cannot write notices (FakeError)")
        self.notice_log += [(n, delivered, note) for n in notices]

    def notices(self, environment: str, since: datetime) -> list[tuple[Notice, bool, str]]:
        return [x for x in self.notice_log if x[0].environment == environment and x[0].at >= since]
