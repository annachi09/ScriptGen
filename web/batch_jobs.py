"""
Background job runner for the Date Anomaly "Batch / Multi-NISS" page.

Why a background job instead of just doing the work inside the request
handler (which is what the first cut of /api/date-anomaly/batch did):
"massively generate for a given set of supplies" implies a NISS list
long enough that Detect->Resolve->Generate for all of them, one at a
time against a real tunnel-hopped SQL Server, could take minutes - long
enough to risk a browser/reverse-proxy timeout, and long enough that the
user should be able to see per-NISS progress rather than stare at a
spinner. So the HTTP route that kicks this off (web/server.py's
date_anomaly_batch_start) returns almost immediately with a job id, a
background thread does the actual DB work, and the frontend polls
GET /api/date-anomaly/batch/{job_id} for progress until it's done.

Threading note: app.db.mssql.run_query() takes a ConnectionConfig
(plain data - server/user/password/etc, not a live connection/socket),
so handing the same ConnectionConfig to a background thread is always
safe - there's no shared live connection object being touched from two
threads at once. By DEFAULT run_query also opens+closes its own fresh
pytds connection on every single call, which is the right choice for
every other page's one-off queries, but was the actual reason Batch
felt slow (RJ, 2026-09-13): each NISS is ~6 queries, and when the DB is
reached through a local tunnel, the connection HANDSHAKE dwarfs the
query's own execution time - a 50-NISS batch was paying that handshake
cost ~300 times over. _run_batch below wraps the whole job in
mssql.reuse_connection(conn_cfg), which opens ONE connection for this
thread and makes every run_query()/get_table_columns() call
transparently reuse it (see that function's own docstring in
app/db/mssql.py) - run_query's call sites here didn't need to change at
all. It also self-heals if the tunnel drops mid-run (see run_query's
reconnect-and-retry logic) instead of every remaining NISS in the batch
failing off a dead connection.

Same in-memory-only trade-off as web/session_store.py's SessionStore:
fine for "a few teammates against one server process" (the target for
this phase), lost on a server restart (a running/finished job simply
disappears - same as closing the desktop app mid-run), and NOT safe if
this ever runs behind multiple server processes/workers without moving
to a shared store - a contained change later if it's ever needed.
"""
from __future__ import annotations

import datetime
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import ConnectionConfig
from app.core import date_anomaly
from app.db import date_anomaly_history, mssql, script_history

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED = "failed"  # batch-wide failure before any NISS could be processed (e.g. bad connection)


def _col(row: dict, name: str):
    """Case-insensitive dict lookup - mirrors the identical helper in
    web/server.py and app/ui/main_window.py (same reasoning: pytds
    column names come back with whatever case the DB used)."""
    for k, v in row.items():
        if k.lower() == name.lower():
            return v
    return None


@dataclass
class BatchJob:
    id: str
    created_by: str
    created_at_utc: str
    niss_list: list[str]
    threshold: int
    program: str
    clean: bool = False
    lowest_billing_period_only: bool = False  # see app/core/date_anomaly.py's lowest_billing_period_rows()
    status: str = STATUS_QUEUED
    processed: int = 0
    results: list[dict] = field(default_factory=list)   # per-NISS summary dicts, appended as each finishes
    combined_sql: Optional[str] = None
    cancel_requested: bool = False
    error: Optional[str] = None  # only set on a STATUS_FAILED batch-wide failure
    # Set when the background thread actually starts running (not when the
    # job is queued - created_at_utc already covers that), and when it
    # reaches a terminal status. The frontend uses the gap between
    # started_at_utc and "now" (while running) or finished_at_utc (once
    # done) to show elapsed time / items-per-second / an ETA - RJ, 2026-09-13:
    # "show how many processed and how many pending and percentage, with
    # item per second processed".
    started_at_utc: Optional[str] = None
    finished_at_utc: Optional[str] = None

    @property
    def niss_total(self) -> int:
        return len(self.niss_list)

    def to_public_dict(self) -> dict:
        return {
            "job_id": self.id,
            "created_by": self.created_by,
            "created_at_utc": self.created_at_utc,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "niss_total": self.niss_total,
            "threshold": self.threshold,
            "program": self.program,
            "clean": self.clean,
            "lowest_billing_period_only": self.lowest_billing_period_only,
            "status": self.status,
            "processed": self.processed,
            "results": self.results,
            "combined_sql": self.combined_sql,
            "error": self.error,
        }


class BatchJobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, BatchJob] = {}

    def create(
        self, *, created_by: str, niss_list: list[str], threshold: int, program: str, clean: bool = False,
        lowest_billing_period_only: bool = False,
    ) -> BatchJob:
        job = BatchJob(
            id=uuid.uuid4().hex[:12],
            created_by=created_by,
            created_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            niss_list=niss_list,
            threshold=threshold,
            program=program,
            clean=clean,
            lowest_billing_period_only=lowest_billing_period_only,
        )
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[BatchJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_for_user(self, username: str, limit: int = 20) -> list[BatchJob]:
        with self._lock:
            mine = [j for j in self._jobs.values() if j.created_by == username]
        mine.sort(key=lambda j: j.created_at_utc, reverse=True)
        return mine[:limit]

    def request_cancel(self, job_id: str) -> bool:
        """Marks the job for cancellation before its NEXT NISS starts -
        does not interrupt a NISS already in flight (its own DB queries
        run to completion; only the loop over remaining NISS stops).
        Returns False if the job doesn't exist or has already finished."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in (STATUS_QUEUED, STATUS_RUNNING):
                return False
            job.cancel_requested = True
            return True


# Single process-wide instance - see the module docstring's trade-off note.
jobs = BatchJobStore()


def start_batch(
    *, created_by: str, conn_cfg: ConnectionConfig, niss_list: list[str], threshold: int, program: str,
    internal_db_path: str, clean: bool = False, lowest_billing_period_only: bool = False,
) -> BatchJob:
    """Creates a job record immediately and hands the actual DB work off
    to a daemon thread, so the caller (the FastAPI route) can return the
    job id right away instead of blocking on the whole batch."""
    job = jobs.create(
        created_by=created_by, niss_list=niss_list, threshold=threshold, program=program, clean=clean,
        lowest_billing_period_only=lowest_billing_period_only,
    )
    thread = threading.Thread(
        target=_run_batch, args=(job, conn_cfg, internal_db_path), daemon=True, name=f"date-anomaly-batch-{job.id}",
    )
    thread.start()
    return job


def _run_batch(job: BatchJob, conn_cfg: ConnectionConfig, internal_db_path: str) -> None:
    job.status = STATUS_RUNNING
    job.started_at_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()

    results: list[date_anomaly.BatchNissResult] = []
    try:
        # One shared connection for the ENTIRE job (schema check + every
        # NISS) instead of one fresh connection per query - see
        # mssql.reuse_connection's own docstring for the full reasoning.
        # This is the actual fix for "batch is slow": through a local
        # tunnel, the connection handshake (not the query) is what
        # dominates, and unbatched this was paying that cost ~6 times per
        # NISS. mssql.run_query's signature/call sites below are
        # completely unchanged - reuse is transparent to every caller.
        with mssql.reuse_connection(conn_cfg):
            # Audit-column presence is a schema fact, not a per-NISS one -
            # check once, up front. A failure here means the
            # connection/schema itself is unreachable, so the whole job
            # fails rather than each NISS reporting the identical error.
            reading_cols = mssql.get_table_columns(conn_cfg, date_anomaly.READING_SCHEMA, date_anomaly.READING_TABLE)
            item_cols = mssql.get_table_columns(conn_cfg, date_anomaly.ADMIN_SCHEMA, date_anomaly.ITEMS_TO_BILL_TABLE)
            reading_has_audit = "update_program" in {c.lower() for c in reading_cols}
            item_has_audit = "update_program" in {c.lower() for c in item_cols}

            for niss in job.niss_list:
                if job.cancel_requested:
                    job.status = STATUS_CANCELLED
                    break

                result = _process_one_niss(
                    conn_cfg, niss, job.threshold, job.program, reading_has_audit, item_has_audit,
                    lowest_billing_period_only=job.lowest_billing_period_only,
                )
                results.append(result)
                job.processed += 1
                job.results.append({
                    "niss": result.niss,
                    "status": result.status,
                    "reading_count": result.reading_count,
                    "item_count": result.item_count,
                    "xml_count": result.xml_count,
                    "anomalous_count": result.anomalous_count,
                    "item_status_count": result.item_status_count,
                    "orphan_reading_count": result.orphan_reading_count,
                    "billing_period_count": result.billing_period_count,
                    "scoped_to_lowest_period": result.scoped_to_lowest_period,
                    "warning_count": len(result.warnings),
                    "error": result.error,
                })

                if result.status == date_anomaly.STATUS_OK:
                    try:
                        script_history.record_script(
                            internal_db_path,
                            username=job.created_by,
                            kind=script_history.KIND_DATE_ANOMALY,
                            schema_name="",
                            table_name=f"NISS {niss} (batch)",
                            program=job.program,
                            statement_count=(
                                result.reading_count + result.item_count + result.xml_count
                                + result.anomalous_count + result.item_status_count
                                + (2 if result.orphan_reading_count else 0)
                            ),
                            warning_count=len(result.warnings),
                            sql_text=result.sql_text,
                            source=script_history.SOURCE_WEB,
                        )
                    except Exception:
                        # Same stance as every other Script History write in
                        # this app: a record of the event, never a
                        # precondition for seeing the script.
                        pass

                if result.status in (date_anomaly.STATUS_OK, date_anomaly.STATUS_NO_ANOMALIES):
                    # One-shot record (see app.db.date_anomaly_history's
                    # record_analysis docstring) - batch already has every
                    # field known by the time a NISS finishes, unlike the
                    # single-NISS web flow which records at Detect and
                    # refines afterward. ERROR results are intentionally
                    # not recorded here - job.results/combined_sql already
                    # surface those, and there's no reliable anomaly_count
                    # to attach to a NISS that failed before Detect could
                    # even determine one.
                    try:
                        date_anomaly_history.record_analysis(
                            internal_db_path,
                            username=job.created_by, niss=niss, threshold=job.threshold,
                            anomaly_count=result.reading_count,
                            item_count=result.item_count, xml_count=result.xml_count,
                            item_status_count=result.item_status_count, anomalous_count=result.anomalous_count,
                            generated=(result.status == date_anomaly.STATUS_OK),
                            explanation=result.explanation,
                            source=date_anomaly_history.SOURCE_BATCH,
                        )
                    except Exception:
                        pass
            else:
                # Loop finished without hitting the cancel `break` above.
                job.status = STATUS_DONE
    except mssql.ConnectionError_ as exc:
        # The shared connection itself couldn't be opened (or the
        # up-front schema check failed) - whatever's in `results` so far
        # (possibly none) still becomes a valid, if incomplete, script.
        job.error = str(exc)
        job.status = STATUS_FAILED

    job.combined_sql = date_anomaly.build_batch_script(results, program=job.program, clean=job.clean)
    job.finished_at_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()


def _process_one_niss(
    conn_cfg: ConnectionConfig, niss: str, threshold: int, program: str,
    reading_has_audit: bool, item_has_audit: bool, lowest_billing_period_only: bool = False,
) -> date_anomaly.BatchNissResult:
    """One NISS's full Detect -> Resolve -> Generate pass. Mirrors
    web/server.py's (now-removed) synchronous /api/date-anomaly/batch
    route body exactly - kept here instead so it can run inside this
    module's background thread.

    lowest_billing_period_only: analyst request, applies the same
    date_anomaly.lowest_billing_period_rows() filter web/server.py's
    single-NISS date_anomaly_generate route uses - see that function's
    docstring. Applied to `rows` (this NISS's full anomaly rows) AFTER
    billing_period_count is computed from the unfiltered set (so the
    reported count/orange-flag still reflects the true total), but
    BEFORE id_readings/orphan_usage_ids are derived - every downstream
    part of this NISS's script only ever covers the earliest billing
    period's reading(s) when this is True. No-op when the NISS only has
    one billing period to begin with (lowest_billing_period_rows returns
    every row unchanged in that case)."""
    try:
        detect_result = mssql.run_query(conn_cfg, date_anomaly.build_detect_query(niss, threshold))
    except mssql.ConnectionError_ as exc:
        return date_anomaly.BatchNissResult(niss=niss, status=date_anomaly.STATUS_ERROR, error=str(exc))

    rows = [dict(zip(detect_result.columns, r)) for r in detect_result.rows]
    if not rows:
        return date_anomaly.BatchNissResult(niss=niss, status=date_anomaly.STATUS_NO_ANOMALIES)

    try:
        correct_result = mssql.run_query(conn_cfg, date_anomaly.build_correct_date_query(niss, threshold))
    except mssql.ConnectionError_ as exc:
        return date_anomaly.BatchNissResult(niss=niss, status=date_anomaly.STATUS_ERROR, error=str(exc))
    if not correct_result.rows:
        return date_anomaly.BatchNissResult(
            niss=niss, status=date_anomaly.STATUS_ERROR,
            error="Anomalous readings found, but no correctly-billed (7000STSRED) reading exists above "
                  "the billing period floor to source the correct date from.",
        )

    crow = dict(zip(correct_result.columns, correct_result.rows[0]))
    correct_date = _col(crow, "READING_DATE")
    correct_date_reading = _col(crow, "ID_READING")
    # Analyst request - see web/server.py's date_anomaly_detect route for
    # the same computation on the single-NISS path. Computed from the
    # FULL (unfiltered) row set, before lowest_billing_period_only is
    # applied below, so this always reflects the true total even when
    # the script itself ends up scoped to just one of them.
    billing_period_count = len({_col(r, "ID_BILLING_PERIOD") for r in rows})
    scoped_rows = date_anomaly.lowest_billing_period_rows(rows) if lowest_billing_period_only else rows
    id_readings = [_col(r, "ID_READING") for r in scoped_rows]
    # Part 6 candidates - see web/server.py's date_anomaly_generate route
    # for the same filter and date_anomaly.READING_TYPE_ORPHAN_USAGE for
    # the rule. scoped_rows already has READING_TYPE/READ_STATUS from
    # build_detect_query - no extra query needed.
    orphan_usage_ids = [
        _col(r, "ID_READING") for r in scoped_rows
        if _col(r, "READING_TYPE") == date_anomaly.READING_TYPE_ORPHAN_USAGE
        and _col(r, "READ_STATUS") == date_anomaly.READ_STATUS_ANOMALY
    ]

    try:
        item_map: dict[Any, list[Any]] = {}
        item_sql = date_anomaly.build_item_to_bill_query(id_readings)
        if item_sql:
            item_result = mssql.run_query(conn_cfg, item_sql)
            for r in item_result.rows:
                row = dict(zip(item_result.columns, r))
                reading_id = _col(row, "ID_READING")
                item_id = _col(row, "ID_ITEM_TO_BILL")
                if item_id is None:
                    continue
                item_map.setdefault(reading_id, [])
                if item_id not in item_map[reading_id]:
                    item_map[reading_id].append(item_id)

        item_ids = sorted({v for ids in item_map.values() for v in ids}, key=str)
        # RJ, 2026-09-13: "you are updating using id_item_to_bill, you
        # need to get first the id_xml from gccom_item_to_bill and
        # update with that id" - same fix as web/server.py's
        # date_anomaly_resolve, needed here too since batch runs its
        # own copy of the resolve step.
        item_to_xml_map: dict = {}
        item_xml_id_sql = date_anomaly.build_item_xml_id_query(item_ids)
        if item_xml_id_sql:
            item_xml_id_result = mssql.run_query(conn_cfg, item_xml_id_sql)
            for r in item_xml_id_result.rows:
                row = dict(zip(item_xml_id_result.columns, r))
                id_xml_val = _col(row, "ID_XML")
                if id_xml_val is not None:
                    item_to_xml_map[_col(row, "ID_ITEM_TO_BILL")] = id_xml_val

        xml_rows: dict = {}
        real_id_xmls = sorted({v for v in item_to_xml_map.values()}, key=str)
        xml_sql = date_anomaly.build_xml_lookup_query(real_id_xmls)
        if xml_sql:
            xml_result = mssql.run_query(conn_cfg, xml_sql)
            for r in xml_result.rows:
                row = dict(zip(xml_result.columns, r))
                xml_rows[_col(row, "ID_XML")] = _col(row, date_anomaly.XML_TO_BILL_COLUMN)

        # Part 5 candidates - item-to-bill ids with an OPEN anomaly
        # (ESTAN00009/ESTAN00001) to cancel. Same lookup as the
        # single-NISS route in web/server.py's date_anomaly_resolve.
        anomalous_item_ids: list = []
        anomalous_sql = date_anomaly.build_anomalous_query(item_ids)
        if anomalous_sql:
            anomalous_result = mssql.run_query(conn_cfg, anomalous_sql)
            seen_anomalous: set = set()
            for r in anomalous_result.rows:
                row = dict(zip(anomalous_result.columns, r))
                aid = _col(row, "ID_ITEM_TO_BILL")
                if aid is not None and aid not in seen_anomalous:
                    seen_anomalous.add(aid)
                    anomalous_item_ids.append(aid)

        # Part 3 candidates - item-to-bill ids still at STTOBILL00, to
        # advance to STTOBILL01. Same lookup as the single-NISS route.
        item_status_ids: list = []
        item_status_sql = date_anomaly.build_item_status_query(item_ids)
        if item_status_sql:
            item_status_result = mssql.run_query(conn_cfg, item_status_sql)
            seen_item_status: set = set()
            for r in item_status_result.rows:
                row = dict(zip(item_status_result.columns, r))
                sid_val = _col(row, "ID_ITEM_TO_BILL")
                if sid_val is not None and sid_val not in seen_item_status:
                    seen_item_status.add(sid_val)
                    item_status_ids.append(sid_val)
    except mssql.ConnectionError_ as exc:
        return date_anomaly.BatchNissResult(niss=niss, status=date_anomaly.STATUS_ERROR, error=str(exc))

    script = date_anomaly.build_correction_script(
        niss=niss, threshold=threshold, correct_date=correct_date,
        correct_date_source_reading=correct_date_reading,
        anomaly_id_readings=id_readings, item_to_bill_map=item_map, xml_rows=xml_rows,
        item_to_xml_map=item_to_xml_map,
        anomalous_item_ids=anomalous_item_ids,
        item_status_ids=item_status_ids,
        orphan_usage_id_readings=orphan_usage_ids,
        billing_period_count=billing_period_count,
        scoped_to_lowest_period=lowest_billing_period_only,
        reading_has_audit_cols=reading_has_audit, item_has_audit_cols=item_has_audit,
        program=program,
    )
    return date_anomaly.BatchNissResult(
        niss=niss, status=date_anomaly.STATUS_OK,
        reading_count=script.reading_count, item_count=script.item_count, xml_count=script.xml_count,
        anomalous_count=script.anomalous_count,
        item_status_count=script.item_status_count,
        orphan_reading_count=script.orphan_reading_count,
        billing_period_count=script.billing_period_count,
        scoped_to_lowest_period=script.scoped_to_lowest_period,
        explanation=script.explanation,
        warnings=script.warnings, sql_text=script.sql_text,
    )
