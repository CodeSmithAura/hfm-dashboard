"""Poller end to end with fake sources: collectors -> node states -> score, across polls.

Pins the numbers reported to the application team with the shipped (assumed) weights:
one HFM node down = 94.67% Amber; Admin Server down = 96% Green; SQL Server down = capped 0% Red.
"""

from __future__ import annotations

import json
import threading
from datetime import timedelta

import pytest

from hfm_dashboard.adapters.fake_probe import FakeUrlProber
from hfm_dashboard.adapters.fake_services import FakeWindowsServiceSource
from hfm_dashboard.adapters.fake_sql import FakeSqlHealthSource
from hfm_dashboard.adapters.fake_weblogic import FakeWeblogicSource
from hfm_dashboard.adapters.sql_health import build_observation
from hfm_dashboard.adapters.weblogic_rest import parse_search
from hfm_dashboard.app.poller import Collectors, Poller
from hfm_dashboard.app.service_collector import plan as service_plan
from hfm_dashboard.config.environment_loader import parse_environments
from hfm_dashboard.domain.evaluation import UrlProbeObservation, WeblogicObservation
from hfm_dashboard.domain.scoring import Band
from hfm_dashboard.domain.status import Status
from tests.conftest import FIXTURES, FILLED, T0

U, G, D, X = Status.UP, Status.DEGRADED, Status.DOWN, Status.UNKNOWN
POLL = timedelta(seconds=60)
HFM2 = "hfm-node-02.example.test"
WEB1 = "web-node-01.example.test"
DB1 = "db-node-01.example.test"


@pytest.fixture(scope="module")
def envs(repo_rules):
    return parse_environments(FILLED, repo_rules).environments


def healthy_collectors(env, rules) -> Collectors:
    states: dict[str, dict[str, str]] = {}
    for t in service_plan(env, rules):
        states.setdefault(t.host, {})[t.service] = "Running"
    weblogic = parse_search(json.loads((FIXTURES / "weblogic" / "domain_all_running.json").read_text()))
    sql_rows = json.loads((FIXTURES / "sql" / "healthy.json").read_text())
    sql = {env.nodes["db_node_01"].host: build_observation(sql_rows, env.databases)}
    return Collectors(FakeWindowsServiceSource(states), FakeWeblogicSource(weblogic), FakeUrlProber(),
                      FakeSqlHealthSource(sql))


class Clock:
    def __init__(self):
        self.now = T0 - POLL

    def __call__(self):
        self.now += POLL
        return self.now


def poller(env, rules, collectors, name="PROD"):
    return Poller(name, env, rules, collectors, clock=Clock())


class TestScores:
    def test_healthy_is_100_green(self, envs, repo_rules):
        s = poller(envs["PROD"], repo_rules, healthy_collectors(envs["PROD"], repo_rules)).poll_once()
        assert s.score.overall_pct == 100.0 and s.score.band is Band.GREEN
        assert len(s.results) == 16 + 3 + 6 + 1 and len(s.load_balancer) == 3
        assert s.observed_at == T0 and s.collector_errors == ()

    def test_one_hfm_node_down_is_94_67_amber_after_confirmation(self, envs, repo_rules):
        env = envs["PROD"]
        c = healthy_collectors(env, repo_rules)
        c.services.states[HFM2]["HyS9FinancialManagementJavaServer_EPMHFM2"] = "Stopped"
        p = poller(env, repo_rules, c)
        first = p.poll_once()
        assert first.score.overall_pct == 100.0                       # not yet confirmed (Q40)
        second = p.poll_once()
        assert second.node_states["epmhfm", "hfm_node_02"].status is D
        assert second.score.overall_pct == 94.67 and second.score.band is Band.AMBER

    def test_admin_server_down_stays_green_at_96(self, envs, repo_rules):
        env = envs["PROD"]
        c = healthy_collectors(env, repo_rules)
        c.services.states[WEB1]["Oracle Weblogic EPMSystem_AdminServer"] = "Stopped"
        c.weblogic.servers["AdminServer"] = WeblogicObservation("SHUTDOWN")
        p = poller(env, repo_rules, c)
        p.poll_once()
        s = p.poll_once()
        assert s.score.overall_pct == 96.0 and s.score.band is Band.GREEN

    def test_sql_server_down_caps_at_zero(self, envs, repo_rules):
        env = envs["PROD"]
        c = healthy_collectors(env, repo_rules)
        c.services.states[DB1]["MSSQLSERVER"] = "Stopped"
        c.sql.failures[DB1] = "SQL Server not reachable"
        p = poller(env, repo_rules, c)
        p.poll_once()
        s = p.poll_once()
        assert s.score.capped_by == ("sql_server",)
        assert s.score.overall_pct == 0 and s.score.band is Band.RED

    def test_load_balancer_failure_not_scored(self, envs, repo_rules):
        env = envs["PROD"]
        c = healthy_collectors(env, repo_rules)
        c.prober.responses["https://lb.example.test/workspace/"] = UrlProbeObservation(503, 0.1)
        s = poller(env, repo_rules, c).poll_once()
        assert s.score.overall_pct == 100.0
        assert [r.status for r in s.load_balancer if r.check == "url_probe:workspace_login"] == [D]

    def test_nonprod_healthy(self, envs, repo_rules):
        env = envs["NONPROD"]
        s = poller(env, repo_rules, healthy_collectors(env, repo_rules), "NONPROD").poll_once()
        assert s.score.overall_pct == 100.0 and len(s.node_states) == 9


