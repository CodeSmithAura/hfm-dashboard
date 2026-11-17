"""SQLite history store (real SQLite, temp files)."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest

from hfm_dashboard.adapters.sqlite_history import SCHEMA_VERSION, SqliteHistory
from hfm_dashboard.domain.snapshot import Transition
from hfm_dashboard.domain.status import NodeState, Status
from hfm_dashboard.ports.history import HistoryError, PollRecord
from tests.conftest import T0

U, D, X = Status.UP, Status.DOWN, Status.UNKNOWN
DAY = timedelta(days=1)


def record(at, env="PROD", pct=100.0, capped=(), errors=()):
    return PollRecord(env, at, pct, pct, "green" if pct == 100.0 else "red", capped, 1.2, errors)


def state(c, n, status=U, since=T0, failures=0, detail=""):
    return NodeState(c, n, status, failures, since, status, detail)


@pytest.fixture
def db(tmp_path):
    h = SqliteHistory(tmp_path / "sub" / "history.sqlite")
    yield h
    h.close()


class TestRoundTrip:
    def test_states_restored_with_utc_times(self, db):
        states = {("epmhfm", "hfm_node_02"): state("epmhfm", "hfm_node_02", D, T0, 3, "service Stopped"),
                  ("sql_server", "db_node_01"): state("sql_server", "db_node_01", X, None)}
        db.save_poll(record(T0), states, [])
        back = db.load_node_states("PROD")
        assert back == states
        assert back["epmhfm", "hfm_node_02"].since.utcoffset() == timedelta(0)
        assert db.load_node_states("NONPROD") == {}

    def test_current_state_is_upserted(self, db):
        key = ("epmhfm", "hfm_node_01")
        db.save_poll(record(T0), {key: state(*key, U)}, [])
        db.save_poll(record(T0 + DAY), {key: state(*key, D, T0 + DAY)}, [])
        assert db.load_node_states("PROD")[key].status is D

    def test_transitions_and_polls(self, db):
        t1 = Transition("epmhfm", "hfm_node_02", None, U, T0)
        t2 = Transition("epmhfm", "hfm_node_02", U, D, T0 + DAY, "service Stopped")
        db.save_poll(record(T0), {}, [t1])
        db.save_poll(record(T0 + DAY, pct=0.0, capped=("sql_server",), errors=("weblogic",)), {}, [t2])
        assert db.transitions("PROD", T0) == [t1, t2]
        assert db.transitions("PROD", T0 + DAY) == [t2]
        polls = db.polls("PROD", T0)
        assert [p.observed_at for p in polls] == [T0, T0 + DAY]
        assert polls[1].capped_by == ("sql_server",) and polls[1].collector_errors == ("weblogic",)
        assert polls[0].capped_by == () and polls[0].overall_pct == 100.0

    def test_survives_reopen(self, tmp_path):
        path = tmp_path / "history.sqlite"
        h = SqliteHistory(path)
        h.save_poll(record(T0), {("a", "n"): state("a", "n")}, [])
        h.close()
        h2 = SqliteHistory(path)
        assert ("a", "n") in h2.load_node_states("PROD")
        h2.close()


class TestRetention:
    def test_purge_old_rows_keeps_current_states(self, db):
        key = ("epmhfm", "hfm_node_01")
        db.save_poll(record(T0), {key: state(*key)}, [Transition(*key, None, U, T0)])
        db.save_poll(record(T0 + 100 * DAY), {}, [])
        assert db.purge_before(T0 + 90 * DAY) == 2
        assert [p.observed_at for p in db.polls("PROD", T0)] == [T0 + 100 * DAY]
        assert db.transitions("PROD", T0) == []
        assert key in db.load_node_states("PROD")


class TestSafety:
    def test_naive_time_rejected(self, db):
        with pytest.raises(ValueError, match="UTC"):
            db.save_poll(record(T0.replace(tzinfo=None)), {}, [])

    def test_failed_write_is_atomic(self, db):
        key = ("a", "n")
        bad = Transition("a", "n", None, U, T0.replace(tzinfo=None))     # fails mid-transaction
        with pytest.raises(ValueError):
            db.save_poll(record(T0), {key: state(*key)}, [bad])
        assert db.polls("PROD", T0 - DAY) == [] and db.load_node_states("PROD") == {}

    def test_newer_schema_refused(self, tmp_path):
        path = tmp_path / "history.sqlite"
        con = sqlite3.connect(path)
        con.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
        con.close()
        with pytest.raises(HistoryError, match="newer"):
            SqliteHistory(path)

    def test_write_after_close_is_history_error(self, tmp_path):
        h = SqliteHistory(tmp_path / "h.sqlite")
        h.close()
        with pytest.raises(HistoryError):
            h.save_poll(record(T0), {}, [])

    def test_concurrent_writers(self, db):
        def write(env):
            for i in range(25):
                db.save_poll(record(T0 + i * timedelta(minutes=1), env=env),
                             {("c", "n"): state("c", "n")}, [])
        threads = [threading.Thread(target=write, args=(e,)) for e in ("PROD", "NONPROD")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(db.polls("PROD", T0)) == 25 and len(db.polls("NONPROD", T0)) == 25
