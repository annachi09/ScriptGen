"""
Script history: a persisted audit trail of every UPDATE/rollback script
ScriptGen has generated - who generated it, when, for which table, and
the full script text - independent of whether it was produced from the
Tkinter desktop app or the web UI (both call into the exact same
app.core.script_generator, and both write into this same internal
SQLite db via get_internal_db_path()).

This exists so a reviewer (or the person who ran it) can answer "what
did I actually generate for JIRA-4821 last Tuesday, and did anyone else
touch that table this week" without having to have kept the .sql file
around. It never stores anything about whether a script was actually
*run* against the source database - ScriptGen doesn't execute scripts,
so it has no way to know that; this is a record of generation only.

Uses the same plain stdlib sqlite3 as app.db.internal_store, in the
same database file, as a separate table.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

_TABLE = "_scriptgen_script_history"

_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at_utc  TEXT NOT NULL,
    username        TEXT NOT NULL,
    kind            TEXT NOT NULL,
    schema_name     TEXT,
    table_name      TEXT NOT NULL,
    program         TEXT,
    statement_count INTEGER NOT NULL,
    warning_count   INTEGER NOT NULL,
    source          TEXT NOT NULL,
    sql_text        TEXT NOT NULL
)
"""

# "source" distinguishes which UI produced the entry - purely informational.
SOURCE_DESKTOP = "desktop"
SOURCE_WEB = "web"

KIND_UPDATE = "update"
KIND_ROLLBACK = "rollback"
KIND_DATE_ANOMALY = "date_anomaly_correction"


@dataclass
class HistoryEntry:
    id: int
    created_at_utc: str
    username: str
    kind: str
    schema_name: str
    table_name: str
    program: str
    statement_count: int
    warning_count: int
    source: str
    sql_text: str


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(_CREATE_SQL)
    conn.commit()
    return conn


def record_script(
    db_path: str,
    username: str,
    kind: str,
    schema_name: str,
    table_name: str,
    program: str,
    statement_count: int,
    warning_count: int,
    sql_text: str,
    source: str = SOURCE_DESKTOP,
) -> int:
    """Records one generated script. Returns its new history id."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            f"INSERT INTO {_TABLE} "
            "(created_at_utc, username, kind, schema_name, table_name, program, "
            " statement_count, warning_count, source, sql_text) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                username or "unknown",
                kind,
                schema_name or "",
                table_name or "",
                program or "",
                statement_count,
                warning_count,
                source,
                sql_text,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_history(db_path: str, limit: int = 100, username: str | None = None) -> list[HistoryEntry]:
    """Most recent first. Pass `username` to scope to one person's own
    entries (the web UI's default view); omit it for everyone's (an
    admin/"team activity" view)."""
    conn = _connect(db_path)
    try:
        sql = (
            f"SELECT id, created_at_utc, username, kind, schema_name, table_name, "
            f"program, statement_count, warning_count, source, sql_text FROM {_TABLE}"
        )
        params: tuple[Any, ...] = ()
        if username:
            sql += " WHERE username = ?"
            params = (username,)
        sql += " ORDER BY created_at_utc DESC LIMIT ?"
        params = params + (limit,)
        cur = conn.execute(sql, params)
        return [HistoryEntry(*row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_entry(db_path: str, entry_id: int) -> HistoryEntry | None:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            f"SELECT id, created_at_utc, username, kind, schema_name, table_name, "
            f"program, statement_count, warning_count, source, sql_text "
            f"FROM {_TABLE} WHERE id = ?",
            (entry_id,),
        )
        row = cur.fetchone()
        return HistoryEntry(*row) if row else None
    finally:
        conn.close()
