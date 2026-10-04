"""
Anomalies Statistics (RJ, 2026-10-03): "create new menu anomalies
statistics, 2 parts billing anomaly and reading anomaly. billing anomaly is
in gccom_anomalous and reading anomaly is in re_anomalous, only status
000001 and 00009, pending and pending after batch" + "show it nicely with
graphs and everything, suggest and implement" + "show the types as well".

Scope (live-confirmed 2026-10-03):
- Billing anomalies: OUC_ADMIN.GCCOM_ANOMALOUS with ANOMALOUS_STATUS
  ESTAN00001 "Pending without billing" (11,258) and ESTAN00009 "Pending
  (after batch)" (9,868). Type = GCCOM_BILL_ANOMALY_COMPANY via
  ID_PRINCIPAL_ANOMALY, grouped by ANOMALY_COD (several company/sector ids
  share one code, e.g. HOLDPREVBI); category = GCCOM_ANOMALY.COD_GROUP ->
  GCCOM_ANOMALY_GROUP (System / Usage / Amount / Previous period / Others /
  Meter). Billing period only exists through the item to bill (13k of the
  21k have none - shown as "No item to bill"; deriving it from BILLING_DATE
  was checked and does NOT match the item-to-bill period).
- Reading anomalies: OUC_COMMON_ADMIN.GCGT_RE_ANOMALOUS. Its own status
  table has no ESTAN codes and no "after batch" state; the pending status
  is 1000ANMSTA "Pending to resolve" (7,753). One header carries one or
  more detail anomalies (GCGT_RE_ANOMALY -> GCGT_RE_ANOMALY_PARAM ->
  GCGT_RE_ANOMALY_MASTER), each with a level (1000 Warning / 2000 Stop
  Billing / 3000 Blocking). Main type = the most severe detail.

Performance: one reading query with per-row APPLYs took 43s; split into
headers / details / service lookups (~3s each) run in parallel and merged
in Python (shape_reading). Billing dataset is one query (~7s).

Pure logic: builds SELECT text and shapes rows; never executes anything.
"""
from __future__ import annotations

import datetime as _dt

BILLING_STATUSES = {"ESTAN00001": "Pending", "ESTAN00009": "Pending after batch"}
READING_STATUSES = {"1000ANMSTA": "Pending to resolve"}
READING_LEVELS = {"1000ANMLEV": "Warning", "2000ANMLEV": "Stop Billing", "3000ANMLEV": "Blocking"}

_B_STATUS_LIST = ", ".join(f"'{s}'" for s in BILLING_STATUSES)
_R_STATUS_LIST = ", ".join(f"'{s}'" for s in READING_STATUSES)


BILLING_LEVELS = ("Blocking", "Non-billable", "Warning")


