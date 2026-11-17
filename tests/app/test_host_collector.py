"""Disk space and event log collector against the filled template, shipped rules and a fake source."""

from __future__ import annotations

import pytest

from hfm_dashboard.adapters.fake_host_health import FakeHostHealthSource
from hfm_dashboard.app.host_collector import collect_host_health, plan
from hfm_dashboard.config.environment_loader import parse_environments
from hfm_dashboard.domain.status import Status
from tests.conftest import FILLED, T0

U, G, D, X = Status.UP, Status.DEGRADED, Status.DOWN, Status.UNKNOWN
WEB1, HFM2, DB1 = "web-node-01.example.test", "hfm-node-02.example.test", "db-node-01.example.test"


@pytest.fixture(scope="module")
def prod(repo_rules):
    return parse_environments(FILLED, repo_rules).environments["PROD"]


def results(prod, rules, src):
    return {(r.component, r.node, r.check): r for r in collect_host_health(prod, rules, src, T0)}


class TestPlan:
    def test_one_entry_per_component_node_grouped_by_host(self, prod, repo_rules):
        p = plan(prod, repo_rules)
        assert len(p) == 6
        assert sum(len(v) for v in p.values()) == 16
        assert {c for c, _, _ in p[WEB1]} == {"shared_services", "ohs_load_balancer", "hfm_web", "fr_web",
                                              "admin_server", "ohs_node_manager"}
        assert ("epmhfm", "hfm_node_02", "HyS9FinancialManagementJavaServer_EPMHFM2") in p[HFM2]


class TestCollect:
    def test_healthy(self, prod, repo_rules):
        src = FakeHostHealthSource()
        r = results(prod, repo_rules, src)
        assert len(r) == 32 and {x.status for x in r.values()} == {U}
        assert len(src.calls) == 6
        host, names, minutes, crash_ids, shutdown_ids = next(c for c in src.calls if c[0] == DB1)
        assert names == ("MSSQLSERVER", "SQLSERVERAGENT")
        assert (minutes, crash_ids, shutdown_ids) == (60, (7031, 7034, 7023, 7024), (6008, 41))

    def test_low_disk_affects_every_component_on_the_node(self, prod, repo_rules):
        src = FakeHostHealthSource(volumes={DB1: {"C:": 8.6, "D:": 11.6}})
        r = results(prod, repo_rules, src)
        assert r["sql_server", "db_node_01", "disk"].status is G
        assert r["sql_agent", "db_node_01", "disk"].status is G
        assert r["sql_agent", "db_node_01", "disk"].detail == "disk: C: 8.6% free < 10%"
        assert r["epmhfm", "hfm_node_01", "disk"].status is U

    def test_nearly_full_volume_is_down(self, prod, repo_rules):
        src = FakeHostHealthSource(volumes={HFM2: {"C:": 2.5}})
        assert results(prod, repo_rules, src)["epmhfm", "hfm_node_02", "disk"].status is D

    def test_service_crash_only_affects_that_component(self, prod, repo_rules):
        src = FakeHostHealthSource(crashes={WEB1: {"HyS9FRReports_EPMWEB1": 2}})
        r = results(prod, repo_rules, src)
        assert r["fr_web", "web_node_01", "event_log"].status is G
        assert r["fr_web", "web_node_01", "event_log"].detail == \
            "event log (HyS9FRReports_EPMWEB1): 2 service crash event(s) in last 60 min"
        assert r["hfm_web", "web_node_01", "event_log"].status is U

    def test_unexpected_restart_affects_whole_node(self, prod, repo_rules):
        src = FakeHostHealthSource(shutdowns={WEB1: 1})
        r = results(prod, repo_rules, src)
        on_web1 = [v for (c, n, k), v in r.items() if n == "web_node_01" and k == "event_log"]
        assert len(on_web1) == 6 and {v.status for v in on_web1} == {G}
        assert "unexpected server shutdown/restart" in on_web1[0].detail
        assert r["hfm_web", "web_node_02", "event_log"].status is U

    def test_unreachable_host_unknown(self, prod, repo_rules):
        src = FakeHostHealthSource(failures={HFM2: "WinRM not reachable (ConnectionError)"})
        r = results(prod, repo_rules, src)
        assert r["epmhfm", "hfm_node_02", "disk"].status is X
        assert r["epmhfm", "hfm_node_02", "event_log"].status is X
        assert r["epmhfm", "hfm_node_01", "disk"].status is U
