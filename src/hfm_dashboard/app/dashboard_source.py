"""The running monitor as seen by the dashboard: latest snapshots, notices and maintenance."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime

from hfm_dashboard.app.poller import Poller, PollSnapshot
from hfm_dashboard.domain.environment import Environment
from hfm_dashboard.domain.notices import MaintenanceWindow, Notice
from hfm_dashboard.ports.history import HistoryRepository
from hfm_dashboard.ports.maintenance import MaintenanceCalendar, NoMaintenance

log = logging.getLogger(__name__)


class LiveDashboardSource:
    def __init__(
        self,
        pollers: Mapping[str, Poller],
        environments: Mapping[str, Environment],
        history: HistoryRepository | None = None,
        maintenance: MaintenanceCalendar | None = None,
    ) -> None:
        self._pollers = dict(pollers)
        self._envs = dict(environments)
        self._history = history
        self._maintenance = maintenance or NoMaintenance()

    def environments(self) -> list[tuple[str, str]]:
        return [(name, self._envs[name].display_name) for name in self._pollers]

    def environment(self, env_id: str) -> Environment:
        return self._envs[env_id]

    def snapshot(self, env_id: str) -> PollSnapshot | None:
        return self._pollers[env_id].latest

    def notices_since(self, env_id: str, since: datetime) -> Sequence[tuple[Notice, bool, str]]:
        if self._history is not None:
            try:
                return self._history.notices(env_id, since)
            except Exception as exc:  # noqa: BLE001 - the page still shows without notices
                log.error("dashboard: cannot read notices (%s)", type(exc).__name__)
        latest = self._pollers[env_id].latest
        return [(n, False, "") for n in latest.notices] if latest else []

    def maintenance(self, env_id: str, at: datetime) -> Sequence[MaintenanceWindow]:
        try:
            return self._maintenance.windows(env_id, at)
        except Exception as exc:  # noqa: BLE001
            log.error("dashboard: cannot read maintenance windows (%s)", type(exc).__name__)
            return []
