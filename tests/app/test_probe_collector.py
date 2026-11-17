"""URL probe collector against the filled template, the shipped rules and a fake prober."""

from __future__ import annotations

import httpx
import pytest

from hfm_dashboard.adapters.fake_probe import FakeUrlProber
from hfm_dashboard.adapters.http_probe import HttpxUrlProber
from hfm_dashboard.app.probe_collector import LB_NODE, collect_url_probes, plan
from hfm_dashboard.config.environment_loader import parse_environments
from hfm_dashboard.domain.evaluation import UrlProbeObservation
from hfm_dashboard.domain.status import Status
from tests.conftest import FILLED, T0

U, G, D, X = Status.UP, Status.DEGRADED, Status.DOWN, Status.UNKNOWN

WEB1 = "http://web-node-01.example.test:8443"
WEB2 = "http://web-node-02.example.test:8443"
LB = "https://lb.example.test"


@pytest.fixture(scope="module")
def envs(repo_rules):
    return parse_environments(FILLED, repo_rules).environments


def by_key(results):
    return {(r.component, r.node): r for r in results}


class TestPlan:
    def test_production_node_targets(self, envs, repo_rules):
        node_targets, _ = plan(envs["PROD"], repo_rules)
        assert {(t.component, t.node, t.url) for t in node_targets} == {
            ("shared_services", "web_node_01", f"{WEB1}/workspace/"),
            ("shared_services", "web_node_02", f"{WEB2}/workspace/"),
            ("hfm_web", "web_node_01", f"{WEB1}/hfmadf/"),
            ("hfm_web", "web_node_02", f"{WEB2}/hfmadf/"),
            ("fr_web", "web_node_01", f"{WEB1}/hr/"),
            ("fr_web", "web_node_02", f"{WEB2}/hr/"),
        }

    def test_production_lb_targets(self, envs, repo_rules):
        _, lb_targets = plan(envs["PROD"], repo_rules)
        assert [(t.component, t.node, t.probe, t.url) for t in lb_targets] == [
            ("ohs_load_balancer", LB_NODE, "workspace_login", f"{LB}/workspace/"),
            ("ohs_load_balancer", LB_NODE, "hfm_web", f"{LB}/hfmadf/"),
            ("ohs_load_balancer", LB_NODE, "fr_web", f"{LB}/hr/"),
        ]

    def test_nonprod(self, envs, repo_rules):
        node_targets, lb_targets = plan(envs["NONPROD"], repo_rules)
        assert len(node_targets) == 3 and len(lb_targets) == 3
        assert all("np-web-node-01" in t.url for t in node_targets)


class TestCollect:
    def test_all_healthy(self, envs, repo_rules):
        prober = FakeUrlProber()
        res = collect_url_probes(envs["PROD"], repo_rules, prober, T0)
        assert len(res.nodes) == 6 and len(res.load_balancer) == 3
        assert {r.status for r in res.nodes + res.load_balancer} == {U}
        assert len(prober.calls) == 9
        assert all(r.observed_at == T0 for r in res.nodes)

    def test_thresholds_per_probe_from_rules(self, envs, repo_rules):
        prober = FakeUrlProber({
            f"{WEB1}/workspace/": UrlProbeObservation(200, 11.0),   # > 10 s
            f"{WEB1}/hfmadf/": UrlProbeObservation(200, 14.0),      # <= 15 s
            f"{WEB2}/hr/": UrlProbeObservation(200, 31.0),          # > 30 s
        })
        r = by_key(collect_url_probes(envs["PROD"], repo_rules, prober, T0).nodes)
        assert r["shared_services", "web_node_01"].status is G
        assert r["hfm_web", "web_node_01"].status is U
        assert r["fr_web", "web_node_02"].status is G

    def test_node_failure(self, envs, repo_rules):
        prober = FakeUrlProber({f"{WEB2}/hfmadf/": UrlProbeObservation(503, 0.2)})
        r = by_key(collect_url_probes(envs["PROD"], repo_rules, prober, T0).nodes)
        assert r["hfm_web", "web_node_02"].status is D
        assert r["hfm_web", "web_node_02"].detail == "hfm_web (/hfmadf/): HTTP 503"
        assert r["hfm_web", "web_node_01"].status is U

    def test_lb_results_separate_from_node_results(self, envs, repo_rules):
        prober = FakeUrlProber({f"{LB}/workspace/": UrlProbeObservation(None, None, timed_out=True)})
        res = collect_url_probes(envs["PROD"], repo_rules, prober, T0)
        assert {r.status for r in res.nodes} == {U}
        lb = {r.check: r for r in res.load_balancer}
        assert lb["url_probe:workspace_login"].status is D
        assert lb["url_probe:workspace_login"].node == LB_NODE
        assert all(r.node != LB_NODE for r in res.nodes)

    def test_details_never_contain_hosts(self, envs, repo_rules):
        prober = FakeUrlProber(default=UrlProbeObservation(None, 0.1, error="connection refused"))
        res = collect_url_probes(envs["PROD"], repo_rules, prober, T0)
        assert all("example.test" not in r.detail for r in res.nodes + res.load_balancer)

    def test_end_to_end_with_httpx_prober(self, envs, repo_rules):
        """Real prober + MockTransport + collector + shipped rules."""
        def handler(request):
            if request.url.host == "web-node-02.example.test" and request.url.path == "/hr/":
                raise httpx.ConnectError("refused")
            if request.url.host == "web-node-02.example.test" and request.url.path == "/hfmadf/":
                raise httpx.ConnectTimeout("no answer")
            return httpx.Response(302 if request.url.path == "/workspace/" else 200)

        prober = HttpxUrlProber(repo_rules.checks.url_probe.timeout_seconds,
                                transport=httpx.MockTransport(handler))
        res = collect_url_probes(envs["PROD"], repo_rules, prober, T0)
        r = by_key(res.nodes)
        assert r["fr_web", "web_node_02"].status is D                       # refused: service down
        assert "connection refused" in r["fr_web", "web_node_02"].detail
        assert r["hfm_web", "web_node_02"].status is X                      # unreachable (D26)
        assert "not reachable from the monitoring server" in r["hfm_web", "web_node_02"].detail
        assert r["shared_services", "web_node_01"].status is U     # 302 counts as Up (Q54)
        assert {x.status for x in res.load_balancer} == {U}
