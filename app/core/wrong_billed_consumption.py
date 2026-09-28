"""
Wrong Billed Consumption - bill-centric, three-way usage comparison.

RJ, 2026-09-28 (rework): "we start with GCCOM_BILL and GCCOM_CONCEPT_DETAIL,
we sum the calculation base for the concept given, then with the same
ID_BILL, we connect to ITEMS_TO_BILL, then reading_items_to_bill, then we
show the sum of the reading_item_to_bill ready usage, then we get the
id_reading, and sum it with the ready usage, so 3 source of ready usage we
are comparing, from calculation base, ready usage of reading item to bill,
and the ready usage in re reading, we start always with the current billing
period".

Per bill of the chosen billing period (GCCOM_BILL.ID_BILLING_PERIOD):
  1. CALCULATION_BASE    = SUM(GCCOM_BILLING_CONCEPT_DETAIL.CALCULATION_BASE)
                           of the bill's concepts CONCSMO003 / CC210
                           (RJ's query).
  2. RIT_READY_USAGE     = SUM(GCCOM_READINGS_ITEMSTOBILL.READY_USAGE) over
                           the bill's items to bill (GCCOM_ITEMS_TO_BILL
                           .ID_BILL).
  3. READING_READY_USAGE = SUM(GCGT_RE_READING.READY_USAGE) of the DISTINCT
                           readings behind those link rows.
A bill is listed when any two of the three differ by more than 0.001.

Kept from earlier rounds: bill must be Invoiced/Generated (ESTFAC0005 /
ESTFAC0008) and not a Credit Note (TIPFAC0011). NISS comes from the bill's
contracted service -> sector supply. MP type (GCGT_RE_MEASUREMENT_POINT)
of the readings is returned for the page's MP checkbox filter.

Pure logic: builds SELECT text only.
"""
from __future__ import annotations

from .sql_format import format_sql_literal

TOLERANCE = 0.001

BILL_TABLE = "OUC_COMMON_ADMIN.GCCOM_BILL"
BILL_STATUS_TABLE = "OUC_ADMIN.GCCOM_BILL_STATUS"
BILLING_TYPE_TABLE = "OUC_ADMIN.GCCOM_BILLING_TYPE"
BILL_STATUSES_ALLOWED = ("ESTFAC0005", "ESTFAC0008")  # Invoiced, Generated
BILLING_TYPE_CREDIT_NOTE = "TIPFAC0011"

BILLING_CONCEPT_TABLE = "OUC_ADMIN.GCCOM_BILLING_CONCEPT"
BILLING_CONCEPT_DETAIL_TABLE = "OUC_ADMIN.GCCOM_BILLING_CONCEPT_DETAIL"
CONSUMPTION_CONCEPTS = ("CONCSMO003", "CC210")

READINGS_ITEMS_TO_BILL_TABLE = "OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL"
CONTRACTED_SERVICE_TABLE = "GCCOM_CONTRACTED_SERVICE"
BILLING_PERIOD_TABLE = "OUC_COMMON_ADMIN.GCCOM_BILLING_PERIOD"
MIN_PERIOD_DATE = "2023-02-01"

MEASUREMENT_POINT_TABLE = "OUC_COMMON_ADMIN.GCGT_RE_MEASUREMENT_POINT"
MP_TYPE_TABLE = "OUC_COMMON_ADMIN.GCGT_RE_MP_TYPE"
MP_TYPE_NORMAL = "TIPEQM0001"


