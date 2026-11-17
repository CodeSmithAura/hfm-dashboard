"""Wiring of real adapters, the scheduler configuration and the hfm-dashboard entry point."""

from __future__ import annotations

import io
import threading
from datetime import UTC, datetime

import pytest
import yaml

from hfm_dashboard.adapters.env_secrets import FakeSecretStore
from hfm_dashboard.adapters.http_probe import HttpxUrlProber
from hfm_dashboard.adapters.sql_health import OdbcSqlHealthSource
from hfm_dashboard.adapters.unavailable import UnavailableSqlSource, UnavailableWeblogicSource
from hfm_dashboard.adapters.weblogic_rest import WeblogicRestSource
from hfm_dashboard.adapters.winrm_services import WinRMServiceSource
from hfm_dashboard.app.main import main
from hfm_dashboard.app.poller import Poller
from hfm_dashboard.app.scheduler import build_scheduler
from hfm_dashboard.app.weblogic_collector import collect_weblogic
from hfm_dashboard.app.wiring import build_collectors
from hfm_dashboard.config.environment_loader import parse_environments
from hfm_dashboard.domain.status import Status
from tests.app.test_poller import healthy_collectors
from tests.conftest import FILLED, RULES_PATH, T0

WLS = "hfm-mon/PROD/weblogic"


@pytest.fixture(scope="module")
def prod(repo_rules):
    return parse_environments(FILLED, repo_rules).environments["PROD"]


class TestWiring:
    def test_real_adapters_when_secrets_present(self, prod, repo_rules):
        c = build_collectors(prod, repo_rules, FakeSecretStore({WLS: ("monitor-user", "pw")}))
        assert isinstance(c.services, WinRMServiceSource)
        assert isinstance(c.weblogic, WeblogicRestSource)
        assert isinstance(c.prober, HttpxUrlProber)
        assert isinstance(c.sql, OdbcSqlHealthSource)          # windows auth: no secret needed
        from hfm_dashboard.adapters.winrm_host_health import WinRMHostHealthSource
        assert isinstance(c.host, WinRMHostHealthSource)

    def test_missing_weblogic_secret_gives_unknown_not_crash(self, prod, repo_rules):
        c = build_collectors(prod, repo_rules, FakeSecretStore())
        assert isinstance(c.weblogic, UnavailableWeblogicSource)
        results = collect_weblogic(prod, repo_rules, c.weblogic, T0)
        assert {r.status for r in results} == {Status.UNKNOWN}
        assert "WebLogic credentials not available" in results[0].detail and WLS in results[0].detail

    def test_sql_auth_without_secret(self, prod, repo_rules):
        env = prod.model_copy(update={"sql": prod.sql.model_copy(update={"auth": "sql"})})
        c = build_collectors(env, repo_rules, FakeSecretStore({WLS: ("u", "p")}))
        assert isinstance(c.sql, UnavailableSqlSource) and "SQL Server credentials" in c.sql.reason


class TestScheduler:
    def test_one_non_overlapping_job_per_environment(self, prod, repo_rules):
        pollers = [Poller(n, prod, repo_rules, healthy_collectors(prod, repo_rules)) for n in ("PROD", "B")]
        start = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
        sched = build_scheduler(pollers, 60, lambda s: None, first_run=start)
        jobs = {j.id: j for j in sched.get_jobs()}
        assert set(jobs) == {"poll-PROD", "poll-B"}
        job = jobs["poll-PROD"]
        assert job.max_instances == 1 and job.coalesce is True
        assert job.trigger.interval.total_seconds() == 60


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)          # any default data/ folder lands in the temp dir, never the repo
    (tmp_path / "rules.yaml").write_text(RULES_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "environments.yaml").write_text(FILLED, encoding="utf-8")
    return tmp_path


def fake_factory(name, env, rules):
    return healthy_collectors(env, rules)


