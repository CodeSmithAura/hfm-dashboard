"""Notice engine: timings, partial/full outages, maintenance, root-cause grouping, Unknown warnings,
close calendar. Driven by the shipped rules.yaml (15 / 5 / 30 / 60 / 15 minutes)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from hfm_dashboard.config.environment_loader import parse_environments
from hfm_dashboard.domain.notices import (
    MaintenanceWindow,
    NoticeKind,
    close_label,
    evaluate_notices,
    is_close_period,
)
from hfm_dashboard.domain.scoring import score
from hfm_dashboard.domain.snapshot import component_nodes, expected_checks, node_statuses
from hfm_dashboard.domain.status import NodeState, Status
from tests.conftest import FILLED

U, G, D, X = Status.UP, Status.DEGRADED, Status.DOWN, Status.UNKNOWN
NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)       # Tuesday, not a close day
MIN = timedelta(minutes=1)
HFM2 = ("epmhfm", "hfm_node_02")


@pytest.fixture(scope="module")
def prod(repo_rules):
    return parse_environments(FILLED, repo_rules).environments["PROD"]


class World:
    """Node states over time; ``step`` runs the engine at a time and returns that poll's notices."""

    def __init__(self, env, rules):
        self.env, self.rules = env, rules
        self.states = {k: NodeState(k[0], k[1], U, 0, NOW - 60 * MIN) for k in expected_checks(env, rules)}
        self.notice_states = {}
        self.maintenance = []

    def set(self, key, status, since, detail=""):
        self.states[key] = NodeState(key[0], key[1], status, 0, since, status, detail)

    def step(self, at):
        comp = score(node_statuses(self.states), self.rules, component_nodes(self.env, self.rules)).components
        self.notice_states, notices = evaluate_notices(
            "PROD", self.notice_states, self.states, {c: s.status for c, s in comp.items()},
            self.rules, at, self.maintenance)
        return notices

    def run(self, start, minutes):
        """Every-minute polls; returns {minute: [kinds]} for polls that produced notices."""
        out = {}
        for m in range(minutes + 1):
            got = self.step(start + m * MIN)
            if got:
                out[m] = [(n.kind, n.component) for n in got]
        return out


@pytest.fixture
def world(prod, repo_rules):
    return World(prod, repo_rules)


class TestTimeline:
    def test_partial_outage_full_timeline(self, world):
        world.set(HFM2, D, NOW, "service Stopped")
        timeline = world.run(NOW, 95)
        assert timeline == {
            15: [(NoticeKind.FIRST, "epmhfm")],
            45: [(NoticeKind.REMINDER, "epmhfm")],
            60: [(NoticeKind.ESCALATION, "epmhfm")],
            90: [(NoticeKind.REMINDER, "epmhfm")],
        }
        world.set(HFM2, U, NOW + 96 * MIN)
        (rec,) = world.step(NOW + 96 * MIN)
        assert rec.kind is NoticeKind.RECOVERY and rec.since == NOW and rec.escalate

    def test_first_notice_content(self, world):
        world.set(HFM2, D, NOW, "service Stopped")
        (n,) = world.step(NOW + 15 * MIN)
        assert (n.environment, n.component, n.display_name) == ("PROD", "epmhfm", "HFM Server (cluster)")
        assert n.affected_nodes == ("hfm_node_02",) and n.total_nodes == 3
        assert not n.full_outage and not n.escalate and not n.close_period
        assert n.details == ("hfm_node_02: service Stopped",) and n.since == NOW

    def test_reminders_after_escalation_include_manager(self, world):
        world.set(HFM2, D, NOW)
        world.run(NOW, 60)
        (n,) = world.step(NOW + 90 * MIN)
        assert n.kind is NoticeKind.REMINDER and n.escalate

    def test_short_outage_sends_nothing(self, world):
        world.set(HFM2, D, NOW)
        assert world.run(NOW, 14) == {}
        world.set(HFM2, U, NOW + 15 * MIN)
        assert world.step(NOW + 15 * MIN) == []

    def test_full_outage_flag(self, world):
        for i in (1, 2, 3):
            world.set(("epmhfm", f"hfm_node_0{i}"), D, NOW)
        (n,) = world.step(NOW + 15 * MIN)
        assert n.full_outage and len(n.affected_nodes) == 3

    def test_late_first_notice_already_escalated(self, world):
        world.set(HFM2, D, NOW - 70 * MIN)          # e.g. the service was down before monitoring started
        (n,) = world.step(NOW)
        assert n.kind is NoticeKind.FIRST and n.escalate
        assert world.step(NOW + MIN) == []          # no separate escalation afterwards


