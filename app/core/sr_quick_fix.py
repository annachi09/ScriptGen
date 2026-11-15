"""
SR Quick FIX (RJ 2026-11-09) - quick fixes for service requests, one tab per SR type.

Tab 1 - SR INCORRECT BILLING PERIOD. RJ's own words: "I will provide you with the account,
and you need to find the sent to bill water and or electricity which usually has a reading
value lower than the previous reading. with that reading you need to create an update script
like the following and we expect it will affect sanitary also, JIRA update program will be
INC_BP". RJ's template (one reading):

    update OUC_COMMON_ADMIN.GCGT_RE_READING
       set READ_STATUS='7000STSRED', VALUE=PREV_VALUE, READING_USAGE=0, READY_USAGE=0, CORRECTED_USAGE=0
     where READ_STATUS='6000STSRED' and ID_READING in (1048246312);
    update OUC_ADMIN.GCCOM_ANOMALOUS set ANOMALOUS_STATUS='ESTAN00005'
     where ANOMALOUS_STATUS='ESTAN00001' and ID_ITEM_TO_BILL in
           (select ID_ITEM_TO_BILL from OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL where id_reading in (1048246312));
    update OUC_ADMIN.GCCOM_ITEMS_TO_BILL set STATUS='STTOBILL04'
     where STATUS='STTOBILL00' and ID_ITEM_TO_BILL in
           (select ID_ITEM_TO_BILL from OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL where id_reading in (1048246312));

Live facts (checked 2026-11-09):
  - 6000STSRED = Sent to bill, 7000STSRED = Billed; STTOBILL00 = Anomalous, STTOBILL04 =
    Cancelled; ESTAN00001 = pending not billed -> ESTAN00005 = resolved after review.
  - A WATER reading is linked in GCCOM_READINGS_ITEMSTOBILL to BOTH the water item-to-bill
    (service 19) and the sanitary item-to-bill (service 190) - e.g. reading 1049334074 ->
    ITB 1042568021 (19) + 1042568025 (190). So RJ's subquery already covers sanitary.
  - A multi-register electricity meter has many readings on ONE item-to-bill (account
    1049715095: ~20 readings -> ITB 1042501273); cancelling that ITB affects all of them -
    the page shows "shared with N readings".
  - All three tables have UPDATE_DATE / UPDATE_USER / UPDATE_PROGRAM; the script stamps them
    (UPDATE_PROGRAM = the JIRA program, default INC_BP) so the rollback can find its rows.

Pure query/script text only; web/server.py runs the SELECTs via mssql.run_query. The
scripts are never executed by ScriptGen (read-only DB).
"""
from __future__ import annotations

import re
from typing import Iterable

from .sql_format import format_sql_literal

READING_TBL = "OUC_COMMON_ADMIN.GCGT_RE_READING"
ANOMALOUS_TBL = "OUC_ADMIN.GCCOM_ANOMALOUS"
ITB_TBL = "OUC_ADMIN.GCCOM_ITEMS_TO_BILL"
RIT_TBL = "OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL"
OFFERED_TBL = "OUC_COMMON_ADMIN.GCCOM_COMPANY_OFFERED_SERVICE"

READ_SENT_TO_BILL = "6000STSRED"
READ_BILLED = "7000STSRED"
ITB_ANOMALOUS = "STTOBILL00"
ITB_CANCELLED = "STTOBILL04"
ANOM_OPEN = "ESTAN00001"
ANOM_RESOLVED = "ESTAN00005"

SERVICES = (1, 19)          # Electricity, Water (sanitary 190 follows the water reading)
DEFAULT_PERIOD_FLOOR = 10000000230   # RJ's own example: ID_BILLING_PERIOD > 10000000230
DEFAULT_PROGRAM = "INC_BP"
DEFAULT_USER = "RMA"
MAX_ACCOUNTS = 200
CHUNK = 500


def clean_accounts(raw) -> list[str]:
    """Accounts from a pasted list (comma / space / newline separated), digits only."""
    if isinstance(raw, str):
        raw = re.split(r"[\s,;]+", raw)
    out = []
    for a in raw or []:
        a = re.sub(r"\D", "", str(a or ""))
        if a and a not in out:
            out.append(a)
    return out[:MAX_ACCOUNTS]


def clean_ids(raw: Iterable) -> list[int]:
    out = []
    for x in raw or []:
        s = re.sub(r"\D", "", str(x or ""))
        if s and int(s) not in out:
            out.append(int(s))
    return out


