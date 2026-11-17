"""Status history in SQLite on the monitoring server (default store, CLAUDE.md).

One file, WAL journal, one connection guarded by a lock (the scheduler polls environments from
several threads). Times are stored as ISO 8601 UTC text and read back as aware UTC datetimes.
The schema version is kept in ``PRAGMA user_version``; an unknown newer version is refused.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hfm_dashboard.domain.snapshot import NodeKey, Transition
from hfm_dashboard.domain.status import NodeState, Status
from hfm_dashboard.ports.history import HistoryError, PollRecord

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS polls (
    environment      TEXT NOT NULL,
    observed_at      TEXT NOT NULL,
    overall_pct      REAL,
    uncapped_pct     REAL,
    band             TEXT NOT NULL,
    capped_by        TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    collector_errors TEXT NOT NULL,
    PRIMARY KEY (environment, observed_at)
);
CREATE TABLE IF NOT EXISTS node_states (
    environment          TEXT NOT NULL,
    component            TEXT NOT NULL,
    node                 TEXT NOT NULL,
    status               TEXT NOT NULL,
    consecutive_failures INTEGER NOT NULL,
    since                TEXT,
    last_raw             TEXT NOT NULL,
    detail               TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    PRIMARY KEY (environment, component, node)
);
CREATE TABLE IF NOT EXISTS transitions (
    environment TEXT NOT NULL,
    component   TEXT NOT NULL,
    node        TEXT NOT NULL,
    before      TEXT,
    after       TEXT NOT NULL,
    at          TEXT NOT NULL,
    detail      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS transitions_by_time ON transitions (environment, at);
"""


def _ts(value: datetime) -> str:
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamps must be timezone-aware UTC")
    return value.astimezone(UTC).isoformat()


def _dt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value).astimezone(UTC)


class SqliteHistory:
    def __init__(self, path: str | Path) -> None:
        self._lock = threading.Lock()
        try:
            if str(path) != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise HistoryError(f"history database schema {version} is newer than this program ({SCHEMA_VERSION})")
            self._db.executescript(_SCHEMA)
            self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        except (sqlite3.Error, OSError) as exc:
            raise HistoryError(f"cannot open history database ({type(exc).__name__})") from exc

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------------ writes

    def save_poll(self, record: PollRecord, states: dict[NodeKey, NodeState],
                  changes: list[Transition]) -> None:
        env, at = record.environment, _ts(record.observed_at)
        try:
            with self._lock:
                self._db.execute("BEGIN")
                try:
                    self._db.execute(
                        "INSERT OR REPLACE INTO polls VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (env, at, record.overall_pct, record.uncapped_pct, record.band,
                         ",".join(record.capped_by), record.duration_seconds,
                         ",".join(record.collector_errors)))
                    self._db.executemany(
                        "INSERT OR REPLACE INTO node_states VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [(env, c, n, s.status.value, s.consecutive_failures,
                          _ts(s.since) if s.since else None, s.last_raw.value, s.detail, at)
                         for (c, n), s in states.items()])
                    self._db.executemany(
                        "INSERT INTO transitions VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(env, t.component, t.node, t.before.value if t.before else None,
                          t.after.value, _ts(t.at), t.detail) for t in changes])
                    self._db.execute("COMMIT")
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise
        except sqlite3.Error as exc:
            raise HistoryError(f"cannot write history ({type(exc).__name__})") from exc

    def purge_before(self, cutoff: datetime) -> int:
        limit = _ts(cutoff)
        try:
            with self._lock:
                a = self._db.execute("DELETE FROM polls WHERE observed_at < ?", (limit,)).rowcount
                b = self._db.execute("DELETE FROM transitions WHERE at < ?", (limit,)).rowcount
            return a + b
        except sqlite3.Error as exc:
            raise HistoryError(f"cannot purge history ({type(exc).__name__})") from exc

    # ------------------------------------------------------------------ reads

    def _query(self, sql: str, params: tuple) -> list[tuple]:
        try:
            with self._lock:
                return self._db.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise HistoryError(f"cannot read history ({type(exc).__name__})") from exc

    def load_node_states(self, environment: str) -> dict[NodeKey, NodeState]:
        rows = self._query(
            "SELECT component, node, status, consecutive_failures, since, last_raw, detail "
            "FROM node_states WHERE environment = ?", (environment,))
        return {(c, n): NodeState(component=c, node=n, status=Status(s), consecutive_failures=f,
                                  since=_dt(since), last_raw=Status(raw), detail=d)
                for c, n, s, f, since, raw, d in rows}

    def transitions(self, environment: str, since: datetime) -> list[Transition]:
        rows = self._query(
            "SELECT component, node, before, after, at, detail FROM transitions "
            "WHERE environment = ? AND at >= ? ORDER BY at, component, node", (environment, _ts(since)))
        return [Transition(c, n, Status(b) if b else None, Status(a), _dt(at), d)  # type: ignore[arg-type]
                for c, n, b, a, at, d in rows]

    def polls(self, environment: str, since: datetime) -> list[PollRecord]:
        rows = self._query(
            "SELECT observed_at, overall_pct, uncapped_pct, band, capped_by, duration_seconds, "
            "collector_errors FROM polls WHERE environment = ? AND observed_at >= ? ORDER BY observed_at",
            (environment, _ts(since)))
        return [PollRecord(environment, _dt(at), o, u, b, tuple(filter(None, cap.split(","))), d,  # type: ignore[arg-type]
                           tuple(filter(None, err.split(","))))
                for at, o, u, b, cap, d, err in rows]
