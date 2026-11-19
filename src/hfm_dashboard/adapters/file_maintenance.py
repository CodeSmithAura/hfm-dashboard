"""MaintenanceCalendar backed by maintenance.yaml, re-read when the file changes (no restart needed).

- At start-up an invalid file raises ``MaintenanceError`` (the service refuses to start).
- Edited while running: re-read on the next poll. If the new version is invalid, the last valid
  windows stay in force and the problem is logged once per change - a typo must not cancel a window
  and email owners about planned work.
- Missing (or deleted) file: no maintenance windows.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path

from hfm_dashboard.config.maintenance_loader import MaintenanceError, parse_maintenance
from hfm_dashboard.domain.notices import MaintenanceWindow
from hfm_dashboard.domain.rules import Rules

log = logging.getLogger(__name__)


class FileMaintenanceCalendar:
    def __init__(self, path: Path, rules: Rules, environments: set[str]) -> None:
        self._path = path
        self._rules = rules
        self._environments = set(environments)
        self._lock = threading.Lock()
        self._stamp: tuple[int, int] | None = None
        self._windows: dict[str, list[MaintenanceWindow]] = {}
        stamp = self._current_stamp()
        if stamp is not None:
            self._windows = self._read()            # raises MaintenanceError: start refused
            self._stamp = stamp
            log.info("maintenance: %d window(s) loaded from %s", self._count(), path.name)

    def _current_stamp(self) -> tuple[int, int] | None:
        try:
            st = self._path.stat()
        except FileNotFoundError:
            return None
        return st.st_mtime_ns, st.st_size

    def _read(self) -> dict[str, list[MaintenanceWindow]]:
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MaintenanceError(f"cannot read {self._path.name}: {exc.strerror}") from exc
        return parse_maintenance(text, self._rules, self._environments, source=self._path.name)

    def _count(self) -> int:
        return sum(len(v) for v in self._windows.values())

    def _refresh(self) -> None:
        stamp = self._current_stamp()
        if stamp == self._stamp:
            return
        self._stamp = stamp
        if stamp is None:
            if self._windows:
                log.info("maintenance: %s removed - no maintenance windows", self._path.name)
            self._windows = {}
            return
        try:
            self._windows = self._read()
        except MaintenanceError as exc:
            log.error("maintenance: %s changed but is invalid - the previous %d window(s) stay in force:\n%s",
                      self._path.name, self._count(), exc)
            return
        log.info("maintenance: %d window(s) loaded from %s", self._count(), self._path.name)

    def windows(self, environment: str, at: datetime) -> list[MaintenanceWindow]:
        """Windows of the environment that have not ended at ``at``."""
        with self._lock:
            self._refresh()
            return [w for w in self._windows.get(environment, []) if w.end > at]
