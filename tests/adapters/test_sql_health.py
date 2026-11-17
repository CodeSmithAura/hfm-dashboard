"""SQL Server health adapter with a fake ODBC connection (no driver, no server)."""

from __future__ import annotations

import json
import re

import pytest

from hfm_dashboard.adapters.sql_health import (
    QUERIES,
    OdbcSqlHealthSource,
    build_observation,
    connection_string,
)
from hfm_dashboard.domain.environment import Sql
from hfm_dashboard.domain.evaluation import evaluate
from hfm_dashboard.domain.status import Status
from hfm_dashboard.ports.secrets import Secret
from hfm_dashboard.ports.sql_health import SqlQueryError
from tests.conftest import FIXTURES

U, G, D, X = Status.UP, Status.DEGRADED, Status.DOWN, Status.UNKNOWN
DBS = ["HSS", "HFM", "EPMRCU"]
WINDOWS = Sql(port="sql", auth="windows")
SQLAUTH = Sql(port="sql", auth="sql")


def sample(name: str) -> dict:
    return json.loads((FIXTURES / "sql" / name).read_text(encoding="utf-8"))


class FakeCursor:
    def __init__(self, conn):
        self.conn, self.rows = conn, []

    def execute(self, sql, *params):
        name = re.match(r"-- q:(\w+)", sql).group(1)
        self.conn.executed.append((name, params))
        answer = self.conn.answers.get(name)
        if answer is None:
            raise RuntimeError("42000 permission denied")
        self.rows = [tuple(r) for r in answer]

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class FakeConnection:
    def __init__(self, answers):
        self.answers, self.executed, self.closed, self.timeout = answers, [], False, 0

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


class Connector:
    def __init__(self, answers=None, raises=None):
        self.conn = FakeConnection(answers or {})
        self.raises = raises
        self.calls: list[tuple[str, int]] = []

    def __call__(self, conn_str, timeout):
        self.calls.append((conn_str, timeout))
        if self.raises:
            raise self.raises
        return self.conn


def source(connector, settings=WINDOWS, credential=None) -> OdbcSqlHealthSource:
    return OdbcSqlHealthSource(settings, 8443, DBS, timeout_seconds=20, agent_lookback_hours=24,
                               credential=credential, connect=connector)


# --------------------------------------------------------------------------- queries

class TestQueries:
    FORBIDDEN = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|"
                           r"DBCC|GRANT|REVOKE|BACKUP|RESTORE|KILL|SHUTDOWN|xp_\w+|sp_\w+)\b", re.I)

    @pytest.mark.parametrize("name", sorted(QUERIES))
    def test_read_only_select(self, name):
        sql = QUERIES[name]
        body = sql.split("\n", 1)[1]
        assert body.lstrip().upper().startswith("SELECT")
        assert not self.FORBIDDEN.search(body.replace("backupset", "").replace("backup_finish_date", ""))
        assert sql.startswith(f"-- q:{name}\n")

    def test_every_metric_has_a_query(self):
        assert set(QUERIES) == {"blocking", "deadlocks", "log_used", "tempdb", "disk", "backups",
                                "agent_failures", "longest_query"}


# --------------------------------------------------------------------------- connection