class TestRulesSwitches:
    def test_partial_outage_switch_off(self, prod, repo_rules):
        rules = repo_rules.model_copy(update={"notices": repo_rules.notices.model_copy(
            update={"notify_on_partial_outage": False})})
        w = World(prod, rules)
        w.set(HFM2, D, NOW)
        assert w.run(NOW, 20) == {}
        for i in (1, 3):
            w.set(("epmhfm", f"hfm_node_0{i}"), D, NOW + 20 * MIN)
        assert w.run(NOW + 20 * MIN, 15)[15] == [(NoticeKind.FIRST, "epmhfm")]

    def test_degraded_does_not_notify_by_default(self, world):
        world.set(HFM2, G, NOW)
        assert world.run(NOW, 30) == {}

    def test_degraded_notifies_when_switched_on(self, prod, repo_rules):
        rules = repo_rules.model_copy(update={"notices": repo_rules.notices.model_copy(
            update={"notify_on_degraded": True})})
        w = World(prod, rules)
        w.set(HFM2, G, NOW)
        assert w.run(NOW, 15) == {15: [(NoticeKind.FIRST, "epmhfm")]}

    def test_no_recovery_notice_when_switched_off(self, prod, repo_rules):
        rules = repo_rules.model_copy(update={"notices": repo_rules.notices.model_copy(
            update={"send_recovery_notice": False})})
        w = World(prod, rules)
        w.set(HFM2, D, NOW)
        w.run(NOW, 15)
        w.set(HFM2, U, NOW + 16 * MIN)
        assert w.step(NOW + 16 * MIN) == []

    def test_acknowledgement_not_supported_yet(self, rules_dict):
        from pydantic import ValidationError

        from hfm_dashboard.domain.rules import Rules
        rules_dict["notices"]["require_acknowledgement"] = True
        with pytest.raises(ValidationError, match="require_acknowledgement"):
            Rules.model_validate(rules_dict)


class TestMaintenance:
    def test_nothing_sent_inside_window_timer_restarts_at_end(self, world):
        world.maintenance = [MaintenanceWindow(NOW, NOW + 30 * MIN, "epmhfm", "patching")]
        world.set(HFM2, D, NOW)
        assert world.run(NOW, 44) == {}
        assert [n.kind for n in world.step(NOW + 45 * MIN)] == [NoticeKind.FIRST]   # 15 min after window end

    def test_window_for_other_component_does_not_suppress(self, world):
        world.maintenance = [MaintenanceWindow(NOW, NOW + 30 * MIN, "fr_web")]
        world.set(HFM2, D, NOW)
        assert 15 in world.run(NOW, 15)

    def test_environment_wide_window(self, world):
        world.maintenance = [MaintenanceWindow(NOW, NOW + 60 * MIN)]
        world.set(HFM2, D, NOW)
        assert world.run(NOW, 59) == {}

    def test_recovery_inside_window_not_sent(self, world):
        world.set(HFM2, D, NOW)
        world.run(NOW, 15)
        world.maintenance = [MaintenanceWindow(NOW + 20 * MIN, NOW + 60 * MIN)]
        world.set(HFM2, U, NOW + 25 * MIN)
        assert world.step(NOW + 25 * MIN) == []

    def test_suppression_switch(self, prod, repo_rules):
        rules = repo_rules.model_copy(update={"maintenance": repo_rules.maintenance.model_copy(
            update={"suppress_notices_in_window": False})})
        w = World(prod, rules)
        w.maintenance = [MaintenanceWindow(NOW, NOW + 60 * MIN)]
        w.set(HFM2, D, NOW)
        assert 15 in w.run(NOW, 15)


