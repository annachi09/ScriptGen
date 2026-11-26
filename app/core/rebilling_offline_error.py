"""
Rebilling Monitor tab 3 - "Rebilling complete with offline task error" (RJ 2026-11-26).

RJ: "create a new tab in rebilling monitor menu, rebilling complete with offline task error. there will be
2 main case, 1 the RCCS, 2 the Service request. Reverse this correction scripts to find all the cases, add the
correction script to correct them" (+ his two fix scripts: RCCS = MODULE0010 / ACTI600007, SR = MODULE0011 /
ACTI600008, ACTION_STATUS ACTSTA0001 -> ACTSTA0002 on GCGT_WF_CACTIVITY).

What happens (read live 2026-11-26):
- The massive rebilling of an RCCS / SR is closed by the workflow action "End Massive Rebilling Activity"
  (GCGT_WF_ACTION ACTI600007 for RCCS MODULE0010, ACTI600008 for SR MODULE0011). When the rebilling activity is
  accepted (GCCOM_REBILLING_ACTIVITY -> ESTREF0004 Rebilled, acceptRebillingActivity) the offline task should
  solve that action; when it errors, the action stays Pending (ACTSTA0001) in GCGT_WF_CACTIVITY and the case
  never moves on. Example RCALL260613631: rebilled 2026-06-24 13:53, action pending until the fix of 2026-11-10.
- The offline task error itself is not traceable to the activity (GCOFF_TASK has no reference to it), so the
  case is found by its state: action pending + its rebilling finished.
- Link to the rebilling activity:
    RCCS - GCCOM_REBILLING_ACTIVITY.ID_RECLAMATION = ID_COMPLAINT (the rebilling is created after the action).
    SR   - ID_RECLAMATION is empty: SELECTED_ACCOUNT = SR.ID_ACCOUNT, created between the SR creation and the
           action + 10 min (or ID_RECLAMATION = ID_SERVREQ when filled). Live: all 34 pending SR actions matched.
- State per case: "complete" = no rebilling still open and the latest one Rebilled -> the fix applies.
  Otherwise "open" (rebilling still Pending / In process - not an error yet), "rejected" (latest Rejected),
  "none" (no rebilling activity found).

Fix script = RJ's two scripts, per case type, with the selected codes, plus an ID_ACTIVITY guard; the rollback
puts back ACTSTA0001 and the original UPDATE_* per activity, guarded on the new status + program.

Pure logic: builds SQL text only.
"""
from __future__ import annotations

from .sql_format import format_sql_literal
from .search_inputs import sql_list

CASE_RCCS = "RCCS"
CASE_SR = "SR"
CASES = {
    CASE_RCCS: {"module": "MODULE0010", "action": "ACTI600007", "label": "RCCS",
                "program": "EWAOUCEAM-13011"},
    CASE_SR: {"module": "MODULE0011", "action": "ACTI600008", "label": "Service request",
              "program": "EWAOUCEAM-15330"},
}
ACT_PENDING = "ACTSTA0001"
ACT_SOLVED = "ACTSTA0002"
REB_OPEN = ("ESTREF0001", "ESTREF0002")
REB_REJECTED = "ESTREF0003"
REB_REBILLED = "ESTREF0004"
REB_STATUS_NAMES = {"ESTREF0001": "Pending", "ESTREF0002": "In process", "ESTREF0003": "Rejected",
                    "ESTREF0004": "Rebilled"}
STATES = {
    "complete": "Rebilling complete - action still pending",
    "open": "Rebilling still open",
    "rejected": "Latest rebilling rejected",
    "none": "No rebilling activity found",
}
DEFAULT_USER = "RMA"
SR_MATCH_MINUTES = 10