class TestConnection:
    def test_windows_auth(self):
        cs = connection_string("db-node-01.example.test", 8443, WINDOWS, 20, None)
        assert "Driver={ODBC Driver 18 for SQL Server}" in cs
        assert "Server=tcp:db-node-01.example.test,8443" in cs
        assert "Trusted_Connection=yes" in cs and "UID=" not in cs and "PWD=" not in cs
        assert "Encrypt=yes" in cs and "TrustServerCertificate=no" in cs
        assert "ApplicationIntent=ReadOnly" in cs and "Connection Timeout=20" in cs

    def test_sql_auth_password_braced(self):
        cs = connection_string("h", 1234, SQLAUTH, 20, Secret("monitor-user", "p;w}d"))
        assert "UID=monitor-user" in cs and "PWD={p;w}}d}" in cs and "Trusted_Connection" not in cs

    def test_auth_and_credential_must_match(self):
        with pytest.raises(ValueError):
            source(Connector(), settings=SQLAUTH)
        with pytest.raises(ValueError):
            source(Connector(), settings=WINDOWS, credential=Secret("u", "p"))

    @pytest.mark.parametrize(("state", "expected"), [
        ("28000", "login failed"), ("08001", "not reachable"), ("HYT00", "timed out"),
        ("IM002", "ODBC driver not installed"), ("42000", "connection failed"),
    ])
    def test_connect_errors_fixed_text(self, state, expected):
        exc = RuntimeError(state, "[Microsoft] Login failed for user 'monitor-user' on db-node-01")
        cred = Secret("monitor-user", "pw-not-real")
        with pytest.raises(SqlQueryError) as info:
            source(Connector(raises=exc), settings=SQLAUTH, credential=cred).fetch("db-node-01.example.test")
        msg = str(info.value)
        assert expected in msg
        assert "monitor-user" not in msg and "pw-not-real" not in msg and "db-node-01" not in msg

    def test_runs_every_query_with_timeout_and_closes(self):
        conn = Connector(sample("healthy.json"))
        source(conn).fetch("db-node-01.example.test")
        assert [n for n, _ in conn.conn.executed] == list(QUERIES)
        assert dict(conn.conn.executed)["agent_failures"] == (24,)
        assert conn.conn.timeout == 20 and conn.conn.closed
        assert conn.calls[0][1] == 20

    def test_failed_query_only_blanks_that_metric(self):
        answers = sample("healthy.json")
        answers["deadlocks"] = None
        obs = source(Connector(answers)).fetch("h")
        assert obs.deadlocks_last_15_min is None
        assert obs.longest_blocking_minutes == 0.0 and obs.disk_free_pct is not None


# --------------------------------------------------------------------------- observation

class TestBuildObservation:
    def test_healthy(self, repo_rules):
        obs = build_observation(sample("healthy.json"), DBS)
        assert obs.max_log_used_pct == 35          # only EPM databases count, not master/_Total
        assert obs.tempdb_used_pct == 12.5
        assert obs.disk_free_pct == {"C:": 40.0, "D:": 50.0}
        assert obs.hours_since_last_backup == {"HSS": 6.5, "HFM": 6.4, "EPMRCU": 6.6}
        assert obs.agent_job_failures_hours_ago == ()
        assert evaluate("sql_server", obs, repo_rules.checks).status is U

    def test_production_disks_degraded(self, repo_rules):
        """Q52: C: 8.6% free and data drive 11.6% -> Degraded on the C: drive only."""
        obs = build_observation(sample("production_disks.json"), DBS)
        assert obs.disk_free_pct == {"C:": 8.6, "D:": 11.6}
        ev = evaluate("sql_server", obs, repo_rules.checks)
        assert ev.status is G and ev.detail == "C: 8.6% free < 10%"

    def test_stressed(self, repo_rules):
        obs = build_observation(sample("stressed.json"), DBS)
        ev = evaluate("sql_server", obs, repo_rules.checks)
        assert ev.status is G
        for part in ("blocking 12.5 min", "deadlocks in 15 min 7", "log used 96%", "tempdb used 95",
                     "HFM last backup 52 h", "EPMRCU never backed up", "1 failed Agent job",
                     "longest query 95 min", "D: 5% free"):
            assert part in ev.detail, part

    def test_missing_permission_is_unknown(self, repo_rules):
        obs = build_observation(sample("no_view_server_state.json"), DBS)
        assert obs.longest_blocking_minutes is None and obs.disk_free_pct is None
        assert obs.hours_since_last_backup is not None
        ev = evaluate("sql_server", obs, repo_rules.checks)
        assert ev.status is X and "not read" in ev.detail

    def test_database_missing_from_backup_list_is_left_out(self):
        rows = sample("healthy.json")
        rows["backups"] = [["HSS", 1.0]]
        assert build_observation(rows, DBS).hours_since_last_backup == {"HSS": 1.0}

    def test_database_names_case_insensitive(self):
        rows = sample("healthy.json")
        rows["log_used"] = [["hfm", 91]]
        assert build_observation(rows, DBS).max_log_used_pct == 91
