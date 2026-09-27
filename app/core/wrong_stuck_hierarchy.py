"""
Wrong Stuck in Hierarchy ITB - "HIERARCHY BUT BILLED PRIMARY".

RJ, 2026-09-25: "create a new menu, Wrong Stuck in Hierarchy ITB, you
need to supply id_billing_period 1 at a time to check correctly per
period" + RJ's own SQL (period hard-coded as 10000000236 in 3 places):

    select pf.REFERENCE, ss.niss, itb.ID_ITEM_TO_BILL, mp.ID_MAIN_MP,
           bp.DESCRIPTION, itb1.ID_ITEM_TO_BILL, itb1.STATUS
    from GCCOM_ITEMS_TO_BILL itb
      join GCCOM_BILLING_SERVICE bs on bs.ID_BILLING_SERVICE = itb.ID_BILLING_SERVICE
      join GCCOM_CONTRACTED_SERVICE cs on cs.ID_CONTRACTED_SERVICE = bs.ID_CONTRACTED_SERVICE
           and cs.STATUS in ('ESTSC00002', 'ESTSC00003', 'ESTSC00007')
      join GCCOM_SECTOR_SUPPLY ss on ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
      join GCGT_RE_MEASUREMENT_POINT mp on mp.ID_SECTOR_SUPPLY = ss.ID_SECTOR_SUPPLY
      join GCCOM_PAYMENT_FORM pf on pf.ID_PAYMENT_FORM = cs.ID_PAYMENT_FORM
      join GCCOM_BILLING_PERIOD bp on bp.ID_BILLING_PERIOD = itb.ID_BILLING_PERIOD
      left join GCCOM_CONTRACTED_SERVICE cs1 on cs1.ID_PAYMENT_FORM = pf.ID_PAYMENT_FORM
           and cs1.ID_OFFERED_SERVICE = 190
      left join GCCOM_BILLING_SERVICE bs1 on bs1.ID_CONTRACTED_SERVICE = cs1.ID_CONTRACTED_SERVICE
      left join GCCOM_ITEMS_TO_BILL itb1 on itb1.ID_BILLING_SERVICE = bs1.ID_BILLING_SERVICE
           and itb1.ID_BILLING_PERIOD = 10000000236
    where itb.STATUS = 'STTOBILL09'
      and itb.ID_BILLING_PERIOD = 10000000236
      and mp.ID_MAIN_MP in (<measuring points of STTOBILL07 items in the same
                              period, same contract-status filter>)
    order by mp.ID_MAIN_MP desc;

Reading of it: a secondary (itb, STTOBILL09 = held in hierarchy) whose
measurement point's MAIN (primary) MP belongs to a supply whose item in
the SAME period is already STTOBILL07 (billed) - i.e. the secondary is
still stuck waiting on a primary that has already been billed. The
offered-service-190 item on the same account (itb1) is shown alongside.

Follow-up, same day: "list all the items without providing the billing
period, so initial search should not have any param". So the default
scan covers every period, and RJ's "per period" correctness moves into
the joins: the billed primary and the service-190 item must be in the
SAME period as the stuck item (see build_wrong_stuck_query).

Changes from RJ's text, all mechanical:
  1. The hard-coded period becomes "same period as the stuck item"
     (optionally narrowed to one id).
  2. Duplicate output names aliased: itb1.ID_ITEM_TO_BILL ->
     ID_ITEM_TO_BILL_190, itb1.STATUS -> STATUS_190 (this app shapes rows
     with dict(zip(columns, row)), so two ID_ITEM_TO_BILL columns would
     collapse into one). Every column gets an explicit alias.
  3. `mp.ID_MAIN_MP IN (subquery)` is written as a JOIN to a DISTINCT CTE
     of the same subquery (`primary_mp`). Same result set (DISTINCT keeps
     the join from multiplying rows); written this way because RJ's
     literal version ran past the app's 120s query timeout on first try.
  4. The subquery's own unused joins are kept exactly as RJ wrote them
     (they carry the contract-status filter, which does change results).

Pure logic, same convention as every other app/core module.
"""
from __future__ import annotations

from .sql_format import format_sql_literal

ITB_STATUS_STUCK_IN_HIERARCHY = "STTOBILL09"
ITB_STATUS_PRIMARY_BILLED = "STTOBILL07"
CONTRACT_STATUSES = ("ESTSC00002", "ESTSC00003", "ESTSC00007")  # Active, Pending termination, Suspended
PARTNER_OFFERED_SERVICE = 190


