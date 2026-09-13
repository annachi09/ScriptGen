"""
Bulk Checker: find bulk accounts (GCCOM_ACCOUNT_BUNCHER groupings) that
don't have a generated lot/file yet for a billing cycle, and drill into
one bulk account to see every underlying bill for that cycle.

Provenance: ported into ScriptGen from the standalone EWA Bulk Checker
project (C:\\MISC_RMA\\ClaudeDev\\EWA_BulkChecker), per RJ's own request
(2026-09-12) to fold it in as a ScriptGen page rather than keep it as a
separate app - "the implementation is the same". The two queries below
are RJ's own analyst-supplied SQL, copied over unchanged in shape/logic
from that project's app/queries.py; see that file's own docstring for
the full column-by-column rationale, kept here only where it matters for
how the query is embedded.

The one real technical difference from the source project: EWA Bulk
Checker's app/db.py runs queries through pytds's own %s parameter
binding (real bind params, never string-formatted). ScriptGen's
app.db.mssql.run_query does NOT take parameters at all - every other
module in app/core (date_anomaly, hierarchy_analysis, script_generator)
builds a complete, literal SQL string via sql_format.format_sql_literal,
which escapes/quotes each value the same way a bind parameter would
(single quotes doubled, numbers/dates/bools formatted as real T-SQL
literals - see that module's docstring). This module follows that same
established ScriptGen convention instead of introducing a second query-
execution path: every place the original had a %s placeholder, this
version has format_sql_literal(...) inlined directly into the query
text at build time.

Deliberately pure logic, same convention as every other app/core module:
this only builds SELECT query text; it never executes anything itself.
The caller (web/server.py) runs it via app.db.mssql.run_query.
"""
from __future__ import annotations

import datetime
from typing import Optional

from .sql_format import format_sql_literal

STATUS_FILTERS = ("all", "pending", "generated", "missing_bill", "in_invoicing")
BILL_FILTERS = ("all", "pending", "missing")

# Same rationale as EWA's own MAX_BULK_ACCOUNTS - a guard on the bulk
# (multi-account) drill-down export, not a limit on the main search.
MAX_BULK_ACCOUNTS = 50

_STATUS_FILTER_CLAUSES = {
    "all": "",
    "pending": "AND BDET.file_number IS NULL",
    "generated": "AND BDET.file_number IS NOT NULL",
    "missing_bill": "AND ISNULL(BILLAGG.has_missing_bill, 0) = 1",
    "in_invoicing": "AND ISNULL(BILLAGG.has_bill_in_invoicing, 0) = 1",
}

_BILL_FILTER_CLAUSES = {
    "all": "",
    # A bill exists for this contracted service/period but hasn't been
    # picked up into a sent lot yet.
    "pending": "AND b.id_bill IS NOT NULL AND BL.FILE_NUMBER IS NULL",
    # No bill was ever generated for this contracted service this period.
    "missing": "AND b.id_bill IS NULL",
}


