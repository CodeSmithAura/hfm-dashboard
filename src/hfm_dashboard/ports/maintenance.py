"""Port: maintenance windows for an environment (entered on the dashboard, slice 14)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from hfm_dashboard.domain.notices import MaintenanceWindow


class MaintenanceCalendar(Protocol):
    def windows(self, environment: str, at: datetime) -> list[MaintenanceWindow]:
        """Windows that could cover ``at`` (the engine checks component and time itself)."""
        ...


class NoMaintenance:
    """Used until the dashboard can enter windows."""

    def windows(self, environment: str, at: datetime) -> list[MaintenanceWindow]:
        return []


class FixedMaintenance:
    """In-memory windows per environment, for tests and offline runs."""

    def __init__(self, windows: dict[str, list[MaintenanceWindow]] | None = None) -> None:
        self._windows = dict(windows or {})

    def windows(self, environment: str, at: datetime) -> list[MaintenanceWindow]:
        return list(self._windows.get(environment, []))
