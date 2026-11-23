"""hfm-service: install / check / status / uninstall against a fake service manager."""

from __future__ import annotations

import io
import sys

import pytest

from hfm_dashboard.adapters.fake_service_control import FakeServiceControl
from hfm_dashboard.app.service_host import CHECK_FLAG, SERVICE_NAME, ServiceOptions
from hfm_dashboard.ports.service_control import ServiceControlError, needs_password
from hfm_dashboard.tools.service_cli import main
from tests.conftest import FILLED, RULES_PATH


@pytest.fixture
def dirs(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "rules.yaml").write_text(RULES_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    (cfg / "environments.yaml").write_text(FILLED, encoding="utf-8")
    return ["--config-dir", str(cfg), "--data-dir", str(tmp_path / "data"), "--log-dir", str(tmp_path / "logs")]


class Runner:
    def __init__(self, code=0, text="OK: checked"):
        self.code, self.text, self.calls = code, text, []

    def __call__(self, args):
        self.calls.append(args)
        return self.code, self.text


def run(argv, **kw):
    out = io.StringIO()
    kw.setdefault("runner", Runner())
    code = main(argv, out=out, **kw)
    return code, out.getvalue()


class TestCheck:
    def test_runs_the_service_command_line_with_check_flag(self, dirs):
        runner = Runner()
        code, text = run(["check", *dirs], runner=runner)
        assert code == 0 and "OK: checked" in text
        (args,) = runner.calls
        assert args[1:3] == ["-I", "-S"] and args[3].endswith("service_boot.py") and args[5] == CHECK_FLAG
        assert ServiceOptions.parse(args[6:]).config_dir.is_absolute()

    def test_failing_self_check(self, dirs):
        code, text = run(["check", *dirs], runner=Runner(2, "PROBLEM: pywin32 missing"))
        assert code == 2 and "failed its self-check" in text

    def test_invalid_configuration_stops_before_the_subprocess(self, dirs, tmp_path):
        (tmp_path / "config" / "environments.yaml").unlink()
        runner = Runner()
        code, text = run(["check", *dirs], runner=runner)
        assert code == 2 and "PROBLEM" in text and runner.calls == []


class TestInstall:
    def test_installs_with_recovery_from_rules(self, dirs):
        fake = FakeServiceControl()
        code, text = run(["install", *dirs, "--account", r"EXAMPLE\svc-hfm-mon$"], control=fake)
        assert code == 0
        spec = fake.services[SERVICE_NAME]
        assert spec.account == r"EXAMPLE\svc-hfm-mon$" and spec.delayed_auto_start
        assert spec.command[1:3] == ("-I", "-S") and CHECK_FLAG not in spec.command
        assert fake.recovery[SERVICE_NAME] == ([60, 120, 300], 24 * 3600)
        assert fake.states[SERVICE_NAME] == "stopped" and "Start-Service" in text
        assert "WARNING" not in text

    def test_registers_the_base_interpreter(self, dirs):
        fake = FakeServiceControl()
        run(["install", *dirs], control=fake)
        if sys.prefix != sys.base_prefix:
            assert ".venv" not in fake.services[SERVICE_NAME].command[0]

    def test_local_system_warns(self, dirs):
        code, text = run(["install", *dirs], control=FakeServiceControl())
        assert code == 0 and "WARNING: running as LocalSystem" in text

    def test_start(self, dirs):
        fake = FakeServiceControl()
        assert run(["install", *dirs, "--start"], control=fake)[0] == 0
        assert fake.states[SERVICE_NAME] == "running"

    def test_already_installed(self, dirs):
        fake = FakeServiceControl()
        run(["install", *dirs], control=fake)
        code, text = run(["install", *dirs], control=fake)
        assert code == 2 and "already installed" in text

    def test_failed_self_check_installs_nothing(self, dirs):
        fake = FakeServiceControl()
        code, _ = run(["install", *dirs], control=fake, runner=Runner(2, "PROBLEM: x"))
        assert code == 2 and fake.calls == []

    def test_access_denied_explains_elevation(self, dirs):
        fake = FakeServiceControl(fail_with=ServiceControlError("install service failed", access_denied=True))
        code, text = run(["install", *dirs], control=fake)
        assert code == 1 and "elevated prompt" in text

    def test_relative_paths_are_made_absolute(self, dirs, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        fake = FakeServiceControl()
        assert run(["install", "--config-dir", "config", "--data-dir", "data", "--log-dir", "logs"],
                   control=fake)[0] == 0
        opts = ServiceOptions.parse(list(fake.services[SERVICE_NAME].command[5:]))
        assert opts.config_dir == (tmp_path / "config").resolve()


class Prompt:
    """Stands in for getpass: returns the given answers in order and records the prompts."""

    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


USER = r"EXAMPLE\svc-hfm-user"
SECRET = "Pa55-word-for-test"


class TestAccounts:
    def test_managed_account_gets_logon_right_and_no_password(self, dirs):
        fake = FakeServiceControl()
        prompt = Prompt()
        code, text = run(["install", *dirs, "--account", r"EXAMPLE\svc-hfm-mon$"], control=fake, ask_password=prompt)
        assert code == 0 and prompt.prompts == []
        assert fake.services[SERVICE_NAME].password is None
        assert fake.logon_rights == {r"EXAMPLE\svc-hfm-mon$"}
        assert [c for c, _ in fake.calls][:2] == ["grant_service_logon", "install"]

    def test_local_system_gets_no_grant(self, dirs):
        fake = FakeServiceControl()
        run(["install", *dirs], control=fake)
        assert fake.logon_rights == set() and "grant_service_logon" not in [c for c, _ in fake.calls]

    def test_built_in_account_gets_no_grant_and_no_password(self, dirs):
        fake = FakeServiceControl()
        code, _ = run(["install", *dirs, "--account", r"NT AUTHORITY\LocalService"], control=fake)
        assert code == 0 and fake.logon_rights == set()

    def test_user_account_with_password(self, dirs):
        fake = FakeServiceControl()
        prompt = Prompt(SECRET, SECRET)
        code, text = run(["install", *dirs, "--account", USER, "--ask-password"], control=fake, ask_password=prompt)
        assert code == 0
        spec = fake.services[SERVICE_NAME]
        assert spec.account == USER and spec.password == SECRET
        assert fake.logon_rights == {USER}
        assert prompt.prompts == [f"Password for {USER}: ", "Again: "]
        assert "'Log on as a service' granted" in text and "NOTE: interim user account (D33)" in text
        assert SECRET not in text and SECRET not in repr(spec)

    def test_user_account_without_ask_password_refused_before_anything(self, dirs):
        fake, runner = FakeServiceControl(), Runner()
        code, text = run(["install", *dirs, "--account", USER], control=fake, runner=runner)
        assert code == 2 and "add --ask-password" in text and "1069" in text
        assert fake.calls == [] and runner.calls == []

    @pytest.mark.parametrize("account", [r"EXAMPLE\svc-hfm-mon$", None])
    def test_ask_password_refused_for_accounts_without_password(self, dirs, account):
        fake = FakeServiceControl()
        argv = ["install", *dirs, "--ask-password"] + (["--account", account] if account else [])
        code, text = run(argv, control=fake, ask_password=Prompt())
        assert code == 2 and "only for an ordinary user account" in text and fake.calls == []

    @pytest.mark.parametrize("answers, reason", [
        ((SECRET, "different"), "differ"),
        (("", ""), "empty password"),
        ((EOFError(),), "no password entered"),
        ((KeyboardInterrupt(),), "no password entered"),
    ])
    def test_bad_password_entry_installs_nothing(self, dirs, answers, reason):
        fake = FakeServiceControl()
        code, text = run(["install", *dirs, "--account", USER, "--ask-password"], control=fake,
                         ask_password=Prompt(*answers))
        assert code == 2 and reason in text and "nothing installed" in text and fake.calls == []
        assert SECRET not in text

    def test_password_asked_only_after_the_self_check_passed(self, dirs):
        prompt = Prompt(SECRET, SECRET)
        code, _ = run(["install", *dirs, "--account", USER, "--ask-password"], control=FakeServiceControl(),
                      runner=Runner(2, "PROBLEM: x"), ask_password=prompt)
        assert code == 2 and prompt.prompts == []

    def test_unknown_account_installs_nothing(self, dirs):
        fake = FakeServiceControl()
        fake.fail_grant_with = ServiceControlError("account 'EXAMPLE\\svc-hfm-usr' was not found")
        code, text = run(["install", *dirs, "--account", USER, "--ask-password"], control=fake,
                         ask_password=Prompt(SECRET, SECRET))
        assert code == 1 and "was not found" in text and SERVICE_NAME not in fake.services


@pytest.mark.parametrize("account, expected", [
    (None, False),
    (r"EXAMPLE\svc-hfm-mon$", False),
    (r"NT AUTHORITY\NetworkService", False),
    (r"nt service\Something", False),
    (r"EXAMPLE\svc-hfm-user", True),
    ("svc-hfm-user@example.test", True),
    (r".\localuser", True),
])
def test_needs_password(account, expected):
    assert needs_password(account) is expected


class TestStatusAndUninstall:
    def test_status_codes(self, dirs):
        fake = FakeServiceControl()
        assert run(["status"], control=fake)[0] == 3
        run(["install", *dirs], control=fake)
        code, text = run(["status"], control=fake)
        assert code == 1 and "stopped" in text and "service_boot.py" in text
        fake.start(SERVICE_NAME)
        assert run(["status"], control=fake)[0] == 0

    def test_uninstall_stops_then_removes(self, dirs):
        fake = FakeServiceControl()
        run(["install", *dirs, "--start"], control=fake)
        code, text = run(["uninstall"], control=fake)
        assert code == 0 and "up to 90 s" in text          # stop_timeout_seconds from the service's rules.yaml
        assert [c for c, _ in fake.calls][-2:] == ["stop", "remove"] and SERVICE_NAME not in fake.services

    def test_uninstall_stopped_service_skips_stop(self, dirs):
        fake = FakeServiceControl()
        run(["install", *dirs], control=fake)
        run(["uninstall"], control=fake)
        assert "stop" not in [c for c, _ in fake.calls]

    def test_uninstall_when_absent(self):
        code, text = run(["uninstall"], control=FakeServiceControl())
        assert code == 0 and "nothing to do" in text
