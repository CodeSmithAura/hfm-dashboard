"""SQL Server health over ODBC (pyodbc, ODBC Driver 18). Read-only: every statement is a SELECT.

Needs VIEW SERVER STATE and read access to msdb (A10). Each metric is its own query; a query that
fails (for example a missing permission) leaves only that metric Unknown. Ages are computed on the
server (``DATEDIFF`` against ``GETDATE()``), so the server's local time zone never matters.
The connection string is built in one place and never logged or put into an error message.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from hfm_dashboard.domain.environment import Sql
from hfm_dashboard.domain.evaluation import SqlServerObservation
from hfm_dashboard.ports.secrets import Secret
from hfm_dashboard.ports.sql_health import SqlQueryError

DRIVER = "ODBC Driver 18 for SQL Server"

# Each query starts with a "-- q:<name>" marker so tests can answer it. All are plain SELECTs.
QUERIES: dict[str, str] = {
    "blocking": """-- q:blocking
SELECT ISNULL(MAX(r.wait_time), 0) / 60000.0
FROM sys.dm_exec_requests AS r
WHERE r.blocking_session_id <> 0""",

    "deadlocks": """-- q:deadlocks
SELECT COUNT(*)
FROM sys.fn_xe_file_target_read_file('system_health*.xel', NULL, NULL, NULL)
WHERE object_name = 'xml_deadlock_report'
  AND timestamp_utc > DATEADD(MINUTE, -15, SYSUTCDATETIME())""",

    "log_used": """-- q:log_used
SELECT RTRIM(instance_name), cntr_value
FROM sys.dm_os_performance_counters
WHERE counter_name = 'Percent Log Used' AND object_name LIKE '%:Databases%'""",

    "tempdb": """-- q:tempdb
SELECT SUM(total_page_count), SUM(unallocated_extent_page_count)
FROM tempdb.sys.dm_db_file_space_usage""",

    "disk": """-- q:disk
SELECT DISTINCT vs.volume_mount_point, vs.total_bytes, vs.available_bytes
FROM sys.master_files AS mf
CROSS APPLY sys.dm_os_volume_stats(mf.database_id, mf.file_id) AS vs""",

    "backups": """-- q:backups
SELECT d.name, DATEDIFF(SECOND, MAX(b.backup_finish_date), GETDATE()) / 3600.0
FROM sys.databases AS d
LEFT JOIN msdb.dbo.backupset AS b ON b.database_name = d.name AND b.type IN ('D', 'I')
GROUP BY d.name""",

    "agent_failures": """-- q:agent_failures
SELECT DATEDIFF(SECOND, t.run_at, GETDATE()) / 3600.0
FROM (
    SELECT DATETIMEFROMPARTS(h.run_date / 10000, h.run_date / 100 % 100, h.run_date % 100,
                             h.run_time / 10000, h.run_time / 100 % 100, h.run_time % 100, 0) AS run_at
    FROM msdb.dbo.sysjobhistory AS h
    WHERE h.step_id = 0 AND h.run_status = 0
) AS t
WHERE t.run_at > DATEADD(HOUR, -?, GETDATE())""",

    "longest_query": """-- q:longest_query