def build_readings_query(accounts: list[str] | None, period_floor: int | None = DEFAULT_PERIOD_FLOOR) -> str:
    """Every SENT TO BILL (6000STSRED) electricity / water reading of the accounts.
    LOWER_THAN_PREV = 1 when VALUE < PREV_VALUE (the usual SR incorrect billing period case).
    No accounts = system-wide scan of the candidates only (VALUE < PREV_VALUE) - the
    "All candidates" button and the Overview card."""
    # RJ 2026-11-12 ("we only need the one with SR ... 1 SR only for each account"): driven by the
    # SR anomaly. Found live: joining account -> contracted service -> sector supply -> readings
    # put the same reading under every account that ever had a contract on that supply (8 readings
    # showed under 65 accounts after a change of tenant). The account is now the one the
    # Incorrect billing period anomaly belongs to (AN.ID_BILLING_SERVICE -> contracted service ->
    # payment form): 8 readings -> 6 accounts -> 1 SR each.
    if accounts:
        acc_where = "PF.REFERENCE IN (" + ", ".join(format_sql_literal(a) for a in accounts) + ")"
    else:
        acc_where = "RR.VALUE < RR.PREV_VALUE"
    floor = f"\n  AND RR.ID_BILLING_PERIOD > {int(period_floor)}" if period_floor else ""
    svc = ", ".join(str(s) for s in SERVICES)
    closed = ", ".join(f"'{s}'" for s in SR_RESOLVED_STATUSES)
    return (
        "SELECT DISTINCT PF.REFERENCE AS ACCOUNT, SS.NISS,\n"
        "  COALESCE(RS.ID_OFFERED_SERVICE, CS.ID_OFFERED_SERVICE) AS ID_OFFERED_SERVICE, OS.NAME_TYPE AS SERVICE_DESC,\n"
        "  RR.ID_READING, RR.ID_BILLING_PERIOD, RR.READ_STATUS, RR.READING_TYPE,\n"
        "  RR.READING_PREV_DATE, RR.READING_DATE, RR.PREV_VALUE, RR.VALUE,\n"
        "  RR.READING_USAGE, RR.READY_USAGE, RR.CORRECTED_USAGE,\n"
        "  CASE WHEN RR.VALUE < RR.PREV_VALUE THEN 1 ELSE 0 END AS LOWER_THAN_PREV\n"
        f"FROM {READING_TBL} RR\n"
        f"JOIN {RIT_TBL} RIT ON RIT.ID_READING = RR.ID_READING\n"
        f"JOIN {ANOMALOUS_TBL} AN ON AN.ID_ITEM_TO_BILL = RIT.ID_ITEM_TO_BILL AND AN.ID_SERVREQ IS NOT NULL\n"
        f"JOIN {BILL_ANOM_TYPE_TBL} G ON G.ID_BILL_ANOM_COMP = AN.ID_PRINCIPAL_ANOMALY AND G.ANOMALY_COD = '{INC_BP_BILL_CODE}'\n"
        f"JOIN OUC_COMMON_ADMIN.GCCOM_SERVICE_REQUEST SR ON SR.ID_SERVREQ = AN.ID_SERVREQ AND SR.SERVREQ_STATUS NOT IN ({closed})\n"
        "JOIN GCCOM_BILLING_SERVICE BS ON BS.ID_BILLING_SERVICE = AN.ID_BILLING_SERVICE\n"
        "JOIN GCCOM_CONTRACTED_SERVICE CS ON CS.ID_CONTRACTED_SERVICE = BS.ID_CONTRACTED_SERVICE\n"
        "JOIN GCCOM_PAYMENT_FORM PF ON PF.ID_PAYMENT_FORM = CS.ID_PAYMENT_FORM\n"
        "JOIN GCCOM_SECTOR_SUPPLY SS ON SS.ID_SECTOR_SUPPLY = RR.ID_SECTOR_SUPPLY\n"
        # the reading's own service (electricity / water) on this account; the anomaly may be the sanitary one
        f"OUTER APPLY (SELECT TOP 1 C2.ID_OFFERED_SERVICE FROM GCCOM_CONTRACTED_SERVICE C2\n"
        f"             WHERE C2.ID_SECTOR_SUPPLY = RR.ID_SECTOR_SUPPLY AND C2.ID_PAYMENT_FORM = CS.ID_PAYMENT_FORM\n"
        f"               AND C2.ID_OFFERED_SERVICE IN ({svc})) RS\n"
        f"LEFT JOIN {OFFERED_TBL} OS ON OS.ID_OFFERED_SERVICE = COALESCE(RS.ID_OFFERED_SERVICE, CS.ID_OFFERED_SERVICE)\n"
        f"WHERE {acc_where}\n"
        f"  AND RR.READ_STATUS = '{READ_SENT_TO_BILL}'{floor}\n"
        "ORDER BY 1, 2, 5;"
    )