def build_billing_query() -> str:
    """Severity (RJ 2026-10-04, "you are not showing if its a blocker like
    the reading anomaly"): each anomaly's detected details
    (GCCOM_DETECTED_ANOMALY -> GCCOM_ANOMALY_PARAM). Blocking = any detail
    with IND_STOP_BILLING = 1; Non-billable = ANOM_BILL_TYPE TFACA00001
    without stop billing; Warning = TFACA00000 only."""
    return f"""
WITH lv AS (
  SELECT x.ID_ANOMALOUS,
         MAX(CAST(p.IND_STOP_BILLING AS int)) AS STOP_BILLING,
         MAX(CASE WHEN p.ANOM_BILL_TYPE = 'TFACA00001' THEN 1 ELSE 0 END) AS NON_BILLABLE
  FROM OUC_ADMIN.GCCOM_ANOMALOUS a0 WITH (NOLOCK)
  JOIN OUC_ADMIN.GCCOM_DETECTED_ANOMALY x WITH (NOLOCK) ON x.ID_ANOMALOUS = a0.ID_ANOMALOUS
  JOIN OUC_ADMIN.GCCOM_ANOMALY_PARAM p ON p.ID_ANOMALY_PARAM = x.ID_ANOMALY_PARAM
  WHERE a0.ANOMALOUS_STATUS IN ({_B_STATUS_LIST})
  GROUP BY x.ID_ANOMALOUS
)
SELECT a.ID_ANOMALOUS, a.ANOMALOUS_STATUS, a.DETECTION_DATE, a.BILLING_DATE, a.ID_BILL, a.EXPECTED_AMOUNT,
       lv.STOP_BILLING, lv.NON_BILLABLE,
       c.ANOMALY_COD AS TYPE_CODE, COALESCE(dc.TEXT, c.DESCRIPTION) AS TYPE_DESC,
       an.COD_GROUP AS CATEGORY_CODE, COALESCE(dg.TEXT, g.NAME_TYPE) AS CATEGORY,
       cs.ID_OFFERED_SERVICE, os.NAME_TYPE AS OFFERED_SERVICE, pf.REFERENCE AS ACCOUNT, ss.NISS,
       itb.ID_BILLING_PERIOD, COALESCE(dbp.TEXT, bp.DESCRIPTION) AS BILLING_PERIOD
FROM OUC_ADMIN.GCCOM_ANOMALOUS a WITH (NOLOCK)
LEFT JOIN lv ON lv.ID_ANOMALOUS = a.ID_ANOMALOUS
LEFT JOIN OUC_ADMIN.GCCOM_BILL_ANOMALY_COMPANY c ON c.ID_BILL_ANOM_COMP = a.ID_PRINCIPAL_ANOMALY
LEFT JOIN GCTS_DICTIONARY dc ON dc.ID = c.NAME_TYPE_XI18N AND dc.LOCALE = 'EN'
LEFT JOIN OUC_ADMIN.GCCOM_ANOMALY an ON an.ANOMALY_COD = c.ANOMALY_COD
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_ANOMALY_GROUP g ON g.COD_DEVELOP = an.COD_GROUP
LEFT JOIN GCTS_DICTIONARY dg ON dg.ID = g.NAME_TYPE_XI18N AND dg.LOCALE = 'EN'
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_BILLING_SERVICE bs WITH (NOLOCK) ON bs.ID_BILLING_SERVICE = a.ID_BILLING_SERVICE
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK) ON cs.ID_CONTRACTED_SERVICE = bs.ID_CONTRACTED_SERVICE
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_COMPANY_OFFERED_SERVICE os ON os.ID_OFFERED_SERVICE = cs.ID_OFFERED_SERVICE
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = cs.ID_PAYMENT_FORM
LEFT JOIN GCCOM_SECTOR_SUPPLY ss WITH (NOLOCK) ON ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
LEFT JOIN OUC_ADMIN.GCCOM_ITEMS_TO_BILL itb WITH (NOLOCK) ON itb.ID_ITEM_TO_BILL = a.ID_ITEM_TO_BILL
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_BILLING_PERIOD bp ON bp.ID_BILLING_PERIOD = itb.ID_BILLING_PERIOD
LEFT JOIN GCTS_DICTIONARY dbp ON dbp.ID = bp.PERIOD_NAME_XI18N AND dbp.LOCALE = 'EN'
WHERE a.ANOMALOUS_STATUS IN ({_B_STATUS_LIST})
"""


def build_billing_count_query() -> str:
    return f"SELECT COUNT(*) AS N FROM OUC_ADMIN.GCCOM_ANOMALOUS WITH (NOLOCK) WHERE ANOMALOUS_STATUS IN ({_B_STATUS_LIST})"


def build_reading_headers_query() -> str:
    return f"""
SELECT a.ID_ANOMALOUS, a.STATUS, a.ANOMALY_DATE, a.COD_GROUP, COALESCE(dgr.TEXT, gr.NAME_TYPE) AS GROUP_DESC,
       a.ID_MEASURING_POINT, mp.ID_SECTOR_SUPPLY, mp.MP_TYPE, ss.NISS
FROM OUC_COMMON_ADMIN.GCGT_RE_ANOMALOUS a WITH (NOLOCK)
LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_ANOMALOUS_GROUP gr ON gr.COD_DEVELOP = a.COD_GROUP
LEFT JOIN GCTS_DICTIONARY dgr ON dgr.ID = gr.NAME_TYPE_XI18N AND dgr.LOCALE = 'EN'
LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_MEASUREMENT_POINT mp WITH (NOLOCK) ON mp.ID_MEASURING_POINT = a.ID_MEASURING_POINT
LEFT JOIN GCCOM_SECTOR_SUPPLY ss WITH (NOLOCK) ON ss.ID_SECTOR_SUPPLY = mp.ID_SECTOR_SUPPLY
WHERE a.STATUS IN ({_R_STATUS_LIST})
"""