def build_pending_bulks_sql(
    billing_period: int,
    date_from: datetime.date,
    date_to: datetime.date,
    status_filter: str = "all",
) -> str:
    """
    One row per active contracted-service / bulk-account combination for
    the given billing cycle. `file_number` comes from a LEFT JOIN against
    GCCOM_BUNCHER_LOT(_DETAIL): a row where file_number is NULL means no
    lot/file has been generated yet for that bulk in this billing period
    - that's the "still pending" signal. Narrows to:
      - "pending"      - no lot/file generated yet for the bulk
      - "generated"    - a lot/file already exists for the bulk
      - "missing_bill" - at least one contracted service under the bulk
        has NO bill generated at all this period (bill-level, independent
        of whether a lot was ever created)
      - "in_invoicing" - at least one contracted service under the bulk
        has a bill that was generated but hasn't been picked up into a
        sent lot/file yet ("still being invoiced")
    The last two are computed by BILLAGG, a per-bulk-account aggregate
    that mirrors the same bill/notice/lot join chain used in
    build_bill_detail_sql (see there for the per-bill version of the
    same missing/pending logic), grouped up to one row per bulk account.
    """
    clause = _STATUS_FILTER_CLAUSES.get(status_filter, "")
    period_lit = format_sql_literal(billing_period)
    date_to_lit = format_sql_literal(date_to)
    date_from_lit = format_sql_literal(date_from)
    return f"""
SELECT DISTINCT
    ab.ID_PAYMENT_FORM_BUNCHER,
    pf2.NEXT_GROUP_DATE,
    pf2.reference AS ACCOUNT_NUMBER,
    pf2.IND_GROUP_BILLS,
    BDET.send_date,
    BDET.total_reg,
    BDET.total_amount,
    BDET.pending_amount,
    BDET.process_date,
    BDET.file_number,
    BDET.num_account,
    ISNULL(BILLAGG.has_missing_bill, 0) AS has_missing_bill,
    ISNULL(BILLAGG.has_bill_in_invoicing, 0) AS has_bill_in_invoicing
FROM GCCOM_ACCOUNT_BUNCHER ab
JOIN GCCOM_PAYMENT_FORM pf ON ab.ID_PAYMENT_FORM = pf.ID_PAYMENT_FORM
JOIN GCCOM_CONTRACTED_SERVICE cs ON cs.ID_PAYMENT_FORM = ab.ID_PAYMENT_FORM
JOIN GCCOM_PAYMENT_FORM pf2 ON pf2.ID_PAYMENT_FORM = ab.ID_PAYMENT_FORM_BUNCHER
LEFT JOIN (
    SELECT DISTINCT
        bl.ID_PAYMENT_FORM_BUNCHER,
        send_date,
        total_reg,
        bl.total_amount,
        bl.pending_amount,
        process_date,
        file_number,
        COUNT(DISTINCT b.id_payment_form) AS num_account
    FROM GCCOM_BUNCHER_LOT bl
    JOIN GCCOM_BUNCHER_LOT_DETAIL bld ON bld.ID_BUNCHER_LOT = bl.ID_BUNCHER_LOT
    JOIN gccom_bill b ON b.ID_BILL = bld.ID_BILL
    WHERE b.ID_BILLING_PERIOD = {period_lit}
    GROUP BY
        bl.ID_PAYMENT_FORM_BUNCHER,
        send_date,
        total_reg,
        bl.total_amount,
        bl.pending_amount,
        process_date,
        file_number
) BDET ON BDET.ID_PAYMENT_FORM_BUNCHER = ab.ID_PAYMENT_FORM_BUNCHER
LEFT JOIN (
    -- Per-bulk-account bill-level rollup, mirroring the exact
    -- bill/notice/lot join chain used in build_bill_detail_sql for a
    -- single account, but grouped across every contracted service
    -- under each bulk account so it can be joined back here as one
    -- row per bulk account (has_missing_bill / has_bill_in_invoicing
    -- are 1 if ANY underlying contracted service is in that state).
    SELECT
        ab2.ID_PAYMENT_FORM_BUNCHER,
        MAX(CASE WHEN b2.id_bill IS NULL THEN 1 ELSE 0 END) AS has_missing_bill,
        MAX(CASE WHEN b2.id_bill IS NOT NULL AND bl2.FILE_NUMBER IS NULL THEN 1 ELSE 0 END) AS has_bill_in_invoicing
    FROM GCCOM_ACCOUNT_BUNCHER ab2
    JOIN GCCOM_PAYMENT_FORM pf1b ON pf1b.ID_PAYMENT_FORM = ab2.ID_PAYMENT_FORM
    JOIN GCCOM_CONTRACTED_SERVICE cs2 ON cs2.ID_PAYMENT_FORM = ab2.ID_PAYMENT_FORM
    JOIN gccom_billing_service bs2 ON bs2.ID_CONTRACTED_SERVICE = cs2.id_contracted_service
    LEFT JOIN gccom_bill b2
        ON b2.id_payment_form = pf1b.id_payment_form
       AND b2.ID_BILLING_SERVICE = bs2.ID_BILLING_SERVICE
       AND b2.bill_type = 'TFGEN00001'
       AND b2.billing_status <> 'ESTFAC0007'
       AND b2.ID_BILLING_PERIOD = {period_lit}
       AND b2.billing_type IN ('TIPFAC0001', 'TIPFAC0002')
    LEFT JOIN GCCB_NOTICE_BILL nb2 ON nb2.id_bill = b2.id_bill
    LEFT JOIN gccb_notice n2 ON n2.id_notice = nb2.ID_NOTICE AND n2.ID_PAYMENT_FORM = pf1b.ID_PAYMENT_FORM
    LEFT JOIN GCCOM_BUNCHER_LOT_DETAIL bld2 ON bld2.ID_NOTICE = n2.ID_NOTICE
    LEFT JOIN GCCOM_BUNCHER_LOT bl2 ON bl2.ID_BUNCHER_LOT = bld2.ID_BUNCHER_LOT
    WHERE NOT EXISTS (
            SELECT cs3.nisc FROM GCCOM_CONTRACTED_SERVICE cs3
            WHERE cs3.id_sector_supply = cs2.id_sector_supply
              AND cs3.end_date > cs2.end_date)
      AND ab2.END_DATE IS NULL
      AND cs2.STATUS <> 'ESTSC00005'
      AND cs2.FROM_DATE < {date_to_lit}
      AND cs2.END_DATE >= {date_from_lit}
    GROUP BY ab2.ID_PAYMENT_FORM_BUNCHER
) BILLAGG ON BILLAGG.ID_PAYMENT_FORM_BUNCHER = ab.ID_PAYMENT_FORM_BUNCHER
WHERE cs.FROM_DATE < {date_to_lit}
  AND cs.END_DATE >= {date_from_lit}
  AND ab.END_DATE IS NULL
  AND cs.STATUS <> 'ESTSC00005'
  {clause}
"""