def build_wrong_billed_consumption_query(id_billing_period: int) -> str:
    bp = format_sql_literal(int(id_billing_period))
    bill_statuses = ", ".join(format_sql_literal(s) for s in BILL_STATUSES_ALLOWED)
    credit_note = format_sql_literal(BILLING_TYPE_CREDIT_NOTE)
    concepts = ", ".join(format_sql_literal(c) for c in CONSUMPTION_CONCEPTS)
    tol = TOLERANCE
    return f"""
WITH cb AS (
    -- 1. calculation base per bill of the period
    SELECT b.ID_BILL, SUM(bd.CALCULATION_BASE) AS CALCULATION_BASE
    FROM {BILL_TABLE} b
    JOIN {BILLING_CONCEPT_TABLE} bc ON bc.ID_BILL = b.ID_BILL
    JOIN {BILLING_CONCEPT_DETAIL_TABLE} bd ON bd.ID_BILLING_CONCEPT = bc.ID_BILLING_CONCEPT
    WHERE b.ID_BILLING_PERIOD = {bp}
      AND bc.COD_CONCEPT IN ({concepts})
      AND b.BILLING_STATUS IN ({bill_statuses})
      AND ISNULL(b.BILLING_TYPE, '') <> {credit_note}
    GROUP BY b.ID_BILL
),
lk AS (
    -- same ID_BILL -> items to bill -> reading/item-to-bill link rows
    SELECT itb.ID_BILL, rit.ID_ITEM_TO_BILL, rit.ID_READING, rit.READY_USAGE
    FROM cb
    JOIN GCCOM_ITEMS_TO_BILL itb ON itb.ID_BILL = cb.ID_BILL
    JOIN {READINGS_ITEMS_TO_BILL_TABLE} rit ON rit.ID_ITEM_TO_BILL = itb.ID_ITEM_TO_BILL
),
rs AS (
    -- 2. ready usage of READINGS_ITEMSTOBILL per bill
    SELECT ID_BILL, SUM(READY_USAGE) AS RIT_READY_USAGE, COUNT(*) AS LINK_ROWS,
           COUNT(DISTINCT ID_ITEM_TO_BILL) AS ITEM_TO_BILL_COUNT,
           MIN(ID_ITEM_TO_BILL) AS ID_ITEM_TO_BILL
    FROM lk
    GROUP BY ID_BILL
),
rr AS (
    -- 3. ready usage of the distinct GCGT_RE_READING rows per bill
    SELECT x.ID_BILL, SUM(r.READY_USAGE) AS READING_READY_USAGE, COUNT(*) AS READING_COUNT,
           STRING_AGG(CAST(r.ID_READING AS varchar(20)), ',') WITHIN GROUP (ORDER BY r.ID_READING) AS ID_READINGS,
           STRING_AGG(CONCAT(r.ID_READING, ': ', r.PREV_VALUE, ' -> ', r.VALUE, ' = ', r.READY_USAGE, ' (', r.USAGE_TYPE, ')'), ' ; ')
               WITHIN GROUP (ORDER BY r.ID_READING) AS READING_DETAILS,
           CASE WHEN MIN(ISNULL(mp.MP_TYPE, '')) = MAX(ISNULL(mp.MP_TYPE, '')) THEN MIN(mp.MP_TYPE) ELSE 'MIXED' END AS MP_TYPE,
           STRING_AGG(ISNULL(mp.MP_TYPE, ''), ',') AS MP_TYPES,
           MIN(r.USAGE_TYPE) AS USAGE_TYPE,
           MAX(r.READING_DATE) AS READING_DATE
    FROM (SELECT DISTINCT ID_BILL, ID_READING FROM lk) x
    JOIN GCGT_RE_READING r ON r.ID_READING = x.ID_READING
    LEFT JOIN {MEASUREMENT_POINT_TABLE} mp ON mp.ID_MEASURING_POINT = r.ID_MEASURING_POINT
    GROUP BY x.ID_BILL
)
SELECT
    ss.NISS AS NISS,
    bill.ID_BILLING_PERIOD AS ID_BILLING_PERIOD,
    COALESCE(dict_bp.text, bpd.DESCRIPTION) AS BILLING_PERIOD,
    cb.ID_BILL AS ID_BILL,
    bill.ID_CONTRACTED_SERVICE AS ID_CONTRACTED_SERVICE,
    bill.BILLING_STATUS AS BILL_STATUS,
    COALESCE(dict_bs.text, bs.NAME_TYPE) AS BILL_STATUS_DESC,
    bill.BILLING_TYPE AS BILLING_TYPE,
    COALESCE(dict_bt.text, bt.NAME_TYPE) AS BILLING_TYPE_DESC,
    cb.CALCULATION_BASE AS CALCULATION_BASE,
    rs.RIT_READY_USAGE AS RIT_READY_USAGE,
    rr.READING_READY_USAGE AS READING_READY_USAGE,
    cb.CALCULATION_BASE - ISNULL(rs.RIT_READY_USAGE, 0) AS DIFF_CALC_VS_RIT,
    cb.CALCULATION_BASE - ISNULL(rr.READING_READY_USAGE, 0) AS DIFF_CALC_VS_READING,
    ISNULL(rs.RIT_READY_USAGE, 0) - ISNULL(rr.READING_READY_USAGE, 0) AS DIFF_RIT_VS_READING,
    rs.ID_ITEM_TO_BILL AS ID_ITEM_TO_BILL,
    rs.ITEM_TO_BILL_COUNT AS ITEM_TO_BILL_COUNT,
    rs.LINK_ROWS AS LINK_ROWS,
    rr.READING_COUNT AS READING_COUNT,
    rr.ID_READINGS AS ID_READINGS,
    rr.READING_DETAILS AS READING_DETAILS,
    rr.USAGE_TYPE AS USAGE_TYPE,
    COALESCE(dict_ct.text, ct.NAME_TYPE) AS USAGE_TYPE_DESC,
    rr.MP_TYPE AS MP_TYPE,
    rr.MP_TYPES AS MP_TYPES,
    CASE WHEN rr.MP_TYPE = 'MIXED' THEN 'Mixed' ELSE COALESCE(dict_mpt.text, mpt.DESCRIPTION) END AS MP_TYPE_DESC,
    rr.READING_DATE AS READING_DATE
FROM cb
JOIN {BILL_TABLE} bill ON bill.ID_BILL = cb.ID_BILL
LEFT JOIN rs ON rs.ID_BILL = cb.ID_BILL
LEFT JOIN rr ON rr.ID_BILL = cb.ID_BILL
LEFT JOIN {CONTRACTED_SERVICE_TABLE} cs ON cs.ID_CONTRACTED_SERVICE = bill.ID_CONTRACTED_SERVICE
LEFT JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
LEFT JOIN {BILL_STATUS_TABLE} bs ON bs.COD_DEVELOP = bill.BILLING_STATUS
LEFT JOIN GCTS_DICTIONARY dict_bs ON dict_bs.id = bs.NAME_TYPE_XI18N AND dict_bs.locale = 'EN'
LEFT JOIN {BILLING_TYPE_TABLE} bt ON bt.COD_DEVELOP = bill.BILLING_TYPE
LEFT JOIN GCTS_DICTIONARY dict_bt ON dict_bt.id = bt.NAME_TYPE_XI18N AND dict_bt.locale = 'EN'
LEFT JOIN GCCOM_BILLING_PERIOD bpd ON bpd.ID_BILLING_PERIOD = bill.ID_BILLING_PERIOD
LEFT JOIN GCTS_DICTIONARY dict_bp ON dict_bp.id = bpd.PERIOD_NAME_XI18N AND dict_bp.locale = 'EN'
LEFT JOIN GCCOM_CONSUM_TYPE ct ON ct.COD_DEVELOP = rr.USAGE_TYPE
LEFT JOIN GCTS_DICTIONARY dict_ct ON dict_ct.id = ct.NAME_TYPE_XI18N AND dict_ct.locale = 'EN'
LEFT JOIN {MP_TYPE_TABLE} mpt ON mpt.COD_DEVELOP = rr.MP_TYPE
LEFT JOIN GCTS_DICTIONARY dict_mpt ON dict_mpt.id = mpt.DESCRIPTION_XI18N AND dict_mpt.locale = 'EN'
WHERE ABS(cb.CALCULATION_BASE - ISNULL(rs.RIT_READY_USAGE, 0)) > {tol}
   OR ABS(cb.CALCULATION_BASE - ISNULL(rr.READING_READY_USAGE, 0)) > {tol}
   OR ABS(ISNULL(rs.RIT_READY_USAGE, 0) - ISNULL(rr.READING_READY_USAGE, 0)) > {tol}
ORDER BY ABS(cb.CALCULATION_BASE - ISNULL(rs.RIT_READY_USAGE, 0)) DESC
"""


def build_billing_periods_query() -> str:
    """Billing periods from Feb 2023 up to next month, newest first, with a
    flag for the CURRENT one (today inside INITIAL_DATE..END_DATE) - the page
    picks it by default ("we start always with the current billing period")."""
    min_date = format_sql_literal(MIN_PERIOD_DATE)
    return f"""
SELECT bp.ID_BILLING_PERIOD, COALESCE(d.text, bp.DESCRIPTION) AS DESCRIPTION,
       CASE WHEN CAST(GETDATE() AS date) BETWEEN CAST(bp.INITIAL_DATE AS date) AND CAST(bp.END_DATE AS date)
            THEN 1 ELSE 0 END AS IS_CURRENT
FROM {BILLING_PERIOD_TABLE} bp
LEFT JOIN GCTS_DICTIONARY d ON d.id = bp.PERIOD_NAME_XI18N AND d.locale = 'EN'
WHERE bp.INITIAL_DATE >= DATEADD(month, -1, CAST({min_date} AS date))
  AND bp.INITIAL_DATE <= DATEADD(month, 1, GETDATE())
ORDER BY bp.INITIAL_DATE DESC
"""
