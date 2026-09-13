"""
Local (non-SQL-Server) collaboration state for the Bulk Checker page:
per-account notes, shared named saved searches, and per-billing-period
search history for the Trend chart. All three live as separate tables
inside ScriptGen's one shared internal SQLite db (get_internal_db_path,
same db_path every other app/db module writes to) rather than the three
standalone .db files the source EWA Bulk Checker project used - that
project had its own dedicated data/ folder; ScriptGen already has one
internal db for everything (see app/db/internal_store.py,
date_anomaly_history.py, script_history.py for the same one-db-many-
tables pattern), so these just add three more tables to it instead of
introducing new files to manage/back up.

Ported from EWA_BulkChecker's app/notes_db.py, app/saved_searches_db.py,
and app/search_history_db.py (2026-09-12) - table shapes and business
rules unchanged, only the connection helper and table names (prefixed
_scriptgen_bulk_checker_* like every other ScriptGen-internal table) are
different.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# --- Notes -----------------------------------------------------------------
_NOTES_TABLE = "_scriptgen_bulk_checker_notes"

STATUS_OPEN = "open"
STATUS_HANDLING = "being_handled"
STATUS_RESOLVED = "resolved"
VALID_STATUSES = (STATUS_OPEN, STATUS_HANDLING, STATUS_RESOLVED)
MAX_NOTE_LENGTH = 2000

# --- Saved searches ----------------------------------------------------------
_SAVED_SEARCHES_TABLE = "_scriptgen_bulk_checker_saved_searches"
MAX_SAVED_SEARCH_NAME_LENGTH = 80

# --- Search history (Trend) -------------------------------------------------
_SEARCH_HISTORY_TABLE = "_scriptgen_bulk_checker_search_history"

_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_NOTES_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_number TEXT NOT NULL,
    billing_period TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    note TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE(account_number, billing_period)
);
CREATE TABLE IF NOT EXISTS {_SAVED_SEARCHES_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    date_from TEXT NOT NULL,
    date_to TEXT NOT NULL,
    billing_period TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS {_SEARCH_HISTORY_TABLE} (
    billing_period TEXT PRIMARY KEY,
    date_from TEXT NOT NULL,
    date_to TEXT NOT NULL,
    total INTEGER NOT NULL,
    pending INTEGER NOT NULL,
    missing_bill INTEGER NOT NULL,
    in_invoicing INTEGER NOT NULL,
    outstanding_amount REAL NOT NULL,
    searched_by TEXT NOT NULL,
    searched_at_utc TEXT NOT NULL
);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(_CREATE_SQL)
    conn.commit()
    return conn


@dataclass
class AccountNote:
    account_number: str
    billing_period: str
    status: str
    note: str
    updated_by: str
    updated_at_utc: str


def list_notes_for_period(db_path: str, billing_period: str) -> list[AccountNote]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT account_number, billing_period, status, note, updated_by, updated_at_utc "
            f"FROM {_NOTES_TABLE} WHERE billing_period = ?",
            (str(billing_period),),
        ).fetchall()
        return [AccountNote(*row) for row in rows]
    finally:
        conn.close()


def set_note(db_path: str, account_number: str, billing_period: str, status: str, note: str, updated_by: str) -> AccountNote:
    if status not in VALID_STATUSES:
        raise ValueError(f"Status must be one of {', '.join(VALID_STATUSES)}.")
    account_number = account_number.strip()
    if not account_number:
        raise ValueError("Account number is required.")
    note = note.strip()
    if len(note) > MAX_NOTE_LENGTH:
        raise ValueError(f"Note can't be longer than {MAX_NOTE_LENGTH} characters.")

    now = datetime.now(timezone.utc).isoformat()
    conn = _connect(db_path)
    try:
        conn.execute(
            f"""
            INSERT INTO {_NOTES_TABLE} (account_number, billing_period, status, note, updated_by, updated_at_utc)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_number, billing_period)
            DO UPDATE SET status = excluded.status, note = excluded.note,
                          updated_by = excluded.updated_by, updated_at_utc = excluded.updated_at_utc
            """,
            (account_number, str(billing_period), status, note, updated_by, now),
        )
        conn.commit()
    finally:
        conn.close()
    return AccountNote(
        account_number=account_number, billing_period=str(billing_period),
        status=status, note=note, updated_by=updated_by, updated_at_utc=now,
    )


def clear_note(db_path: str, account_number: str, billing_period: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            f"DELETE FROM {_NOTES_TABLE} WHERE account_number = ? AND billing_period = ?",
            (account_number.strip(), str(billing_period)),
        )
        conn.commit()
    finally:
        conn.close()


@dataclass
class SavedSearch:
    id: int
    name: str
    date_from: str
    date_to: str
    billing_period: str
    created_by: str
    created_at_utc: str


def list_saved_searches(db_path: str) -> list[SavedSearch]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT id, name, date_from, date_to, billing_period, created_by, created_at_utc "
            f"FROM {_SAVED_SEARCHES_TABLE}"
        ).fetchall()
        return sorted([SavedSearch(*row) for row in rows], key=lambda s: s.name.lower())
    finally:
        conn.close()


def save_search(db_path: str, name: str, date_from: str, date_to: str, billing_period: str, created_by: str) -> SavedSearch:
    name = name.strip()
    if not name:
        raise ValueError("Name is required.")
    if len(name) > MAX_SAVED_SEARCH_NAME_LENGTH:
        raise ValueError(f"Name can't be longer than {MAX_SAVED_SEARCH_NAME_LENGTH} characters.")

    now = datetime.now(timezone.utc).isoformat()
    conn = _connect(db_path)
    try:
        conn.execute(
            f"""
            INSERT INTO {_SAVED_SEARCHES_TABLE} (name, date_from, date_to, billing_period, created_by, created_at_utc)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                date_from = excluded.date_from, date_to = excluded.date_to,
                billing_period = excluded.billing_period, created_by = excluded.created_by,
                created_at_utc = excluded.created_at_utc
            """,
            (name, date_from, date_to, str(billing_period), created_by, now),
        )
        conn.commit()
        row = conn.execute(
            f"SELECT id, name, date_from, date_to, billing_period, created_by, created_at_utc "
            f"FROM {_SAVED_SEARCHES_TABLE} WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        return SavedSearch(*row)
    finally:
        conn.close()


def delete_saved_search(db_path: str, search_id: int) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(f"DELETE FROM {_SAVED_SEARCHES_TABLE} WHERE id = ?", (search_id,))
        conn.commit()
    finally:
        conn.close()


@dataclass
class SearchHistoryEntry:
    billing_period: str
    date_from: str
    date_to: str
    total: int
    pending: int
    missing_bill: int
    in_invoicing: int
    outstanding_amount: float
    searched_by: str
    searched_at_utc: str


def record_search(
    db_path: str,
    *,
    billing_period: str,
    date_from: str,
    date_to: str,
    total: int,
    pending: int,
    missing_bill: int,
    in_invoicing: int,
    outstanding_amount: float,
    searched_by: str,
) -> None:
    """Snapshots a full ("all"-filter) search's already-computed summary
    numbers into the history table, keyed by billing period - a later
    search for the same period overwrites that period's row. NOT a live
    cross-period aggregate query (see EWA's original search_history_db.py
    docstring for why: nothing in the schema documents how a billing
    period's contract-active date window maps to real dates across
    periods) - the Trend page only ever shows periods actually searched
    with the filter set to "All"."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect(db_path)
    try:
        conn.execute(
            f"""
            INSERT INTO {_SEARCH_HISTORY_TABLE}
                (billing_period, date_from, date_to, total, pending, missing_bill,
                 in_invoicing, outstanding_amount, searched_by, searched_at_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(billing_period) DO UPDATE SET
                date_from = excluded.date_from, date_to = excluded.date_to,
                total = excluded.total, pending = excluded.pending,
                missing_bill = excluded.missing_bill, in_invoicing = excluded.in_invoicing,
                outstanding_amount = excluded.outstanding_amount,
                searched_by = excluded.searched_by, searched_at_utc = excluded.searched_at_utc
            """,
            (
                str(billing_period), date_from, date_to, total, pending, missing_bill,
                in_invoicing, outstanding_amount, searched_by, now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_search_history(db_path: str) -> list[SearchHistoryEntry]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT billing_period, date_from, date_to, total, pending, missing_bill, "
            f"in_invoicing, outstanding_amount, searched_by, searched_at_utc "
            f"FROM {_SEARCH_HISTORY_TABLE}"
        ).fetchall()
        entries = [SearchHistoryEntry(*row) for row in rows]
    finally:
        conn.close()

    def sort_key(entry: SearchHistoryEntry):
        try:
            return (0, int(entry.billing_period))
        except ValueError:
            return (1, entry.billing_period)

    return sorted(entries, key=sort_key)
