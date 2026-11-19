"""maintenance.yaml validation (interim maintenance windows, D32)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hfm_dashboard.config.maintenance_loader import MaintenanceError, parse_maintenance
from tests.conftest import REPO_ROOT

TEMPLATE = (REPO_ROOT / "config" / "maintenance.template.yaml").read_text(encoding="utf-8")
ENVS = {"PROD", "NONPROD"}


def one(**fields) -> str:
    w = {"environment": "PROD", "start": '"2026-11-22T02:00:00Z"', "end": '"2026-11-22T03:00:00Z"', **fields}
    return "windows:\n  - " + "\n    ".join(f"{k}: {v}" for k, v in w.items()) + "\n"


def bad(text, rules, match):
    with pytest.raises(MaintenanceError, match=match):
        parse_maintenance(text, rules, ENVS)


def test_template_is_valid(repo_rules):
    windows = parse_maintenance(TEMPLATE, repo_rules, ENVS)
    assert [w.component for w in windows["NONPROD"]] == [None]          # whole environment
    (prod,) = windows["PROD"]
    assert prod.component == "epmhfm" and prod.start == datetime(2026, 11, 22, 2, tzinfo=UTC)


def test_empty_file_means_no_windows(repo_rules):
    assert parse_maintenance("", repo_rules, ENVS) == {}
    assert parse_maintenance("windows: []\n", repo_rules, ENVS) == {}


def test_offset_converted_to_utc(repo_rules):
    (w,) = parse_maintenance(one(start='"2026-11-22T07:30:00+05:30"', end='"2026-11-22T08:30:00+05:30"'),
                             repo_rules, ENVS)["PROD"]
    assert w.start == datetime(2026, 11, 22, 2, tzinfo=UTC) and w.start.tzinfo is UTC


def test_unquoted_yaml_timestamp_accepted(repo_rules):
    (w,) = parse_maintenance(one(start="2026-11-22T02:00:00Z", end="2026-11-22T03:00:00Z"),
                             repo_rules, ENVS)["PROD"]
    assert w.end == datetime(2026, 11, 22, 3, tzinfo=UTC)


def test_time_without_zone_refused(repo_rules):
    bad(one(start='"2026-11-22T02:00:00"'), repo_rules, "start")


def test_end_must_be_after_start(repo_rules):
    bad(one(end='"2026-11-22T02:00:00Z"'), repo_rules, "end must be after start")


def test_unknown_environment(repo_rules):
    bad(one(environment="UAT"), repo_rules, r"windows\[0\]\.environment: 'UAT' is not in environments.yaml")


@pytest.mark.parametrize("component", ["no_such", "fr_rmi"])          # fr_rmi is disabled in rules.yaml
def test_component_must_be_monitored(repo_rules, component):
    bad(one(component=component), repo_rules, r"windows\[0\]\.component")


def test_longer_than_max_window_hours_refused(repo_rules):
    # a mistyped month: 2026-12 instead of 2026-11
    bad(one(end='"2026-12-22T03:00:00Z"'), repo_rules, "longer than maintenance.max_window_hours")


def test_exactly_max_window_hours_allowed(repo_rules):
    assert parse_maintenance(one(end='"2026-11-25T02:00:00Z"'), repo_rules, ENVS)          # 72 h


def test_unknown_key_refused(repo_rules):
    bad(one(components="epmhfm"), repo_rules, "components")


def test_placeholder_refused(repo_rules):
    bad(one(environment='"{{ENV}}"'), repo_rules, "placeholder")


def test_errors_do_not_echo_reason_text(repo_rules):
    with pytest.raises(MaintenanceError) as err:
        parse_maintenance(one(reason='"patch on secret-host-01"', environment="UAT"), repo_rules, ENVS)
    assert "secret-host-01" not in str(err.value)


def test_repo_rules_values(repo_rules):
    assert repo_rules.maintenance.windows_file == "maintenance.yaml"
    assert repo_rules.maintenance.max_window_hours == 72
