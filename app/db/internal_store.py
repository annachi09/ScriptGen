"""
Internal SQLite store: lets the user export a query result set as a
timestamped snapshot, kept locally for audit trail / before-after
comparison. Independent of the source SQL Server connection - this is
plain stdlib sqlite3, no extra dependency.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

_META_TABLE = "_scriptgen_snapshots"

_CREATE_META_SQL = f"""
CREATE TABLE IF NOT EXISTS {_META_TABLE} (
    snapshot_table TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    source_sql     TEXT,
    source_table   TEXT,
    row_count      INTEGER NOT NULL
)
"""

_SLUG_RE = re.compile(r"[^A-Za-z0-9_]")


def _slugify(text: str) -> str:
    slug = _SLUG_RE.sub("_", text).strip("_")
    return slug or "snapshot"


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass
class SnapshotInfo:
    snapshot_table: str
    display_name: str
    created_at_utc: str
    source_sql: str
    source_table: str
    row_count: int


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(_CREATE_META_SQL)
    conn.commit()
    return conn


def export_snapshot(
    db_path: str,
    display_name: str,
    columns: list[str],
    rows: list[list[Any]],
    source_sql: str = "",
    source_table: str = "",
) -> str:
    """
    Writes `rows` into a brand-new table in the internal SQLite db and
    records it in the metadata table. Returns the generated table name.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    snapshot_table = f"snap_{_slugify(display_name)}_{timestamp}"

    col_defs = ", ".join(f"{_quote_ident(col)} TEXT" for col in columns) or "_no_columns TEXT"
    create_sql = f"CREATE TABLE {_quote_ident(snapshot_table)} ({col_defs})"

    placeholders = ", ".join("?" for _ in columns) or "NULL"
    insert_sql = f"INSERT INTO {_quote_ident(snapshot_table)} VALUES ({placeholders})"

    conn = _connect(db_path)
    try:
        conn.execute(create_sql)
        if columns:
            conn.executemany(
                insert_sql,
                [[None if v is None else str(v) for v in row] for row in rows],
            )
        conn.execute(
            f"INSERT INTO {_META_TABLE} "
            "(snapshot_table, display_name, created_at_utc, source_sql, source_table, row_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                snapshot_table,
                display_name,
                datetime.now(timezone.utc).isoformat(),
                source_sql,
                source_table,
                len(rows),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return snapshot_table


def list_snapshots(db_path: str) -> list[SnapshotInfo]:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            f"SELECT snapshot_table, display_name, created_at_utc, source_sql, "
            f"source_table, row_count FROM {_META_TABLE} ORDER BY created_at_utc DESC"
        )
        return [SnapshotInfo(*row) for row in cur.fetchall()]
    finally:
        conn.close()


def load_snapshot(db_path: str, snapshot_table: str) -> tuple[list[str], list[list[Any]]]:
    conn = _connect(db_path)
    try:
        cur = conn.execute(f"SELECT * FROM {_quote_ident(snapshot_table)}")
        columns = [d[0] for d in cur.description]
        rows = [list(row) for row in cur.fetchall()]
        return columns, rows
    finally:
        conn.close()


def delete_snapshot(db_path: str, snapshot_table: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(f"DROP TABLE IF EXISTS {_quote_ident(snapshot_table)}")
        conn.execute(f"DELETE FROM {_META_TABLE} WHERE snapshot_table = ?", (snapshot_table,))
        conn.commit()
    finally:
        conn.close()
