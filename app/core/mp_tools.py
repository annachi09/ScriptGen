"""
MP Tools (RJ 2026-11-18) - measuring point fixes, one tab per tool.

Tab 1 - TMP ACTIVATE MP. RJ: "we will find if there is a reading in calculated usage status
(GCGT_RE_READING) using ID_MEASURING point or NISS of GCCOM_SECTOR_SUPPLY. we will update 3 tables,
first the reading table will be update with status available, ind_usage_to_cal = 1, next the MP will be
updated from 3000 INACTIVE to 10000 ACTIVE, next the RE_MP_STATUS_HIST, we will update the one with null
end date from 30000 to 10000 active.. then you have to give the revert updates for only the Measuring
point table and mp_STATUS_HIST to its original status".

Live schema / codes (checked 2026-11-18):
  - GCGT_RE_READ_STATUS: 4000STSRED "Calculated usage", 1000STSRED "Available".
  - MP table = OUC_COMMON_ADMIN.GCGT_RE_MEASUREMENT_POINT (ID_MEASURING_POINT, ID_SECTOR_SUPPLY, STATUS).
    GCGT_RE_MEASURE_POINT_STATUS: 0000STAMPO Non-existent, 1000STAMPO Connected (= "active"),
    2000STAMPO Disconnected, 3000STAMPO Inactive.  RJ's "3000 -> 10000" = 3000STAMPO -> 1000STAMPO.
  - OUC_COMMON_ADMIN.GCGT_RE_MP_STATUS_HIST (ID_MP_STATUS_HIST, ID_MEASURING_POINT, INITIAL_DATE,
    END_DATE, ID_STATUS, IND_TARIFICABLE): the open row is END_DATE IS NULL.
  - NISS -> GCCOM_SECTOR_SUPPLY.ID_SECTOR_SUPPLY = MP.ID_SECTOR_SUPPLY; reading -> MP via
    GCGT_RE_READING.ID_MEASURING_POINT.
  - Example live: NISS 10084569-301, MP 735693 3000STAMPO, open hist 21044794 3000STAMPO,
    reading 1049772747 4000STSRED IND_USAGE_TO_CAL 0.
Pure text builders - web/server.py runs the SELECT; the scripts are only shown.
"""
from __future__ import annotations

import re

from .sql_format import format_sql_literal

MP_TBL = "OUC_COMMON_ADMIN.GCGT_RE_MEASUREMENT_POINT"
HIST_TBL = "OUC_COMMON_ADMIN.GCGT_RE_MP_STATUS_HIST"
READING_TBL = "OUC_COMMON_ADMIN.GCGT_RE_READING"
READ_CALCULATED = "4000STSRED"
READ_AVAILABLE = "1000STSRED"
MP_INACTIVE = "3000STAMPO"
MP_ACTIVE = "1000STAMPO"
# RJ 2026-11-18: "in case the reading is in status available, or id_device is null and the mp type is
# secondary, we need to temporarily make it as a normal measuring point ... set id_main_mp to null, change
# mp_type to 00001 normal, do not forget the revert". GCGT_RE_MP_TYPE live: TIPEQM0001 Normal,
# TIPEQM0002 Secondary (260,746 of 260,747 have ID_MAIN_MP), TIPEQM0003 Main.
# Rule used: MP_TYPE = Secondary AND one of its scanned readings is Available / Calculated usage (becomes
# Available in step 1) or has GCGT_RE_READING.ID_DEVICE NULL (live e.g. NISS 10577056-301, MP 771257,
# main 97774, reading 1049663817 4000STSRED with no device).
MP_TYPE_NORMAL = "TIPEQM0001"
MP_TYPE_SECONDARY = "TIPEQM0002"
DEFAULT_PROGRAM = "TMP_ACTIVATE_MP"
DEFAULT_USER = "RMA"
MAX_INPUTS = 3000   # RJ 2026-11-18: was 300 - "detect all" can select up to DETECT_ALL_MAX_MPS MPs


def parse_inputs(raw) -> tuple[list[int], list[str]]:
    """Pasted MP ids and / or NISS: a value with a '-' (10084569-301) is a NISS, digits only = MP id."""
    if isinstance(raw, str):
        raw = re.split(r"[\s,;]+", raw)
    mps, niss = [], []
    for v in raw or []:
        v = str(v or "").strip()
        if not v:
            continue
        if re.fullmatch(r"\d+", v):
            if int(v) not in mps:
                mps.append(int(v))
        elif re.fullmatch(r"[0-9A-Za-z]+-[0-9A-Za-z]+", v):
            if v not in niss:
                niss.append(v)
    return mps[:MAX_INPUTS], niss[:MAX_INPUTS]


