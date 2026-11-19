"""Typed model of maintenance.yaml: planned maintenance windows (interim until the dashboard form).

Times must carry a zone (``Z`` or an offset) and are converted to UTC (hard rule 5). A window with
no component covers every component of its environment.
"""

from __future__ import annotations

from datetime import UTC, timedelta

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from hfm_dashboard.domain.notices import MaintenanceWindow
from hfm_dashboard.domain.rules import Rules


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WindowEntry(_Strict):
    environment: str
    component: str | None = None
    start: AwareDatetime
    end: AwareDatetime
    reason: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _ordered(self) -> WindowEntry:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class MaintenanceFile(_Strict):
    windows: list[WindowEntry] = Field(default_factory=list)


def check_against(file: MaintenanceFile, rules: Rules, environments: set[str]) -> list[str]:
    """Problems against rules.yaml and environments.yaml, by position (no reason text)."""
    problems = []
    limit = timedelta(hours=rules.maintenance.max_window_hours)
    enabled = rules.enabled_components()
    for i, w in enumerate(file.windows):
        where = f"windows[{i}]"
        if w.environment not in environments:
            problems.append(f"{where}.environment: {w.environment!r} is not in environments.yaml")
        if w.component is not None and w.component not in enabled:
            problems.append(f"{where}.component: {w.component!r} is not a monitored component in rules.yaml")
        if w.end - w.start > limit:
            problems.append(f"{where}: longer than maintenance.max_window_hours "
                            f"({rules.maintenance.max_window_hours} h) - check the dates")
    return problems


def windows_by_environment(file: MaintenanceFile) -> dict[str, list[MaintenanceWindow]]:
    out: dict[str, list[MaintenanceWindow]] = {}
    for w in file.windows:
        out.setdefault(w.environment, []).append(MaintenanceWindow(
            start=w.start.astimezone(UTC), end=w.end.astimezone(UTC), component=w.component,
            reason=w.reason))
    return out
