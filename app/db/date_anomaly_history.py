"""
Diff Date anomaly analysis history: a persisted record of every NISS
that's been *analyzed* through the DIFF DATES Anomaly workflow - not
just the ones a correction script was eventually generated for.

This is a separate concern from app.db.script_history (which only
records a generated script's text, kind-agnostic across the whole
app): script_history answers "what did I generate", this module
answers "what NISS have I already looked at, and what did Detect /
Resolve / Generate find each time" - useful for an analyst working
through a long list of supplies who needs to avoid re-investigating
one they (or a teammate) already checked, with or without a script
ever having been generated for it (e.g. Detect found nothing, or the
analyst stopped after Resolve to review before generating).

One row per analysis. Recorded at Detect time (the earliest point
"this NISS was analyzed" is true) with whatever is known then, and
refined in place via update_analysis as Resolve/Generate add more -
never a second row for the same analysis pass, so "history of the
ones we analyzed" stays one entry per Detect click, not three.

Uses the same plain stdlib sqlite3 as app.db.script_history, in the
same database file, as a separate table.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

_TABLE = "_scriptgen_date_anomaly_history"

_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at_utc          TEXT NOT NULL,
    updated_at_utc          TEXT NOT NULL,
    username                TEXT NOT NULL,
    niss                    TEXT NOT NULL,
    threshold               INTEGER NOT NULL,
    source                  TEXT NOT NULL,
    anomaly_count           INTEGER NOT NULL,
    correct_date            TEXT,
    correct_date_reading    TEXT,
    item_count              INTEGER,
    xml_count               INTEGER,
    item_status_count       INTEGER,
    anomalous_count         INTEGER,
    generated               INTEGER NOT NULL DEFAULT 0,
    explanation             TEXT NOT NULL DEFAULT ''
)
"""

# Same meaning as app.db.script_history's SOURCE_* constants - kept as a
# separate set (not imported from there) so this module has no hard
# dependency on script_history, even though the values line up today.
SOURCE_DESKTOP = "desktop"
SOURCE_WEB = "web"
SOURCE_BATCH = "batch"


@dataclass
class AnalysisEntry:
    id: int
    created_at_utc: str
    updated_at_utc: str
    username: str
    niss: str
    threshold: int
    source: str
    anomaly_count: int
    correct_date: Optional[str]
    correct_date_reading: Optional[str]
    item_count: Optional[int]
    xml_count: Optional[int]
    item_status_count: Optional[int]
    anomalous_count: Optional[int]
    generated: bool
    explanation: str


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(_CREATE_SQL)
    conn.commit()
    return conn


def _row_to_entry(row: tuple) -> AnalysisEntry:
    (
        id_, created_at_utc, updated_at_utc, username, niss, threshold, source, anomaly_count,
        correct_date, correct_date_reading, item_count, xml_count, item_status_count, anomalous_count,
        generated, explanation,
    ) = row
    return AnalysisEntry(
        id=id_, created_at_utc=created_at_utc, updated_at_utc=updated_at_utc, username=username,
        niss=niss, threshold=threshold, source=source, anomaly_count=anomaly_count,
        correct_date=correct_date, correct_date_reading=correct_date_reading,
        item_count=item_count, xml_count=xml_count, item_status_count=item_status_count,
        anomalous_count=anomalous_count, generated=bool(generated), explanation=explanation or "",
    )