# RJ 2026-11-18: "for MP tools, create a detect all button, here we will find all Inactive mp where there is a
# reading in status available or calculated usage". Live 2026-11-18 (reading-first, ~5 s): 26,075 Inactive MPs
# have an Available reading (85,425 readings, most of them years old) and 617 a Calculated-usage one; with a
# reading in the last 6 months: 1,212 + 44. So the scan takes a "readings since N months" window
# (0 = all) and a row cap.
DETECT_ALL_DEFAULT_MONTHS = 6
DETECT_ALL_MAX_MPS = 3000


def build_detect_all_query(since_months: int = DETECT_ALL_DEFAULT_MONTHS, max_mps: int = DETECT_ALL_MAX_MPS) -> str:
    """Every Inactive (3000STAMPO) MP with a reading in Available or Calculated usage (within the window),
    same columns / reading rows as build_detect_query."""
    win = (f"\n    AND R.READING_DATE >= DATEADD(MONTH, -{int(since_months)}, CAST(GETDATE() AS date))"
           if since_months and int(since_months) > 0 else "")
    return (
        "WITH M AS (\n"
        f"  SELECT DISTINCT TOP ({int(max_mps)}) R.ID_MEASURING_POINT\n"
        f"  FROM {READING_TBL} R\n"
        f"  JOIN {MP_TBL} MP0 ON MP0.ID_MEASURING_POINT = R.ID_MEASURING_POINT AND MP0.STATUS = '{MP_INACTIVE}'\n"
        f"  WHERE R.READ_STATUS IN ('{READ_CALCULATED}', '{READ_AVAILABLE}'){win}\n"
        ")\n" + _detect_select("JOIN M ON M.ID_MEASURING_POINT = MP.ID_MEASURING_POINT\n", "1 = 1")
    )


def build_detect_query(mp_ids: list[int], niss: list[str]) -> str:
    """Every MP of the inputs (with its open status-hist row) and its readings in Calculated usage."""
    conds = []
    if mp_ids:
        conds.append("MP.ID_MEASURING_POINT IN (" + ", ".join(str(i) for i in mp_ids) + ")")
    if niss:
        conds.append("SS.NISS IN (" + ", ".join(format_sql_literal(n) for n in niss) + ")")
    if not conds:
        raise ValueError("Enter an ID_MEASURING_POINT or a NISS.")
    return _detect_select("", " OR ".join(conds))


def _detect_select(extra_join: str, where: str) -> str:
    return (
        "SELECT SS.NISS, MP.ID_MEASURING_POINT, MP.STATUS AS MP_STATUS, MP.MP_TYPE, MP.ID_MAIN_MP,\n"
        "  H.ID_MP_STATUS_HIST, H.ID_STATUS AS HIST_STATUS, H.INITIAL_DATE AS HIST_INITIAL_DATE,\n"
        "  RR.ID_READING, RR.READ_STATUS, RR.IND_USAGE_TO_CAL, RR.READING_DATE, RR.ID_BILLING_PERIOD,\n"
        "  RR.READING_TYPE, RR.VALUE, RR.READY_USAGE, RR.ID_DEVICE AS READING_ID_DEVICE\n"
        f"FROM {MP_TBL} MP\n"
        + extra_join +
        "JOIN GCCOM_SECTOR_SUPPLY SS ON SS.ID_SECTOR_SUPPLY = MP.ID_SECTOR_SUPPLY\n"
        f"LEFT JOIN {HIST_TBL} H ON H.ID_MEASURING_POINT = MP.ID_MEASURING_POINT AND H.END_DATE IS NULL\n"
        # RJ 2026-11-18: "also allow to update the reading when status is available to make sure that
        # ind usage to cal = 1" - Available readings with IND_USAGE_TO_CAL not 1 are listed too
        f"LEFT JOIN {READING_TBL} RR ON RR.ID_MEASURING_POINT = MP.ID_MEASURING_POINT\n"
        f"  AND (RR.READ_STATUS = '{READ_CALCULATED}'\n"
        # only the MP's LATEST Available reading: live (MP 393444) an MP can carry Available readings back to
        # 2016 with IND_USAGE_TO_CAL 0 - flagging all of them would recalculate old history
        f"       OR (RR.READ_STATUS = '{READ_AVAILABLE}' AND ISNULL(RR.IND_USAGE_TO_CAL, 0) <> 1\n"
        f"           AND RR.ID_READING = (SELECT TOP 1 A.ID_READING FROM {READING_TBL} A\n"
        f"                                WHERE A.ID_MEASURING_POINT = MP.ID_MEASURING_POINT AND A.READ_STATUS = '{READ_AVAILABLE}'\n"
        "                                ORDER BY A.READING_DATE DESC, A.ID_READING DESC)))\n"
        f"WHERE {where}\n"
        "ORDER BY SS.NISS, MP.ID_MEASURING_POINT, RR.READING_DATE;"
    )