def build_detect_query(inputs: list[str] | None = None) -> str:
    """inputs (RJ 2026-11-26 Search screen): RCCS / SR codes, account references or ids - keeps only those."""
    r, s = CASES[CASE_RCCS], CASES[CASE_SR]
    L = sql_list(inputs) if inputs else ""
    search = (f"""
-- Search: only the RCCS / SR / accounts entered
DELETE FROM #c WHERE NOT (REF_CODE IN ({L}) OR CAST(ID_OBJECT AS varchar(30)) IN ({L})
    OR CAST(ID_ACCOUNT AS varchar(30)) IN ({L})
    OR ID_ACCOUNT IN (SELECT ID_PAYMENT_FORM FROM GCCOM_PAYMENT_FORM WITH (NOLOCK) WHERE REFERENCE IN ({L})));
""" if inputs else "")
    reb_open = ", ".join(f"'{x}'" for x in REB_OPEN)
    return f"""
SET NOCOUNT ON;
IF OBJECT_ID('tempdb..#c') IS NOT NULL DROP TABLE #c;
IF OBJECT_ID('tempdb..#m') IS NOT NULL DROP TABLE #m;

-- 1) "End Massive Rebilling Activity" actions still Pending (RCCS + SR)
SELECT '{CASE_RCCS}' AS CASE_TYPE, c.ID_ACTIVITY, c.ID_OBJECT, c.COD_STEP, c.ACTION_DATE_TS,
       c.UPDATE_DATE, c.UPDATE_USER, c.UPDATE_PROGRAM,
       cp.COD_REF_COMPLAINT AS REF_CODE, cp.ID_ACCOUNT, cp.COD_STATUS_COMPLAINT AS OBJ_STATUS, cp.CREATE_DATE AS OBJ_CREATED
INTO #c
FROM GCGT_WF_CACTIVITY c WITH (NOLOCK)
JOIN GCCC_COMPLAINT cp WITH (NOLOCK) ON cp.ID_COMPLAINT = c.ID_OBJECT
WHERE c.COD_MODULE = '{r["module"]}' AND c.COD_ACTION = '{r["action"]}' AND c.ACTION_STATUS = '{ACT_PENDING}'
UNION ALL
SELECT '{CASE_SR}', c.ID_ACTIVITY, c.ID_OBJECT, c.COD_STEP, c.ACTION_DATE_TS,
       c.UPDATE_DATE, c.UPDATE_USER, c.UPDATE_PROGRAM,
       sr.SR_CODE, sr.ID_ACCOUNT, sr.SERVREQ_STATUS, sr.CREATION_SR_DATE
FROM GCGT_WF_CACTIVITY c WITH (NOLOCK)
JOIN GCCOM_SERVICE_REQUEST sr WITH (NOLOCK) ON sr.ID_SERVREQ = c.ID_OBJECT
WHERE c.COD_MODULE = '{s["module"]}' AND c.COD_ACTION = '{s["action"]}' AND c.ACTION_STATUS = '{ACT_PENDING}';
{search}
-- 2) their rebilling activities (RCCS: ID_RECLAMATION; SR: account + time window)
SELECT c.ID_ACTIVITY, ra.ID_REBILLING_ACTIVITY, ra.REB_ACTIVITY_STATUS, ra.CREATE_DATE, ra.UPDATE_DATE,
       ra.UPDATE_PROGRAM, ra.RESOLUTION_DATE, ra.BILLS_NUMBER,
       LEFT(CAST(ra.PROCESSING_INFO AS nvarchar(4000)), 300) AS PROCESSING_INFO,
       ROW_NUMBER() OVER (PARTITION BY c.ID_ACTIVITY ORDER BY ra.CREATE_DATE DESC, ra.ID_REBILLING_ACTIVITY DESC) AS RN
INTO #m
FROM #c c
JOIN OUC_ADMIN.GCCOM_REBILLING_ACTIVITY ra WITH (NOLOCK)
  ON (c.CASE_TYPE = '{CASE_RCCS}' AND ra.ID_RECLAMATION = c.ID_OBJECT)
  OR (c.CASE_TYPE = '{CASE_SR}' AND (ra.ID_RECLAMATION = c.ID_OBJECT
      OR (ra.SELECTED_ACCOUNT = c.ID_ACCOUNT AND ra.CREATE_DATE >= c.OBJ_CREATED
          AND ra.CREATE_DATE <= DATEADD(MINUTE, {SR_MATCH_MINUTES}, c.ACTION_DATE_TS))));

SELECT c.CASE_TYPE, c.ID_ACTIVITY, c.ID_OBJECT, c.REF_CODE, c.ID_ACCOUNT, apf.REFERENCE AS ACCOUNT_REF, c.OBJ_STATUS, c.OBJ_CREATED,
       c.COD_STEP, c.ACTION_DATE_TS, DATEDIFF(DAY, c.ACTION_DATE_TS, GETDATE()) AS ACTION_AGE_DAYS,
       c.UPDATE_DATE, c.UPDATE_USER, c.UPDATE_PROGRAM,
       ISNULL(ag.REB_COUNT, 0) AS REB_COUNT, ISNULL(ag.REB_OPEN, 0) AS REB_OPEN, ISNULL(ag.REB_REBILLED, 0) AS REB_REBILLED,
       ag.REB_IDS,
       l.ID_REBILLING_ACTIVITY AS LAST_REB_ID, l.REB_ACTIVITY_STATUS AS LAST_REB_STATUS,
       l.CREATE_DATE AS LAST_REB_CREATED, l.UPDATE_DATE AS LAST_REB_UPDATED, l.UPDATE_PROGRAM AS LAST_REB_PROGRAM,
       l.RESOLUTION_DATE AS LAST_REB_RESOLVED, l.BILLS_NUMBER AS LAST_REB_BILLS, l.PROCESSING_INFO AS LAST_REB_INFO,
       DATEDIFF(DAY, l.UPDATE_DATE, GETDATE()) AS STUCK_DAYS
FROM #c c
LEFT JOIN GCCOM_PAYMENT_FORM apf WITH (NOLOCK) ON apf.ID_PAYMENT_FORM = c.ID_ACCOUNT
LEFT JOIN (
    SELECT ID_ACTIVITY, COUNT(*) AS REB_COUNT,
           SUM(CASE WHEN REB_ACTIVITY_STATUS IN ({reb_open}) THEN 1 ELSE 0 END) AS REB_OPEN,
           SUM(CASE WHEN REB_ACTIVITY_STATUS = '{REB_REBILLED}' THEN 1 ELSE 0 END) AS REB_REBILLED,
           STRING_AGG(CAST(ID_REBILLING_ACTIVITY AS varchar(20)) + ':' + REB_ACTIVITY_STATUS, ', ') AS REB_IDS
    FROM #m GROUP BY ID_ACTIVITY
) ag ON ag.ID_ACTIVITY = c.ID_ACTIVITY
LEFT JOIN #m l ON l.ID_ACTIVITY = c.ID_ACTIVITY AND l.RN = 1
ORDER BY c.CASE_TYPE, l.UPDATE_DATE;

DROP TABLE #c; DROP TABLE #m;
"""


