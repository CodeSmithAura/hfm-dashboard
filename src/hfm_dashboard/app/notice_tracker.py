"""Runs the notice engine for one environment each poll and keeps its state.

Notice states are restored from the history store at start-up, so a restart never re-sends a first
notice, and saved after every poll. Every notice is logged in the history store with whether it was
handed to delivery and why not: notices disabled for the environment (rules.yaml
``environments.<name>.notices_enabled``), no delivery configured yet (email: slice 13), or a failed
delivery. Neither history nor delivery problems ever stop polling.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime

from hfm_dashboard.domain.notices import ComponentNoticeState, Notice, evaluate_notices
from hfm_dashboard.domain.rules import Rules
from hfm_dashboard.domain.snapshot import NodeKey
from hfm_dashboard.domain.status import NodeState, Status
from hfm_dashboard.ports.history import HistoryRepository
from hfm_dashboard.ports.mail import DeliveryError
from hfm_dashboard.ports.maintenance import MaintenanceCalendar, NoMaintenance

log = logging.getLogger(__name__)

Deliver = Callable[[list[Notice]], None]


class NoticeTracker:
    def __init__(
        self,
        environment: str,
        rules: Rules,
        maintenance: MaintenanceCalendar | None = None,
        history: HistoryRepository | None = None,
        deliver: Deliver | None = None,
    ) -> None:
        self.environment = environment
        self._rules = rules
        self._maintenance = maintenance or NoMaintenance()
        self._history = history
        self._deliver = deliver
        self._states: dict[str, ComponentNoticeState] = self._restore()

    @property
    def enabled(self) -> bool:
        env = self._rules.environments.get(self.environment)
        return bool(env and env.notices_enabled)

    @property
    def states(self) -> dict[str, ComponentNoticeState]:
        return dict(self._states)

    def _restore(self) -> dict[str, ComponentNoticeState]:
        if self._history is None:
            return {}
        try:
            saved = self._history.load_notice_states(self.environment)
        except Exception as exc:  # noqa: BLE001 - start without notice history rather than not at all
            log.error("%s: could not restore notice states (%s)", self.environment, type(exc).__name__)
            return {}
        return {c: s for c, s in saved.items() if c in self._rules.enabled_components()}

    def process(self, at: datetime, node_states: Mapping[NodeKey, NodeState],
                component_status: Mapping[str, Status]) -> list[Notice]:
        try:
            windows = self._maintenance.windows(self.environment, at)
        except Exception as exc:  # noqa: BLE001 - unknown maintenance: notify rather than stay silent
            log.error("%s: could not read maintenance windows (%s)", self.environment, type(exc).__name__)
            windows = []
        self._states, notices = evaluate_notices(self.environment, self._states, node_states,
                                                 component_status, self._rules, at, windows)
        delivered, note = self._hand_over(notices)
        if self._history is not None:
            try:
                self._history.save_notice_states(self.environment, self._states)
                if notices:
                    self._history.record_notices(notices, delivered, note)
            except Exception as exc:  # noqa: BLE001 - history must never stop monitoring
                log.error("%s: could not save notices (%s)", self.environment, type(exc).__name__)
        return notices

    def _hand_over(self, notices: list[Notice]) -> tuple[bool, str]:
        if not notices:
            return False, ""
        if not self.enabled:
            return False, "notices disabled for this environment"
        if self._deliver is None:
            return False, "no delivery configured"
        try:
            self._deliver(notices)
            return True, ""
        except Exception as exc:  # noqa: BLE001 - a delivery failure must not stop polling
            reason = str(exc) if isinstance(exc, DeliveryError) else type(exc).__name__
            log.error("%s: notice delivery failed (%s)", self.environment, reason)
            return False, f"delivery failed ({reason})"