class TestMain:
    def test_once_prints_each_environment(self, config_dir):
        out = io.StringIO()
        code = main(["--config-dir", str(config_dir), "--once"], collectors_factory=fake_factory, out=out)
        text = out.getvalue()
        assert code == 0
        assert "PROD: 100.00% GREEN" in text and "NONPROD: 100.00% GREEN" in text
        assert "example.test" not in text

    def test_once_lists_problem_nodes(self, config_dir):
        def factory(name, env, rules):
            c = healthy_collectors(env, rules)
            c.sql.failures[env.nodes["db_node_01"].host] = "SQL Server not reachable"
            return c
        out = io.StringIO()
        main(["--config-dir", str(config_dir), "--once", "--env", "PROD"], collectors_factory=factory, out=out)
        assert "sql_server/db_node_01: UNKNOWN - check could not run: SQL Server not reachable" in out.getvalue()

    def test_config_problem_refuses_to_start(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "rules.yaml").write_text(RULES_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        assert main(["--config-dir", str(tmp_path), "--once"], collectors_factory=fake_factory) == 2

    def test_unknown_environment(self, config_dir):
        assert main(["--config-dir", str(config_dir), "--once", "--env", "UAT"],
                    collectors_factory=fake_factory) == 2

    def test_scheduled_mode_polls_then_stops(self, config_dir):
        out, stop = io.StringIO(), threading.Event()

        class Watch(io.StringIO):
            def flush(self):
                super().flush()
                if "NONPROD:" in self.getvalue() and "PROD:" in self.getvalue():
                    stop.set()

        out = Watch()
        timer = threading.Timer(10, stop.set)     # safety net
        timer.start()
        try:
            code = main(["--config-dir", str(config_dir)], collectors_factory=fake_factory, out=out, stop=stop)
        finally:
            timer.cancel()
        assert code == 0 and "PROD: 100.00% GREEN" in out.getvalue()


def test_library_loggers_quietened():
    import logging

    from hfm_dashboard.app.main import NOISY_LOGGERS, quiet_libraries
    quiet_libraries()
    assert all(logging.getLogger(n).level == logging.WARNING for n in NOISY_LOGGERS)
    assert {"pypsrp", "httpx"} <= set(NOISY_LOGGERS)


class TestHistoryOptions:
    def test_default_history_file_in_data_dir(self, config_dir):
        from hfm_dashboard.adapters.sqlite_history import SqliteHistory
        main(["--config-dir", str(config_dir), "--once", "--env", "PROD", "--data-dir", "hist"],
             collectors_factory=fake_factory, out=io.StringIO())
        main(["--config-dir", str(config_dir), "--once", "--env", "PROD", "--data-dir", "hist"],
             collectors_factory=fake_factory, out=io.StringIO())
        h = SqliteHistory(config_dir / "hist" / "history.sqlite")
        assert len(h.polls("PROD", T0.replace(year=2000))) == 2
        assert len(h.load_node_states("PROD")) == 16
        h.close()

    def test_no_history_writes_nothing(self, config_dir):
        main(["--config-dir", str(config_dir), "--once", "--no-history"], collectors_factory=fake_factory,
             out=io.StringIO())
        assert not (config_dir / "data").exists()

    def test_unopenable_history_refuses_to_start(self, config_dir):
        (config_dir / "blocked").write_text("a file, not a folder")
        assert main(["--config-dir", str(config_dir), "--once", "--data-dir", "blocked"],
                    collectors_factory=fake_factory, out=io.StringIO()) == 2

    def test_purge_job_scheduled(self, prod, repo_rules):
        pollers = [Poller("PROD", prod, repo_rules, healthy_collectors(prod, repo_rules))]
        sched = build_scheduler(pollers, 60, lambda s: None, purge=lambda: None)
        job = sched.get_job("purge-history")
        assert job is not None and job.trigger.interval.total_seconds() == 6 * 3600
        assert build_scheduler(pollers, 60, lambda s: None).get_job("purge-history") is None


def test_purge_uses_history_retention_days(config_dir):
    from hfm_dashboard.adapters.fake_history import FakeHistory

    cutoffs, stop = [], threading.Event()

    class Recording(FakeHistory):
        def purge_before(self, cutoff):
            cutoffs.append(cutoff)
            stop.set()
            return super().purge_before(cutoff)

    timer = threading.Timer(10, stop.set)
    timer.start()
    try:
        main(["--config-dir", str(config_dir), "--env", "PROD"], collectors_factory=fake_factory,
             out=io.StringIO(), stop=stop, history=Recording())
    finally:
        timer.cancel()
    assert cutoffs, "purge job did not run at start-up"
    age = datetime.now(UTC) - cutoffs[0]
    assert abs(age.total_seconds() - 90 * 86400) < 60        # rules.yaml history_retention_days: 90