def build_wrong_stuck_query(id_billing_period: int | None = None) -> str:
    """All periods at once by default (RJ, 2026-09-25: "list all the items
    without providing the billing period, so initial search should not
    have any para"). RJ's per-period rule is kept by matching on the
    period instead of hard-coding it: the billed primary (primary_mp) and
    the service-190 item (itb1) must be in the SAME billing period as the
    stuck secondary's own item - exactly what running RJ's query once per
    period gave, just for every period in one pass. Passing
    id_billing_period still narrows to one period."""
    stuck = format_sql_literal(ITB_STATUS_STUCK_IN_HIERARCHY)
    billed = format_sql_literal(ITB_STATUS_PRIMARY_BILLED)
    statuses = ", ".join(format_sql_literal(s) for s in CONTRACT_STATUSES)
    if id_billing_period is not None:
        bp = format_sql_literal(int(id_billing_period))
        period_primary = f"\n      AND itb.ID_BILLING_PERIOD = {bp}"
        period_main = f"\n  AND itb.ID_BILLING_PERIOD = {bp}"
    else:
        period_primary = ""
        period_main = ""
    return f"""
WITH primary_mp AS (
    SELECT DISTINCT mp.ID_MEASURING_POINT, itb.ID_BILLING_PERIOD
    FROM GCCOM_ITEMS_TO_BILL itb
    JOIN GCCOM_BILLING_SERVICE bs ON bs.ID_BILLING_SERVICE = itb.ID_BILLING_SERVICE
    JOIN GCCOM_CONTRACTED_SERVICE cs ON cs.ID_CONTRACTED_SERVICE = bs.ID_CONTRACTED_SERVICE
        AND cs.STATUS IN ({statuses})
    JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
    JOIN GCGT_RE_MEASUREMENT_POINT mp ON mp.ID_SECTOR_SUPPLY = ss.ID_SECTOR_SUPPLY
    WHERE itb.STATUS = {billed}{period_primary}
)
SELECT
    pf.REFERENCE AS REFERENCE,
    ss.NISS AS NISS,
    itb.ID_ITEM_TO_BILL AS ID_ITEM_TO_BILL,
    mp.ID_MAIN_MP AS ID_MAIN_MP,
    itb.ID_BILLING_PERIOD AS ID_BILLING_PERIOD,
    bp.DESCRIPTION AS DESCRIPTION,
    itb1.ID_ITEM_TO_BILL AS ID_ITEM_TO_BILL_190,
    itb1.STATUS AS STATUS_190
FROM GCCOM_ITEMS_TO_BILL itb
JOIN GCCOM_BILLING_SERVICE bs ON bs.ID_BILLING_SERVICE = itb.ID_BILLING_SERVICE
JOIN GCCOM_CONTRACTED_SERVICE cs ON cs.ID_CONTRACTED_SERVICE = bs.ID_CONTRACTED_SERVICE
    AND cs.STATUS IN ({statuses})
JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
JOIN GCGT_RE_MEASUREMENT_POINT mp ON mp.ID_SECTOR_SUPPLY = ss.ID_SECTOR_SUPPLY
JOIN primary_mp pmp ON pmp.ID_MEASURING_POINT = mp.ID_MAIN_MP
    AND pmp.ID_BILLING_PERIOD = itb.ID_BILLING_PERIOD
JOIN GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = cs.ID_PAYMENT_FORM
JOIN GCCOM_BILLING_PERIOD bp ON bp.ID_BILLING_PERIOD = itb.ID_BILLING_PERIOD
LEFT JOIN GCCOM_CONTRACTED_SERVICE cs1 ON cs1.ID_PAYMENT_FORM = pf.ID_PAYMENT_FORM
    AND cs1.ID_OFFERED_SERVICE = {PARTNER_OFFERED_SERVICE}
LEFT JOIN GCCOM_BILLING_SERVICE bs1 ON bs1.ID_CONTRACTED_SERVICE = cs1.ID_CONTRACTED_SERVICE
LEFT JOIN GCCOM_ITEMS_TO_BILL itb1 ON itb1.ID_BILLING_SERVICE = bs1.ID_BILLING_SERVICE
    AND itb1.ID_BILLING_PERIOD = itb.ID_BILLING_PERIOD
WHERE itb.STATUS = {stuck}{period_main}
ORDER BY itb.ID_BILLING_PERIOD DESC, mp.ID_MAIN_MP DESC
"""
