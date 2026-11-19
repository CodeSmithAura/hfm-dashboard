"""hfm-service: install / check / status / uninstall against a fake service manager."""

from __future__ import annotations

import io
import sys

import pytest

from hfm_dashboard.adapters.fake_service_control import FakeServiceControl
from hfm_dashboard.app.service_host import CHECK_FLAG, SERVICE_NAME, ServiceOptions
from hfm_dashboard.ports.service_control import ServiceControlError
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