def build_reading_details_query() -> str:
    """One row per detail anomaly of every pending header, with its type
    (master), level and the reading it points to."""
    return f"""
SELECT x.ID_ANOMALOUS, x.ID_ANOMALY, p.ID_ANOMALY_MASTER, m.ANOMALY_CODE AS TYPE_CODE,
       COALESCE(dm.TEXT, m.ANOMALY_NAME) AS TYPE_DESC, p.ANOMALY_LEVEL,
       x.ID_READING, r.READING_TYPE, COALESCE(drt.TEXT, rt.DESCRIPTION) AS READING_TYPE_DESC,
       r.ID_BILLING_PERIOD, COALESCE(dbp.TEXT, bp.DESCRIPTION) AS BILLING_PERIOD
FROM OUC_COMMON_ADMIN.GCGT_RE_ANOMALOUS a WITH (NOLOCK)
JOIN OUC_COMMON_ADMIN.GCGT_RE_ANOMALY x WITH (NOLOCK) ON x.ID_ANOMALOUS = a.ID_ANOMALOUS
LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_ANOMALY_PARAM p ON p.ID_ANOMALY_PARAM = x.ID_ANOMALY_PARAM
LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_ANOMALY_MASTER m ON m.ID_ANOMALY_MASTER = p.ID_ANOMALY_MASTER
LEFT JOIN GCTS_DICTIONARY dm ON dm.ID = m.ANOMALY_NAME_XI18N AND dm.LOCALE = 'EN'
LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_READING r WITH (NOLOCK) ON r.ID_READING = x.ID_READING
LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_READING_TYPE rt ON rt.COD_DEVELOP = r.READING_TYPE
LEFT JOIN GCTS_DICTIONARY drt ON drt.ID = rt.DESCRIPTION_XI18N AND drt.LOCALE = 'EN'
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_BILLING_PERIOD bp ON bp.ID_BILLING_PERIOD = r.ID_BILLING_PERIOD
LEFT JOIN GCTS_DICTIONARY dbp ON dbp.ID = bp.PERIOD_NAME_XI18N AND dbp.LOCALE = 'EN'
WHERE a.STATUS IN ({_R_STATUS_LIST})
"""


def build_reading_services_query() -> str:
    """Offered service + account of each affected supply (latest
    non-cancelled contracted service first)."""
    return f"""
WITH s AS (
  SELECT DISTINCT mp.ID_SECTOR_SUPPLY
  FROM OUC_COMMON_ADMIN.GCGT_RE_ANOMALOUS a WITH (NOLOCK)
  JOIN OUC_COMMON_ADMIN.GCGT_RE_MEASUREMENT_POINT mp WITH (NOLOCK) ON mp.ID_MEASURING_POINT = a.ID_MEASURING_POINT
  WHERE a.STATUS IN ({_R_STATUS_LIST})
), c AS (
  SELECT cs.ID_SECTOR_SUPPLY, cs.ID_OFFERED_SERVICE, cs.ID_PAYMENT_FORM,
         ROW_NUMBER() OVER (PARTITION BY cs.ID_SECTOR_SUPPLY
                            ORDER BY CASE WHEN cs.STATUS = 'ESTSC00005' THEN 1 ELSE 0 END, cs.ID_CONTRACTED_SERVICE DESC) AS RN
  FROM s JOIN OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK) ON cs.ID_SECTOR_SUPPLY = s.ID_SECTOR_SUPPLY
)
SELECT c.ID_SECTOR_SUPPLY, c.ID_OFFERED_SERVICE, os.NAME_TYPE AS OFFERED_SERVICE, pf.REFERENCE AS ACCOUNT
FROM c
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_COMPANY_OFFERED_SERVICE os ON os.ID_OFFERED_SERVICE = c.ID_OFFERED_SERVICE
LEFT JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = c.ID_PAYMENT_FORM
WHERE c.RN = 1
"""


def build_reading_count_query() -> str:
    return f"SELECT COUNT(*) AS N FROM OUC_COMMON_ADMIN.GCGT_RE_ANOMALOUS WITH (NOLOCK) WHERE STATUS IN ({_R_STATUS_LIST})"


