"""NoticeTracker (persistence, delivery hand-over, maintenance) and its use in the poller."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from hfm_dashboard.adapters.fake_history import FakeHistory
from hfm_dashboard.adapters.sqlite_history import SqliteHistory
from hfm_dashboard.app.main import summarise
from hfm_dashboard.app.notice_tracker import NoticeTracker
from hfm_dashboard.app.poller import Poller
from hfm_dashboard.config.environment_loader import parse_environments
from hfm_dashboard.domain.notices import ComponentNoticeState, MaintenanceWindow, NoticeKind
from hfm_dashboard.domain.snapshot import expected_checks
from hfm_dashboard.domain.status import NodeState, Status
from hfm_dashboard.ports.maintenance import FixedMaintenance
from tests.app.test_poller import Clock, healthy_collectors
from tests.conftest import FILLED, T0

U, D = Status.UP, Status.DOWN
NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
MIN = timedelta(minutes=1)


@pytest.fixture(scope="module")
def envs(repo_rules):
    return parse_environments(FILLED, repo_rules).environments


def states_with_hfm2_down(env, rules, since=NOW):
    s = {k: NodeState(k[0], k[1], U, 0, NOW - 60 * MIN) for k in expected_checks(env, rules)}
    s["epmhfm", "hfm_node_02"] = NodeState("epmhfm", "hfm_node_02", D, 2, since, D, "service Stopped")
    return s


STATUS = {"epmhfm": Status.DEGRADED}


class TestTracker:
    def test_restart_does_not_resend_first_notice(self, envs, repo_rules):
        h = FakeHistory()
        states = states_with_hfm2_down(envs["PROD"], repo_rules)
        t1 = NoticeTracker("PROD", repo_rules, history=h)
        assert [n.kind for n in t1.process(NOW + 15 * MIN, states, STATUS)] == [NoticeKind.FIRST]
        t2 = NoticeTracker("PROD", repo_rules, history=h)                 # service restarted
        assert t2.process(NOW + 16 * MIN, states, STATUS) == []
        assert [n.kind for n in t2.process(NOW + 45 * MIN, states, STATUS)] == [NoticeKind.REMINDER]

    def test_delivered_in_prod(self, envs, repo_rules):
        sent, h = [], FakeHistory()
        t = NoticeTracker("PROD", repo_rules, history=h, deliver=sent.extend)
        t.process(NOW + 15 * MIN, states_with_hfm2_down(envs["PROD"], repo_rules), STATUS)
        assert [n.kind for n in sent] == [NoticeKind.FIRST]
        assert h.notice_log[0][1:] == (True, "")

    def test_nonprod_notices_logged_not_delivered(self, envs, repo_rules):
        sent, h = [], FakeHistory()
        env = envs["NONPROD"]
        s = {k: NodeState(k[0], k[1], U, 0, NOW) for k in expected_checks(env, repo_rules)}
        s["epmhfm", "hfm_node_01"] = NodeState("epmhfm", "hfm_node_01", D, 2, NOW, D)
        t = NoticeTracker("NONPROD", repo_rules, history=h, deliver=sent.extend)
        notices = t.process(NOW + 15 * MIN, s, {"epmhfm": Status.DOWN})
        assert notices and sent == [] and not t.enabled
        assert h.notice_log[0][1:] == (False, "notices disabled for this environment")

    def test_no_delivery_configured(self, envs, repo_rules):
        h = FakeHistory()
        NoticeTracker("PROD", repo_rules, history=h).process(
            NOW + 15 * MIN, states_with_hfm2_down(envs["PROD"], repo_rules), STATUS)
        assert h.notice_log[0][1:] == (False, "no delivery configured")

    def test_delivery_failure_logged_and_polling_continues(self, envs, repo_rules):
        def boom(notices):
            raise ConnectionError("smtp down")
        h = FakeHistory()
        t = NoticeTracker("PROD", repo_rules, history=h, deliver=boom)
        assert t.process(NOW + 15 * MIN, states_with_hfm2_down(envs["PROD"], repo_rules), STATUS)
        assert h.notice_log[0][1:] == (False, "delivery failed (ConnectionError)")

    def test_maintenance_calendar_applied(self, envs, repo_rules):
        cal = FixedMaintenance({"PROD": [MaintenanceWindow(NOW, NOW + 60 * MIN, "epmhfm")]})
        t = NoticeTracker("PROD", repo_rules, maintenance=cal)
        assert t.process(NOW + 15 * MIN, states_with_hfm2_down(envs["PROD"], repo_rules), STATUS) == []

    def test_unreadable_maintenance_calendar_still_notifies(self, envs, repo_rules):
        class Broken:
            def windows(self, environment, at):
                raise RuntimeError("db locked")
        t = NoticeTracker("PROD", repo_rules, maintenance=Broken())
        assert t.process(NOW + 15 * MIN, states_with_hfm2_down(envs["PROD"], repo_rules), STATUS)

    def test_history_failures_do_not_break(self, envs, repo_rules):
        t = NoticeTracker("PROD", repo_rules, history=FakeHistory(fail_writes=True))
        assert t.process(NOW + 15 * MIN, states_with_hfm2_down(envs["PROD"], repo_rules), STATUS)

    def test_restored_states_limited_to_enabled_components(self, repo_rules):
        h = FakeHistory()
        h.notice_states["PROD"] = {"fr_rmi": ComponentNoticeState(failing_since=NOW),
                                   "epmhfm": ComponentNoticeState(failing_since=NOW)}
        assert set(NoticeTracker("PROD", repo_rules, history=h).states) == {"epmhfm"}


class TestSqlitePersistence:
    def test_notice_states_and_log_round_trip(self, tmp_path, envs, repo_rules):
        db = SqliteHistory(tmp_path / "h.sqlite")
        t = NoticeTracker("PROD", repo_rules, history=db)
        (n,) = t.process(NOW + 15 * MIN, states_with_hfm2_down(envs["PROD"], repo_rules), STATUS)
        assert db.load_notice_states("PROD") == t.states
        ((logged, delivered, note),) = db.notices("PROD", NOW)
        assert logged == n and (delivered, note) == (False, "no delivery configured")
        assert db.purge_before(NOW + 100 * MIN) >= 1 and db.notices("PROD", NOW) == []
        assert db.load_notice_states("PROD") == t.states            # states never purged
        db.close()

    def test_version_1_file_upgraded(self, tmp_path):
        path = tmp_path / "old.sqlite"
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE polls (environment TEXT, observed_at TEXT, overall_pct REAL, uncapped_pct REAL, "
                    "band TEXT, capped_by TEXT, duration_seconds REAL, collector_errors TEXT, "
                    "PRIMARY KEY (environment, observed_at))")
        con.execute("PRAGMA user_version=1")
        con.commit()
        con.close()
        db = SqliteHistory(path)
        assert db.load_notice_states("PROD") == {}
        db.close()
        assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == 2


class TestPollerIntegration:
    def test_notice_flows_into_snapshot_and_summary(self, envs, repo_rules):
        env = envs["PROD"]
        c = healthy_collectors(env, repo_rules)
        c.services.states["hfm-node-02.example.test"]["HyS9FinancialManagementJavaServer_EPMHFM2"] = "Stopped"
        p = Poller("PROD", env, repo_rules, c, clock=Clock(), notices=NoticeTracker("PROD", repo_rules))
        snaps = [p.poll_once() for _ in range(8)]
        # Clock starts at T0 (4 Nov, within the first 7 business days: close period -> 5 minutes).
        # Down confirmed at the 2nd poll (T0 + 1 min); first notice 5 min later at the 7th poll.
        with_notice = [i for i, s in enumerate(snaps) if s.notices]
        assert with_notice == [6]
        (n,) = snaps[6].notices
        assert n.kind is NoticeKind.FIRST and n.close_period and n.since == T0 + MIN
        line = [x for x in summarise(snaps[6]) if "NOTICE" in x][0]
        assert line == "  NOTICE first: epmhfm - 1 of 3 node(s), since 2026-11-04 09:01 UTC"

    def test_notice_engine_failure_never_stops_polling(self, envs, repo_rules):
        class Broken(NoticeTracker):
            def process(self, at, node_states, component_status):
                raise RuntimeError("bug")
        env = envs["PROD"]
        p = Poller("PROD", env, repo_rules, healthy_collectors(env, repo_rules), clock=Clock(),
                   notices=Broken("PROD", repo_rules))
        s = p.poll_once()
        assert s.score.overall_pct == 100.0 and s.notices == ()
