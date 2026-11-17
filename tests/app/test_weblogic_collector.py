"""WebLogic collector against the filled template, the shipped rules and fake / sample data."""

from __future__ import annotations

import json

import httpx
import pytest

from hfm_dashboard.adapters.fake_weblogic import FakeWeblogicSource
from hfm_dashboard.adapters.weblogic_rest import WeblogicRestSource, parse_search
from hfm_dashboard.app.weblogic_collector import collect_weblogic, plan
from hfm_dashboard.config.environment_loader import parse_environments
from hfm_dashboard.domain.evaluation import WeblogicObservation
from hfm_dashboard.domain.status import Status
from hfm_dashboard.ports.secrets import Secret
from tests.conftest import FIXTURES, FILLED, T0

U, G, D, X = Status.UP, Status.DEGRADED, Status.DOWN, Status.UNKNOWN


@pytest.fixture(scope="module")
def envs(repo_rules):
    return parse_environments(FILLED, repo_rules).environments


def sample(name):
    return json.loads((FIXTURES / "weblogic" / name).read_text(encoding="utf-8"))


def by_key(results):
    return {(r.component, r.node): r for r in results}


class TestPlan:
    def test_production(self, envs, repo_rules):
        targets = {(t.component, t.node): t.server for t in plan(envs["PROD"], repo_rules)}
        assert targets == {
            ("shared_services", "web_node_01"): "FoundationServices0",
            ("shared_services", "web_node_02"): "FoundationServices1",
            ("admin_server", "web_node_01"): "AdminServer",
        }

    def test_nonprod(self, envs, repo_rules):
        assert len(plan(envs["NONPROD"], repo_rules)) == 2


class TestCollect:
    def test_all_running(self, envs, repo_rules):
        src = FakeWeblogicSource(parse_search(sample("domain_all_running.json")))
        results = collect_weblogic(envs["PROD"], repo_rules, src, T0)
        assert len(results) == 3 and {r.status for r in results} == {U}
        assert all(r.check == "weblogic_server" and r.observed_at == T0 for r in results)
        assert src.calls == 1

    def test_one_managed_server_shutdown(self, envs, repo_rules):
        src = FakeWeblogicSource(parse_search(sample("domain_one_shutdown.json")))
        r = by_key(collect_weblogic(envs["PROD"], repo_rules, src, T0))
        assert r["shared_services", "web_node_02"].status is D
        assert r["shared_services", "web_node_02"].detail == "FoundationServices1: server SHUTDOWN"
        assert r["shared_services", "web_node_01"].status is U

    def test_stressed_server_degraded_with_all_reasons(self, envs, repo_rules):
        src = FakeWeblogicSource(parse_search(sample("domain_stressed.json")))
        r = by_key(collect_weblogic(envs["PROD"], repo_rules, src, T0))["shared_services", "web_node_01"]
        assert r.status is G
        assert "health warning" in r.detail and "2 stuck" in r.detail and "heap 95.0%" in r.detail

    def test_admin_unreachable_makes_every_check_unknown(self, envs, repo_rules):
        src = FakeWeblogicSource(failure="WebLogic REST not reachable (ConnectError)")
        results = collect_weblogic(envs["PROD"], repo_rules, src, T0)
        assert {r.status for r in results} == {X}
        assert "not reachable" in results[0].detail

    def test_unknown_server_name_is_unknown(self, envs, repo_rules):
        servers = parse_search(sample("domain_all_running.json"))
        del servers["FoundationServices1"]
        r = by_key(collect_weblogic(envs["PROD"], repo_rules, FakeWeblogicSource(servers), T0))
        assert r["shared_services", "web_node_02"].status is X
        assert "not found in the WebLogic domain" in r["shared_services", "web_node_02"].detail

    def test_running_without_runtime_data_is_unknown(self, envs, repo_rules):
        servers = {"AdminServer": WeblogicObservation("RUNNING", 0, 10, "ok"),
                   "FoundationServices0": WeblogicObservation("RUNNING"),
                   "FoundationServices1": WeblogicObservation("RUNNING", 0, 10, "ok")}
        r = by_key(collect_weblogic(envs["PROD"], repo_rules, FakeWeblogicSource(servers), T0))
        assert r["shared_services", "web_node_01"].status is X

    def test_end_to_end_with_rest_adapter(self, envs, repo_rules):
        """Real adapter + MockTransport + collector + shipped rules."""
        env = envs["PROD"]
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json=sample("domain_one_shutdown.json")))
        src = WeblogicRestSource(env.weblogic.admin_url, Secret("u", "p"),
                                 repo_rules.checks.weblogic_server.timeout_seconds, transport=transport)
        statuses = {k: v.status for k, v in by_key(collect_weblogic(env, repo_rules, src, T0)).items()}
        assert statuses == {("shared_services", "web_node_01"): U,
                            ("shared_services", "web_node_02"): D,
                            ("admin_server", "web_node_01"): U}