def build_items_query(reading_ids: list[int]) -> str:
    """Item-to-bills linked to the readings (water -> water + sanitary), with their open
    anomalies and how many readings share the item-to-bill."""
    ids = ", ".join(str(i) for i in reading_ids)
    return (
        "SELECT RIT.ID_READING, ITB.ID_ITEM_TO_BILL, ITB.STATUS AS ITEM_STATUS,\n"
        "  CS.ID_OFFERED_SERVICE, OS.NAME_TYPE AS SERVICE_DESC,\n"
        f"  (SELECT COUNT(*) FROM {ANOMALOUS_TBL} AN WHERE AN.ID_ITEM_TO_BILL = ITB.ID_ITEM_TO_BILL\n"
        f"     AND AN.ANOMALOUS_STATUS = '{ANOM_OPEN}') AS OPEN_ANOMALIES,\n"
        f"  (SELECT STRING_AGG(CONVERT(varchar(20), AN.ID_ANOMALOUS) + ' ' + AN.ANOMALOUS_STATUS, ', ')\n"
        f"     FROM {ANOMALOUS_TBL} AN WHERE AN.ID_ITEM_TO_BILL = ITB.ID_ITEM_TO_BILL) AS ANOMALIES,\n"
        f"  (SELECT COUNT(*) FROM {RIT_TBL} R2 WHERE R2.ID_ITEM_TO_BILL = ITB.ID_ITEM_TO_BILL) AS SHARED_READINGS\n"
        f"FROM {RIT_TBL} RIT\n"
        f"JOIN {ITB_TBL} ITB ON ITB.ID_ITEM_TO_BILL = RIT.ID_ITEM_TO_BILL\n"
        "LEFT JOIN GCCOM_BILLING_SERVICE BS ON BS.ID_BILLING_SERVICE = ITB.ID_BILLING_SERVICE\n"
        "LEFT JOIN GCCOM_CONTRACTED_SERVICE CS ON CS.ID_CONTRACTED_SERVICE = BS.ID_CONTRACTED_SERVICE\n"
        f"LEFT JOIN {OFFERED_TBL} OS ON OS.ID_OFFERED_SERVICE = CS.ID_OFFERED_SERVICE\n"
        f"WHERE RIT.ID_READING IN ({ids})\n"
        "ORDER BY RIT.ID_READING, CS.ID_OFFERED_SERVICE;"
    )


# RJ 2026-11-12: "we need to make sure that the reading is anomalous in GCCOM_ANOMALOUS or
# RE_ANOMALOUS and the anomaly has id_SERVREQ to be qualified on this and the anomaly is incorrect
# billing period". Billing anomaly type = GCCOM_ANOMALOUS.ID_PRINCIPAL_ANOMALY ->
# GCCOM_BILL_ANOMALY_COMPANY; "Incorrect billing period" = ANOMALY_COD 'ERRPERIOD'.
#
# RJ 2026-11-12 (later): "no need for re anomaly, i want you to display the SR and the type of SR
# and status also only unresolved SR". So: GCCOM_ANOMALOUS only (ERRPERIOD + ID_SERVREQ), and the
# SR must be unresolved. SR statuses live (GCCOM_SERVREQUEST_STATUS, EN): closed ones =
# SREQSTA003 Solved, SREQSTA004 Cancelled, SREQSTA203 End not applicable, SREQSTA207 Resolved
# final, SREQSTA208 Resolved, reject rebill (same set as Pending Service Request + 208).
BILL_ANOM_TYPE_TBL = "GCCOM_BILL_ANOMALY_COMPANY"
INC_BP_BILL_CODE = "ERRPERIOD"
SR_RESOLVED_STATUSES = ("SREQSTA003", "SREQSTA004", "SREQSTA203", "SREQSTA207", "SREQSTA208")


