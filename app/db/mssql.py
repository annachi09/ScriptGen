"""
SQL Server access via python-tds (import name `pytds`) - a pure-Python
TDS client with NO compiled extensions and no ODBC/FreeTDS driver
install required on the host machine. (Earlier drafts used `pymssql`,
which bundles a compiled FreeTDS binding; that has no prebuilt wheel
for some newer Python versions and falls back to a from-source build
that can fail depending on the local build toolchain. pytds sidesteps
that class of problem entirely - it's plain Python, so `pip install
python-tds` never compiles anything.)

Deliberately thin: this module knows how to open a connection, test
it, run a SELECT and hand back plain Python data, and look up primary
key columns for a table. It does NOT know anything about the UI or
about diffing - see app/core for that.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import pytds

from ..config import ConnectionConfig

# pytds raises its own exception hierarchy (all subclassing pytds.Error)
# for protocol/auth-level failures, but a low-level connect timeout or
# "connection refused" surfaces as a plain builtin OSError/TimeoutError
# instead (the TDS layer never gets to engage). We treat both as
# "the connection failed" from the caller's point of view.
_CONNECTION_EXCEPTIONS = (pytds.Error, OSError)


class ConnectionError_(Exception):
    """Raised for any connection/auth failure, wrapping the driver error."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    elapsed_ms: float
    source_table: Optional[str] = None      # best-effort "schema.table" guess
    source_schema: Optional[str] = None
    row_count: int = field(init=False)

    def __post_init__(self):
        self.row_count = len(self.rows)


def _connect(conn_cfg: ConnectionConfig):
    try:
        return pytds.connect(
            server=conn_cfg.server,
            port=conn_cfg.port,
            user=conn_cfg.username,
            password=conn_cfg.get_password(),
            database=conn_cfg.database or None,
            timeout=conn_cfg.timeout_seconds,
            login_timeout=conn_cfg.timeout_seconds,
            as_dict=False,
            readonly=True,
        )
    except _CONNECTION_EXCEPTIONS as exc:
        raise ConnectionError_(_friendly_error(exc, conn_cfg)) from exc


def _friendly_error(exc: Exception, conn_cfg: ConnectionConfig) -> str:
    if isinstance(exc, (TimeoutError, ConnectionRefusedError)) or "timed out" in str(exc).lower():
        return (
            f"Could not reach {conn_cfg.server}:{conn_cfg.port} within "
            f"{conn_cfg.timeout_seconds}s. Is your local tunnel open?"
        )
    if isinstance(exc, pytds.LoginError) or "login" in str(exc).lower():
        return f"Login failed for user '{conn_cfg.username}'. Check the username/password in Config."
    return f"Connection failed: {exc}"


def test_connection(conn_cfg: ConnectionConfig) -> tuple[bool, str, float]:
    """
    Returns (success, message, elapsed_ms). Never raises - this is meant
    to be called directly from the "Test Connection" button in the UI.
    """
    start = time.perf_counter()
    try:
        conn = _connect(conn_cfg)
        cur = conn.cursor()
        cur.execute("SELECT @@VERSION, DB_NAME()")
        version, dbname = cur.fetchone()
        conn.close()
        elapsed_ms = (time.perf_counter() - start) * 1000
        short_version = str(version).splitlines()[0]
        return True, f"Connected to '{dbname}'. {short_version}", elapsed_ms
    except ConnectionError_ as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return False, str(exc), elapsed_ms
    except Exception as exc:  # noqa: BLE001 - surface anything unexpected to the user
        elapsed_ms = (time.perf_counter() - start) * 1000
        return False, f"Unexpected error: {exc}", elapsed_ms


# Matches "FROM <schema.table|table> [AS] [alias]" as the first table
# reference in a query. Best-effort only - deliberately does not try
# to parse JOINs, subqueries, or CTEs. Used to pre-fill (not force) the
# "target table" field the script generator needs.
_FROM_RE = re.compile(
    r"\bFROM\s+(\[?[\w]+\]?\.)?(\[?[\w]+\]?)\s*(?:AS\s+)?(\w+)?",
    re.IGNORECASE,
)


def guess_source_table(sql: str) -> tuple[Optional[str], Optional[str]]:
    """Returns (schema, table) guessed from the query text, or (None, None)."""
    match = _FROM_RE.search(sql)
    if not match:
        return None, None
    schema_raw, table_raw = match.group(1), match.group(2)
    schema = schema_raw.rstrip(".").strip("[]") if schema_raw else "dbo"
    table = table_raw.strip("[]") if table_raw else None
    return schema, table


def run_query(conn_cfg: ConnectionConfig, sql: str) -> QueryResult:
    start = time.perf_counter()
    conn = _connect(conn_cfg)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        if cur.description is None:
            # Not a row-returning statement (shouldn't normally happen -
            # the read-only account can't run DML/DDL anyway).
            columns: list[str] = []
            rows: list[list[Any]] = []
        else:
            columns = [d[0] for d in cur.description]
            rows = [list(r) for r in cur.fetchall()]
    except pytds.Error as exc:
        raise ConnectionError_(f"Query failed: {exc}") from exc
    finally:
        conn.close()

    elapsed_ms = (time.perf_counter() - start) * 1000
    schema, table = guess_source_table(sql)
    source_table = f"{schema}.{table}" if schema and table else None
    return QueryResult(
        columns=columns,
        rows=rows,
        elapsed_ms=elapsed_ms,
        source_table=source_table,
        source_schema=schema,
    )


def get_primary_key_columns(conn_cfg: ConnectionConfig, schema: str, table: str) -> list[str]:
    """
    Looks up the primary key column(s) for schema.table via
    INFORMATION_SCHEMA. Returns [] if the table has no PK (or wasn't
    found) - the caller should then fall back to asking the user which
    column(s) uniquely identify a row.
    """
    sql = """
        SELECT kcu.COLUMN_NAME
        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
          ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
         AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
        WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
          AND tc.TABLE_SCHEMA = %s
          AND tc.TABLE_NAME = %s
        ORDER BY kcu.ORDINAL_POSITION
    """
    conn = _connect(conn_cfg)
    try:
        cur = conn.cursor()
        cur.execute(sql, (schema, table))
        return [row[0] for row in cur.fetchall()]
    except pytds.Error as exc:
        raise ConnectionError_(f"Primary key lookup failed: {exc}") from exc
    finally:
        conn.close()


def get_table_columns(conn_cfg: ConnectionConfig, schema: str, table: str) -> dict[str, str]:
    """
    Column name -> SQL Server data type (e.g. "varchar", "int") for
    schema.table, via INFORMATION_SCHEMA. Returns {} if the table
    doesn't exist (or has no columns, which in practice means the same
    thing) - the caller (schema validation in the UI) treats an empty
    result as "table not found".
    """
    sql = """
        SELECT COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        ORDER BY ORDINAL_POSITION
    """
    conn = _connect(conn_cfg)
    try:
        cur = conn.cursor()
        cur.execute(sql, (schema, table))
        return {row[0]: row[1] for row in cur.fetchall()}
    except pytds.Error as exc:
        raise ConnectionError_(f"Schema lookup failed: {exc}") from exc
    finally:
        conn.close()


def list_databases(conn_cfg: ConnectionConfig) -> list[str]:
    conn = _connect(conn_cfg)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sys.databases ORDER BY name")
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
