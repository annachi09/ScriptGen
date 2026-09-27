"""
Wrong Billed Consumption: cycle readings whose billed consumption in
GCCOM_READINGS_ITEMSTOBILL doesn't match the reading's own READY_USAGE.

RJ, 2026-09-27: "find all cases where the billed consumption (sum) in
READING_ITEMS_TO_BILL is not equal to the READY_USAGE IN GCGT_RE_READING
which is coming from VALUE - PREV VALUE multiplied the multiplier from
ME_USAGE_TYPE_METER having same id_device and usage type. Show me the
ID_BILLING_PERIOD, NISS, READ_STATUS, USAGE_TYPE, READING PREV_VALUE,
VALUE, READING_USAGE, CORRECTED_USAGE, READY_USAGE, ITEM_TO_BILL, AND
READY_USAGE IN READING_ITEMS_TO_BILL. Lets limit with cycle readings for
now, and readings from Feb 2023". RJ's example: ID_READING 1046340065 -
reading 57236 -> 61688 (READY_USAGE 4452) but its link row billed 7799
from PREV_VALUE 53889.

Decisions, both from live testing (2026-09-27):
  1. The billed sum is PER ITEM TO BILL (GROUP BY ID_READING,
     ID_ITEM_TO_BILL), not per reading. One water reading is normally
     linked to two items to bill (water AND sanitary), each carrying the
     full usage - summing across them doubled every such reading (29 ->
     58) and flagged 272,455 false positives in one period. Per item, the
     same period gave 2,267 real mismatches (e.g. 1,980 used vs 892,000
     billed, from a link VALUE of 12135 instead of 122115).
  2. One billing period per scan. The per-reading comparison over one
     period takes ~23s on the tunnel; all periods since Feb 2023 at once
     runs past the 120s timeout.

The comparison is billed sum vs the reading's stored READY_USAGE. The
formula RJ described - (VALUE - PREV_VALUE) * MULTIPLIER, multiplier from
GCGT_ME_USAGE_TYPE_METER on (ID_DEVICE, COD_USAGE_TYPE), missing = 1 - is
returned as EXPECTED_READY_USAGE so a reading whose own READY_USAGE doesn't
follow the formula is visible too (READY_USAGE_OFF_FORMULA flag). The
link row's own PREV_VALUE / VALUE come back as BILLED_PREV_VALUE /
BILLED_VALUE - a different start or end value is usually the cause.

Bill condition (RJ, 2026-09-27 follow-up: "check the GCCOM_BILL of
ITEMS_TO_BILL, it should be in status invoiced or generated, not
rebilled. the billing_type should not be credit note"). Item to bill ->
GCCOM_ITEMS_TO_BILL.ID_BILL -> OUC_COMMON_ADMIN.GCCOM_BILL. Live codes:
OUC_ADMIN.GCCOM_BILL_STATUS ESTFAC0005 = Invoiced, ESTFAC0008 =
Generated (Rebilled = ESTFAC0042, excluded by the allow-list);
OUC_ADMIN.GCCOM_BILLING_TYPE TIPFAC0011 = Credit Note (excluded). An
item with no bill is excluded too (inner join). Live, September 2026:
2,267 -> 42 rows (all Invoiced; 35 In cycle, 7 Substitutive Rebilling),
~22s; RJ's example reading 1046340065 is still in.

Pure logic: builds SELECT text only.
"""
from __future__ import annotations

from .sql_format import format_sql_literal

READING_TYPE_CYCLE = "TIPTL00003"
MIN_READING_DATE = "2023-02-01"
READINGS_ITEMS_TO_BILL_TABLE = "OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL"
USAGE_MULTIPLIER_TABLE = "GCGT_ME_USAGE_TYPE_METER"
BILLING_PERIOD_TABLE = "OUC_COMMON_ADMIN.GCCOM_BILLING_PERIOD"
TOLERANCE = 0.001

BILL_TABLE = "OUC_COMMON_ADMIN.GCCOM_BILL"
BILL_STATUS_TABLE = "OUC_ADMIN.GCCOM_BILL_STATUS"
BILLING_TYPE_TABLE = "OUC_ADMIN.GCCOM_BILLING_TYPE"
BILL_STATUSES_ALLOWED = ("ESTFAC0005", "ESTFAC0008")  # Invoiced, Generated
BILLING_TYPE_CREDIT_NOTE = "TIPFAC0011"

# MP type (RJ, 2026-09-27: "add the filter and column for MP_TYPE ... by
# default only NORMAL measuring point, connect the re_reading
# id_measuring_point in RE_MEASUREMENT_POINT"). Live: GCGT_RE_MP_TYPE
# TIPEQM0001 Normal, 0002 Secondary, 0003 Main, 0004 Control, 0005 Main
# coupled. The query returns every type; the "Normal only by default"
# filter is applied on the page, so other types are one click away
# without a re-scan.
MEASUREMENT_POINT_TABLE = "OUC_COMMON_ADMIN.GCGT_RE_MEASUREMENT_POINT"
MP_TYPE_TABLE = "OUC_COMMON_ADMIN.GCGT_RE_MP_TYPE"
MP_TYPE_NORMAL = "TIPEQM0001"