def build_sr_anomalies_query(reading_ids: list[int]) -> str:
    """Incorrect billing period (ERRPERIOD) billing anomalies of the readings that are linked to an
    UNRESOLVED service request, with the SR code, type and status (English descriptions)."""
    ids = ", ".join(str(i) for i in reading_ids)
    closed = ", ".join(f"'{s}'" for s in SR_RESOLVED_STATUSES)
    return (
        "SELECT DISTINCT RIT.ID_READING, AN.ID_ANOMALOUS, AN.ANOMALOUS_STATUS AS ANOM_STATUS,\n"
        "  G.ANOMALY_COD AS ANOM_CODE, G.DESCRIPTION AS ANOM_DESC, AN.ID_SERVREQ,\n"
        "  SR.SR_CODE, SR.SERVREQ_TYPE, TD.TEXT AS SERVREQ_TYPE_DESC,\n"
        "  SR.SERVREQ_STATUS, SD.TEXT AS SERVREQ_STATUS_DESC, SR.CREATION_SR_DATE, PF.REFERENCE AS ACCOUNT\n"
        f"FROM {RIT_TBL} RIT\n"
        f"JOIN {ANOMALOUS_TBL} AN ON AN.ID_ITEM_TO_BILL = RIT.ID_ITEM_TO_BILL\n"
        "JOIN GCCOM_BILLING_SERVICE BS ON BS.ID_BILLING_SERVICE = AN.ID_BILLING_SERVICE\n"
        "JOIN GCCOM_CONTRACTED_SERVICE CS ON CS.ID_CONTRACTED_SERVICE = BS.ID_CONTRACTED_SERVICE\n"
        "JOIN GCCOM_PAYMENT_FORM PF ON PF.ID_PAYMENT_FORM = CS.ID_PAYMENT_FORM\n"
        f"JOIN {BILL_ANOM_TYPE_TBL} G ON G.ID_BILL_ANOM_COMP = AN.ID_PRINCIPAL_ANOMALY\n"
        "JOIN OUC_COMMON_ADMIN.GCCOM_SERVICE_REQUEST SR ON SR.ID_SERVREQ = AN.ID_SERVREQ\n"
        "LEFT JOIN OUC_COMMON_ADMIN.GCCOM_SERVREQUEST_TYPE T ON T.COD_DEVELOP = SR.SERVREQ_TYPE\n"
        "LEFT JOIN GCTS_DICTIONARY TD ON TD.ID = T.NAME_TYPE_XI18N AND TD.LOCALE = 'EN'\n"
        "LEFT JOIN OUC_ADMIN.GCCOM_SERVREQUEST_STATUS SS ON SS.COD_DEVELOP = SR.SERVREQ_STATUS\n"
        "LEFT JOIN GCTS_DICTIONARY SD ON SD.ID = SS.NAME_TYPE_XI18N AND SD.LOCALE = 'EN'\n"
        f"WHERE RIT.ID_READING IN ({ids})\n"
        f"  AND G.ANOMALY_COD = '{INC_BP_BILL_CODE}'\n"
        f"  AND SR.SERVREQ_STATUS NOT IN ({closed})\n"
        "ORDER BY 1, 2;"
    )


def is_qualifying(anom: dict) -> bool:
    """Every row of build_sr_anomalies_query qualifies (ERRPERIOD + unresolved SR); kept as a
    guard in case a caller passes other rows."""
    return (str(anom.get("anom_code") or "").strip() == INC_BP_BILL_CODE
            and bool(str(anom.get("id_servreq") or "").strip())
            and str(anom.get("servreq_status") or "") not in SR_RESOLVED_STATUSES)


def _sub(id_list: str) -> str:
    return f"(SELECT ID_ITEM_TO_BILL FROM {RIT_TBL} WHERE ID_READING IN ({id_list}))"