def record_analysis(
    db_path: str,
    *,
    username: str,
    niss: str,
    threshold: int,
    anomaly_count: int,
    correct_date: Any = None,
    correct_date_reading: Any = None,
    item_count: Optional[int] = None,
    xml_count: Optional[int] = None,
    item_status_count: Optional[int] = None,
    anomalous_count: Optional[int] = None,
    generated: bool = False,
    explanation: str = "",
    source: str = SOURCE_DESKTOP,
) -> int:
    """
    Records one analysis pass. Called at Detect with just
    anomaly_count/correct_date known (every other count left None) for
    the web/desktop single-NISS flow - the returned id is then passed
    back into update_analysis as Resolve/Generate add more. The batch
    flow instead calls this once per NISS with every field already
    known (generated=True, all counts filled in) since batch runs
    Detect->Resolve->Generate in one uninterrupted pass with no
    separate id to thread through.
    """
    conn = _connect(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            f"INSERT INTO {_TABLE} "
            "(created_at_utc, updated_at_utc, username, niss, threshold, source, anomaly_count, "
            " correct_date, correct_date_reading, item_count, xml_count, item_status_count, "
            " anomalous_count, generated, explanation) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now, now, username or "unknown", niss, threshold, source, anomaly_count,
                str(correct_date) if correct_date is not None else None,
                str(correct_date_reading) if correct_date_reading is not None else None,
                item_count, xml_count, item_status_count, anomalous_count,
                1 if generated else 0, explanation or "",
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def update_analysis(
    db_path: str,
    entry_id: int,
    *,
    item_count: Optional[int] = None,
    xml_count: Optional[int] = None,
    item_status_count: Optional[int] = None,
    anomalous_count: Optional[int] = None,
    generated: Optional[bool] = None,
    explanation: Optional[str] = None,
) -> None:
    """Refines an existing row in place (Resolve and/or Generate calling
    back with more detail than was known at Detect time). Only the
    fields actually passed are updated - None means "leave as-is", not
    "clear this field", so Resolve updating item/xml/anomalous counts
    doesn't wipe out whatever Generate later fills in for `generated`,
    and vice versa."""
    fields: list[str] = []
    params: list[Any] = []
    if item_count is not None:
        fields.append("item_count = ?")
        params.append(item_count)
    if xml_count is not None:
        fields.append("xml_count = ?")
        params.append(xml_count)
    if item_status_count is not None:
        fields.append("item_status_count = ?")
        params.append(item_status_count)
    if anomalous_count is not None:
        fields.append("anomalous_count = ?")
        params.append(anomalous_count)
    if generated is not None:
        fields.append("generated = ?")
        params.append(1 if generated else 0)
    if explanation is not None:
        fields.append("explanation = ?")
        params.append(explanation)
    if not fields:
        return

    fields.append("updated_at_utc = ?")
    params.append(datetime.now(timezone.utc).isoformat())
    params.append(entry_id)

    conn = _connect(db_path)
    try:
        conn.execute(f"UPDATE {_TABLE} SET {', '.join(fields)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


def list_analyses(db_path: str, limit: int = 50, username: str | None = None) -> list[AnalysisEntry]:
    """Most recent first. Pass `username` to scope to one person's own
    analyses (the normal web UI view); omit it for everyone's."""
    conn = _connect(db_path)
    try:
        sql = (
            f"SELECT id, created_at_utc, updated_at_utc, username, niss, threshold, source, anomaly_count, "
            f"correct_date, correct_date_reading, item_count, xml_count, item_status_count, anomalous_count, "
            f"generated, explanation FROM {_TABLE}"
        )
        params: tuple[Any, ...] = ()
        if username:
            sql += " WHERE username = ?"
            params = (username,)
        sql += " ORDER BY updated_at_utc DESC LIMIT ?"
        params = params + (limit,)
        cur = conn.execute(sql, params)
        return [_row_to_entry(row) for row in cur.fetchall()]
    finally:
        conn.close()


def get_analysis(db_path: str, entry_id: int) -> Optional[AnalysisEntry]:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            f"SELECT id, created_at_utc, updated_at_utc, username, niss, threshold, source, anomaly_count, "
            f"correct_date, correct_date_reading, item_count, xml_count, item_status_count, anomalous_count, "
            f"generated, explanation FROM {_TABLE} WHERE id = ?",
            (entry_id,),
        )
        row = cur.fetchone()
        return _row_to_entry(row) if row else None
    finally:
        conn.close()
