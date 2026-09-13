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

import contextlib
import re
import threading
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

# Same reasoning applies to a query that times out mid-EXECUTE/FETCH (not
# just at connect time) - RJ hit this live (2026-09-16) running Bill
# Issuance Validator's Case 2 scan: a query heavy enough to run past
# conn_cfg.timeout_seconds raised something that ISN'T a pytds.Error (a
# raw socket-level OSError/TimeoutError, same as the connect-time case
# above), which the original `except pytds.Error` in run_query/
# get_primary_key_columns/get_table_columns didn't catch - so instead of
# the intended friendly "Query failed: ..." -> 502 path, the exception
# propagated all the way past web/server.py's own `except mssql.
# ConnectionError_` handlers uncaught, and FastAPI's default handler
# returned a bare, detail-less 500. Every one of those places now uses
# this broader tuple instead of `pytds.Error` alone.
_QUERY_EXCEPTIONS = (pytds.Error, OSError)


def _friendly_query_error(exc: Exception, conn_cfg: ConnectionConfig, context: str = "Query") -> str:
    if isinstance(exc, (TimeoutError, ConnectionRefusedError)) or "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
        return (
            f"{context} did not finish within {conn_cfg.timeout_seconds}s and timed out. This can "
            "happen with a heavier query (large scans, several joins/window functions) over a slow "
            "tunnel - try raising the connection Timeout in Settings > Connections, or narrow the "
            "query (add filters, a lower row cap)."
        )
    return f"{context} failed: {exc}"


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


# --- Optional connection reuse (web/batch_jobs.py) --------------------------
#
# Every function below defaults to opening a brand-new TCP+TDS connection
# for each call and tearing it down immediately after - fine, even
# preferable, for one-off queries (every page except the Date Anomaly
# Batch page works this way). But Batch runs Detect->Resolve->Generate
# for a whole list of NISS, and each NISS is itself ~6 queries (detect,
# correct-date, item-to-bill, xml lookup, anomalous, item-status). When
# the DB is reached through a local SSH/RDP tunnel (RJ's own setup - see
# app/config.py's docs), the connection HANDSHAKE, not the query, is what
# dominates: each fresh connect can cost several times what the query
# itself takes, so a 50-NISS batch was paying that handshake cost ~300
# times over. reuse_connection() below opens ONE connection and makes
# every run_query()/get_table_columns() call on THIS THREAD reuse it
# instead, for as long as the `with` block is active.
#
# Implemented with threading.local (not a plain module-level variable)
# specifically because web/batch_jobs.py runs each batch on its own
# background thread - a plain global would leak one batch's connection
# into an unrelated request/thread running at the same time.
#
# This is deliberately invisible to every existing caller: run_query's
# own signature/behavior is unchanged for anyone who never calls
# reuse_connection, and the tests in tests/test_web_api.py monkeypatch
# run_query itself wholesale, so none of this logic even runs under test
# - nothing here needed a single test file changed.
_thread_local = threading.local()


def _is_alive(conn) -> bool:
    """Cheap round-trip to tell a merely-slow reused connection from one
    the tunnel has actually dropped. Only called on the reused-connection
    error path (see run_query) - never on the normal one-shot path, so it
    adds no overhead to any other caller in the app."""
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchall()
        return True
    except Exception:
        return False


@contextlib.contextmanager
def reuse_connection(conn_cfg: ConnectionConfig):
    """Opens one connection and makes every run_query()/get_table_columns()
    call made on THIS THREAD, for the life of this `with` block, reuse it
    instead of opening its own. See the module comment above for why this
    is the actual fix for a slow Batch run over a tunneled connection.

    Cleanup closes whatever connection is CURRENTLY published in
    _thread_local.conn, not whichever object this function itself opened
    - run_query's reconnect-on-dead-connection path (see its own
    docstring) may have swapped in a fresher connection by the time this
    `with` block exits, and that's the one actually still open."""
    conn = _connect(conn_cfg)
    _thread_local.conn = conn
    try:
        yield conn
    finally:
        current = getattr(_thread_local, "conn", None)
        _thread_local.conn = None
        if current is not None:
            try:
                current.close()
            except Exception:
                pass


def _fetch(conn, sql: str) -> tuple[list[str], list[list[Any]]]:
    cur = conn.cursor()
    cur.execute(sql)
    if cur.description is None:
        # Not a row-returning statement (shouldn't normally happen - the
        # read-only account can't run DML/DDL anyway).
        return [], []
    return [d[0] for d in cur.description], [list(r) for r in cur.fetchall()]


def run_query(conn_cfg: ConnectionConfig, sql: str) -> QueryResult:
    start = time.perf_counter()
    reused = getattr(_thread_local, "conn", None)
    owns_connection = reused is None
    conn = reused if reused is not None else _connect(conn_cfg)
    try:
        try:
            columns, rows = _fetch(conn, sql)
        except _QUERY_EXCEPTIONS as exc:
            if owns_connection or _is_alive(conn):
                # Either a fresh connection that just failed on this SQL
                # (no point reconnecting - the query itself is the
                # problem), or a reused connection that's still fine (same
                # conclusion). Either way, surface it as-is.
                raise ConnectionError_(_friendly_query_error(exc, conn_cfg)) from exc
            # The reused connection itself died mid-batch (tunnel blip) -
            # reopen once, publish the fresh connection for every later
            # call in this batch too, and retry this one query on it.
            try:
                conn.close()
            except Exception:
                pass
            conn = _connect(conn_cfg)
            _thread_local.conn = conn
            try:
                columns, rows = _fetch(conn, sql)
            except _QUERY_EXCEPTIONS as exc2:
                raise ConnectionError_(_friendly_query_error(exc2, conn_cfg, "Query (after reconnect)")) from exc2
    finally:
        if owns_connection:
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
    except _QUERY_EXCEPTIONS as exc:
        raise ConnectionError_(_friendly_query_error(exc, conn_cfg, "Primary key lookup")) from exc
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
    # Reuses the batch's shared connection too (see reuse_connection) -
    # web/batch_jobs.py calls this twice up front for the audit-column
    # check, before the per-NISS loop even starts.
    reused = getattr(_thread_local, "conn", None)
    conn = reused if reused is not None else _connect(conn_cfg)
    try:
        cur = conn.cursor()
        cur.execute(sql, (schema, table))
        return {row[0]: row[1] for row in cur.fetchall()}
    except _QUERY_EXCEPTIONS as exc:
        raise ConnectionError_(_friendly_query_error(exc, conn_cfg, "Schema lookup")) from exc
    finally:
        if reused is None:
            conn.close()


def list_databases(conn_cfg: ConnectionConfig) -> list[str]:
    conn = _connect(conn_cfg)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sys.databases ORDER BY name")
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