class TestRobustness:
    def test_crashing_collector_only_loses_its_checks(self, envs, repo_rules):
        env = envs["PROD"]
        c = healthy_collectors(env, repo_rules)

        class Boom:
            def fetch_servers(self):
                raise RuntimeError("bug")

        c = Collectors(c.services, Boom(), c.prober, c.sql)
        s = poller(env, repo_rules, c).poll_once()
        assert s.collector_errors == ("weblogic",)
        admin = s.node_states["admin_server", "web_node_01"]
        assert admin.status is X and "weblogic_server: no result this poll" in admin.detail
        assert s.node_states["epmhfm", "hfm_node_01"].status is U
        assert s.score.components["admin_server"].score_pct is None      # excluded (Q53)

    def test_overlapping_poll_is_skipped(self, envs, repo_rules):
        env = envs["PROD"]
        c = healthy_collectors(env, repo_rules)
        entered, release = threading.Event(), threading.Event()

        class Slow(FakeSqlHealthSource):
            def fetch(self, host):
                entered.set()
                release.wait(5)
                return super().fetch(host)

        slow = Slow(c.sql.observations)
        p = poller(env, repo_rules, Collectors(c.services, c.weblogic, c.prober, slow))
        t = threading.Thread(target=p.poll_once)
        t.start()
        assert entered.wait(5)
        assert p.poll_once() is None                      # second poll skipped while first runs
        release.set()
        t.join(5)
        assert p.latest is not None and p.latest.score.overall_pct == 100.0

    def test_states_carry_between_polls(self, envs, repo_rules):
        env = envs["PROD"]
        p = poller(env, repo_rules, healthy_collectors(env, repo_rules))
        first, second = p.poll_once(), p.poll_once()
        key = ("epmhfm", "hfm_node_01")
        assert second.observed_at == T0 + POLL
        assert second.node_states[key].since == first.observed_at       # Up since the first poll


class TestHistory:
    def test_polls_and_changes_saved(self, envs, repo_rules):
        from hfm_dashboard.adapters.fake_history import FakeHistory
        h = FakeHistory()
        env = envs["PROD"]
        p = Poller("PROD", env, repo_rules, healthy_collectors(env, repo_rules), clock=Clock(), history=h)
        first = p.poll_once()
        p.poll_once()
        assert [r.overall_pct for r in h.poll_rows] == [100.0, 100.0]
        assert len(first.transitions) == 16 and len(h.changes["PROD"]) == 16    # first sighting only
        assert h.states["PROD"]["epmhfm", "hfm_node_01"].status is U

    def test_restart_keeps_down_since_and_raises_no_new_transition(self, envs, repo_rules):
        from hfm_dashboard.adapters.fake_history import FakeHistory
        h = FakeHistory()
        env = envs["PROD"]
        c = healthy_collectors(env, repo_rules)
        c.services.states[HFM2]["HyS9FinancialManagementJavaServer_EPMHFM2"] = "Stopped"
        clock = Clock()
        p1 = Poller("PROD", env, repo_rules, c, clock=clock, history=h)
        p1.poll_once()
        down = p1.poll_once()
        key = ("epmhfm", "hfm_node_02")
        assert down.node_states[key].status is D
        down_since = down.node_states[key].since

        p2 = Poller("PROD", env, repo_rules, c, clock=clock, history=h)       # service restarted
        after = p2.poll_once()
        assert after.node_states[key].status is D and after.node_states[key].since == down_since
        assert after.transitions == ()                  # no spurious "went down" after restart
        assert after.score.overall_pct == 94.67

    def test_restored_states_limited_to_monitored_nodes(self, envs, repo_rules):
        from hfm_dashboard.adapters.fake_history import FakeHistory
        from hfm_dashboard.domain.status import NodeState
        h = FakeHistory()
        h.states["NONPROD"] = {("epmhfm", "hfm_node_02"): NodeState("epmhfm", "hfm_node_02", D)}
        env = envs["NONPROD"]
        s = Poller("NONPROD", env, repo_rules, healthy_collectors(env, repo_rules), clock=Clock(),
                   history=h).poll_once()
        assert ("epmhfm", "hfm_node_02") not in s.node_states

    def test_history_failures_never_stop_polling(self, envs, repo_rules):
        from hfm_dashboard.adapters.fake_history import FakeHistory

        class Broken(FakeHistory):
            def load_node_states(self, environment):
                raise RuntimeError("disk gone")

        env = envs["PROD"]
        p = Poller("PROD", env, repo_rules, healthy_collectors(env, repo_rules), clock=Clock(),
                   history=Broken(fail_writes=True))
        assert p.poll_once().score.overall_pct == 100.0