# --------------------------------------------------------------------------
# Shaping - rows arrive as dicts with lower-case keys and display strings.
# Output is compact (short keys) because the browser aggregates ~21k rows.
# --------------------------------------------------------------------------

def _d(v) -> str | None:
    """'2026-10-01 00:00:00' -> '2026-10-01'."""
    s = str(v or "").strip()
    return s[:10] if s else None


def _s(v) -> str | None:
    s = str(v if v is not None else "").strip()
    return s or None


def _f(v) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _billing_level(stop, non_billable) -> str:
    stop, nb = _s(stop), _s(non_billable)
    if stop is None and nb is None:
        return "(no level)"
    if stop not in (None, "0"):
        return "Blocking"
    if nb not in (None, "0"):
        return "Non-billable"
    return "Warning"


def shape_billing(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        code = _s(r.get("type_code")) or "(no type)"
        out.append({
            "id": _s(r.get("id_anomalous")),
            "st": _s(r.get("anomalous_status")),
            "lv": _billing_level(r.get("stop_billing"), r.get("non_billable")),
            "dt": _d(r.get("detection_date")),
            "bd": _d(r.get("billing_date")),
            "tc": code,
            "td": _s(r.get("type_desc")) or code,
            "cat": _s(r.get("category")) or "(no category)",
            "svc": _s(r.get("offered_service")) or "(unknown)",
            "acct": _s(r.get("account")),
            "niss": _s(r.get("niss")),
            "bp": _s(r.get("id_billing_period")),
            "bpd": _s(r.get("billing_period")) or ("No item to bill" if not _s(r.get("id_billing_period")) else None),
            "bill": _s(r.get("id_bill")),
            "amt": _f(r.get("expected_amount")),
        })
    return out


def _lvl_rank(code: str | None) -> int:
    return {"3000ANMLEV": 3, "2000ANMLEV": 2, "1000ANMLEV": 1}.get(code or "", 0)


def shape_reading(headers: list[dict], details: list[dict], services: list[dict]) -> list[dict]:
    svc_by_supply = {str(s.get("id_sector_supply")): s for s in services}
    det_by_hdr: dict[str, list[dict]] = {}
    for d in details:
        det_by_hdr.setdefault(str(d.get("id_anomalous")), []).append(d)
    out = []
    for h in headers:
        hid = str(h.get("id_anomalous"))
        dets = det_by_hdr.get(hid, [])
        # main detail = most severe level, then lowest ID_ANOMALY
        main = None
        if dets:
            main = sorted(dets, key=lambda d: (-_lvl_rank(_s(d.get("anomaly_level"))), str(d.get("id_anomaly") or "")))[0]
        types = []
        seen = set()
        for d in sorted(dets, key=lambda d: -_lvl_rank(_s(d.get("anomaly_level")))):
            code = _s(d.get("type_code")) or "(no type)"
            if code in seen:
                continue
            seen.add(code)
            types.append({"tc": code, "td": _s(d.get("type_desc")) or code,
                          "lv": READING_LEVELS.get(_s(d.get("anomaly_level")) or "", "(no level)")})
        svc = svc_by_supply.get(str(h.get("id_sector_supply")), {})
        lvl_code = _s(main.get("anomaly_level")) if main else None
        out.append({
            "id": hid,
            "st": _s(h.get("status")),
            "dt": _d(h.get("anomaly_date")),
            "grp": _s(h.get("group_desc")) or "(no group)",
            "mp": _s(h.get("id_measuring_point")),
            "mpt": _s(h.get("mp_type")),
            "niss": _s(h.get("niss")),
            "tc": types[0]["tc"] if types else "(no detail)",
            "td": types[0]["td"] if types else "(no detail)",
            "lv": READING_LEVELS.get(lvl_code or "", "(no level)"),
            "types": types,
            "nd": len(dets),
            "rd": _s(main.get("id_reading")) if main else None,
            "rt": (_s(main.get("reading_type_desc")) if main else None) or "(no reading)",
            "bp": _s(main.get("id_billing_period")) if main else None,
            "bpd": (_s(main.get("billing_period")) if main else None) or "(no reading)",
            "svc": _s(svc.get("offered_service")) or "(unknown)",
            "acct": _s(svc.get("account")),
        })
    return out


def generated_at() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")