# RJ, 2026-09-27: "read_status should only be billed". Live
# GCGT_RE_READ_STATUS: 7000STSRED = Billed (7001STSRED "UAU - Billed" is a
# separate status and is NOT included).
READ_STATUS_BILLED = "7000STSRED"

# BILLED_CONSUMPTION per bill (RJ's query): SUM(CALCULATION_BASE) of the
# bill's consumption concepts.
BILLING_CONCEPT_TABLE = "OUC_ADMIN.GCCOM_BILLING_CONCEPT"
BILLING_CONCEPT_DETAIL_TABLE = "OUC_ADMIN.GCCOM_BILLING_CONCEPT_DETAIL"
CONSUMPTION_CONCEPTS = ("CONCSMO003", "CC210")


def build_wrong_billed_consumption_query(id_billing_period: int) -> str:
    bp = format_sql_literal(int(id_billing_period))
    cyc = format_sql_literal(READING_TYPE_CYCLE)
    min_date = format_sql_literal(MIN_READING_DATE)
    bill_statuses = ", ".join(format_sql_literal(s) for s in BILL_STATUSES_ALLOWED)
    credit_note = format_sql_literal(BILLING_TYPE_CREDIT_NOTE)
    read_billed = format_sql_literal(READ_STATUS_BILLED)
    concepts = ", ".join(format_sql_literal(c) for c in CONSUMPTION_CONCEPTS)
    return f"""
SELECT
    r.ID_BILLING_PERIOD AS ID_BILLING_PERIOD,
    COALESCE(dict_bp.text, bpd.DESCRIPTION) AS BILLING_PERIOD,
    ss.NISS AS NISS,
    r.ID_READING AS ID_READING,
    r.ID_MEASURING_POINT AS ID_MEASURING_POINT,
    mp.MP_TYPE AS MP_TYPE,
    COALESCE(dict_mpt.text, mpt.DESCRIPTION) AS MP_TYPE_DESC,
    r.READ_STATUS AS READ_STATUS,
    COALESCE(dict_st.text, st.DESCRIPTION) AS READ_STATUS_DESC,
    r.USAGE_TYPE AS USAGE_TYPE,
    COALESCE(dict_ct.text, ct.NAME_TYPE) AS USAGE_TYPE_DESC,
    r.READING_PREV_DATE AS READING_PREV_DATE,
    r.READING_DATE AS READING_DATE,
    r.PREV_VALUE AS PREV_VALUE,
    r.VALUE AS VALUE,
    r.READING_USAGE AS READING_USAGE,
    r.CORRECTED_USAGE AS CORRECTED_USAGE,
    r.READY_USAGE AS READY_USAGE,
    ISNULL(um.MULTIPLIER, 1) AS MULTIPLIER,
    (r.VALUE - r.PREV_VALUE) * ISNULL(um.MULTIPLIER, 1) AS EXPECTED_READY_USAGE,
    b.ID_ITEM_TO_BILL AS ID_ITEM_TO_BILL,
    itb.STATUS AS ITB_STATUS,
    bill.ID_BILL AS ID_BILL,
    bill.BILLING_STATUS AS BILL_STATUS,
    COALESCE(dict_bs.text, bs.NAME_TYPE) AS BILL_STATUS_DESC,
    bill.BILLING_TYPE AS BILLING_TYPE,
    COALESCE(dict_bt.text, bt.NAME_TYPE) AS BILLING_TYPE_DESC,
    b.BILLED_READY_USAGE AS BILLED_READY_USAGE,
    bc_sum.BILLED_CONSUMPTION AS BILLED_CONSUMPTION,
    b.BILLED_PREV_VALUE AS BILLED_PREV_VALUE,
    b.BILLED_VALUE AS BILLED_VALUE,
    b.LINK_ROWS AS LINK_ROWS,
    ISNULL(b.BILLED_READY_USAGE, 0) - ISNULL(r.READY_USAGE, 0) AS DIFFERENCE,
    CASE WHEN ABS(ISNULL(r.READY_USAGE, 0) - (r.VALUE - r.PREV_VALUE) * ISNULL(um.MULTIPLIER, 1)) > {TOLERANCE}
         THEN 1 ELSE 0 END AS READY_USAGE_OFF_FORMULA
FROM GCGT_RE_READING r
JOIN (
    SELECT rit.ID_READING, rit.ID_ITEM_TO_BILL,
           SUM(rit.READY_USAGE) AS BILLED_READY_USAGE,
           MIN(rit.PREV_VALUE) AS BILLED_PREV_VALUE,
           MAX(rit.VALUE) AS BILLED_VALUE,
           COUNT(*) AS LINK_ROWS
    FROM {READINGS_ITEMS_TO_BILL_TABLE} rit
    GROUP BY rit.ID_READING, rit.ID_ITEM_TO_BILL
) b ON b.ID_READING = r.ID_READING
JOIN GCCOM_ITEMS_TO_BILL itb ON itb.ID_ITEM_TO_BILL = b.ID_ITEM_TO_BILL
JOIN {BILL_TABLE} bill ON bill.ID_BILL = itb.ID_BILL
OUTER APPLY (
    SELECT SUM(bd.CALCULATION_BASE) AS BILLED_CONSUMPTION
    FROM {BILLING_CONCEPT_TABLE} bc
    JOIN {BILLING_CONCEPT_DETAIL_TABLE} bd ON bd.ID_BILLING_CONCEPT = bc.ID_BILLING_CONCEPT
    WHERE bc.ID_BILL = bill.ID_BILL AND bc.COD_CONCEPT IN ({concepts})
) bc_sum
LEFT JOIN {BILL_STATUS_TABLE} bs ON bs.COD_DEVELOP = bill.BILLING_STATUS
LEFT JOIN GCTS_DICTIONARY dict_bs ON dict_bs.id = bs.NAME_TYPE_XI18N AND dict_bs.locale = 'EN'
LEFT JOIN {BILLING_TYPE_TABLE} bt ON bt.COD_DEVELOP = bill.BILLING_TYPE
LEFT JOIN GCTS_DICTIONARY dict_bt ON dict_bt.id = bt.NAME_TYPE_XI18N AND dict_bt.locale = 'EN'
LEFT JOIN {MEASUREMENT_POINT_TABLE} mp ON mp.ID_MEASURING_POINT = r.ID_MEASURING_POINT
LEFT JOIN {MP_TYPE_TABLE} mpt ON mpt.COD_DEVELOP = mp.MP_TYPE
LEFT JOIN GCTS_DICTIONARY dict_mpt ON dict_mpt.id = mpt.DESCRIPTION_XI18N AND dict_mpt.locale = 'EN'
LEFT JOIN {USAGE_MULTIPLIER_TABLE} um ON um.ID_DEVICE = r.ID_DEVICE AND um.COD_USAGE_TYPE = r.USAGE_TYPE
LEFT JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = r.ID_SECTOR_SUPPLY
LEFT JOIN GCCOM_BILLING_PERIOD bpd ON bpd.ID_BILLING_PERIOD = r.ID_BILLING_PERIOD
LEFT JOIN GCTS_DICTIONARY dict_bp ON dict_bp.id = bpd.PERIOD_NAME_XI18N AND dict_bp.locale = 'EN'
LEFT JOIN GCGT_RE_READ_STATUS st ON st.COD_DEVELOP = r.READ_STATUS
LEFT JOIN GCTS_DICTIONARY dict_st ON dict_st.id = st.DESCRIPTION_XI18N AND dict_st.locale = 'EN'
LEFT JOIN GCCOM_CONSUM_TYPE ct ON ct.COD_DEVELOP = r.USAGE_TYPE
LEFT JOIN GCTS_DICTIONARY dict_ct ON dict_ct.id = ct.NAME_TYPE_XI18N AND dict_ct.locale = 'EN'
WHERE r.READING_TYPE = {cyc}
  AND r.READING_DATE >= {min_date}
  AND r.ID_BILLING_PERIOD = {bp}
  AND r.READ_STATUS = {read_billed}
  AND ABS(ISNULL(b.BILLED_READY_USAGE, 0) - ISNULL(r.READY_USAGE, 0)) > {TOLERANCE}
  AND bill.BILLING_STATUS IN ({bill_statuses})
  AND ISNULL(bill.BILLING_TYPE, '') <> {credit_note}
ORDER BY ABS(ISNULL(b.BILLED_READY_USAGE, 0) - ISNULL(r.READY_USAGE, 0)) DESC
"""


def build_billing_periods_query() -> str:
    """Billing periods from Feb 2023 up to next month, newest first - the
    page's period picker (one period per scan)."""
    min_date = format_sql_literal(MIN_READING_DATE)
    return f"""
SELECT bp.ID_BILLING_PERIOD, COALESCE(d.text, bp.DESCRIPTION) AS DESCRIPTION
FROM {BILLING_PERIOD_TABLE} bp
LEFT JOIN GCTS_DICTIONARY d ON d.id = bp.PERIOD_NAME_XI18N AND d.locale = 'EN'
WHERE bp.INITIAL_DATE >= DATEADD(month, -1, CAST({min_date} AS date))
  AND bp.INITIAL_DATE <= DATEADD(month, 1, GETDATE())
ORDER BY bp.INITIAL_DATE DESC
"""
