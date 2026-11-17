"""NoticeMailer, its use by the NoticeTracker, and the --test-email command."""

from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest

from hfm_dashboard.adapters.fake_history import FakeHistory
from hfm_dashboard.adapters.fake_mail import RecordingMailSender
from hfm_dashboard.app.main import main
from hfm_dashboard.app.notice_mailer import NoticeMailer
from hfm_dashboard.app.notice_tracker import NoticeTracker
from hfm_dashboard.config.environment_loader import parse_environments
from hfm_dashboard.config.owners_loader import parse_owners
from hfm_dashboard.domain.notices import Notice, NoticeKind
from hfm_dashboard.ports.mail import DeliveryError
from tests.app.test_notice_tracker import NOW, MIN, STATUS, states_with_hfm2_down
from tests.app.test_wiring_and_main import fake_factory
from tests.conftest import FILLED, REPO_ROOT, RULES_PATH

OWNERS = (REPO_ROOT / "config" / "owners.template.yaml").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def prod(repo_rules):
    return parse_environments(FILLED, repo_rules).environments["PROD"]


@pytest.fixture(scope="module")
def owners(repo_rules):
    return parse_owners(OWNERS, repo_rules)


def notice(component="epmhfm"):
    return Notice(NoticeKind.FIRST, "PROD", component, "X", datetime(2026, 11, 17, tzinfo=UTC), None,
                  ("hfm_node_02",), 3, False, False, False)


class TestMailer:
    def test_sends_each_notice(self, prod, repo_rules, owners):
        s = RecordingMailSender()
        NoticeMailer(prod, repo_rules, owners, s)([notice("epmhfm"), notice("fr_web")])
        assert [m.to for m in s.sent] == [("hfm-team@example.test",), ("epm-support@example.test",)]

    def test_owner_list_missing(self, prod, repo_rules):
        with pytest.raises(DeliveryError, match="owner list missing"):
            NoticeMailer(prod, repo_rules, None, RecordingMailSender())([notice()])

    def test_failures_summarised_after_trying_all(self, prod, repo_rules, owners):
        class Flaky(RecordingMailSender):
            def send(self, mail):
                if "hfm-team@example.test" in mail.to:
                    raise DeliveryError("SMTP relay not reachable (OSError)")
                super().send(mail)
        s = Flaky()
        with pytest.raises(DeliveryError, match=r"1 of 2 not sent - epmhfm: SMTP relay not reachable"):
            NoticeMailer(prod, repo_rules, owners, s)([notice("epmhfm"), notice("fr_web")])
        assert len(s.sent) == 1


class TestThroughTracker:
    def test_first_notice_emailed(self, prod, repo_rules, owners):
        s, h = RecordingMailSender(), FakeHistory()
        t = NoticeTracker("PROD", repo_rules, history=h, deliver=NoticeMailer(prod, repo_rules, owners, s))
        t.process(NOW + 15 * MIN, states_with_hfm2_down(prod, repo_rules), STATUS)
        (mail,) = s.sent
        assert mail.subject.startswith("[HFM PROD] PARTIAL OUTAGE: HFM Server (cluster)")
        assert h.notice_log[0][1:] == (True, "")

    def test_missing_owner_list_logged_with_reason(self, prod, repo_rules):
        h = FakeHistory()
        t = NoticeTracker("PROD", repo_rules, history=h,
                          deliver=NoticeMailer(prod, repo_rules, None, RecordingMailSender()))
        t.process(NOW + 15 * MIN, states_with_hfm2_down(prod, repo_rules), STATUS)
        assert h.notice_log[0][1:] == (False, "delivery failed (owner list missing)")


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rules.yaml").write_text(RULES_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "environments.yaml").write_text(FILLED, encoding="utf-8")
    return tmp_path


class TestTestEmail:
    def test_sent_to_monitoring_team(self, config_dir):
        (config_dir / "owners.yaml").write_text(OWNERS, encoding="utf-8")
        s, out = RecordingMailSender(), io.StringIO()
        code = main(["--config-dir", str(config_dir), "--test-email", "PROD", "--no-history"],
                    collectors_factory=fake_factory, sender_factory=lambda env, rules: s, out=out)
        assert code == 0 and "test email sent to 1 monitoring address(es)" in out.getvalue()
        (mail,) = s.sent
        assert mail.to == ("monitoring-team@example.test",) and mail.subject == "[HFM PROD] TEST: notice delivery check"

    def test_delivery_failure_exit_1(self, config_dir):
        (config_dir / "owners.yaml").write_text(OWNERS, encoding="utf-8")
        out = io.StringIO()
        code = main(["--config-dir", str(config_dir), "--test-email", "PROD", "--no-history"],
                    collectors_factory=fake_factory, out=out,
                    sender_factory=lambda env, rules: RecordingMailSender(fail="SMTP relay not reachable (OSError)"))
        assert code == 1 and "NOT sent: SMTP relay not reachable" in out.getvalue()

    def test_needs_owners_file(self, config_dir):
        assert main(["--config-dir", str(config_dir), "--test-email", "PROD", "--no-history"],
                    collectors_factory=fake_factory, sender_factory=lambda e, r: RecordingMailSender()) == 2

    def test_invalid_owners_file_refuses_start(self, config_dir):
        (config_dir / "owners.yaml").write_text('monitoring_team: ["not-an-address"]\n', encoding="utf-8")
        assert main(["--config-dir", str(config_dir), "--once", "--no-history"],
                    collectors_factory=fake_factory, sender_factory=lambda e, r: RecordingMailSender()) == 2

    def test_missing_owners_file_still_polls(self, config_dir):
        out = io.StringIO()
        assert main(["--config-dir", str(config_dir), "--once", "--no-history", "--env", "PROD"],
                    collectors_factory=fake_factory, sender_factory=lambda e, r: RecordingMailSender(),
                    out=out) == 0
        assert "PROD: 100.00% GREEN" in out.getvalue()