def build_bill_detail_sql(
    billing_period: int,
    date_from: datetime.date,
    date_to: datetime.date,
    account_number: str,
    bill_filter: str = "all",
) -> str:
    """
    Drill-down for one bulk account (pf2.reference): every bill under it
    for the same cycle/billing period, so an analyst can see exactly
    which underlying bills are pending on it. A row's `b.id_bill` is
    NULL when no bill was ever generated for that contracted service
    this period at all (distinct from a bill that exists but hasn't been
    sent yet). Narrows to bills awaiting a lot ("pending") or contracted
    services with no bill at all ("missing").

    ss.ID_SECTOR_SUPPLY is selected (added 2026-09-12, RJ's request) so
    the frontend can open the same reading-history popup Hierarchy
    Analysis uses (build_reading_history_query in that module - reused
    as-is, not duplicated here, since it's already a generic "readings
    for this supply" lookup keyed on nothing but the sector-supply id).
    """
    clause = _BILL_FILTER_CLAUSES.get(bill_filter, "")
    period_lit = format_sql_literal(billing_period)
    date_to_lit = format_sql_literal(date_to)
    date_from_lit = format_sql_literal(date_from)
    account_lit = format_sql_literal(account_number)
    return f"""
SELECT DISTINCT
    b.create_date,
    (SELECT COUNT(cs.ID_CONTRACTED_SERVICE)
     FROM GCCOM_CONTRACTED_SERVICE cs
     JOIN GCCOM_ACCOUNT_BUNCHER ab1 ON cs.ID_PAYMENT_FORM = ab1.ID_PAYMENT_FORM
     WHERE ab1.ID_PAYMENT_FORM_BUNCHER = ab.ID_PAYMENT_FORM_BUNCHER
       AND ab.END_DATE IS NULL
       AND cs.status IN ('ESTSC00002', 'ESTSC00003', 'ESTSC00007')) AS num_services,
    PF2.REFERENCE AS BULK_ACCOUNT,
    b.TOTAL_AMOUNT,
    b.BILL_TYPE,
    b.id_bill,
    CAST(pf2.next_group_date AS date) AS next_group_date,
    PF1.REFERENCE AS SUB_ACCOUNT,
    ss.niss,
    ss.ID_SECTOR_SUPPLY,
    CAST(cs.FROM_DATE AS date) AS Contract_start,
    CAST(cs.END_DATE AS date) AS Contract_end,
    b.bill_number,
    d.TEXT AS bill_status,
    CAST(b.creation_date AS date) AS creation_date,
    CAST(b.dispatch_coll_date AS date) AS invoice_date,
    b.ID_BILLING_PERIOD,
    n.NOTICE_NUMBER,
    n.id_notice,
    N.IND_PENDING_GROUP,
    CAST(n.SEND_DATE AS date) AS send_date_notice,
    BL.FILE_NUMBER,
    CAST(bl.SEND_DATE AS date) AS send_date_RV,
    CAST(BL.PROCESS_DATE AS date) AS process_date_RV,
    cs.ID_CONTRACTED_SERVICE,
    cs.status,
    pf2.update_date,
    pf2.update_program,
    b.billing_status,
    b.dispatch_coll_date
FROM ouc_admin.GCCOM_ACCOUNT_BUNCHER ab
JOIN ouc_common_admin.GCCOM_PAYMENT_FORM pf1 ON pf1.ID_PAYMENT_FORM = ab.ID_PAYMENT_FORM
JOIN ouc_common_admin.GCCOM_PAYMENT_FORM PF2 ON PF2.ID_PAYMENT_FORM = AB.ID_PAYMENT_FORM_BUNCHER
JOIN gccom_contracted_service cs ON cs.ID_PAYMENT_FORM = pf1.ID_PAYMENT_FORM
JOIN gccom_sector_supply ss ON ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
JOIN gccom_billing_service bs ON bs.ID_CONTRACTED_SERVICE = cs.id_contracted_service
LEFT JOIN gccom_bill b
    ON b.id_payment_form = pf1.id_payment_form
   AND b.ID_BILLING_SERVICE = bs.ID_BILLING_SERVICE
   AND b.bill_type = 'TFGEN00001'
   AND b.billing_status <> 'ESTFAC0007'
   AND b.ID_BILLING_PERIOD = {period_lit}
   AND b.billing_type IN ('TIPFAC0001', 'TIPFAC0002')
LEFT JOIN ouc_admin.GCCB_NOTICE_BILL nb ON nb.id_bill = b.id_bill
LEFT JOIN ouc_admin.gccb_notice n ON n.id_notice = nb.ID_NOTICE AND n.ID_PAYMENT_FORM = PF1.ID_PAYMENT_FORM
LEFT JOIN ouc_admin.GCCOM_BUNCHER_LOT_DETAIL BLD ON BLD.ID_NOTICE = N.ID_NOTICE
LEFT JOIN ouc_admin.GCCOM_BUNCHER_LOT bl ON bl.ID_BUNCHER_LOT = BLD.ID_BUNCHER_LOT
LEFT JOIN GCCOM_BILL_STATUS bst ON bst.cod_develop = b.BILLING_STATUS
LEFT JOIN GCCOM_BILLING_PERIOD bp ON bp.id_billing_period = b.ID_BILLING_PERIOD
LEFT JOIN GCTS_DICTIONARY d ON d.id = bst.NAME_TYPE_XI18N AND LOCALE = 'EN'
WHERE NOT EXISTS (
        SELECT cs2.nisc
        FROM gccom_contracted_service cs2
        WHERE cs2.id_sector_supply = cs.id_sector_supply
          AND cs2.end_date > cs.end_date)
  AND cs.status <> 'ESTSC00005'
  AND cs.FROM_DATE < {date_to_lit}
  AND cs.END_DATE >= {date_from_lit}
  AND pf2.reference = {account_lit}
  {clause}
ORDER BY d.TEXT ASC, pf2.reference, PF1.REFERENCE, ss.niss, b.ID_BILLING_PERIOD ASC, cs.status
"""


# Generic, read-only helper query for the "recent billing periods" picker
# in the UI - no assumption about which extra columns exist on
# GCCOM_BILLING_PERIOD beyond its id, so the frontend just renders
# whatever columns come back. If this table/column name doesn't match
# the real schema, the caller's route should fail soft and hide the
# picker - typing a billing period by hand always works regardless.
RECENT_BILLING_PERIODS_SQL = """
SELECT TOP 50 *
FROM GCCOM_BILLING_PERIOD
ORDER BY ID_BILLING_PERIOD DESC
"""
