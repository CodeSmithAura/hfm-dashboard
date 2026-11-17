"""Notice email composition: recipients, subject, body, restart guidance (invented addresses only)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hfm_dashboard.config.environment_loader import parse_environments
from hfm_dashboard.config.owners_loader import parse_owners
from hfm_dashboard.domain.notice_text import compose
from hfm_dashboard.domain.notices import Notice, NoticeKind
from tests.conftest import FILLED, REPO_ROOT

NOW = datetime(2026, 11, 17, 10, 37, tzinfo=UTC)
SINCE = datetime(2026, 11, 17, 9, 1, tzinfo=UTC)
OWNERS_TEMPLATE = (REPO_ROOT / "config" / "owners.template.yaml").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def prod(repo_rules):
    return parse_environments(FILLED, repo_rules).environments["PROD"]


@pytest.fixture(scope="module")
def owners(repo_rules):
    return parse_owners(OWNERS_TEMPLATE, repo_rules)


def notice(kind=NoticeKind.FIRST, component="epmhfm", name="HFM Server (cluster)", affected=("hfm_node_02",),
           total=3, full=False, escalate=False, close=False, downstream=(), details=("hfm_node_02: service Stopped",),
           at=NOW, since=SINCE):
    return Notice(kind, "PROD", component, name, at, since, affected, total, full, escalate, close,
                  downstream, details)


class TestRecipients:
    def test_owners_without_managers_before_escalation(self, owners, repo_rules, prod):
        m = compose(notice(), owners, repo_rules, prod)
        assert m.to == ("hfm-team@example.test",) and m.cc == ()

    def test_managers_copied_when_escalated(self, owners, repo_rules, prod):
        m = compose(notice(NoticeKind.ESCALATION, escalate=True), owners, repo_rules, prod)
        assert m.cc == ("hfm-manager@example.test",)

    def test_default_contacts_for_unlisted_component(self, owners, repo_rules, prod):
        m = compose(notice(component="fr_web", name="Financial Reporting Web"), owners, repo_rules, prod)
        assert m.to == ("epm-support@example.test",)

    def test_monitoring_team_for_cannot_monitor(self, owners, repo_rules, prod):
        m = compose(notice(NoticeKind.UNKNOWN_WARNING, details=("hfm_node_02: WinRM not reachable",)),
                    owners, repo_rules, prod)
        assert m.to == ("monitoring-team@example.test",) and m.cc == ()

    def test_no_contacts_returns_none(self, repo_rules, prod):
        from hfm_dashboard.domain.owners import OwnersConfig
        bare = OwnersConfig(monitoring_team=["m@example.test"])          # no default, no components
        assert compose(notice(), bare, repo_rules, prod) is None


class TestContent:
    def test_first_partial_outage(self, owners, repo_rules, prod):
        m = compose(notice(), owners, repo_rules, prod)
        assert m.subject == "[HFM PROD] PARTIAL OUTAGE: HFM Server (cluster) - 1 of 3 node(s) since 09:01 UTC"
        assert "Status:    Partial outage: 1 of 3 node(s) Down" in m.body
        assert "Since:     2026-11-17 09:01 UTC (1 h 36 min ago)" in m.body
        assert "  - hfm_node_02 (hfm-node-02.example.test): service Stopped" in m.body
        assert m.body.startswith("HFM availability notice - Production\n")

    def test_full_outage_subject(self, owners, repo_rules, prod):
        m = compose(notice(full=True, affected=("hfm_node_01", "hfm_node_02", "hfm_node_03"), details=()),
                    owners, repo_rules, prod)
        assert m.subject.startswith("[HFM PROD] DOWN: HFM Server (cluster) - all 3 node(s)")

    @pytest.mark.parametrize(("kind", "label"), [
        (NoticeKind.REMINDER, "STILL PARTIAL OUTAGE"), (NoticeKind.ESCALATION, "ESCALATED - PARTIAL OUTAGE"),
        (NoticeKind.RECOVERY, "RECOVERED"), (NoticeKind.UNKNOWN_CLEARED, "MONITORING RESTORED"),
    ])
    def test_subject_labels(self, owners, repo_rules, prod, kind, label):
        assert compose(notice(kind), owners, repo_rules, prod).subject.startswith(f"[HFM PROD] {label}: ")

    def test_recovery_states_outage_window(self, owners, repo_rules, prod):
        m = compose(notice(NoticeKind.RECOVERY, affected=(), details=()), owners, repo_rules, prod)
        assert "Outage:    2026-11-17 09:01 UTC to 2026-11-17 10:37 UTC (1 h 36 min)" in m.body
        assert "Restart guidance" not in m.body

    def test_escalation_line(self, owners, repo_rules, prod):
        m = compose(notice(NoticeKind.ESCALATION, escalate=True), owners, repo_rules, prod)
        assert "Escalated: still not restored after 60 minutes; managers copied" in m.body

    def test_close_period_line(self, owners, repo_rules, prod):
        assert "close period (first notice after 5 minutes)" in compose(notice(close=True), owners, repo_rules, prod).body

    def test_downstream_listed_by_name(self, owners, repo_rules, prod):
        m = compose(notice(component="sql_server", name="SQL Server", affected=("db_node_01",), total=1, full=True,
                           downstream=("epmhfm", "sql_agent"), details=()), owners, repo_rules, prod)
        assert "  - HFM Server (cluster)\n  - SQL Server Agent" in m.body

    def test_cannot_monitor_explains_meaning(self, owners, repo_rules, prod):
        m = compose(notice(NoticeKind.UNKNOWN_WARNING, details=("hfm_node_02: WinRM not reachable",)),
                    owners, repo_rules, prod)
        assert m.subject == "[HFM PROD] CANNOT MONITOR: HFM Server (cluster) - 1 node(s) cannot be checked"
        assert "the application may be fine" in m.body and "Restart guidance" not in m.body

    def test_dashboard_link_when_configured(self, owners, repo_rules, prod):
        env = prod.model_copy(update={"notices": prod.notices.model_copy(
            update={"dashboard_url": "https://dashboard.example.test/"})})
        assert "Dashboard: https://dashboard.example.test/" in compose(notice(), owners, repo_rules, env).body


class TestRestartGuidance:
    def test_order_step_and_service(self, owners, repo_rules, prod):
        body = compose(notice(), owners, repo_rules, prod).body
        assert ("Start order: 1. SQL Server, 2. WebLogic Admin Server, 3. Foundation / Shared Services (Workspace), "
                "4. Oracle HTTP Server / Load balancer, 5. Financial Reporting Web, 6. HFM Web, 7. HFM Server (cluster)"
                in body)
        assert "This component is step 7; make sure steps 1-6 are running first." in body
        assert "    - hfm_node_02: HyS9FinancialManagementJavaServer_EPMHFM2" in body
        assert "the monitor never restarts anything" in body

    def test_first_step(self, owners, repo_rules, prod):
        body = compose(notice(component="sql_server", name="SQL Server", affected=("db_node_01",), total=1,
                              full=True, details=()), owners, repo_rules, prod).body
        assert "This component is step 1." in body and "    - db_node_01: MSSQLSERVER" in body

    def test_switched_off(self, owners, repo_rules, prod):
        rules = repo_rules.model_copy(update={"notices": repo_rules.notices.model_copy(
            update={"include_restart_guidance": False})})
        assert "Restart guidance" not in compose(notice(), owners, rules, prod).body

    def test_restart_order_validated(self, rules_dict):
        from pydantic import ValidationError

        from hfm_dashboard.domain.rules import Rules
        rules_dict["notices"]["restart_order"] = ["db", "nope"]
        with pytest.raises(ValidationError, match="unknown component 'nope'"):
            Rules.model_validate(rules_dict)
        rules_dict["notices"]["restart_order"] = ["db", "db"]
        with pytest.raises(ValidationError, match="listed twice"):
            Rules.model_validate(rules_dict)

    def test_durations(self, owners, repo_rules, prod):
        m = compose(notice(at=SINCE + timedelta(minutes=15)), owners, repo_rules, prod)
        assert "(15 min ago)" in m.body
