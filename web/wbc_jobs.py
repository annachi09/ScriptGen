"""
Background scan for Wrong Billed Consumption (RJ, 2026-09-28 rework).

The bill-centric three-way comparison covers every bill of a billing period
(September 2026: 1,693,261 bills; the calculation-base sum alone took
~115s as one query), so one query can't fit the 120s timeout. The scan is
split into ID_BILL ranges (app/core/wrong_billed_consumption.bill_id_chunks)
and run on a background thread; the page polls for progress. One connection
is reused for the whole job (mssql.reuse_connection, same as
web/batch_jobs.py). In-memory only - a server restart drops running/finished
jobs, same trade-off as batch_jobs.py.
"""
from __future__ import annotations

import datetime
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import ConnectionConfig
from app.core import diff_engine, wrong_billed_consumption as wbc
from app.db import mssql

CHUNK_SIZE = 50_000   # bills of the period per query (chunk bounds = every Nth ID_BILL)
MAX_ROWS = 50_000     # safety cap on mismatches kept in memory
PARALLEL_CHUNKS = 3   # chunks scanned at the same time (RJ 2026-10-02 optimization)

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"


def _col(row: dict, name: str):
    for k, v in row.items():
        if k.lower() == name.lower():
            return v
    return None


@dataclass
class WbcJob:
    id: str
    created_by: str
    id_billing_period: int
    status: str = STATUS_RUNNING
    chunks_total: int = 0
    chunks_done: int = 0
    bill_count: int = 0
    rows: list[dict] = field(default_factory=list)
    truncated: bool = False
    error: Optional[str] = None
    started_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat(timespec="seconds"))
    finished_at: Optional[str] = None
    cancel_requested: bool = False

    def summary(self, include_rows: bool = False) -> dict[str, Any]:
        out = {
            "job_id": self.id,
            "id_billing_period": self.id_billing_period,
            "status": self.status,
            "chunks_total": self.chunks_total,
            "chunks_done": self.chunks_done,
            "bill_count": self.bill_count,
            "count": len(self.rows),
            "truncated": self.truncated,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
        if include_rows:
            out["rows"] = self.rows
            out["supply_count"] = len({r.get("niss") for r in self.rows})
        return out


class WbcJobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, WbcJob] = {}

    def add(self, job: WbcJob) -> None:
        with self._lock:
            # keep only the latest few jobs per user
            mine = sorted((j for j in self._jobs.values() if j.created_by == job.created_by),
                          key=lambda j: j.started_at)
            for old in mine[:-4]:
                self._jobs.pop(old.id, None)
            self._jobs[job.id] = job

    def get(self, job_id: str) -> Optional[WbcJob]:
        with self._lock:
            return self._jobs.get(job_id)


jobs = WbcJobStore()


def _rows(result) -> list[dict]:
    return [{c.lower(): diff_engine.cell_display(v) for c, v in zip(result.columns, r)} for r in result.rows]


def _run(job: WbcJob, conn: ConnectionConfig, chunk_size: int) -> None:
    try:
        with mssql.reuse_connection(conn):
            rng = mssql.run_query(conn, wbc.build_bill_range_query(job.id_billing_period))
            first = dict(zip(rng.columns, rng.rows[0])) if rng.rows else {}
            hi = _col(first, "MAX_ID_BILL")
            job.bill_count = int(_col(first, "BILL_COUNT") or 0)
            chunks = []
            if hi is not None:
                # equal-sized chunks by bill COUNT (ids aren't evenly spread)
                bnd = mssql.run_query(conn, wbc.build_bill_boundaries_query(job.id_billing_period, chunk_size))
                chunks = wbc.chunks_from_boundaries([r[0] for r in bnd.rows], int(hi))
            job.chunks_total = len(chunks)
            # Query optimization (RJ 2026-10-02): the chunks are independent
            # ID_BILL ranges, so PARALLEL_CHUNKS of them run at once (each
            # worker thread opens its own connection). Same SQL per chunk;
            # rows are merged and sorted at the end exactly as before.
            lock = threading.Lock()

            def _chunk(bounds):
                if job.cancel_requested:
                    return
                c_lo, c_hi = bounds
                res = mssql.run_query(conn, wbc.build_wrong_billed_consumption_query(job.id_billing_period, c_lo, c_hi))
                rows = _rows(res)
                with lock:
                    job.rows.extend(rows)
                    if len(job.rows) > MAX_ROWS:
                        job.rows.sort(key=lambda r: -abs(_num(r.get("diff_calc_vs_rit"))))
                        job.rows = job.rows[:MAX_ROWS]
                        job.truncated = True
                    job.chunks_done += 1

            with ThreadPoolExecutor(max_workers=PARALLEL_CHUNKS) as pool:
                for fut in [pool.submit(_chunk, b) for b in chunks]:
                    fut.result()  # re-raises the first failure
            if job.cancel_requested:
                job.status = STATUS_CANCELLED
            if job.status == STATUS_RUNNING:
                job.status = STATUS_DONE
    except Exception as exc:  # noqa: BLE001 - surfaced to the page
        job.error = str(exc)
        job.status = STATUS_FAILED
    finally:
        job.rows.sort(key=lambda r: -abs(_num(r.get("diff_calc_vs_rit"))))
        job.finished_at = datetime.datetime.now().isoformat(timespec="seconds")


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def start(conn: ConnectionConfig, id_billing_period: int, username: str, chunk_size: int = CHUNK_SIZE) -> WbcJob:
    job = WbcJob(id=uuid.uuid4().hex, created_by=username, id_billing_period=int(id_billing_period))
    jobs.add(job)
    threading.Thread(target=_run, args=(job, conn, chunk_size), daemon=True,
                     name=f"wbc-{job.id[:8]}").start()
    return job
