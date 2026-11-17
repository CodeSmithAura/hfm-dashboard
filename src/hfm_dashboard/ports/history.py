"""Port: status history kept off the EPM SQL Server (a single point of failure, CLAUDE.md).

Stores, per environment: one summary row per poll, every node's current state (restored after a
restart so "down since" and flap counters survive), and every confirmed status change.
All times are timezone-aware UTC.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from hfm_dashboard.domain.snapshot import NodeKey, Transition
from hfm_dashboard.domain.status import NodeState


class HistoryError(Exception):
    """The history store could not be read or written. Monitoring continues without it."""


@dataclass(frozen=True, slots=True)
class PollRecord:
    environment: str
    observed_at: datetime
    overall_pct: float | None
    uncapped_pct: float | None
    band: str
    capped_by: tuple[str, ...]
    duration_seconds: float
    collector_errors: tuple[str, ...]


class HistoryRepository(Protocol):
    def save_poll(self, record: PollRecord, states: dict[NodeKey, NodeState],
                  changes: list[Transition]) -> None:
        """Store one poll atomically: summary, current node states, status changes."""
        ...

    def load_node_states(self, environment: str) -> dict[NodeKey, NodeState]:
        ...

    def transitions(self, environment: str, since: datetime) -> list[Transition]:
        ...

    def polls(self, environment: str, since: datetime) -> list[PollRecord]:
        ...

    def purge_before(self, cutoff: datetime) -> int:
        """Delete poll rows and transitions older than ``cutoff``; returns rows deleted.
        Current node states are never purged."""
        ...