def _ids(values) -> list[int]:
    out = []
    for v in values:
        s = re.sub(r"\D", "", str(v or ""))
        if s and int(s) not in out:
            out.append(int(s))
    return out


def build_activate_script(rows: list[dict], mp_ids: list[int], *, program: str = DEFAULT_PROGRAM,
                          user: str = DEFAULT_USER, clean: bool = False) -> tuple[str, str, dict]:
    """rows = detect rows (lower-case keys); mp_ids = the selected MPs.
    Update: readings 4000STSRED -> 1000STSRED + IND_USAGE_TO_CAL = 1; MP 3000STAMPO -> 1000STAMPO;
    open hist row (END_DATE NULL) 3000STAMPO -> 1000STAMPO.
    Revert (RJ: "only the Measuring point table and mp_STATUS_HIST"): MP and open hist row back to
    the status each had at scan time."""
    sel = {str(i) for i in mp_ids}
    rs = [r for r in rows if str(r.get("id_measuring_point")) in sel]
    if not rs:
        raise ValueError("Select at least one measuring point.")
    usr, prog, rprog = format_sql_literal(user), format_sql_literal(program), format_sql_literal(program + " REVERT")
    stamp = f"UPDATE_DATE = GETDATE(), UPDATE_USER = {usr}, UPDATE_PROGRAM = {prog}"
    rstamp = f"UPDATE_DATE = GETDATE(), UPDATE_USER = {usr}, UPDATE_PROGRAM = {rprog}"
    readings = _ids(r.get("id_reading") for r in rs if str(r.get("read_status")) == READ_CALCULATED)
    avail = _ids(r.get("id_reading") for r in rs if str(r.get("read_status")) == READ_AVAILABLE
                 and str(r.get("ind_usage_to_cal") or "0").split(".")[0] != "1")
    mps = _ids(r.get("id_measuring_point") for r in rs if str(r.get("mp_status")) == MP_INACTIVE)
    hists = _ids(r.get("id_mp_status_hist") for r in rs if str(r.get("hist_status")) == MP_INACTIVE)
    # secondary MP -> temporarily Normal when one of its readings is (or becomes, step 1) Available, or has
    # no device. {mp_id: original ID_MAIN_MP} - the revert puts that main MP back.
    sec: dict[int, str] = {}
    for r in rs:
        if str(r.get("mp_type")) != MP_TYPE_SECONDARY or not r.get("id_reading"):
            continue
        st = str(r.get("read_status"))
        if st in (READ_AVAILABLE, READ_CALCULATED) or r.get("reading_id_device") in (None, ""):
            mid = _ids([r.get("id_measuring_point")])
            if mid:
                sec.setdefault(mid[0], str(r.get("id_main_mp") or "").split(".")[0])
    parts = []
    if not clean:
        parts.append(f"-- TMP ACTIVATE MP ({program}): {len(readings)} Calculated-usage reading(s), {len(avail)} Available "
                     f"reading(s) to IND_USAGE_TO_CAL = 1, {len(mps)} MP(s), {len(hists)} status-hist row(s)")
        seen = set()
        for r in rs:
            k = str(r.get("id_measuring_point"))
            if k in seen:
                continue
            seen.add(k)
            parts.append(f"--   NISS {r.get('niss')}  MP {k} {r.get('mp_status')}  open hist {r.get('id_mp_status_hist') or '-'} "
                         f"{r.get('hist_status') or ''}")
        parts.append("")
    if readings:
        if not clean:
            parts.append("-- 1) readings in Calculated usage -> Available, IND_USAGE_TO_CAL = 1")
        parts.append(f"UPDATE {READING_TBL}\nSET READ_STATUS = '{READ_AVAILABLE}', IND_USAGE_TO_CAL = 1, {stamp}\n"
                     f"WHERE READ_STATUS = '{READ_CALCULATED}' AND ID_READING IN ({', '.join(map(str, readings))});\n")
    if avail:   # RJ 2026-11-18
        if not clean:
            parts.append("-- 1b) readings already Available -> make sure IND_USAGE_TO_CAL = 1")
        parts.append(f"UPDATE {READING_TBL}\nSET IND_USAGE_TO_CAL = 1, {stamp}\n"
                     f"WHERE READ_STATUS = '{READ_AVAILABLE}' AND ISNULL(IND_USAGE_TO_CAL, 0) <> 1 "
                     f"AND ID_READING IN ({', '.join(map(str, avail))});\n")
    if mps:
        if not clean:
            parts.append("-- 2) measuring point Inactive (3000STAMPO) -> Connected / active (1000STAMPO)")
        parts.append(f"UPDATE {MP_TBL}\nSET STATUS = '{MP_ACTIVE}', {stamp}\n"
                     f"WHERE STATUS = '{MP_INACTIVE}' AND ID_MEASURING_POINT IN ({', '.join(map(str, mps))});\n")
    if hists:
        if not clean:
            parts.append("-- 3) open MP status history row (END_DATE NULL) 3000STAMPO -> 1000STAMPO")
        parts.append(f"UPDATE {HIST_TBL}\nSET ID_STATUS = '{MP_ACTIVE}', {stamp}\n"
                     f"WHERE ID_STATUS = '{MP_INACTIVE}' AND END_DATE IS NULL AND ID_MP_STATUS_HIST IN ({', '.join(map(str, hists))});\n")
    for mid, main in sec.items():
        if not clean:
            parts.append(f"-- 4) secondary MP {mid} (main MP {main or 'none'}) -> temporarily Normal: ID_MAIN_MP NULL, MP_TYPE {MP_TYPE_NORMAL}")
        guard = f"ID_MAIN_MP = {main}" if main else "ID_MAIN_MP IS NULL"
        parts.append(f"UPDATE {MP_TBL}\nSET ID_MAIN_MP = NULL, MP_TYPE = '{MP_TYPE_NORMAL}', {stamp}\n"
                     f"WHERE ID_MEASURING_POINT = {mid} AND MP_TYPE = '{MP_TYPE_SECONDARY}' AND {guard};\n")
    if not (readings or avail or mps or hists or sec):
        parts.append("-- nothing to update: no Calculated-usage / Available (IND_USAGE_TO_CAL <> 1) reading, MP not Inactive, open hist not Inactive")
    # revert - MP + MP_STATUS_HIST only, back to their scanned status
    rb = [f"-- REVERT of TMP ACTIVATE MP ({program}) - measuring point + MP status history only (readings are not reverted)\n"]
    if mps:
        rb.append(f"UPDATE {MP_TBL}\nSET STATUS = '{MP_INACTIVE}', {rstamp}\n"
                  f"WHERE STATUS = '{MP_ACTIVE}' AND UPDATE_PROGRAM = {prog} AND ID_MEASURING_POINT IN ({', '.join(map(str, mps))});\n")
    if hists:
        rb.append(f"UPDATE {HIST_TBL}\nSET ID_STATUS = '{MP_INACTIVE}', {rstamp}\n"
                  f"WHERE ID_STATUS = '{MP_ACTIVE}' AND END_DATE IS NULL AND UPDATE_PROGRAM = {prog} "
                  f"AND ID_MP_STATUS_HIST IN ({', '.join(map(str, hists))});\n")
    # after the status revert (that one is guarded on UPDATE_PROGRAM = the update's program)
    for mid, main in sec.items():
        rb.append(f"-- secondary MP {mid} back to Secondary under main MP {main or 'none'}\n"
                  f"UPDATE {MP_TBL}\nSET MP_TYPE = '{MP_TYPE_SECONDARY}', ID_MAIN_MP = {main or 'NULL'}, {rstamp}\n"
                  f"WHERE ID_MEASURING_POINT = {mid} AND MP_TYPE = '{MP_TYPE_NORMAL}' AND ID_MAIN_MP IS NULL;\n")
    if not (mps or hists or sec):
        rb.append("-- nothing to revert")
    sql = "\n".join(parts)
    if clean:
        sql = "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--"))
    return sql, "\n".join(rb), {"readings": len(readings), "available_readings": len(avail), "mps": len(mps), "hists": len(hists),
                                     "secondary_to_normal": len(sec)}
