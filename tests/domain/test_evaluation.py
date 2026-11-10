"""Check evaluation: observations -> status, driven only by rules.checks.

Synthetic rules (conftest ``rules``) test the logic; ``repo_rules`` pins behaviour of the shipped
rules.yaml values (these change when rules.yaml changes - on purpose).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hfm_dashboard.domain.evaluation import (
    CollectionFailure,
    ServiceObservation,
    SqlServerObservation,
    UrlProbeObservation,
    WeblogicObservation,
    evaluate,
)
from hfm_dashboard.domain.rules import Rules
from hfm_dashboard.domain.status import Status

U, G, D, X = Status.UP, Status.DEGRADED, Status.DOWN, Status.UNKNOWN


def healthy_sql(**overrides) -> SqlServerObservation:
    values = dict(
        service_running=True,
        longest_blocking_minutes=0,
        deadlocks_last_15_min=0,
        max_log_used_pct=20,
        tempdb_used_pct=10,
        disk_free_pct={"C:": 40, "D:": 50},
        backup_failures_hours_ago=(),
        agent_job_failures_hours_ago=(),
        longest_query_minutes=1,
    )
    values.update(overrides)
    return SqlServerObservation(**values)


# --------------------------------------------------------------------------- common

class TestDispatch:
    @pytest.mark.parametrize("ref", ["windows_service", "url_probe:web_login", "weblogic_server",
                                     "sql_server"])
    def test_collection_failure_is_unknown(self, rules, ref):
        ev = evaluate(ref, CollectionFailure("access denied"), rules.checks)
        assert ev.status is X
        assert "access denied" in ev.detail

    def test_wrong_observation_type_is_a_programming_error(self, rules):
        with pytest.raises(TypeError):
            evaluate("windows_service", WeblogicObservation("RUNNING", 0, 10), rules.checks)

    def test_unknown_check_type(self, rules):
        with pytest.raises(ValueError):
            evaluate("ping", ServiceObservation("Running"), rules.checks)


# --------------------------------------------------------------------------- windows_service

class TestWindowsService:
    @pytest.mark.parametrize(("state", "expected"),
                             [("Running", U), ("Paused", G), ("Stopped", D)])
    def test_state_map(self, rules, state, expected):
        assert evaluate("windows_service", ServiceObservation(state), rules.checks).status is expected

    def test_unlisted_state_is_unknown(self, rules):
        ev = evaluate("windows_service", ServiceObservation("Exploded"), rules.checks)
        assert ev.status is X
        assert "not listed" in ev.detail

    def test_state_map_is_read_from_rules(self, rules_dict):
        rules_dict["checks"]["windows_service"]["down"].append("Paused")
        rules_dict["checks"]["windows_service"]["degraded"].remove("Paused")
        checks = Rules.model_validate(rules_dict).checks
        assert evaluate("windows_service", ServiceObservation("Paused"), checks).status is D

    @pytest.mark.parametrize(("state", "expected"), [
        ("Running", U), ("StartPending", G), ("StopPending", G), ("Paused", G),
        ("Stopped", D), ("NotFound", D),
    ])
    def test_shipped_rules(self, repo_rules, state, expected):
        assert evaluate("windows_service", ServiceObservation(state), repo_rules.checks).status is expected


# --------------------------------------------------------------------------- url_probe

class TestUrlProbe:
    REF = "url_probe:web_login"   # degraded after 10 s, timeout 60 s in the synthetic rules

    def ev(self, rules, **kw):
        kw.setdefault("http_status", 200)
        kw.setdefault("elapsed_seconds", 1.0)
        return evaluate(self.REF, UrlProbeObservation(**kw), rules.checks)

    def test_fast_ok(self, rules):
        assert self.ev(rules).status is U

    def test_redirect_listed_as_up(self, rules):
        assert self.ev(rules, http_status=302).status is U

    def test_at_threshold_is_up_above_is_degraded(self, rules):
        assert self.ev(rules, elapsed_seconds=10.0).status is U
        ev = self.ev(rules, elapsed_seconds=10.5)
        assert ev.status is G
        assert "slow" in ev.detail

    @pytest.mark.parametrize("code", [500, 503])
    def test_down_codes(self, rules, code):
        assert self.ev(rules, http_status=code).status is D

    @pytest.mark.parametrize("code", [404, 401, 502])   # 502 is not in the synthetic down list
    def test_unlisted_code_is_degraded(self, rules, code):
        ev = self.ev(rules, http_status=code)
        assert ev.status is G
        assert f"HTTP {code}" in ev.detail

    def test_timeout_flag_is_down(self, rules):
        assert self.ev(rules, http_status=None, elapsed_seconds=None, timed_out=True).status is D

    def test_elapsed_at_timeout_is_down(self, rules):
        assert self.ev(rules, elapsed_seconds=60.0).status is D

    def test_no_response_is_down(self, rules):
        ev = self.ev(rules, http_status=None, elapsed_seconds=0.1, error="connection refused")
        assert ev.status is D
        assert "connection refused" in ev.detail

    def test_missing_elapsed_is_unknown_unless_worse(self, rules):
        assert self.ev(rules, elapsed_seconds=None).status is X
        assert self.ev(rules, elapsed_seconds=None, http_status=404).status is G

    def test_slow_and_bad_code_report_both(self, rules):
        ev = self.ev(rules, http_status=404, elapsed_seconds=20)
        assert ev.status is G
        assert "HTTP 404" in ev.detail and "slow" in ev.detail

    @pytest.mark.parametrize(("probe", "secs", "expected"), [
        ("workspace_login", 10, U), ("workspace_login", 11, G),
        ("hfm_web", 15, U), ("hfm_web", 16, G),
        ("fr_web", 30, U), ("fr_web", 31, G), ("fr_web", 60, D),
    ])
    def test_shipped_thresholds(self, repo_rules, probe, secs, expected):
        ev = evaluate(f"url_probe:{probe}", UrlProbeObservation(200, secs), repo_rules.checks)
        assert ev.status is expected

    @pytest.mark.parametrize(("code", "expected"), [
        (200, U), (302, U), (307, U), (404, G), (403, G), (500, D), (502, D), (503, D), (504, D),
    ])
    def test_shipped_codes(self, repo_rules, code, expected):
        ev = evaluate("url_probe:hfm_web", UrlProbeObservation(code, 1.0), repo_rules.checks)
        assert ev.status is expected

    def test_code_in_both_lists_rejected(self, rules_dict):
        rules_dict["checks"]["url_probe"]["up_on_http_status"].append(500)
        with pytest.raises(ValidationError, match="both"):
            Rules.model_validate(rules_dict)

    def test_up_codes_required(self, rules_dict):
        del rules_dict["checks"]["url_probe"]["up_on_http_status"]
        with pytest.raises(ValidationError):
            Rules.model_validate(rules_dict)


# --------------------------------------------------------------------------- weblogic_server

class TestWeblogic:
    def ev(self, rules, state="RUNNING", stuck=0, heap=50.0):
        return evaluate("weblogic_server", WeblogicObservation(state, stuck, heap), rules.checks)

    def test_healthy(self, rules):
        assert self.ev(rules).status is U

    @pytest.mark.parametrize(("state", "expected"),
                             [("WARNING", G), ("FAILED", D), ("STARTING", X)])
    def test_state_map(self, rules, state, expected):
        assert self.ev(rules, state=state).status is expected

    def test_stuck_threads_above_threshold(self, rules):
        ev = self.ev(rules, stuck=1)
        assert ev.status is G
        assert "stuck" in ev.detail

    def test_stuck_threshold_read_from_rules(self, rules_dict):
        rules_dict["checks"]["weblogic_server"]["degraded_if_stuck_threads_above"] = 2
        r = Rules.model_validate(rules_dict)
        assert self.ev(r, stuck=2).status is U
        assert self.ev(r, stuck=3).status is G

    def test_heap_threshold_strict(self, rules):
        assert self.ev(rules, heap=90.0).status is U
        ev = self.ev(rules, heap=90.1)
        assert ev.status is G
        assert "heap" in ev.detail

    def test_down_state_ignores_metrics(self, rules):
        ev = self.ev(rules, state="FAILED", stuck=None, heap=None)
        assert ev.status is D
        assert ev.detail == "server FAILED"

    def test_missing_metrics_are_unknown(self, rules):
        assert self.ev(rules, stuck=None).status is X
        assert self.ev(rules, heap=None).status is X
        assert self.ev(rules, state="WARNING", heap=None).status is G

    @pytest.mark.parametrize(("state", "expected"), [
        ("RUNNING", U), ("ADMIN", G), ("SUSPENDING", G), ("SHUTDOWN", D), ("UNKNOWN", D),
    ])
    def test_shipped_states(self, repo_rules, state, expected):
        assert self.ev(repo_rules, state=state).status is expected


# --------------------------------------------------------------------------- sql_server

class TestSqlServer:
    def ev(self, rules, obs):
        return evaluate("sql_server", obs, rules.checks)

    def test_healthy(self, rules):
        ev = self.ev(rules, healthy_sql())
        assert ev.status is U
        assert "within thresholds" in ev.detail

    def test_service_not_running_is_down(self, rules):
        obs = SqlServerObservation(service_running=False)   # no metrics when the service is down
        ev = self.ev(rules, obs)
        assert ev.status is D
        assert "not running" in ev.detail

    def test_service_state_ignored_when_rule_off(self, rules_dict):
        rules_dict["checks"]["sql_server"]["down_if_service_not_running"] = False
        r = Rules.model_validate(rules_dict)
        assert self.ev(r, healthy_sql(service_running=False)).status is U
        assert self.ev(r, healthy_sql(service_running=None)).status is U

    def test_service_state_not_read_is_unknown(self, rules):
        assert self.ev(rules, healthy_sql(service_running=None)).status is X

    @pytest.mark.parametrize(("field", "at_limit", "over_limit"), [
        ("longest_blocking_minutes", 5, 5.5),
        ("deadlocks_last_15_min", 5, 6),
        ("max_log_used_pct", 90, 91),
        ("tempdb_used_pct", 90, 90.5),
        ("longest_query_minutes", 60, 61),
    ])
    def test_degraded_above_thresholds(self, rules, field, at_limit, over_limit):
        assert self.ev(rules, healthy_sql(**{field: at_limit})).status is U
        assert self.ev(rules, healthy_sql(**{field: over_limit})).status is G

    @pytest.mark.parametrize("field", [
        "longest_blocking_minutes", "deadlocks_last_15_min", "max_log_used_pct", "tempdb_used_pct",
        "longest_query_minutes", "disk_free_pct", "backup_failures_hours_ago",
        "agent_job_failures_hours_ago",
    ])
    def test_metric_not_read_is_unknown(self, rules, field):
        ev = self.ev(rules, healthy_sql(**{field: None}))
        assert ev.status is X
        assert "not read" in ev.detail

    @pytest.mark.parametrize(("free", "expected"), [
        (10, U), (9.9, G), (3, G), (2.9, D),
    ])
    def test_disk_bands(self, rules, free, expected):
        assert self.ev(rules, healthy_sql(disk_free_pct={"C:": 50, "D:": free})).status is expected

    def test_worst_drive_wins_and_all_reported(self, rules):
        ev = self.ev(rules, healthy_sql(disk_free_pct={"C:": 2, "D:": 8}))
        assert ev.status is D
        assert "C:" in ev.detail and "D:" in ev.detail

    @pytest.mark.parametrize("field", ["backup_failures_hours_ago", "agent_job_failures_hours_ago"])
    def test_failure_window(self, rules, field):
        assert self.ev(rules, healthy_sql(**{field: (25.0, 48.0)})).status is U
        ev = self.ev(rules, healthy_sql(**{field: (24.0, 30.0)}))
        assert ev.status is G
        assert "1 failed" in ev.detail

    def test_window_read_from_rules(self, rules_dict):
        rules_dict["checks"]["sql_server"]["degraded_if_backup_failed_in_last_hours"] = 48
        r = Rules.model_validate(rules_dict)
        assert self.ev(r, healthy_sql(backup_failures_hours_ago=(30.0,))).status is G

    def test_several_problems_all_listed(self, rules):
        ev = self.ev(rules, healthy_sql(max_log_used_pct=95, longest_blocking_minutes=12))
        assert ev.status is G
        assert "log used" in ev.detail and "blocking" in ev.detail

    def test_definite_degraded_beats_unknown(self, rules):
        assert self.ev(rules, healthy_sql(tempdb_used_pct=None, max_log_used_pct=99)).status is G

    def test_production_disk_today_is_degraded(self, repo_rules):
        """Q52: production C: at 8.6% free, data drive 11.6% -> Degraded from day one."""
        ev = self.ev(repo_rules, healthy_sql(disk_free_pct={"C:": 8.6, "D:": 11.6}))
        assert ev.status is G
        assert "C: 8.6% free" in ev.detail
        assert "D:" not in ev.detail
