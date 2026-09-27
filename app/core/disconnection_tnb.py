"""
Disconnection TNB: disconnection readings that are Terminated Not Billed
but still carry a ready usage - "ideally, this disconnection should not
be TNB".

RJ, 2026-09-27: "in GCGT_RE_READING, find for me all the reading where
the Disconnection reading type is having Terminated not bill status and
the ready usage is not zero. Then add the contract end_date from
gccom_contracted_service linking with the id_sector_supply, find the
nearest ID_CONTRACTED service base on the reading date and the
GCCOM_CONTRACTED_SERVICE ... Display NISS, BILLING_PERIOD,
READING_PREV_DATE, READING_DATE, PREV_VALUE, VALUE, READING USAGE,
CORRECTED_USAGE, READY USAGE, READ_STATUS, IND_ESTIMATE".

Codes (live-confirmed earlier, see tnb_cycle_disc.py): Disconnection =
TIPTL00010, Terminated Not Billed = 8000STSRED.

"Nearest contracted service": a supply can have several
GCCOM_CONTRACTED_SERVICE rows over time (move-in / move-out). For each
reading, OUTER APPLY picks ONE contracted service on the same
ID_SECTOR_SUPPLY whose END_DATE is closest to the reading date (in
either direction). Contracts with no END_DATE (still open) rank after
every dated one - an open contract has no end date to be "near", and a
disconnection is normally tied to a contract that ended. Ties go to the
newest ID_CONTRACTED_SERVICE. DAYS_FROM_END = READING_DATE - END_DATE in
days (positive = reading after the contract ended), shown so the gap is
visible at a glance. No status filter on the contract - its status is
displayed instead, so nothing is hidden.

Descriptions in English via GCTS_DICTIONARY, same pattern as
reading_validation.py. Pure logic: builds SELECT text only.
"""
from __future__ import annotations

from .sql_format import format_sql_literal

READING_TYPE_DISCONNECTION = "TIPTL00010"
READ_STATUS_TERMINATED_NOT_BILLED = "8000STSRED"
CONTRACTED_SERVICE_TABLE = "OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE"
CONTRACT_STATUS_TABLE = "OUC_COMMON_ADMIN.GCCOM_CONTRACT_SERV_STATUS"


def build_disconnection_tnb_query() -> str:
    disc = format_sql_literal(READING_TYPE_DISCONNECTION)
    tnb = format_sql_literal(READ_STATUS_TERMINATED_NOT_BILLED)
    return f"""
SELECT
    r.ID_SECTOR_SUPPLY AS ID_SECTOR_SUPPLY,
    ss.NISS AS NISS,
    r.ID_READING AS ID_READING,
    r.ID_BILLING_PERIOD AS ID_BILLING_PERIOD,
    COALESCE(dict_bp.text, bp.DESCRIPTION) AS BILLING_PERIOD,
    r.READING_PREV_DATE AS READING_PREV_DATE,
    r.READING_DATE AS READING_DATE,
    r.PREV_VALUE AS PREV_VALUE,
    r.VALUE AS VALUE,
    r.READING_USAGE AS READING_USAGE,
    r.CORRECTED_USAGE AS CORRECTED_USAGE,
    r.READY_USAGE AS READY_USAGE,
    COALESCE(dict_st.text, st.DESCRIPTION) AS READ_STATUS,
    r.IND_ESTIMATE AS IND_ESTIMATE,
    ncs.ID_CONTRACTED_SERVICE AS ID_CONTRACTED_SERVICE,
    COALESCE(dict_css.text, css.NAME_TYPE, ncs.STATUS) AS CONTRACT_STATUS,
    ncs.FROM_DATE AS CONTRACT_FROM_DATE,
    ncs.END_DATE AS CONTRACT_END_DATE,
    DATEDIFF(day, ncs.END_DATE, r.READING_DATE) AS DAYS_FROM_END
FROM GCGT_RE_READING r
LEFT JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = r.ID_SECTOR_SUPPLY
LEFT JOIN GCCOM_BILLING_PERIOD bp ON bp.ID_BILLING_PERIOD = r.ID_BILLING_PERIOD
LEFT JOIN GCTS_DICTIONARY dict_bp ON dict_bp.id = bp.PERIOD_NAME_XI18N AND dict_bp.locale = 'EN'
LEFT JOIN GCGT_RE_READ_STATUS st ON st.COD_DEVELOP = r.READ_STATUS
LEFT JOIN GCTS_DICTIONARY dict_st ON dict_st.id = st.DESCRIPTION_XI18N AND dict_st.locale = 'EN'
OUTER APPLY (
    SELECT TOP 1 cs.ID_CONTRACTED_SERVICE, cs.STATUS, cs.FROM_DATE, cs.END_DATE
    FROM {CONTRACTED_SERVICE_TABLE} cs
    WHERE cs.ID_SECTOR_SUPPLY = r.ID_SECTOR_SUPPLY
    ORDER BY CASE WHEN cs.END_DATE IS NULL THEN 1 ELSE 0 END,
             ABS(DATEDIFF(day, cs.END_DATE, r.READING_DATE)),
             cs.ID_CONTRACTED_SERVICE DESC
) ncs
LEFT JOIN {CONTRACT_STATUS_TABLE} css ON css.COD_DEVELOP = ncs.STATUS
LEFT JOIN GCTS_DICTIONARY dict_css ON dict_css.id = css.NAME_TYPE_XI18N AND dict_css.locale = 'EN'
WHERE r.READING_TYPE = {disc}
  AND r.READ_STATUS = {tnb}
  AND ISNULL(r.READY_USAGE, 0) <> 0
ORDER BY r.READING_DATE DESC, ss.NISS
"""