def _int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def case_state(r: dict) -> str:
    if _int(r.get("reb_count")) == 0:
        return "none"
    if _int(r.get("reb_open")) > 0:
        return "open"
    if (r.get("last_reb_status") or "") == REB_REBILLED:
        return "complete"
    return "rejected"


def shape_rows(rows: list[dict]) -> list[dict]:
    for r in rows:
        r["state"] = case_state(r)
        r["state_en"] = STATES[r["state"]]
        r["case_label"] = CASES.get(r.get("case_type"), {}).get("label", r.get("case_type"))
        r["last_reb_status_en"] = REB_STATUS_NAMES.get(r.get("last_reb_status") or "", r.get("last_reb_status") or "")
    return rows


def summary(rows: list[dict]) -> dict:
    out = {"count": len(rows), "complete": 0, "by_case": {}, "by_state": {}}
    for r in rows:
        out["by_state"][r["state"]] = out["by_state"].get(r["state"], 0) + 1
        c = out["by_case"].setdefault(r["case_type"], {"total": 0, "complete": 0})
        c["total"] += 1
        if r["state"] == "complete":
            c["complete"] += 1
            out["complete"] += 1
    return out


def _lit(v) -> str:
    return format_sql_literal(v) if v not in (None, "") else "NULL"


def build_fix_script(rows: list[dict], activity_ids: list[str], *, user: str, programs: dict,
                     clean: bool = False) -> tuple[str, str, dict]:
    """rows: live re-scan (shaped). Only selected activities still in state "complete" are fixed.
    Returns (script, rollback, counts)."""
    want = {str(a).strip() for a in activity_ids if str(a).strip()}
    pick = [r for r in rows if str(r.get("id_activity")) in want and r.get("state") == "complete"]
    skipped = sorted(want - {str(r.get("id_activity")) for r in pick})
    if not pick:
        raise ValueError("None of the selected cases is still 'rebilling complete with the action pending' - re-scan.")
    user = (user or DEFAULT_USER).strip()[:25]
    parts, rb = [], []
    head = [f"-- Rebilling complete with offline task error - {len(pick)} case(s)",
            "-- 'End Massive Rebilling Activity' action Pending (ACTSTA0001) -> Solved (ACTSTA0002)",
            "-- the rebilling activity of each case is already Rebilled (ESTREF0004)"]
    parts.extend(head)
    rb.append("-- ROLLBACK of: Rebilling complete with offline task error")
    rb.append("-- puts each action back to Pending (ACTSTA0001) with its original UPDATE_* values;")
    rb.append("-- only rows still Solved by this program are touched (if the workflow already moved on, nothing changes)")
    counts = {"rccs": 0, "sr": 0, "skipped": skipped}
    for ct in (CASE_RCCS, CASE_SR):
        sel = [r for r in pick if r["case_type"] == ct]
        if not sel:
            continue
        cfg = CASES[ct]
        program = (programs.get(ct) or cfg["program"]).strip()[:100]
        codes = ", ".join(format_sql_literal(r["ref_code"]) for r in sel)
        acts = ", ".join(str(_int(r["id_activity"])) for r in sel)
        counts["rccs" if ct == CASE_RCCS else "sr"] = len(sel)
        if ct == CASE_RCCS:
            obj = f"(SELECT ID_COMPLAINT FROM GCCC_COMPLAINT WHERE COD_REF_COMPLAINT IN ({codes}))"
        else:
            obj = f"(SELECT ID_SERVREQ FROM GCCOM_SERVICE_REQUEST WHERE SR_CODE IN ({codes}))"
        # RJ 2026-11-26: "do not use begin, and delcare, just the normal script" - plain UPDATE, values inline
        parts += ["", f"-- REBILLING FIX - {cfg['label']} ({len(sel)}) - {cfg['action']}",
                  "UPDATE GCGT_WF_CACTIVITY",
                  f"SET UPDATE_USER = {format_sql_literal(user)},",
                  f"    UPDATE_PROGRAM = {format_sql_literal(program)},",
                  "    UPDATE_DATE = GETDATE(),",
                  f"    ACTION_STATUS = '{ACT_SOLVED}'",
                  f"WHERE COD_MODULE = '{cfg['module']}'",
                  f"  AND ID_OBJECT IN {obj}",
                  f"  AND COD_ACTION = '{cfg['action']}'",
                  f"  AND ACTION_STATUS = '{ACT_PENDING}'",
                  f"  AND ID_ACTIVITY IN ({acts});"]
        rb += ["", f"-- {cfg['label']} ({len(sel)})"]
        for r in sel:
            rb += [f"-- {r['ref_code']}",
                   "UPDATE GCGT_WF_CACTIVITY",
                   f"SET ACTION_STATUS = '{ACT_PENDING}', UPDATE_USER = {_lit(r.get('update_user'))},"
                   f" UPDATE_PROGRAM = {_lit(r.get('update_program'))}, UPDATE_DATE = {_lit(str(r.get('update_date') or '')[:23])}",
                   f"WHERE ID_ACTIVITY = {_int(r['id_activity'])} AND COD_ACTION = '{cfg['action']}'"
                   f" AND ACTION_STATUS = '{ACT_SOLVED}' AND UPDATE_PROGRAM = {format_sql_literal(program)};"]
    sql = "\n".join(parts) + "\n"
    rollback = "\n".join(rb) + "\n"
    if clean:
        sql = "\n".join(l for l in sql.splitlines() if not l.lstrip().startswith("--")) + "\n"
        rollback = "\n".join(l for l in rollback.splitlines() if not l.lstrip().startswith("--")) + "\n"
    return sql, rollback, counts
