"""Windows services collector against the filled template, the shipped rules and a fake source."""

from __future__ import annotations

import pytest

from hfm_dashboard.adapters.fake_services import FakeWindowsServiceSource
from hfm_dashboard.app.service_collector import collect_windows_services, plan
from hfm_dashboard.config.environment_loader import parse_environments
from hfm_dashboard.domain.status import Status
from tests.conftest import FILLED, T0

U, G, D, X = Status.UP, Status.DEGRADED, Status.DOWN, Status.UNKNOWN

WEB1, WEB2 = "web-node-01.example.test", "web-node-02.example.test"
HFM1, HFM2, HFM3 = (f"hfm-node-0{i}.example.test" for i in (1, 2, 3))
DB1 = "db-node-01.example.test"


@pytest.fixture(scope="module")
def envs(repo_rules):
    return parse_environments(FILLED, repo_rules).environments


@pytest.fixture
def prod(envs):
    return envs["PROD"]


def all_running(env, rules) -> FakeWindowsServiceSource:
    states: dict[str, dict[str, str]] = {}
    for t in plan(env, rules):
        states.setdefault(t.host, {})[t.service] = "Running"
    return FakeWindowsServiceSource(states)


def by_key(results):
    return {(r.component, r.node): r for r in results}


class TestPlan:
    def test_production_targets(self, prod, repo_rules):
        targets = plan(prod, repo_rules)
        # sql_server 1, sql_agent 1, shared_services 2, ohs_lb 2, epmhfm 3, hfm_web 2, fr_web 2,
        # admin_server 1, ohs_node_manager 2
        assert len(targets) == 16
        assert {t.component for t in targets} == {
            "sql_server", "sql_agent", "shared_services", "ohs_load_balancer", "epmhfm", "hfm_web",
            "fr_web", "admin_server", "ohs_node_manager",
        }

    def test_disabled_component_not_planned(self, prod, repo_rules):
        assert all(t.component != "fr_rmi" for t in plan(prod, repo_rules))

    def test_service_names_looked_up_per_node(self, prod, repo_rules):
        hfm = {t.node: t.service for t in plan(prod, repo_rules) if t.component == "epmhfm"}
        assert hfm == {
            "hfm_node_01": "HyS9FinancialManagementJavaServer_EPMHFM1",
            "hfm_node_02": "HyS9FinancialManagementJavaServer_EPMHFM2",
            "hfm_node_03": "HyS9FinancialManagementJavaServer_EPMHFM3",
        }

    def test_nonprod_targets(self, envs, repo_rules):
        targets = plan(envs["NONPROD"], repo_rules)
        assert len(targets) == 9
        assert {t.host for t in targets} == {
            "np-web-node-01.example.test", "np-hfm-node-01.example.test", "np-db-node-01.example.test"}


class TestCollect:
    def test_all_running(self, prod, repo_rules):
        results = collect_windows_services(prod, repo_rules, all_running(prod, repo_rules), T0)
        assert len(results) == 16
        assert {r.status for r in results} == {U}
        assert all(r.check == "windows_service" and r.observed_at == T0 for r in results)

    def test_one_query_per_host_with_unique_names(self, prod, repo_rules):
        fake = all_running(prod, repo_rules)
        collect_windows_services(prod, repo_rules, fake, T0)
        hosts = [h for h, _ in fake.calls]
        assert sorted(hosts) == sorted({WEB1, WEB2, HFM1, HFM2, HFM3, DB1})
        web1_names = dict(fake.calls)[WEB1]
        assert len(web1_names) == len(set(web1_names)) == 6

    def test_states_evaluated_with_rules(self, prod, repo_rules):
        fake = all_running(prod, repo_rules)
        fake.states[HFM2]["HyS9FinancialManagementJavaServer_EPMHFM2"] = "Stopped"
        fake.states[WEB2]["HyS9FoundationServices_EPMWEB2"] = "StartPending"
        del fake.states[WEB1]["HyS9FRReports_EPMWEB1"]               # -> NotFound
        r = by_key(collect_windows_services(prod, repo_rules, fake, T0))
        assert r["epmhfm", "hfm_node_02"].status is D
        assert r["epmhfm", "hfm_node_01"].status is U
        assert r["shared_services", "web_node_02"].status is G
        assert r["fr_web", "web_node_01"].status is D
        assert r["fr_web", "web_node_01"].detail == "HyS9FRReports_EPMWEB1: service NotFound"

    def test_unreachable_host_is_unknown_for_its_services_only(self, prod, repo_rules):
        fake = all_running(prod, repo_rules)
        fake.failures[WEB2] = "WinRM not reachable (ConnectionError)"
        results = collect_windows_services(prod, repo_rules, fake, T0)
        on_web2 = [r for r in results if r.node == "web_node_02"]
        assert len(on_web2) == 5
        assert {r.status for r in on_web2} == {X}
        assert "WinRM not reachable" in on_web2[0].detail
        assert {r.status for r in results if r.node != "web_node_02"} == {U}

    def test_source_omitting_a_service_is_unknown(self, prod, repo_rules):
        class Partial(FakeWindowsServiceSource):
            def query(self, host, names):
                return {n: "Running" for n in names if n != "MSSQLSERVER"}
        r = by_key(collect_windows_services(prod, repo_rules, Partial(), T0))
        assert r["sql_server", "db_node_01"].status is X
        assert r["sql_agent", "db_node_01"].status is U

    def test_unlisted_state_is_unknown(self, prod, repo_rules):
        fake = all_running(prod, repo_rules)
        fake.states[DB1]["SQLSERVERAGENT"] = "Weird"
        assert by_key(collect_windows_services(prod, repo_rules, fake, T0))["sql_agent", "db_node_01"].status is X

    def test_results_in_plan_order(self, prod, repo_rules):
        results = collect_windows_services(prod, repo_rules, all_running(prod, repo_rules), T0)
        assert [(r.component, r.node) for r in results] == [(t.component, t.node) for t in plan(prod, repo_rules)]

    def test_naive_time_rejected(self, prod, repo_rules):
        with pytest.raises(ValueError, match="UTC"):
            collect_windows_services(prod, repo_rules, all_running(prod, repo_rules), T0.replace(tzinfo=None))