def build_fix_script(readings: list[dict], *, program: str = DEFAULT_PROGRAM,
                     user: str = DEFAULT_USER, clean: bool = False) -> str:
    """RJ's 3 UPDATEs for the selected readings + UPDATE_DATE/USER/PROGRAM stamps."""
    ids = clean_ids(r.get("id_reading") for r in readings)
    if not ids:
        raise ValueError("Select at least one reading.")
    usr, prog = format_sql_literal(user), format_sql_literal(program)
    stamp = f"UPDATE_DATE = GETDATE(), UPDATE_USER = {usr}, UPDATE_PROGRAM = {prog}"
    parts = []
    if not clean:
        parts.append(f"-- SR INCORRECT BILLING PERIOD ({program}) - {len(ids)} reading(s)")
        for r in readings:
            parts.append(f"--   account {r.get('account')}  {r.get('service_desc') or r.get('id_offered_service')}"
                         f"  reading {r.get('id_reading')}  prev {r.get('prev_value')} -> value {r.get('value')}"
                         f"  (usage {r.get('reading_usage')})  period {r.get('id_billing_period')}"
                         + (f"  SR {', '.join(r.get('sr_codes') or [])}" if r.get("sr_codes") else ""))
        parts.append("-- Reading -> Billed with VALUE = PREV_VALUE and zero usage; its open anomalies -> resolved;"
                     "\n-- its anomalous item-to-bills (water reading = water + sanitary) -> cancelled.\n")
    for s in range(0, len(ids), CHUNK):
        id_list = ", ".join(str(i) for i in ids[s:s + CHUNK])
        parts.append(
            f"UPDATE {READING_TBL}\n"
            f"SET READ_STATUS = '{READ_BILLED}', VALUE = PREV_VALUE, READING_USAGE = 0, READY_USAGE = 0, CORRECTED_USAGE = 0,\n"
            f"    {stamp}\n"
            f"WHERE READ_STATUS = '{READ_SENT_TO_BILL}' AND ID_READING IN ({id_list});\n\n"
            f"UPDATE {ANOMALOUS_TBL}\n"
            f"SET ANOMALOUS_STATUS = '{ANOM_RESOLVED}', {stamp}\n"
            f"WHERE ANOMALOUS_STATUS = '{ANOM_OPEN}' AND ID_ITEM_TO_BILL IN\n"
            f"      {_sub(id_list)};\n\n"
            f"UPDATE {ITB_TBL}\n"
            f"SET STATUS = '{ITB_CANCELLED}', {stamp}\n"
            f"WHERE STATUS = '{ITB_ANOMALOUS}' AND ID_ITEM_TO_BILL IN\n"
            f"      {_sub(id_list)};\n")
    return "\n".join(parts)


def _num(v) -> str:
    s = str(v if v is not None else "").strip()
    return s if re.fullmatch(r"-?\d+(\.\d+)?", s) else "NULL"


def build_rollback_script(readings: list[dict], *, program: str = DEFAULT_PROGRAM,
                          user: str = DEFAULT_USER) -> str:
    """Puts each reading back to Sent to bill with the VALUE / usages it had at scan time; the
    anomalies / item-to-bills this program resolved / cancelled go back to open / anomalous."""
    ids = clean_ids(r.get("id_reading") for r in readings)
    if not ids:
        raise ValueError("Select at least one reading.")
    usr, prog, rprog = format_sql_literal(user), format_sql_literal(program), format_sql_literal(program + " ROLLBACK")
    stamp = f"UPDATE_DATE = GETDATE(), UPDATE_USER = {usr}, UPDATE_PROGRAM = {rprog}"
    parts = [f"-- ROLLBACK of SR INCORRECT BILLING PERIOD ({program}) - values as scanned before the fix.\n"]
    for r in readings:
        rid = clean_ids([r.get("id_reading")])
        if not rid:
            continue
        parts.append(
            f"UPDATE {READING_TBL}\n"
            f"SET READ_STATUS = '{READ_SENT_TO_BILL}', VALUE = {_num(r.get('value'))}, "
            f"READING_USAGE = {_num(r.get('reading_usage'))}, READY_USAGE = {_num(r.get('ready_usage'))}, "
            f"CORRECTED_USAGE = {_num(r.get('corrected_usage'))},\n"
            f"    {stamp}\n"
            f"WHERE ID_READING = {rid[0]} AND READ_STATUS = '{READ_BILLED}' AND UPDATE_PROGRAM = {prog};\n")
    for s in range(0, len(ids), CHUNK):
        id_list = ", ".join(str(i) for i in ids[s:s + CHUNK])
        parts.append(
            f"UPDATE {ANOMALOUS_TBL}\n"
            f"SET ANOMALOUS_STATUS = '{ANOM_OPEN}', {stamp}\n"
            f"WHERE ANOMALOUS_STATUS = '{ANOM_RESOLVED}' AND UPDATE_PROGRAM = {prog} AND ID_ITEM_TO_BILL IN\n"
            f"      {_sub(id_list)};\n\n"
            f"UPDATE {ITB_TBL}\n"
            f"SET STATUS = '{ITB_ANOMALOUS}', {stamp}\n"
            f"WHERE STATUS = '{ITB_CANCELLED}' AND UPDATE_PROGRAM = {prog} AND ID_ITEM_TO_BILL IN\n"
            f"      {_sub(id_list)};\n")
    return "\n".join(parts)
