"""Service host: options, command line, run/stop outcomes, log file, self-check and the bootstrap."""

from __future__ import annotations

import functools
import logging
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from hfm_dashboard.app.logs import LOG_FILE
from hfm_dashboard.app.main import main
from hfm_dashboard.app.service_host import (
    CHECK_FLAG, ServiceOptions, ServiceOutcome, boot_script, command_line, interpreter_paths, run_service,
    self_check, service_command,
)
from tests.app.test_wiring_and_main import fake_factory
from tests.conftest import FILLED, RULES_PATH


@pytest.fixture
def options(tmp_path) -> ServiceOptions:
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "rules.yaml").write_text(RULES_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    (cfg / "environments.yaml").write_text(FILLED, encoding="utf-8")
    return ServiceOptions(cfg, tmp_path / "data", tmp_path / "logs")


def log_text(options: ServiceOptions) -> str:
    return (options.log_dir / LOG_FILE).read_text(encoding="utf-8")


class TestOptions:
    def test_round_trip(self, tmp_path):
        o = ServiceOptions(tmp_path / "c", tmp_path / "d", tmp_path / "l", tmp_path / "ui", ("PROD",), True)
        assert ServiceOptions.parse(o.to_args()) == o

    def test_main_argv_has_no_log_dir(self, options):
        argv = options.main_argv()
        assert "--log-dir" not in argv and argv[:2] == ["--config-dir", str(options.config_dir)]

    def test_relative_folder_refused(self, tmp_path):
        with pytest.raises(ValueError, match="config-dir must be an absolute path"):
            ServiceOptions(Path("config"), tmp_path, tmp_path)


class TestCommandLine:
    def test_isolated_interpreter_then_boot_script_then_site_packages(self, options):
        args = service_command(Path(r"C:\Python312\python.exe"), Path(r"C:\app\service_boot.py"),
                               Path(r"C:\app\venv\Lib\site-packages"), options)
        assert args[:5] == [r"C:\Python312\python.exe", "-I", "-S", r"C:\app\service_boot.py",
                            r"C:\app\venv\Lib\site-packages"]
        assert ServiceOptions.parse(args[5:]) == options

    def test_paths_with_spaces_are_quoted(self, tmp_path):
        o = ServiceOptions(Path(r"D:\HFM Monitor\config"), Path(r"D:\HFM Monitor\data"), Path(r"D:\HFM Monitor\logs"))
        line = command_line(service_command(Path(r"C:\Program Files\Python312\python.exe"),
                                            Path(r"C:\x\service_boot.py"), Path(r"C:\x\sp"), o))
        assert line.startswith('"C:\\Program Files\\Python312\\python.exe" -I -S')
        assert '"D:\\HFM Monitor\\config"' in line

    def test_registers_base_interpreter_not_venv_launcher(self):
        exe, site_dir = interpreter_paths()
        assert exe.name.lower().startswith("python") and exe.exists()
        if sys.prefix != sys.base_prefix:                  # running in a venv
            assert Path(sys.base_prefix) in exe.parents
            assert Path(sys.prefix) in site_dir.parents


class TestRunService:
    def test_requested_stop(self, options):
        stop = threading.Event()

        def fake_main(argv, *, stop, out):
            print("PROD: 100.00% GREEN - poll 0.1 s")
            stop.set()
            return 0

        assert run_service(options, stop, main_fn=fake_main) is ServiceOutcome.STOPPED
        text = log_text(options)
        assert "service starting" in text and "PROD: 100.00% GREEN" in text and "service stopped" in text

    def test_start_up_refused_is_not_restarted(self, options):
        def fake_main(argv, *, stop, out):
            print("refusing to start: environments.yaml has placeholders", file=sys.stderr)
            return 2

        assert run_service(options, threading.Event(), main_fn=fake_main) is ServiceOutcome.REFUSED
        text = log_text(options)
        assert "ERROR hfm_dashboard.service: refusing to start" in text and "not restarted" in text

    def test_exception_is_a_crash_with_traceback(self, options):
        def fake_main(argv, *, stop, out):
            raise RuntimeError("boom")

        assert run_service(options, threading.Event(), main_fn=fake_main) is ServiceOutcome.CRASHED
        assert "monitor crashed" in log_text(options) and "RuntimeError: boom" in log_text(options)

    def test_ending_without_a_stop_request_is_a_crash(self, options):
        assert run_service(options, threading.Event(), main_fn=lambda argv, *, stop, out: 0) \
            is ServiceOutcome.CRASHED

    def test_log_retention_from_rules(self, options, monkeypatch):
        seen = {}
        import hfm_dashboard.app.service_host as host
        real = host.configure_logging

        def spy(**kw):
            seen.update(kw)
            return real(**kw)

        monkeypatch.setattr(host, "configure_logging", spy)
        run_service(options, threading.Event(), main_fn=lambda argv, *, stop, out: stop.set() or 0)
        assert seen["keep_days"] == 30 and seen["log_dir"] == options.log_dir and seen["console"] is False

    def test_unreadable_rules_keep_all_logs_and_say_why(self, options):
        (options.config_dir / "rules.yaml").write_text("version: 1\nbogus: true\n", encoding="utf-8")
        run_service(options, threading.Event(), main_fn=lambda argv, *, stop, out: 2)
        assert "rules.yaml problem" in log_text(options)

    def test_handlers_removed_afterwards(self, options):
        before = list(logging.getLogger().handlers)
        run_service(options, threading.Event(), main_fn=lambda argv, *, stop, out: 0)
        assert logging.getLogger().handlers == before

    def test_real_monitor_loop_stops_on_request(self, options):
        options = ServiceOptions(options.config_dir, options.data_dir, options.log_dir, no_web=True)
        stop = threading.Event()
        timer = threading.Timer(3, stop.set)
        timer.start()
        try:
            outcome = run_service(options, stop, main_fn=functools.partial(
                main, collectors_factory=fake_factory, sender_factory=lambda env, rules: None))
        finally:
            timer.cancel()
        assert outcome is ServiceOutcome.STOPPED
        text = log_text(options)
        assert "PROD: 100.00% GREEN" in text
        assert (options.data_dir / "history.sqlite").exists()


class TestSelfCheck:
    def test_ok(self, options, capsys):
        assert self_check(options) == 0
        assert "OK:" in capsys.readouterr().out

    def test_placeholder_left_is_a_problem(self, options, capsys):
        (options.config_dir / "environments.yaml").write_text(
            FILLED.replace("monitor-host.example.test", "{{MONITOR_HOST}}"), encoding="utf-8")
        assert self_check(options) == 2
        assert "PROBLEM" in capsys.readouterr().out

    def test_unwritable_folder_is_a_problem(self, options, capsys):
        options.data_dir.parent.mkdir(exist_ok=True)
        options.data_dir.write_text("a file, not a folder")
        assert self_check(options) == 2
        assert "cannot write to" in capsys.readouterr().out


@pytest.mark.skipif(sys.platform != "win32", reason="Windows service bootstrap")
def test_bootstrap_check_runs_under_the_registered_command_line(options):
    """The exact command the service manager will run (base interpreter, -I -S, boot script),
    in --check mode: proves the venv packages and pywin32 load without the venv launcher."""
    exe, site_dir = interpreter_paths()
    args = service_command(exe, boot_script(), site_dir, options)
    result = subprocess.run([*args[:5], CHECK_FLAG, *args[5:]], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK: interpreter" in result.stdout


def test_self_check_covers_maintenance_file(options, capsys):
    (options.config_dir / "maintenance.yaml").write_text("windows:\n  - environment: UAT\n", encoding="utf-8")
    assert self_check(options) == 2
    assert "maintenance.yaml" in capsys.readouterr().out
