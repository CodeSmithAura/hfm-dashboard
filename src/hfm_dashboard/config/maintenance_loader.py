"""Load maintenance.yaml (interim maintenance windows, until the dashboard form of slice 15).

A missing file means no planned maintenance. A present file must be valid: known environment and
component, times with a zone, end after start, not longer than ``maintenance.max_window_hours``.
"""

from __future__ import annotations

from pydantic import ValidationError

from hfm_dashboard.config.environment_loader import find_placeholders
from hfm_dashboard.config.yaml_loader import format_validation_error, load_mapping
from hfm_dashboard.domain.maintenance_file import MaintenanceFile, check_against, windows_by_environment
from hfm_dashboard.domain.notices import MaintenanceWindow
from hfm_dashboard.domain.rules import Rules


class MaintenanceError(Exception):
    """maintenance.yaml is invalid. Messages name positions, never the reason text."""


def parse_maintenance(text: str, rules: Rules, environments: set[str],
                      source: str = "maintenance.yaml") -> dict[str, list[MaintenanceWindow]]:
    if not text.strip():
        return {}
    data = load_mapping(text, source, MaintenanceError)
    unfilled = find_placeholders(data)
    if unfilled:
        raise MaintenanceError(f"{source}: {len(unfilled)} placeholder(s) not filled in:\n"
                               + "\n".join(f"  {u}" for u in unfilled))
    try:
        parsed = MaintenanceFile.model_validate(data)
    except ValidationError as exc:
        raise MaintenanceError(format_validation_error(exc, source)) from exc
    problems = check_against(parsed, rules, environments)
    if problems:
        raise MaintenanceError(f"{source}: {len(problems)} problem(s)\n" + "\n".join(f"  {p}" for p in problems))
    return windows_by_environment(parsed)
