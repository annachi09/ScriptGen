"""
Pure-logic tests for app/db/mssql.py's error-handling - specifically the
2026-09-16 fix for a real bug RJ hit live: a query that times out
mid-EXECUTE/FETCH (not at connect time) used to raise something outside
the narrow `except pytds.Error` in run_query/get_primary_key_columns/
get_table_columns, so it propagated uncaught past web/server.py's own
`except mssql.ConnectionError_` handlers and came back as a bare,
detail-less 500 instead of a friendly "timed out" message.

These tests never touch a real database - they monkeypatch mssql._connect
to hand back a fake connection/cursor whose .execute()/.fetchall() raise
a plain OSError/TimeoutError (the exact class of exception that slipped
through before this fix), and assert run_query now wraps it as
ConnectionError_ with a helpful message instead of letting it escape.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ConnectionConfig
from app.db import mssql


class _FakeCursor:
    def __init__(self, exc):
        self._exc = exc

    def execute(self, sql, params=None):
        raise self._exc

    def fetchall(self):
        raise self._exc


class _FakeConn:
    def __init__(self, exc):
        self._exc = exc
        self.closed = False

    def cursor(self):
        return _FakeCursor(self._exc)

    def close(self):
        self.closed = True


def _conn_cfg(timeout=30):
    cfg = ConnectionConfig(name="t", server="localhost", port=1433, username="u", timeout_seconds=timeout)
    cfg.set_password("p")
    return cfg


def test_run_query_wraps_timeout_error_not_pytds_error(monkeypatch):
    # TimeoutError is a plain builtin (subclass of OSError), NOT of
    # pytds.Error - this is the exact shape of exception that used to
    # slip through the old `except pytds.Error` and crash as a bare 500.
    fake_conn = _FakeConn(TimeoutError("timed out"))
    monkeypatch.setattr(mssql, "_connect", lambda cfg: fake_conn)
    with pytest.raises(mssql.ConnectionError_) as excinfo:
        mssql.run_query(_conn_cfg(timeout=30), "SELECT 1")
    assert "30s" in str(excinfo.value)
    assert "timed out" in str(excinfo.value).lower()
    assert fake_conn.closed is True


def test_run_query_wraps_plain_oserror(monkeypatch):
    fake_conn = _FakeConn(OSError("connection reset"))
    monkeypatch.setattr(mssql, "_connect", lambda cfg: fake_conn)
    with pytest.raises(mssql.ConnectionError_) as excinfo:
        mssql.run_query(_conn_cfg(), "SELECT 1")
    assert "connection reset" in str(excinfo.value)


def test_run_query_still_wraps_pytds_error(monkeypatch):
    fake_conn = _FakeConn(mssql.pytds.Error("syntax error"))
    monkeypatch.setattr(mssql, "_connect", lambda cfg: fake_conn)
    with pytest.raises(mssql.ConnectionError_) as excinfo:
        mssql.run_query(_conn_cfg(), "SELECT bad syntax")
    assert "syntax error" in str(excinfo.value)


def test_friendly_query_error_mentions_timeout_setting_and_where_to_fix_it():
    msg = mssql._friendly_query_error(TimeoutError("timed out"), _conn_cfg(timeout=15), "Query")
    assert "15s" in msg
    assert "Settings > Connections" in msg


def test_friendly_query_error_non_timeout_passes_through():
    msg = mssql._friendly_query_error(ValueError("something else"), _conn_cfg(), "Query")
    assert msg == "Query failed: something else"


def test_get_table_columns_wraps_timeout(monkeypatch):
    fake_conn = _FakeConn(TimeoutError("timed out"))
    monkeypatch.setattr(mssql, "_connect", lambda cfg: fake_conn)
    with pytest.raises(mssql.ConnectionError_):
        mssql.get_table_columns(_conn_cfg(), "dbo", "SOME_TABLE")


def test_get_primary_key_columns_wraps_timeout(monkeypatch):
    fake_conn = _FakeConn(TimeoutError("timed out"))
    monkeypatch.setattr(mssql, "_connect", lambda cfg: fake_conn)
    with pytest.raises(mssql.ConnectionError_):
        mssql.get_primary_key_columns(_conn_cfg(), "dbo", "SOME_TABLE")
