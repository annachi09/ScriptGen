"""
Tests for app/db/mssql.py's connection-reuse machinery
(reuse_connection / the thread-local reused-connection path inside
run_query and get_table_columns) - added 2026-09-13 when RJ reported the
DIFF DATES Anomaly Batch page as slow. Root cause: run_query opened and
tore down a brand-new pytds connection on EVERY call, and Batch makes
~6 calls per NISS; over a local tunnel the connection handshake, not the
query, dominates. reuse_connection() lets web/batch_jobs.py open one
connection for a whole run instead.

pytds.connect itself is monkeypatched here (never touches a real
network) - these tests only verify the connection-count/reuse/retry
bookkeeping, not real SQL Server behavior. No existing test needed to
change: every other test in this repo monkeypatches mssql.run_query /
mssql.get_table_columns wholesale, so none of this internal logic runs
under those tests at all.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytds

from app.config import ConnectionConfig
from app.db import mssql


class _FakeCursor:
    def __init__(self, conn, responses):
        self._conn = conn
        self._responses = responses
        self.description = None
        self._rows = []

    def execute(self, sql):
        if self._conn.dead:
            raise pytds.Error("connection is dead")
        if sql == "SELECT 1":
            self.description = [("1",)]
            self._rows = [[1]]
            return
        columns, rows = self._responses.get(sql, (["X"], [[1]]))
        self.description = [(c,) for c in columns]
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, responses, dead: bool = False):
        self._responses = responses
        self.dead = dead
        self.closed = False

    def cursor(self):
        return _FakeCursor(self, self._responses)

    def close(self):
        self.closed = True


def _make_conn_cfg() -> ConnectionConfig:
    return ConnectionConfig(name="Test", server="localhost", port=1433, username="test")


@pytest.fixture(autouse=True)
def _clear_thread_local():
    # Belt-and-suspenders: make sure no test leaks a reused connection
    # into the next one via the module-level thread-local.
    mssql._thread_local.conn = None
    yield
    mssql._thread_local.conn = None


def test_run_query_without_reuse_opens_and_closes_its_own_connection(monkeypatch):
    opened = []

    def fake_connect(**kwargs):
        conn = _FakeConnection({"SELECT A": (["A"], [[1]])})
        opened.append(conn)
        return conn

    monkeypatch.setattr(mssql.pytds, "connect", fake_connect)
    result = mssql.run_query(_make_conn_cfg(), "SELECT A")
    assert result.columns == ["A"]
    assert len(opened) == 1
    assert opened[0].closed is True


def test_reuse_connection_opens_exactly_one_connection_for_many_queries(monkeypatch):
    opened = []

    def fake_connect(**kwargs):
        conn = _FakeConnection({"SELECT A": (["A"], [[1]]), "SELECT B": (["B"], [[2]])})
        opened.append(conn)
        return conn

    monkeypatch.setattr(mssql.pytds, "connect", fake_connect)
    conn_cfg = _make_conn_cfg()
    with mssql.reuse_connection(conn_cfg):
        r1 = mssql.run_query(conn_cfg, "SELECT A")
        r2 = mssql.run_query(conn_cfg, "SELECT B")
        r3 = mssql.run_query(conn_cfg, "SELECT A")

    assert len(opened) == 1  # one connection for all three queries
    assert [r1.columns, r2.columns, r3.columns] == [["A"], ["B"], ["A"]]
    assert opened[0].closed is True  # closed when the `with` block exits


def test_one_shot_connection_closes_even_if_the_query_raises(monkeypatch):
    """Not a reused connection at all - confirms run_query's plain
    per-call connect/close path still closes on error, unchanged from
    before this round's reuse_connection addition."""
    opened = []

    class _AlwaysBadCursor(_FakeCursor):
        def execute(self, sql):
            raise pytds.Error("syntax error near SELECT")

    def fake_connect(**kwargs):
        conn = _FakeConnection({})
        opened.append(conn)
        return conn

    monkeypatch.setattr(mssql.pytds, "connect", fake_connect)
    monkeypatch.setattr(_FakeConnection, "cursor", lambda self: _AlwaysBadCursor(self, {}))
    with pytest.raises(mssql.ConnectionError_):
        mssql.run_query(_make_conn_cfg(), "SELECT A")
    assert opened[0].closed is True


def test_reused_connection_that_died_is_transparently_reopened_and_retried(monkeypatch):
    """The core self-healing behavior: a reused connection that's gone
    dead (tunnel blip) gets reopened once and the failing query is
    retried on the fresh connection - the caller never sees an error."""
    opened = []

    def fake_connect(**kwargs):
        conn = _FakeConnection({"SELECT A": (["A"], [[1]])})
        opened.append(conn)
        return conn

    monkeypatch.setattr(mssql.pytds, "connect", fake_connect)
    conn_cfg = _make_conn_cfg()
    with mssql.reuse_connection(conn_cfg):
        opened[0].dead = True  # simulate the tunnel dropping mid-batch
        result = mssql.run_query(conn_cfg, "SELECT A")

    assert result.columns == ["A"]
    assert len(opened) == 2  # the original dead one + the reopened one
    assert opened[0].closed is True
    assert opened[1].closed is True  # closed when reuse_connection's `with` exits


def test_reused_connection_query_error_without_dead_connection_is_not_retried(monkeypatch):
    """A reused connection that's still perfectly alive (its own health
    check, 'SELECT 1', succeeds), but whose actual query text is simply
    wrong, must NOT be treated as a dead-connection retry case - only one
    connect() call should happen, and the error should surface
    immediately instead of masking a real SQL bug as a reconnect."""
    opened = []

    class _BadQueryCursor(_FakeCursor):
        def execute(self, sql):
            if sql == "SELECT 1":  # the health check itself still works
                self.description = [("1",)]
                self._rows = [[1]]
                return
            raise pytds.Error("syntax error near SELECT")

    def fake_connect(**kwargs):
        conn = _FakeConnection({})
        opened.append(conn)
        return conn

    monkeypatch.setattr(mssql.pytds, "connect", fake_connect)
    monkeypatch.setattr(_FakeConnection, "cursor", lambda self: _BadQueryCursor(self, {}))
    conn_cfg = _make_conn_cfg()
    with pytest.raises(mssql.ConnectionError_):
        with mssql.reuse_connection(conn_cfg):
            mssql.run_query(conn_cfg, "SELECT A")
    assert len(opened) == 1  # no reconnect attempted - the connection itself was fine


def test_get_table_columns_reuses_the_shared_connection(monkeypatch):
    opened = []

    def fake_connect(**kwargs):
        conn = _FakeConnection({})
        opened.append(conn)
        return conn

    monkeypatch.setattr(mssql.pytds, "connect", fake_connect)
    conn_cfg = _make_conn_cfg()
    with mssql.reuse_connection(conn_cfg):
        mssql.get_table_columns(conn_cfg, "dbo", "SOME_TABLE")
        mssql.get_table_columns(conn_cfg, "dbo", "OTHER_TABLE")
    assert len(opened) == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
