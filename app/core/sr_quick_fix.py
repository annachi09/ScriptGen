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
    if accounts:
        acc_where = "PF.REFERENCE IN (" + ", ".join(format_sql_literal(a) for a in accounts) + ")"
    else:
        acc_where = "RR.VALUE < RR.PREV_VALUE"
    floor = f"\n  AND RR.ID_BILLING_PERIOD > {int(period_floor)}" if period_floor else ""
    svc = ", ".join(str(s) for s in SERVICES)
    return (
        "SELECT DISTINCT PF.REFERENCE AS ACCOUNT, SS.NISS, CS.ID_OFFERED_SERVICE, OS.NAME_TYPE AS SERVICE_DESC,\n"
        "  RR.ID_READING, RR.ID_BILLING_PERIOD, RR.READ_STATUS, RR.READING_TYPE,\n"
        "  RR.READING_PREV_DATE, RR.READING_DATE, RR.PREV_VALUE, RR.VALUE,\n"
        "  RR.READING_USAGE, RR.READY_USAGE, RR.CORRECTED_USAGE,\n"
        "  CASE WHEN RR.VALUE < RR.PREV_VALUE THEN 1 ELSE 0 END AS LOWER_THAN_PREV\n"
        "FROM GCCOM_PAYMENT_FORM PF\n"
        f"JOIN GCCOM_CONTRACTED_SERVICE CS ON CS.ID_PAYMENT_FORM = PF.ID_PAYMENT_FORM AND CS.ID_OFFERED_SERVICE IN ({svc})\n"
        "JOIN GCCOM_SECTOR_SUPPLY SS ON SS.ID_SECTOR_SUPPLY = CS.ID_SECTOR_SUPPLY\n"
        f"LEFT JOIN {OFFERED_TBL} OS ON OS.ID_OFFERED_SERVICE = CS.ID_OFFERED_SERVICE\n"
        f"JOIN {READING_TBL} RR ON RR.ID_SECTOR_SUPPLY = CS.ID_SECTOR_SUPPLY\n"
        f"WHERE {acc_where}\n"
        f"  AND RR.READ_STATUS = '{READ_SENT_TO_BILL}'{floor}\n"
        "ORDER BY 1, 3, 5;"
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
                         f"  (usage {r.get('reading_usage')})  period {r.get('id_billing_period')}")
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
