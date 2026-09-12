"""Tests for app/db/script_history.py - the generated-script audit trail
shared by the desktop app and the web UI (see that module's docstring)."""
from __future__ import annotations

import sqlite3

import pytest

from app.db import script_history


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "history_test.db")


def _record(db_path, **overrides):
    kwargs = dict(
        username="alice",
        kind=script_history.KIND_UPDATE,
        schema_name="dbo",
        table_name="Accounts",
        program="JIRA-1",
        statement_count=3,
        warning_count=0,
        sql_text="UPDATE dbo.Accounts SET x = 1 WHERE id = 1;",
        source=script_history.SOURCE_DESKTOP,
    )
    kwargs.update(overrides)
    return script_history.record_script(db_path, **kwargs)


def test_record_and_get_entry_round_trips(db_path):
    entry_id = _record(db_path)
    entry = script_history.get_entry(db_path, entry_id)
    assert entry is not None
    assert entry.id == entry_id
    assert entry.username == "alice"
    assert entry.kind == script_history.KIND_UPDATE
    assert entry.schema_name == "dbo"
    assert entry.table_name == "Accounts"
    assert entry.program == "JIRA-1"
    assert entry.statement_count == 3
    assert entry.warning_count == 0
    assert entry.source == script_history.SOURCE_DESKTOP
    assert "UPDATE dbo.Accounts" in entry.sql_text
    assert entry.created_at_utc  # non-empty ISO timestamp


def test_get_entry_missing_returns_none(db_path):
    assert script_history.get_entry(db_path, 9999) is None


def test_list_history_most_recent_first(db_path):
    id1 = _record(db_path, table_name="First")
    id2 = _record(db_path, table_name="Second")
    id3 = _record(db_path, table_name="Third")
    entries = script_history.list_history(db_path)
    assert [e.id for e in entries] == [id3, id2, id1]
    assert [e.table_name for e in entries] == ["Third", "Second", "First"]


def test_list_history_respects_limit(db_path):
    for i in range(5):
        _record(db_path, table_name=f"T{i}")
    entries = script_history.list_history(db_path, limit=2)
    assert len(entries) == 2


def test_list_history_filters_by_username(db_path):
    _record(db_path, username="alice", table_name="A")
    _record(db_path, username="bob", table_name="B")
    _record(db_path, username="alice", table_name="C")

    alice_entries = script_history.list_history(db_path, username="alice")
    assert {e.table_name for e in alice_entries} == {"A", "C"}

    everyone = script_history.list_history(db_path)
    assert {e.table_name for e in everyone} == {"A", "B", "C"}


def test_record_script_defaults_blank_username_to_unknown(db_path):
    entry_id = _record(db_path, username="")
    entry = script_history.get_entry(db_path, entry_id)
    assert entry.username == "unknown"


def test_rollback_kind_recorded(db_path):
    entry_id = _record(db_path, kind=script_history.KIND_ROLLBACK, source=script_history.SOURCE_WEB)
    entry = script_history.get_entry(db_path, entry_id)
    assert entry.kind == script_history.KIND_ROLLBACK
    assert entry.source == script_history.SOURCE_WEB


def test_shares_the_same_sqlite_file_as_internal_store(db_path):
    # app.db.internal_store creates its own metadata table in the same
    # db file (get_internal_db_path()) - script_history must not clobber
    # or collide with it when both touch the same file.
    from app.db import internal_store

    internal_store.export_snapshot(db_path, "snap1", ["id"], [[1], [2]])
    entry_id = _record(db_path)

    assert script_history.get_entry(db_path, entry_id) is not None
    assert len(internal_store.list_snapshots(db_path)) == 1

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    finally:
        conn.close()
    assert "_scriptgen_script_history" in tables
    assert "_scriptgen_snapshots" in tables