class TestRootCause:
    def test_downstream_grouped_under_hard_dependency(self, world):
        world.set(("sql_server", "db_node_01"), D, NOW, "service Stopped")
        world.set(HFM2, D, NOW)
        world.set(("sql_agent", "db_node_01"), D, NOW)
        timeline = world.run(NOW, 15)
        assert timeline == {15: [(NoticeKind.FIRST, "sql_server")]}
        (n,) = [x for x in world.step(NOW + 45 * MIN) if x.component == "sql_server"]
        assert n.kind is NoticeKind.REMINDER and n.downstream == ("epmhfm", "sql_agent")
        assert world.notice_states["epmhfm"].grouped_under == "sql_server"

    def test_downstream_notified_once_root_recovers(self, world):
        world.set(("sql_server", "db_node_01"), D, NOW)
        world.set(HFM2, D, NOW)
        world.run(NOW, 20)
        world.set(("sql_server", "db_node_01"), U, NOW + 21 * MIN)
        kinds = {(n.kind, n.component) for n in world.step(NOW + 21 * MIN)}
        assert kinds == {(NoticeKind.RECOVERY, "sql_server"), (NoticeKind.FIRST, "epmhfm")}

    def test_partial_hard_dependency_is_not_a_root_cause(self, world):
        world.set(("shared_services", "web_node_01"), D, NOW)   # 1 of 2 Foundation nodes
        world.set(HFM2, D, NOW)
        got = world.run(NOW, 15)[15]
        assert set(got) == {(NoticeKind.FIRST, "shared_services"), (NoticeKind.FIRST, "epmhfm")}

    def test_grouping_switch_off(self, prod, repo_rules):
        rules = repo_rules.model_copy(update={"notices": repo_rules.notices.model_copy(
            update={"group_downstream_under_root_cause": False})})
        w = World(prod, rules)
        w.set(("sql_server", "db_node_01"), D, NOW)
        w.set(HFM2, D, NOW)
        assert set(w.run(NOW, 15)[15]) == {(NoticeKind.FIRST, "sql_server"), (NoticeKind.FIRST, "epmhfm")}


class TestUnknownWarnings:
    def test_one_warning_then_cleared(self, world):
        world.set(HFM2, X, NOW, "WinRM not reachable")
        assert world.run(NOW, 30) == {15: [(NoticeKind.UNKNOWN_WARNING, "epmhfm")]}
        world.set(HFM2, U, NOW + 31 * MIN)
        assert [n.kind for n in world.step(NOW + 31 * MIN)] == [NoticeKind.UNKNOWN_CLEARED]

    def test_short_unknown_no_warning_and_unknown_is_not_an_outage(self, world):
        world.set(HFM2, X, NOW)
        assert world.run(NOW, 14) == {}
        assert world.notice_states["epmhfm"].failing_since is None


class TestCloseCalendar:
    @pytest.mark.parametrize(("day", "close", "label"), [
        (date(2026, 11, 17), False, ""),
        (date(2026, 11, 2), True, "month-end close"),        # 1st business day of Nov: October close
        (date(2026, 11, 10), True, "month-end close"),       # 7th business day
        (date(2026, 11, 11), False, ""),                     # 8th business day
        (date(2026, 11, 27), True, "month-end close"),       # second-to-last business day
        (date(2026, 11, 30), True, "month-end close"),       # last business day
        (date(2026, 11, 28), False, ""),                     # Saturday
        (date(2026, 12, 31), True, "quarter-end close"),
        (date(2027, 1, 4), True, "quarter-end close"),       # early January closes December
        (date(2027, 2, 3), True, "month-end close"),
    ])
    def test_days(self, repo_rules, day, close, label):
        assert is_close_period(day, repo_rules.close_calendar) is close
        assert close_label(day, repo_rules.close_calendar) == label

    def test_extra_dates(self, repo_rules):
        cal = repo_rules.close_calendar.model_copy(update={"extra_dates": [date(2026, 11, 18)]})
        assert is_close_period(date(2026, 11, 18), cal)

    def test_close_period_uses_five_minutes(self, prod, repo_rules):
        w = World(prod, repo_rules)
        start = datetime(2026, 11, 30, 9, 0, tzinfo=UTC)
        w.set(HFM2, D, start)
        notices = {m: k for m, k in w.run(start, 6).items()}
        assert notices == {5: [(NoticeKind.FIRST, "epmhfm")]}
        assert w.step(start + 6 * MIN) == []
