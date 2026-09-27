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


# --- Correction script (RJ, 2026-09-27) -------------------------------
# "for wrong stuck in hierarchy, create an update script. for all ITB
# that is in status STTOBILL09:
#   update GCCOM_ITEMS_TO_BILL
#   set STATUS = 'STTOBILL01', UPDATE_PROGRAM = 'WRONG_ITB_HIERARCHY_STATUS',
#       UPDATE_USER = 'RMA', UPDATE_DATE = getdate()
#   where ID_ITEM_TO_BILL in (<ITEMS TO BILL: column ID_ITEM_TO_BILL_STUCK
#                              and ID_ITEM_TO_BILL SERVICE 190>)
#     and STATUS = 'STTOBILL09';"
# The id list = every selected row's stuck item plus its service-190 item
# when that item is also STTOBILL09 (an item in any other status would be
# a no-op under RJ's own STATUS guard, so it's simply left out). Ids are
# de-duplicated (one 190 item can sit next to several stuck secondaries on
# the same account) and split into chunks of CORRECTION_CHUNK_SIZE per
# UPDATE so the IN list stays a sane size.
ITB_STATUS_RELEASED = "STTOBILL01"
CORRECTION_SCRIPT_DEFAULT_PROGRAM = "WRONG_ITB_HIERARCHY_STATUS"
CORRECTION_SCRIPT_DEFAULT_USER = "RMA"
CORRECTION_CHUNK_SIZE = 500


def collect_correction_item_ids(rows: list[dict]) -> list:
    """rows: detection rows with keys ID_ITEM_TO_BILL, ID_ITEM_TO_BILL_190,
    STATUS_190 (any case). Returns unique ids in first-seen order."""
    def get(r, k):
        for key, val in r.items():
            if key.upper() == k:
                return val
        return None

    seen, ids = set(), []
    for r in rows:
        stuck = get(r, "ID_ITEM_TO_BILL")
        partner = get(r, "ID_ITEM_TO_BILL_190")
        partner_status = get(r, "STATUS_190")
        candidates = [stuck]
        if partner is not None and str(partner_status or "").strip() == ITB_STATUS_STUCK_IN_HIERARCHY:
            candidates.append(partner)
        for c in candidates:
            if c is None or str(c).strip() == "":
                continue
            if str(c) not in seen:
                seen.add(str(c))
                ids.append(c)
    return ids


def build_correction_script(item_ids: list, *, program: str = CORRECTION_SCRIPT_DEFAULT_PROGRAM,
                            user: str = CORRECTION_SCRIPT_DEFAULT_USER, clean: bool = False) -> str:
    if not item_ids:
        raise ValueError("No items to bill to update.")
    released = format_sql_literal(ITB_STATUS_RELEASED)
    stuck = format_sql_literal(ITB_STATUS_STUCK_IN_HIERARCHY)
    prog = format_sql_literal(program)
    usr = format_sql_literal(user)
    parts = []
    if not clean:
        parts.append(
            f"-- Wrong Stuck in Hierarchy ITB: {len(item_ids)} item(s) to bill "
            f"{ITB_STATUS_STUCK_IN_HIERARCHY} -> {ITB_STATUS_RELEASED}\n"
            f"-- (stuck secondaries + their service-{PARTNER_OFFERED_SERVICE} items that are also "
            f"{ITB_STATUS_STUCK_IN_HIERARCHY})\n"
        )
    for start in range(0, len(item_ids), CORRECTION_CHUNK_SIZE):
        chunk = item_ids[start:start + CORRECTION_CHUNK_SIZE]
        id_list = ",\n       ".join(format_sql_literal(i) for i in chunk)
        parts.append(
            "update GCCOM_ITEMS_TO_BILL\n"
            f"set STATUS         = {released},\n"
            f"    UPDATE_PROGRAM = {prog},\n"
            f"    UPDATE_USER    = {usr},\n"
            "    UPDATE_DATE    = getdate()\n"
            "where ID_ITEM_TO_BILL in\n"
            f"      ({id_list})\n"
            f"  and STATUS = {stuck};\n"
        )
    return "\n".join(parts)