SELECT ISNULL(MAX(DATEDIFF(SECOND, r.start_time, GETDATE())), 0) / 60.0
FROM sys.dm_exec_requests AS r
JOIN sys.dm_exec_sessions AS s ON s.session_id = r.session_id
WHERE s.is_user_process = 1 AND r.session_id <> @@SPID
  AND r.wait_type NOT IN ('WAITFOR', 'BROKER_RECEIVE_WAITFOR')""",
}


def connection_string(host: str, port: int, settings: Sql, timeout_seconds: int,
                      credential: Secret | None) -> str:
    parts = [
        f"Driver={{{DRIVER}}}",
        f"Server=tcp:{host},{port}",
        "Database=master",
        f"Encrypt={'yes' if settings.encrypt else 'no'}",
        f"TrustServerCertificate={'yes' if settings.trust_server_certificate else 'no'}",
        f"Connection Timeout={timeout_seconds}",
        "ApplicationIntent=ReadOnly",
        "APP=hfm-availability-monitor",
    ]
    if credential is None:
        parts.append("Trusted_Connection=yes")
    else:
        parts += [f"UID={credential.username}", "PWD={" + credential.password.replace("}", "}}") + "}"]
    return ";".join(parts) + ";"


def _default_connect(conn_str: str, timeout: int):
    import pyodbc  # imported lazily so tests do not need an ODBC driver

    return pyodbc.connect(conn_str, timeout=timeout, autocommit=True, readonly=True)


class OdbcSqlHealthSource:
    def __init__(
        self,
        settings: Sql,
        port: int,
        databases: Sequence[str],
        timeout_seconds: int,
        agent_lookback_hours: float,
        credential: Secret | None = None,
        connect: Callable[[str, int], Any] = _default_connect,
    ) -> None:
        if (settings.auth == "sql") != (credential is not None):
            raise ValueError("auth 'sql' needs a credential; auth 'windows' must not have one")
        self._settings = settings
        self._port = port
        self._databases = list(databases)
        self._timeout = timeout_seconds
        self._lookback = agent_lookback_hours
        self._credential = credential
        self._connect = connect

    def fetch(self, host: str) -> SqlServerObservation:
        conn_str = connection_string(host, self._port, self._settings, self._timeout, self._credential)
        try:
            conn = self._connect(conn_str, self._timeout)
        except Exception as exc:  # noqa: BLE001 - any connect failure means "cannot check"
            raise SqlQueryError(_describe(exc)) from exc
        try:
            conn.timeout = self._timeout   # per-query timeout
            rows = {name: self._run(conn, name) for name in QUERIES}
        finally:
            conn.close()
        return build_observation(rows, self._databases)

    def _run(self, conn, name: str) -> list[tuple] | None:
        params = (int(self._lookback),) if name == "agent_failures" else ()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(QUERIES[name], *params)
                return [tuple(r) for r in cursor.fetchall()]
            finally:
                cursor.close()
        except Exception:  # noqa: BLE001 - one failed metric must not hide the others
            return None


def _describe(exc: Exception) -> str:
    """Fixed, secret-free reason from the ODBC SQLSTATE (exception text can echo the login)."""
    state = exc.args[0] if exc.args and isinstance(exc.args[0], str) else ""
    if state == "28000":
        return "SQL Server login failed"
    if state in {"HYT00", "HYT01"}:
        return "SQL Server connection timed out"
    if state == "IM002":
        return f"ODBC driver not installed ({DRIVER})"
    if state.startswith("08"):
        return "SQL Server not reachable"
    return f"SQL Server connection failed ({type(exc).__name__})"


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _scalar(rows: list[tuple] | None) -> float | None:
    if not rows or not rows[0]:
        return None
    return _num(rows[0][0])


def build_observation(rows: dict[str, list[tuple] | None], databases: Sequence[str]) -> SqlServerObservation:
    """Turn raw query rows into an observation. Pure; ``None`` rows mean the query failed."""
    wanted = {d.upper(): d for d in databases}

    log_used = None
    if rows.get("log_used") is not None:
        values = [_num(v) for name, v in rows["log_used"] if str(name).upper() in wanted]
        values = [v for v in values if v is not None]
        log_used = max(values) if values else None

    tempdb = None
    if rows.get("tempdb"):
        total, free = (_num(x) for x in rows["tempdb"][0][:2])
        if total and free is not None:
            tempdb = round(100.0 * (total - free) / total, 1)

    disk = None
    if rows.get("disk") is not None:
        disk = {}
        for mount, total, available in rows["disk"]:
            total, available = _num(total), _num(available)
            if total:
                label = str(mount).rstrip("\\/") or str(mount)
                disk[label] = round(100.0 * available / total, 1)

    backups = None
    if rows.get("backups") is not None:
        found = {str(name).upper(): _num(age) for name, age in rows["backups"]}
        backups = {db: found.get(key) for key, db in wanted.items() if key in found}

    agent = None
    if rows.get("agent_failures") is not None:
        ages = (_num(row[0]) for row in rows["agent_failures"] if row)
        agent = tuple(age for age in ages if age is not None)

    deadlocks = _scalar(rows.get("deadlocks"))
    return SqlServerObservation(
        service_running=True,   # connected; a stopped service is caught by the windows_service check
        longest_blocking_minutes=_scalar(rows.get("blocking")),
        deadlocks_last_15_min=int(deadlocks) if deadlocks is not None else None,
        max_log_used_pct=log_used,
        tempdb_used_pct=tempdb,
        disk_free_pct=disk,
        hours_since_last_backup=backups,
        agent_job_failures_hours_ago=agent,
        longest_query_minutes=_scalar(rows.get("longest_query")),
    )
