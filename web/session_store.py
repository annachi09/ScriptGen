"""
Per-session server-side query state: the last query result's real,
Python-typed columns/rows, kept in memory so the diff engine can compare
against them exactly the way the desktop app's self._original_rows does
- WITHOUT round-tripping typed values (datetime, Decimal, bytes, ...)
through JSON to the browser and back as strings, which would be lossy.
The browser only ever sees/sends back display strings and edited-cell
strings, same as tksheet always has.

Trade-off worth being explicit about: this is a single-process, in-memory
dict keyed by session id. Fine for "a few teammates against one server
process" (the target for this phase); it does NOT survive a server
restart (an in-flight edit is lost - same as closing the desktop app
without generating a script) and does NOT work if this is ever run
behind multiple server processes/workers without moving to a shared
store (Redis, a DB table, etc.) - a contained change later if it's ever
needed, same spirit as the crypto/keyring note in app/utils/crypto.py.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class QueryState:
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    original_rows: list[list[Any]] = field(default_factory=list)
    source_schema: Optional[str] = None
    source_table: Optional[str] = None


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, QueryState] = {}

    def get(self, session_id: str) -> QueryState:
        with self._lock:
            return self._states.setdefault(session_id, QueryState())

    def set(self, session_id: str, state: QueryState) -> None:
        with self._lock:
            self._states[session_id] = state

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._states.pop(session_id, None)


# Single process-wide instance - see the module docstring's trade-off note.
sessions = SessionStore()


@dataclass
class DateAnomalyState:
    """Server-side state for the Date Anomaly page's 3-step workflow
    (Detect -> Resolve Bill Links -> Generate Correction Script), same
    trade-off/reasoning as QueryState above: id_reading/item/xml values
    stay Python-typed here (never round-tripped through the browser as
    strings) since app.core.date_anomaly.build_correction_script needs
    real values to embed correctly-typed SQL literals."""
    niss: str = ""
    threshold: int = 0
    anomaly_rows: list[dict] = field(default_factory=list)   # column(any case) -> value, from the detect query
    correct_date: Any = None
    correct_date_reading: Any = None
    item_to_bill_map: dict = field(default_factory=dict)     # id_reading -> list[id_item_to_bill] (one reading can map to more than one item-to-bill row)
    item_to_xml_map: dict = field(default_factory=dict)       # id_item_to_bill -> real id_xml (GCCOM_ITEMS_TO_BILL.ID_XML - NOT the same value as id_item_to_bill, confirmed 2026-09-13)
    xml_rows: dict = field(default_factory=dict)              # REAL id_xml (from item_to_xml_map) -> raw XML_TO_BILL text
    anomalous_item_ids: list = field(default_factory=list)    # id_item_to_bill values with an OPEN GCCOM_ANOMALOUS record to cancel (Part 5)
    item_status_ids: list = field(default_factory=list)       # id_item_to_bill values still at STTOBILL00, to advance to STTOBILL01 (Part 3)
    history_id: Any = None                                    # app.db.date_anomaly_history row id for this analysis pass, set at Detect
    reading_has_audit: bool = False
    item_has_audit: bool = False
    # Account/offered-service/contract-status for this NISS, from
    # date_anomaly.build_niss_account_query - fetched once at Detect
    # (best-effort, never blocks Detect if it errors/finds nothing) purely
    # to give the "Analyze with AI" second opinion real account context
    # instead of just counts. None means not found/not fetched, not
    # necessarily "no account exists".
    account: Optional[str] = None
    offered_service: Any = None
    contract_status: Any = None


class DateAnomalySessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, DateAnomalyState] = {}

    def get(self, session_id: str) -> DateAnomalyState:
        with self._lock:
            return self._states.setdefault(session_id, DateAnomalyState())

    def set(self, session_id: str, state: DateAnomalyState) -> None:
        with self._lock:
            self._states[session_id] = state

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._states.pop(session_id, None)


date_anomaly_sessions = DateAnomalySessionStore()
