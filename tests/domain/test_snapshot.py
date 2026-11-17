"""fold_poll: worst check per node, missing checks Unknown, flap protection across polls."""

from __future__ import annotations

from datetime import timedelta

import pytest

from hfm_dashboard.config.environment_loader import parse_environments
from hfm_dashboard.domain.scoring import score
from hfm_dashboard.domain.snapshot import component_nodes, expected_checks, fold_poll, node_statuses
from hfm_dashboard.domain.status import CheckResult, Status
from tests.conftest import FILLED, T0

U, G, D, X = Status.UP, Status.DEGRADED, Status.DOWN, Status.UNKNOWN
POLL = timedelta(seconds=60)


@pytest.fixture(scope="module")
def envs(repo_rules):
    return parse_environments(FILLED, repo_rules).environments


def all_up(expected, at=T0, overrides=None):
    """One Up result per expected check, with overrides {(cid, node, check): status}."""
    out = []
    for (cid, node), checks in expected.items():
        for check in checks:
            st = (overrides or {}).get((cid, node, check), U)
            out.append(CheckResult(cid, node, check, st, at, f"{check}: {st.value}"))
    return out


class TestExpected:
    def test_production(self, envs, repo_rules):
        exp = expected_checks(envs["PROD"], repo_rules)
        assert len(exp) == 16
        assert exp["shared_services", "web_node_02"] == ("windows_service", "weblogic_server",
                                                         "url_probe:workspace_login", "disk", "event_log")
        assert ("fr_rmi", "web_node_01") not in exp

    def test_nonprod_scope(self, envs, repo_rules):
        assert component_nodes(envs["NONPROD"], repo_rules)["epmhfm"] == ["hfm_node_01"]
        assert len(expected_checks(envs["NONPROD"], repo_rules)) == 9


class TestFold:
    def test_all_up(self, envs, repo_rules):
        exp = expected_checks(envs["PROD"], repo_rules)
        states = fold_poll({}, all_up(exp), exp, repo_rules, T0)
        assert {s.status for s in states.values()} == {U}
        assert all(s.detail == "" and s.since == T0 for s in states.values())

    def test_worst_check_wins_and_details_listed(self, envs, repo_rules):
        exp = expected_checks(envs["PROD"], repo_rules)
        res = all_up(exp, overrides={("shared_services", "web_node_01", "weblogic_server"): G,
                                     ("shared_services", "web_node_01", "url_probe:workspace_login"): X})
        s = fold_poll({}, res, exp, repo_rules, T0)["shared_services", "web_node_01"]
        assert s.status is G
        assert "weblogic_server: degraded" in s.detail and "url_probe:workspace_login: unknown" in s.detail

    def test_missing_check_counts_as_unknown(self, envs, repo_rules):
        exp = expected_checks(envs["PROD"], repo_rules)
        res = [r for r in all_up(exp) if r.check != "weblogic_server"]
        states = fold_poll({}, res, exp, repo_rules, T0)
        s = states["admin_server", "web_node_01"]
        assert s.status is X and "weblogic_server: no result this poll" in s.detail
        assert states["epmhfm", "hfm_node_01"].status is U

    def test_down_confirmed_only_after_consecutive_failures(self, envs, repo_rules):
        exp = expected_checks(envs["PROD"], repo_rules)
        key = ("epmhfm", "hfm_node_02")
        stopped = {(*key, "windows_service"): D}
        s1 = fold_poll({}, all_up(exp, T0), exp, repo_rules, T0)
        s2 = fold_poll(s1, all_up(exp, T0 + POLL, stopped), exp, repo_rules, T0 + POLL)
        assert s2[key].status is U and s2[key].consecutive_failures == 1      # confirm after 2 (Q40)
        s3 = fold_poll(s2, all_up(exp, T0 + 2 * POLL, stopped), exp, repo_rules, T0 + 2 * POLL)
        assert s3[key].status is D and s3[key].since == T0 + 2 * POLL
        s4 = fold_poll(s3, all_up(exp, T0 + 3 * POLL), exp, repo_rules, T0 + 3 * POLL)
        assert s4[key].status is U and s4[key].since == T0 + 3 * POLL         # recovery immediate

    def test_result_for_unexpected_node_is_a_bug(self, envs, repo_rules):
        exp = expected_checks(envs["NONPROD"], repo_rules)
        with pytest.raises(ValueError, match="unexpected node"):
            fold_poll({}, [CheckResult("epmhfm", "hfm_node_02", "windows_service", U, T0)], exp,
                      repo_rules, T0)


class TestScoringScope:
    def test_nonprod_not_penalised_for_missing_nodes(self, envs, build_rules, repo_rules):
        env = envs["NONPROD"]
        exp = expected_checks(env, repo_rules)
        statuses = node_statuses(fold_poll({}, all_up(exp), exp, repo_rules, T0))
        strict = repo_rules.model_copy(update={"scoring": repo_rules.scoring.model_copy(
            update={"unknown_nodes": "count_as_down"})})
        assert score(statuses, strict, component_nodes(env, repo_rules)).overall_pct == 100.0
        assert score(statuses, strict).overall_pct < 100.0     # without scope, absent nodes count as Down

    def test_status_for_node_outside_scope_rejected(self, envs, repo_rules):
        scope = component_nodes(envs["NONPROD"], repo_rules)
        with pytest.raises(ValueError, match="unknown nodes"):
            score({"epmhfm": {"hfm_node_02": U}}, repo_rules, scope)


class TestTransitions:
    def test_first_sighting_and_changes_only(self, envs, repo_rules):
        from hfm_dashboard.domain.snapshot import transitions
        exp = expected_checks(envs["PROD"], repo_rules)
        s1 = fold_poll({}, all_up(exp, T0), exp, repo_rules, T0)
        first = transitions({}, s1, T0)
        assert len(first) == 16 and all(t.before is None and t.after is U for t in first)
        s2 = fold_poll(s1, all_up(exp, T0 + POLL), exp, repo_rules, T0 + POLL)
        assert transitions(s1, s2, T0 + POLL) == []
        stopped = {("epmhfm", "hfm_node_02", "windows_service"): D}
        s3 = fold_poll(s2, all_up(exp, T0 + 2 * POLL, stopped), exp, repo_rules, T0 + 2 * POLL)
        assert transitions(s2, s3, T0 + 2 * POLL) == []            # not confirmed yet: no change
        s4 = fold_poll(s3, all_up(exp, T0 + 3 * POLL, stopped), exp, repo_rules, T0 + 3 * POLL)
        (t,) = transitions(s3, s4, T0 + 3 * POLL)
        assert (t.component, t.node, t.before, t.after, t.at) == ("epmhfm", "hfm_node_02", U, D, T0 + 3 * POLL)
        assert "windows_service: down" in t.detail
