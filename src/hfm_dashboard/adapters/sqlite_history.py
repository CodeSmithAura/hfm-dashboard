"""Status history in SQLite on the monitoring server (default store, CLAUDE.md).

One file, WAL journal, one connection guarded by a lock (the scheduler polls environments from
several threads). Times are stored as ISO 8601 UTC text and read back as aware UTC datetimes.
The schema version is kept in ``PRAGMA user_version``; an unknown newer version is refused.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hfm_dashboard.domain.notices import ComponentNoticeState, Notice, NoticeKind
from hfm_dashboard.domain.snapshot import NodeKey, Transition
from hfm_dashboard.domain.status import NodeState, Status
from hfm_dashboard.ports.history import HistoryError, PollRecord

SCHEMA_VERSION = 2   # 2 adds notice_states and notices (created automatically on a version-1 file)

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
CREATE TABLE IF NOT EXISTS notice_states (
    environment     TEXT NOT NULL,
    component       TEXT NOT NULL,
    failing_since   TEXT,
    clock_start     TEXT,
    first_sent_at   TEXT,
    last_sent_at    TEXT,
    reminders_sent  INTEGER NOT NULL,
    escalated       INTEGER NOT NULL,
    grouped_under   TEXT,
    unknown_since   TEXT,
    unknown_warned  INTEGER NOT NULL,
    PRIMARY KEY (environment, component)
);
CREATE TABLE IF NOT EXISTS notices (
    environment    TEXT NOT NULL,
    component      TEXT NOT NULL,
    kind           TEXT NOT NULL,
    at             TEXT NOT NULL,
    since          TEXT,
    display_name   TEXT NOT NULL,
    affected_nodes TEXT NOT NULL,
    total_nodes    INTEGER NOT NULL,
    full_outage    INTEGER NOT NULL,
    escalate       INTEGER NOT NULL,
    close_period   INTEGER NOT NULL,
    downstream     TEXT NOT NULL,
    details        TEXT NOT NULL,
    delivered      INTEGER NOT NULL,
    note           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notices_by_time ON notices (environment, at);
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
                c = self._db.execute("DELETE FROM notices WHERE at < ?", (limit,)).rowcount
            return a + b + c
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

    # ------------------------------------------------------------------ notices

    def save_notice_states(self, environment: str, states: dict[str, ComponentNoticeState]) -> None:
        def ts(v: datetime | None) -> str | None:
            return _ts(v) if v else None
        rows = [(environment, cid, ts(s.failing_since), ts(s.clock_start), ts(s.first_sent_at),
                 ts(s.last_sent_at), s.reminders_sent, int(s.escalated), s.grouped_under,
                 ts(s.unknown_since), int(s.unknown_warned)) for cid, s in states.items()]
        try:
            with self._lock:
                self._db.execute("BEGIN")
                try:
                    self._db.execute("DELETE FROM notice_states WHERE environment = ?", (environment,))
                    self._db.executemany(
                        "INSERT INTO notice_states VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
                    self._db.execute("COMMIT")
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise
        except sqlite3.Error as exc:
            raise HistoryError(f"cannot write notice states ({type(exc).__name__})") from exc

    def load_notice_states(self, environment: str) -> dict[str, ComponentNoticeState]:
        rows = self._query(
            "SELECT component, failing_since, clock_start, first_sent_at, last_sent_at, reminders_sent, "
            "escalated, grouped_under, unknown_since, unknown_warned FROM notice_states WHERE environment = ?",
            (environment,))
        return {c: ComponentNoticeState(_dt(fs), _dt(cs), _dt(first), _dt(last), r, bool(esc), grp,
                                        _dt(us), bool(uw))
                for c, fs, cs, first, last, r, esc, grp, us, uw in rows}

    def record_notices(self, notices: list[Notice], delivered: bool, note: str = "") -> None:
        rows = [(n.environment, n.component, n.kind.value, _ts(n.at), _ts(n.since) if n.since else None,
                 n.display_name, json.dumps(list(n.affected_nodes)), n.total_nodes, int(n.full_outage),
                 int(n.escalate), int(n.close_period), json.dumps(list(n.downstream)),
                 json.dumps(list(n.details)), int(delivered), note) for n in notices]
        try:
            with self._lock:
                self._db.executemany(
                    "INSERT INTO notices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        except sqlite3.Error as exc:
            raise HistoryError(f"cannot write notices ({type(exc).__name__})") from exc

    def notices(self, environment: str, since: datetime) -> list[tuple[Notice, bool, str]]:
        rows = self._query(
            "SELECT environment, component, kind, at, since, display_name, affected_nodes, total_nodes, "
            "full_outage, escalate, close_period, downstream, details, delivered, note FROM notices "
            "WHERE environment = ? AND at >= ? ORDER BY at, component", (environment, _ts(since)))
        return [(Notice(NoticeKind(k), e, c, dn, _dt(at), _dt(s), tuple(json.loads(aff)), tot,  # type: ignore[arg-type]
                        bool(full), bool(esc), bool(close), tuple(json.loads(ds)), tuple(json.loads(det))),
                 bool(dlv), note)
                for e, c, k, at, s, dn, aff, tot, full, esc, close, ds, det, dlv, note in rows]
