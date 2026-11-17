"""Dashboard view model: every display decision, from real poller snapshots (fake sources)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from hfm_dashboard.app.poller import Poller
from hfm_dashboard.config.environment_loader import parse_environments
from hfm_dashboard.domain.notices import MaintenanceWindow, Notice, NoticeKind
from hfm_dashboard.presentation.dashboard import (
    CONTRACT_VERSION,
    as_dict,
    build_dashboard,
    duration_text,
    node_label,
)
from tests.app.test_poller import DB1, HFM2, Clock, healthy_collectors
from tests.conftest import FILLED, T0

ENVS = [("PROD", "Production"), ("NONPROD", "Non-Production (Development)")]


@pytest.fixture(scope="module")
def envs(repo_rules):
    return parse_environments(FILLED, repo_rules).environments


def snapshots(env, rules, collectors=None, polls=2, name="PROD"):
    p = Poller(name, env, rules, collectors or healthy_collectors(env, rules), clock=Clock())
    return [p.poll_once() for _ in range(polls)][-1]


def view(envs, rules, snap, env="PROD", now=None, **kw):
    return build_dashboard(env, envs[env], rules, snap, now or (snap.observed_at if snap else T0), ENVS, **kw)


class TestHealthy:
    def test_score_counts_and_grid(self, envs, repo_rules):
        v = view(envs, repo_rules, snapshots(envs["PROD"], repo_rules))
        assert (v.score.pct_text, v.score.band, v.score.band_label) == ("100.00%", "green", "Green")
        assert (v.counts.total, v.counts.up, v.counts.down) == (9, 9, 0)
        assert [n.label for n in v.nodes] == ["DB 1", "Web 1", "Web 2", "HFM 1", "HFM 2", "HFM 3"]
        hfm = next(r for r in v.rows if r.id == "epmhfm")
        assert [c.applicable for c in hfm.cells] == [False, False, False, True, True, True]
        assert all(c.status == "up" and c.label == "Up" for c in hfm.cells if c.applicable)
        assert v.attention == () and v.banners == ()
        assert v.contract_version == CONTRACT_VERSION and v.environments[0].active

    def test_selected_cell_lists_checks_in_rules_order(self, envs, repo_rules):
        v = view(envs, repo_rules, snapshots(envs["PROD"], repo_rules), selected="shared_services.web_node_02")
        d = v.selected
        assert (d.name, d.node_label, d.host) == ("Foundation / Shared Services (Workspace)", "Web 2",
                                                  "web-node-02.example.test")
        assert [c.label for c in d.checks] == ["Windows service", "WebLogic server", "URL probe workspace login",
                                               "Disk space", "Event log"]
        cell = next(c for r in v.rows if r.id == "shared_services" for c in r.cells if c.node == "web_node_02")
        assert cell.selected and cell.select == "shared_services.web_node_02"

    def test_load_balancer_and_component_scores(self, envs, repo_rules):
        v = view(envs, repo_rules, snapshots(envs["PROD"], repo_rules))
        assert [(p.name, p.path, p.status) for p in v.load_balancer] == [
            ("Foundation / Shared Services (Workspace)", "/workspace/", "up"), ("HFM Web", "/hfmadf/", "up"),
            ("Financial Reporting Web", "/hr/", "up")]
        assert {s.id: s.pct_text for s in v.component_scores}["epmhfm"] == "100%"


class TestProblems:
    def test_one_hfm_node_down(self, envs, repo_rules):
        env = envs["PROD"]
        c = healthy_collectors(env, repo_rules)
        c.services.states[HFM2]["HyS9FinancialManagementJavaServer_EPMHFM2"] = "Stopped"
        snap = snapshots(env, repo_rules, c)
        v = view(envs, repo_rules, snap, now=snap.observed_at + timedelta(minutes=41))
        assert (v.score.pct_text, v.score.band_label) == ("94.67%", "Amber")
        (a,) = v.attention
        assert (a.name, a.node_label, a.status, a.duration_text) == ("HFM Server (cluster)", "HFM 2", "down", "41 min")
        assert "service Stopped" in a.detail
        assert next(r for r in v.rows if r.id == "epmhfm").status == "degraded"

    def test_cap_banner(self, envs, repo_rules):
        env = envs["PROD"]
        c = healthy_collectors(env, repo_rules)
        c.services.states[DB1]["MSSQLSERVER"] = "Stopped"
        c.sql.failures[DB1] = "SQL Server not reachable"
        v = view(envs, repo_rules, snapshots(env, repo_rules, c))
        assert v.banners[0].kind == "cap" and "SQL Server (hard dependency)" in v.banners[0].text
        assert v.score.band_label == "Red · capped" and v.score.capped_by == ("SQL Server",)

    def test_attention_sorted_worst_first(self, envs, repo_rules):
        env = envs["PROD"]
        c = healthy_collectors(env, repo_rules)
        c.host.volumes[DB1] = {"C:": 8.6}
        c.services.states[HFM2]["HyS9FinancialManagementJavaServer_EPMHFM2"] = "Stopped"
        v = view(envs, repo_rules, snapshots(env, repo_rules, c))
        assert [a.status for a in v.attention] == ["down", "degraded", "degraded"]

    def test_maintenance_banner_and_cell(self, envs, repo_rules):
        env = envs["PROD"]
        c = healthy_collectors(env, repo_rules)
        c.services.states[HFM2]["HyS9FinancialManagementJavaServer_EPMHFM2"] = "Stopped"
        snap = snapshots(env, repo_rules, c)
        w = MaintenanceWindow(snap.observed_at - timedelta(minutes=5), snap.observed_at + timedelta(hours=1),
                              "epmhfm", "patching")
        v = view(envs, repo_rules, snap, maintenance=[w])
        assert any(b.kind == "maintenance" and "HFM Server (cluster) (patching)" in b.text for b in v.banners)
        cell = next(c for r in v.rows if r.id == "epmhfm" for c in r.cells if c.node == "hfm_node_02")
        assert (cell.status, cell.label) == ("maintenance", "Maintenance")


class TestBanners:
    def test_nonprod_notices_off_and_smaller_grid(self, envs, repo_rules):
        snap = snapshots(envs["NONPROD"], repo_rules, healthy_collectors(envs["NONPROD"], repo_rules), name="NONPROD")
        v = view(envs, repo_rules, snap, env="NONPROD")
        assert [b.kind for b in v.banners] == ["notices_off"] and not v.notices_enabled
        assert [n.id for n in v.nodes] == ["db_node_01", "web_node_01", "hfm_node_01"]

    def test_stale(self, envs, repo_rules):
        snap = snapshots(envs["PROD"], repo_rules)
        v = view(envs, repo_rules, snap, now=snap.observed_at + timedelta(minutes=5))
        assert v.banners[0].kind == "stale" and "5 min ago" in v.banners[0].text

    def test_no_data_yet(self, envs, repo_rules):
        v = view(envs, repo_rules, None)
        assert v.banners[0].kind == "no_data" and v.score.pct_text == "n/a" and v.observed_at == ""
        assert all(c.status == "none" for r in v.rows for c in r.cells)


class TestNotices:
    def test_lines_newest_first_with_delivery(self, envs, repo_rules):
        snap = snapshots(envs["PROD"], repo_rules)
        n1 = Notice(NoticeKind.FIRST, "PROD", "epmhfm", "HFM Server (cluster)", T0, T0, ("hfm_node_02",), 3,
                    False, False, False)
        n2 = Notice(NoticeKind.RECOVERY, "PROD", "epmhfm", "HFM Server (cluster)", T0 + timedelta(minutes=30), T0,
                    (), 3, False, False, False)
        v = view(envs, repo_rules, snap, notices_today=[(n1, True, ""), (n2, False, "no delivery configured")])
        assert [(x.kind_label, x.tone, x.delivery) for x in v.notices] == [
            ("Recovered", "up", "no delivery configured"), ("First notice", "degraded", "emailed")]
        assert v.notices[1].text == "HFM Server (cluster) - 1 of 3 node(s) down since 09:00 UTC"


class TestHelpers:
    @pytest.mark.parametrize(("key", "label"), [("web_node_01", "Web 1"), ("hfm_node_03", "HFM 3"),
                                                ("db_node_01", "DB 1"), ("custom", "custom")])
    def test_node_label(self, key, label):
        assert node_label(key) == label

    @pytest.mark.parametrize(("minutes", "text"), [(0, "0 min"), (41, "41 min"), (96, "1 h 36 min"), (1500, "1 d 1 h")])
    def test_duration(self, minutes, text):
        assert duration_text(timedelta(minutes=minutes)) == text

    def test_json_serialisable(self, envs, repo_rules):
        v = view(envs, repo_rules, snapshots(envs["PROD"], repo_rules), selected="epmhfm.hfm_node_01")
        data = json.loads(json.dumps(as_dict(v)))
        assert data["contract_version"] == 1 and data["selected"]["component"] == "epmhfm"
        assert set(data) >= {"score", "counts", "banners", "nodes", "rows", "attention", "notices",
                             "load_balancer", "component_scores", "environments"}
