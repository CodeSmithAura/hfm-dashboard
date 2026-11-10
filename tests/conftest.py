"""Shared fixtures.

Two kinds of rules are used:
- ``repo_rules``: the real config/rules.yaml, to prove the shipped file is valid and to pin the
  behaviour the application team agreed (these tests change when rules.yaml changes - on purpose).
- ``rules_dict`` / ``build_rules``: a small synthetic rules set with round numbers, so the scoring
  maths is tested independently of the current assumed weights.
"""

from __future__ import annotations

import copy
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from hfm_dashboard.config.rules_loader import load_rules
from hfm_dashboard.domain.rules import Rules

REPO_ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = REPO_ROOT / "config" / "rules.yaml"
TEMPLATE_PATH = REPO_ROOT / "config" / "environments.template.yaml"
TEMPLATE = TEMPLATE_PATH.read_text(encoding="utf-8")
FIXTURES = REPO_ROOT / "tests" / "fixtures"

T0 = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


def _fake_value(name: str) -> str:
    """Invented value for a template placeholder (hard rule 1: never a real identifier)."""
    if name.endswith("_PORT"):
        return "8443"
    if name.endswith("_URL"):
        return "https://lb.example.test"
    if name.endswith("_ADDRESS"):
        return "hfm-monitor@example.test"
    return name.lower().replace("_", "-") + ".example.test"


def fill(text: str) -> str:
    return re.sub(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", lambda m: _fake_value(m.group(1)), text)


FILLED = fill(TEMPLATE)

_BASE: dict[str, Any] = {
    "version": 1,
    "checks": {
        "windows_service": {"up": ["Running"], "degraded": ["Paused"], "down": ["Stopped"],
                            "timeout_seconds": 20},
        "url_probe": {
            "timeout_seconds": 60,
            "up_on_http_status": [200, 302],
            "down_on_http_status": [500, 503],
            "degraded_after_seconds": {"web_login": 10},
        },
        "weblogic_server": {
            "up": ["RUNNING"],
            "degraded": ["WARNING"],
            "down": ["FAILED"],
            "stuck_thread_max_time_seconds": 3600,
            "degraded_if_stuck_threads_above": 0,
            "degraded_if_heap_used_pct_above": 90,
        },
        "sql_server": {
            "down_if_service_not_running": True,
            "degraded_if_blocking_longer_than_minutes": 5,
            "degraded_if_deadlocks_in_15_min_above": 5,
            "degraded_if_log_used_pct_above": 90,
            "degraded_if_tempdb_used_pct_above": 90,
            "degraded_if_disk_free_pct_below": 10,
            "down_if_disk_free_pct_below": 3,
            "degraded_if_backup_failed_in_last_hours": 24,
            "degraded_if_agent_job_failed_in_last_hours": 24,
            "degraded_if_query_running_longer_than_minutes": 60,
        },
        "confirm_down_after_consecutive_failures": 2,
    },
    "criticality_weights": {"critical": 3, "low": 1},
    "components": {
        # hard dependency, single node
        "db": {"display_name": "DB", "nodes": ["db1"], "checks": ["windows_service"],
               "criticality": "critical", "hard_dependency": True},
        # hard dependency, two nodes (caps only when ALL are down)
        "lb": {"display_name": "LB", "nodes": ["web1", "web2"], "checks": ["windows_service"],
               "criticality": "critical", "hard_dependency": True},
        # three-node cluster, not a hard dependency
        "app": {"display_name": "App", "nodes": ["app1", "app2", "app3"],
                "checks": ["windows_service", "url_probe:web_login"], "criticality": "critical"},
        "admin": {"display_name": "Admin", "nodes": ["web1"], "checks": ["windows_service"],
                  "criticality": "low"},
        "retired": {"display_name": "Retired", "nodes": ["web1"], "checks": ["windows_service"],
                    "criticality": "low", "enabled": False},
    },
    "scoring": {
        "node_points": {"up": 100, "degraded": 50, "down": 0},
        "multi_node_mode": "node_share",
        "overall": "weighted_average",
        "unknown_nodes": "exclude",
        "hard_dependency_cap_pct": 0,
        "bands": {"green_min_pct": 95, "amber_min_pct": 70},
    },
    "notices": {
        "channel": "email",
        "first_notice_after_minutes": 15,
        "first_notice_after_minutes_close": 5,
        "repeat_every_minutes": 30,
        "escalate_to_manager_after_minutes": 60,
        "notify_on_degraded": False,
        "send_recovery_notice": True,
        "require_acknowledgement": False,
        "group_downstream_under_root_cause": True,
        "include_restart_guidance": True,
        "auto_restart": False,
        "owners_file": "owners.yaml",
    },
    "close_calendar": {
        "business_days_before_month_end": 2,
        "business_days_after_month_start": 7,
        "quarter_end_months": [3, 6, 9, 12],
        "extra_dates": [],
    },
    "maintenance": {"suppress_notices_in_window": True, "show_on_dashboard_as": "Maintenance"},
    "collection": {
        "poll_interval_seconds": 60,
        "store_times_in": "UTC",
        "history_retention_days": 90,
        "history_views_enabled": False,
    },
    "environments": {"PROD": {"notices_enabled": True}},
}


@pytest.fixture
def rules_dict() -> dict[str, Any]:
    return copy.deepcopy(_BASE)


@pytest.fixture
def build_rules(rules_dict):
    def _build(**scoring_overrides: Any) -> Rules:
        rules_dict["scoring"].update(scoring_overrides)
        return Rules.model_validate(rules_dict)

    return _build


@pytest.fixture
def rules(build_rules) -> Rules:
    return build_rules()


@pytest.fixture(scope="session")
def repo_rules() -> Rules:
    return load_rules(RULES_PATH)
