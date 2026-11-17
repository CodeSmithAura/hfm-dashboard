"""Port: disk space and System event log counts for one host (read-only)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol


class HostQueryError(Exception):
    """The host could not be queried. The message is shown on the dashboard: never a secret."""


@dataclass(frozen=True, slots=True)
class HostHealth:
    volumes: dict[str, float]                                       # volume label -> % free
    service_crashes: dict[str, int] = field(default_factory=dict)   # requested service -> crash events
    unexpected_shutdowns: int = 0


class HostHealthSource(Protocol):
    def query(self, host: str, service_names: Sequence[str], lookback_minutes: int,
              crash_event_ids: Sequence[int], shutdown_event_ids: Sequence[int]) -> HostHealth:
        """Every requested service name appears in ``service_crashes`` (0 when none)."""
        ...
