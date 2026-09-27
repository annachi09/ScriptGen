"""
DOUBLE ITB: a billing service with two items to bill for the SAME
INI_DATE/END_DATE - one already Billed, the other stuck Anomalous.

RJ, 2026-09-27: "create a new menu: DOUBLE ITB, this will be
GCCOM_ITEMS_TO_BILL, where we have 2 or more records for the same
ID_BILLING_SERVICE, where one is in status BILLED, and the other one is in
status anomalous, both share the same INI_DATE, and END_DATE. SHOW ME THE
NISS, BILLING_PERIOD, AND ITB DETAILS, then connect the ITB with
READING_ITEMS_TO_BILL, for the one in status anomalous, show the
READY_USAGE, IF the ready usage <> 0, then add an indicator that this one
needs a rebilling."

Live-confirmed 2026-09-27:
  - GCCOM_ITEMS_TO_BILL_STATUS: STTOBILL07 = Billed ("Facturado"),
    STTOBILL00 = Anomalous ("Anómalo").
  - The reading link table is OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL
    (ID_ITEM_TO_BILL, ID_READING, READY_USAGE, ...). One item to bill can
    carry several readings, so READY_USAGE is SUMMED per anomalous item
    (OUTER APPLY), with the reading count alongside.
  - First live run: 141 anomalous items paired with a billed twin, 107 of
    them with non-zero ready usage (needs rebilling), ~2s.

Shape: one row per (anomalous item, billed twin) pair. NISS / account /
offered service come through GCCOM_BILLING_SERVICE -> GCCOM_CONTRACTED_
SERVICE -> GCCOM_SECTOR_SUPPLY / GCCOM_PAYMENT_FORM. The headline billing
period (English, via GCTS_DICTIONARY) is the anomalous item's; the billed
twin's own period id is shown too since the two can differ.

NEEDS_REBILLING = 1 when the anomalous item's summed READY_USAGE <> 0
(NULL counts as 0).

Pure logic: builds SELECT text only.
"""
from __future__ import annotations

from .sql_format import format_sql_literal

ITB_STATUS_BILLED = "STTOBILL07"
ITB_STATUS_ANOMALOUS = "STTOBILL00"
READINGS_ITEMS_TO_BILL_TABLE = "OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL"
OFFERED_SERVICE_TABLE = "OUC_COMMON_ADMIN.GCCOM_COMPANY_OFFERED_SERVICE"


def build_double_itb_query() -> str:
    billed = format_sql_literal(ITB_STATUS_BILLED)
    anomalous = format_sql_literal(ITB_STATUS_ANOMALOUS)
    return f"""
SELECT
    ss.NISS AS NISS,
    pf.REFERENCE AS ACCOUNT,
    os.NAME_TYPE AS OFFERED_SERVICE,
    a.ID_BILLING_SERVICE AS ID_BILLING_SERVICE,
    a.ID_BILLING_PERIOD AS ID_BILLING_PERIOD,
    COALESCE(dict_bp.text, bp.DESCRIPTION) AS BILLING_PERIOD,
    a.INI_DATE AS INI_DATE,
    a.END_DATE AS END_DATE,
    a.ID_ITEM_TO_BILL AS ANOM_ID_ITEM_TO_BILL,
    a.STATUS AS ANOM_STATUS,
    a.BILLING_DATE AS ANOM_BILLING_DATE,
    a.CREATION_DATE AS ANOM_CREATION_DATE,
    b.ID_ITEM_TO_BILL AS BILLED_ID_ITEM_TO_BILL,
    b.STATUS AS BILLED_STATUS,
    b.BILLING_DATE AS BILLED_BILLING_DATE,
    b.ID_BILL AS BILLED_ID_BILL,
    b.ID_BILLING_PERIOD AS BILLED_ID_BILLING_PERIOD,
    ru.READY_USAGE AS ANOM_READY_USAGE,
    ru.READING_COUNT AS ANOM_READING_COUNT,
    CASE WHEN ISNULL(ru.READY_USAGE, 0) <> 0 THEN 1 ELSE 0 END AS NEEDS_REBILLING
FROM GCCOM_ITEMS_TO_BILL a
JOIN GCCOM_ITEMS_TO_BILL b ON b.ID_BILLING_SERVICE = a.ID_BILLING_SERVICE
    AND b.STATUS = {billed}
    AND b.INI_DATE = a.INI_DATE
    AND b.END_DATE = a.END_DATE
    AND b.ID_ITEM_TO_BILL <> a.ID_ITEM_TO_BILL
JOIN GCCOM_BILLING_SERVICE bs ON bs.ID_BILLING_SERVICE = a.ID_BILLING_SERVICE
JOIN GCCOM_CONTRACTED_SERVICE cs ON cs.ID_CONTRACTED_SERVICE = bs.ID_CONTRACTED_SERVICE
LEFT JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
LEFT JOIN GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = cs.ID_PAYMENT_FORM
LEFT JOIN {OFFERED_SERVICE_TABLE} os ON os.ID_OFFERED_SERVICE = cs.ID_OFFERED_SERVICE
LEFT JOIN GCCOM_BILLING_PERIOD bp ON bp.ID_BILLING_PERIOD = a.ID_BILLING_PERIOD
LEFT JOIN GCTS_DICTIONARY dict_bp ON dict_bp.id = bp.PERIOD_NAME_XI18N AND dict_bp.locale = 'EN'
OUTER APPLY (
    SELECT SUM(rit.READY_USAGE) AS READY_USAGE, COUNT(*) AS READING_COUNT
    FROM {READINGS_ITEMS_TO_BILL_TABLE} rit
    WHERE rit.ID_ITEM_TO_BILL = a.ID_ITEM_TO_BILL
) ru
WHERE a.STATUS = {anomalous}
ORDER BY a.ID_BILLING_PERIOD DESC, ss.NISS
"""