# --- Tab 2: Sanitary stuck, Water billed/pending (RJ, 2026-09-27) ------
# "find the ones where the sanitary service is in hierarchy status, but
# the water is already billed or pending for the same billing period and
# billing_date". Live-confirmed codes: offered service 190 = Sanitary,
# 19 = Water; ITB status STTOBILL09 = Hierarchy in calculation process,
# STTOBILL07 = Billed, STTOBILL01 = Pending. Sanitary and water are linked
# through the account (same ID_PAYMENT_FORM), exactly how the first tab
# links the service-190 item. The sanitary contract uses the same
# contract-status filter as the first tab; the water contract is not
# filtered (its item's status is what matters). BILLING_DATE is compared
# as a date (time of day ignored).
# Live check 2026-09-27: 10,345 sanitary items in STTOBILL09, and every
# water item in the same period was also STTOBILL09 - so this tab returned
# 0 rows that day; it exists to catch the case when it happens.
OFFERED_SERVICE_SANITARY = 190
OFFERED_SERVICE_WATER = 19
ITB_STATUS_PENDING = "STTOBILL01"
WATER_DONE_STATUSES = (ITB_STATUS_PRIMARY_BILLED, ITB_STATUS_PENDING)


def build_sanitary_stuck_water_done_query() -> str:
    stuck = format_sql_literal(ITB_STATUS_STUCK_IN_HIERARCHY)
    statuses = ", ".join(format_sql_literal(s) for s in CONTRACT_STATUSES)
    water_done = ", ".join(format_sql_literal(s) for s in WATER_DONE_STATUSES)
    return f"""
SELECT
    pf.REFERENCE AS REFERENCE,
    ss.NISS AS SANITARY_NISS,
    itb.ID_ITEM_TO_BILL AS ID_ITEM_TO_BILL_SANITARY,
    itb.STATUS AS STATUS_SANITARY,
    itb.ID_BILLING_PERIOD AS ID_BILLING_PERIOD,
    bp.DESCRIPTION AS DESCRIPTION,
    itb.BILLING_DATE AS BILLING_DATE,
    ssw.NISS AS WATER_NISS,
    itbw.ID_ITEM_TO_BILL AS ID_ITEM_TO_BILL_WATER,
    itbw.STATUS AS STATUS_WATER
FROM GCCOM_ITEMS_TO_BILL itb
JOIN GCCOM_BILLING_SERVICE bs ON bs.ID_BILLING_SERVICE = itb.ID_BILLING_SERVICE
JOIN GCCOM_CONTRACTED_SERVICE cs ON cs.ID_CONTRACTED_SERVICE = bs.ID_CONTRACTED_SERVICE
    AND cs.ID_OFFERED_SERVICE = {OFFERED_SERVICE_SANITARY}
    AND cs.STATUS IN ({statuses})
JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
JOIN GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = cs.ID_PAYMENT_FORM
JOIN GCCOM_BILLING_PERIOD bp ON bp.ID_BILLING_PERIOD = itb.ID_BILLING_PERIOD
JOIN GCCOM_CONTRACTED_SERVICE csw ON csw.ID_PAYMENT_FORM = pf.ID_PAYMENT_FORM
    AND csw.ID_OFFERED_SERVICE = {OFFERED_SERVICE_WATER}
JOIN GCCOM_BILLING_SERVICE bsw ON bsw.ID_CONTRACTED_SERVICE = csw.ID_CONTRACTED_SERVICE
JOIN GCCOM_ITEMS_TO_BILL itbw ON itbw.ID_BILLING_SERVICE = bsw.ID_BILLING_SERVICE
    AND itbw.ID_BILLING_PERIOD = itb.ID_BILLING_PERIOD
    AND CAST(itbw.BILLING_DATE AS date) = CAST(itb.BILLING_DATE AS date)
    AND itbw.STATUS IN ({water_done})
LEFT JOIN GCCOM_SECTOR_SUPPLY ssw ON ssw.ID_SECTOR_SUPPLY = csw.ID_SECTOR_SUPPLY
WHERE itb.STATUS = {stuck}
ORDER BY itb.ID_BILLING_PERIOD DESC, pf.REFERENCE
"""


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
