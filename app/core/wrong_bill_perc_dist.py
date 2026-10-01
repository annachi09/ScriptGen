"""
Wrong Bill - Case 2: Percentage distribution primary with a metered secondary
(RJ, 2026-10-01).

RJ: "list all primary with calculation module percentage distribution yet
exist a secondary which is having id_device and the status of secondary
measuring point of main and secondary is not inactive, list the main per
row and the secondaries as details".

- Primary: GCGT_RE_MEASUREMENT_POINT with ID_CALCULATION_MODULE = 1150
  ("Hierarchy - Percentage of association between Primary and Secondary"),
  STATUS not Inactive (3000STAMPO).
- Secondary: a measuring point whose ID_MAIN_MP is the primary, STATUS not
  Inactive, with a CURRENT device in GCGT_RE_MEASURING_POINT_DEVICE
  (REMOVAL_DATE IS NULL). Live 2026-10-01: 984 primaries / 1,202 such
  secondaries (1,192 are TIPEQM0005 Principal acoplado, 10 TIPEQM0002).
  After excluding Main coupled (see MP_TYPE_MAIN_COUPLED): 2 primaries /
  10 metered Secondary points.
- Detail rows: EVERY non-inactive secondary of a flagged primary is
  returned (HAS_DEVICE = 1/0), so the drill-down shows the whole
  hierarchy and the % split; the flag/KPIs count HAS_DEVICE = 1 only.

Pure logic: builds SELECT text only.
"""
from __future__ import annotations

SCHEMA = "OUC_COMMON_ADMIN"
CALC_MODULE_PERCENTAGE = 1150
MP_STATUS_INACTIVE = "3000STAMPO"
# RJ 2026-10-01: "do not consider main couple as this one is expected to
# have meter" - a metered Principal acoplado secondary does NOT flag the
# primary (it's still listed in the details for the % split, not highlighted).
MP_TYPE_MAIN_COUPLED = "TIPEQM0005"


def _en(alias: str, base_alias: str, base_col: str, _unused: str = "") -> str:
    """English text from GCTS_DICTIONARY (LOCALE 'EN'), falling back to the
    native column; strips the stray right-quote the dictionary has on
    'Maincoupled'."""
    return (f"REPLACE(COALESCE({alias}.TEXT, {base_alias}.{base_col}), NCHAR(8217), '')")


def build_query() -> str:
    s = SCHEMA
    return f"""
WITH flagged AS (
    SELECT DISTINCT p.ID_MEASURING_POINT
    FROM {s}.GCGT_RE_MEASUREMENT_POINT p WITH (NOLOCK)
    JOIN {s}.GCGT_RE_MEASUREMENT_POINT c WITH (NOLOCK)
         ON c.ID_MAIN_MP = p.ID_MEASURING_POINT AND c.ID_MEASURING_POINT <> p.ID_MEASURING_POINT
    WHERE p.ID_CALCULATION_MODULE = {CALC_MODULE_PERCENTAGE}
      AND ISNULL(p.STATUS, '') <> '{MP_STATUS_INACTIVE}'
      AND ISNULL(c.STATUS, '') <> '{MP_STATUS_INACTIVE}'
      AND ISNULL(c.MP_TYPE, '') <> '{MP_TYPE_MAIN_COUPLED}'
      AND EXISTS (SELECT 1 FROM {s}.GCGT_RE_MEASURING_POINT_DEVICE d WITH (NOLOCK)
                  WHERE d.ID_MEASURING_POINT = c.ID_MEASURING_POINT AND d.REMOVAL_DATE IS NULL)
)
SELECT
    p.ID_MEASURING_POINT AS P_ID_MP,
    pss.NISS AS P_NISS,
    pacc.REFERENCE AS P_ACCOUNT,
    p.MP_TYPE AS P_MP_TYPE,
    {_en("dpt", "pt", "DESCRIPTION", "")} AS P_MP_TYPE_DESC,
    p.STATUS AS P_STATUS,
    {_en("dpst", "pst", "NAME_TYPE", "")} AS P_STATUS_DESC,
    p.ID_CALCULATION_MODULE AS P_CALC_MODULE,
    {_en("dcm", "cm", "NAME_TYPE", "")} AS P_CALC_MODULE_DESC,
    p.PERC_DIST AS P_PERC_DIST,
    p.IND_DIST_PPAL AS P_IND_DIST_PPAL,
    pdev.ID_DEVICE AS P_ID_DEVICE,
    c.ID_MEASURING_POINT AS S_ID_MP,
    css.NISS AS S_NISS,
    c.MP_TYPE AS S_MP_TYPE,
    {_en("dct", "ct", "DESCRIPTION", "")} AS S_MP_TYPE_DESC,
    c.STATUS AS S_STATUS,
    {_en("dcst", "cst", "NAME_TYPE", "")} AS S_STATUS_DESC,
    c.ID_CALCULATION_MODULE AS S_CALC_MODULE,
    c.PERC_DIST AS S_PERC_DIST,
    c.IND_DIST_PPAL AS S_IND_DIST_PPAL,
    cdev.ID_DEVICE AS S_ID_DEVICE,
    dv.SERIAL_NUM AS S_SERIAL_NUM,
    cdev.INSTALLATION_DATE AS S_INSTALLATION_DATE,
    CASE WHEN cdev.ID_DEVICE IS NULL OR ISNULL(c.MP_TYPE, '') = '{MP_TYPE_MAIN_COUPLED}' THEN 0 ELSE 1 END AS S_HAS_DEVICE
FROM flagged f
JOIN {s}.GCGT_RE_MEASUREMENT_POINT p WITH (NOLOCK) ON p.ID_MEASURING_POINT = f.ID_MEASURING_POINT
JOIN {s}.GCGT_RE_MEASUREMENT_POINT c WITH (NOLOCK)
     ON c.ID_MAIN_MP = p.ID_MEASURING_POINT AND c.ID_MEASURING_POINT <> p.ID_MEASURING_POINT
    AND ISNULL(c.STATUS, '') <> '{MP_STATUS_INACTIVE}'
LEFT JOIN GCCOM_SECTOR_SUPPLY pss WITH (NOLOCK) ON pss.ID_SECTOR_SUPPLY = p.ID_SECTOR_SUPPLY
LEFT JOIN GCCOM_SECTOR_SUPPLY css WITH (NOLOCK) ON css.ID_SECTOR_SUPPLY = c.ID_SECTOR_SUPPLY
OUTER APPLY (
    SELECT TOP 1 pf.REFERENCE
    FROM GCCOM_CONTRACTED_SERVICE cs WITH (NOLOCK)
    JOIN GCCOM_PAYMENT_FORM pf WITH (NOLOCK) ON pf.ID_PAYMENT_FORM = cs.ID_PAYMENT_FORM
    WHERE cs.ID_SECTOR_SUPPLY = p.ID_SECTOR_SUPPLY
    ORDER BY cs.ID_CONTRACTED_SERVICE DESC
) pacc
OUTER APPLY (
    SELECT TOP 1 d.ID_DEVICE FROM {s}.GCGT_RE_MEASURING_POINT_DEVICE d WITH (NOLOCK)
    WHERE d.ID_MEASURING_POINT = p.ID_MEASURING_POINT AND d.REMOVAL_DATE IS NULL
    ORDER BY d.INSTALLATION_DATE DESC
) pdev
OUTER APPLY (
    SELECT TOP 1 d.ID_DEVICE, d.INSTALLATION_DATE FROM {s}.GCGT_RE_MEASURING_POINT_DEVICE d WITH (NOLOCK)
    WHERE d.ID_MEASURING_POINT = c.ID_MEASURING_POINT AND d.REMOVAL_DATE IS NULL
    ORDER BY d.INSTALLATION_DATE DESC
) cdev
LEFT JOIN OUCW_ADMIN.GCGT_ME_DEVICE dv WITH (NOLOCK) ON dv.ID_DEVICE = cdev.ID_DEVICE
LEFT JOIN {s}.GCGT_RE_MP_TYPE pt ON pt.COD_DEVELOP = p.MP_TYPE
LEFT JOIN {s}.GCGT_RE_MP_TYPE ct ON ct.COD_DEVELOP = c.MP_TYPE
LEFT JOIN {s}.GCGT_RE_MEASURE_POINT_STATUS pst ON pst.COD_DEVELOP = p.STATUS
LEFT JOIN {s}.GCGT_RE_MEASURE_POINT_STATUS cst ON cst.COD_DEVELOP = c.STATUS
LEFT JOIN {s}.GCCOM_CALCULATION_MODULE cm ON cm.ID_CALCULATION_MODULE = p.ID_CALCULATION_MODULE
LEFT JOIN GCTS_DICTIONARY dpt ON dpt.ID = pt.DESCRIPTION_XI18N AND dpt.LOCALE = 'EN'
LEFT JOIN GCTS_DICTIONARY dct ON dct.ID = ct.DESCRIPTION_XI18N AND dct.LOCALE = 'EN'
LEFT JOIN GCTS_DICTIONARY dpst ON dpst.ID = pst.NAME_TYPE_XI18N AND dpst.LOCALE = 'EN'
LEFT JOIN GCTS_DICTIONARY dcst ON dcst.ID = cst.NAME_TYPE_XI18N AND dcst.LOCALE = 'EN'
LEFT JOIN GCTS_DICTIONARY dcm ON dcm.ID = cm.NAME_TYPE_XI18N AND dcm.LOCALE = 'EN'
ORDER BY p.ID_MEASURING_POINT, S_HAS_DEVICE DESC, c.ID_MEASURING_POINT
"""


PRIMARY_KEYS = ("p_id_mp", "p_niss", "p_account", "p_mp_type", "p_mp_type_desc", "p_status", "p_status_desc",
                "p_calc_module", "p_calc_module_desc", "p_perc_dist", "p_ind_dist_ppal", "p_id_device")


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def group_rows(rows: list[dict]) -> list[dict]:
    """Flat (primary x secondary) rows -> one dict per primary with a
    'secondaries' list and summary fields. rows have lowercase keys."""
    out: dict = {}
    for r in rows:
        key = r.get("p_id_mp")
        g = out.get(key)
        if g is None:
            g = {k: r.get(k) for k in PRIMARY_KEYS}
            g["secondaries"] = []
            out[key] = g
        g["secondaries"].append({k[2:]: v for k, v in r.items() if k.startswith("s_")})
    for g in out.values():
        secs = g["secondaries"]
        metered = [x for x in secs if str(x.get("has_device")) == "1"]
        g["secondary_count"] = len(secs)
        g["metered_count"] = len(metered)
        g["metered_types"] = sorted({x.get("mp_type_desc") or x.get("mp_type") or "" for x in metered})
        g["metered_mp_types"] = sorted({x.get("mp_type") or "" for x in metered})
        # Primary's own PERC_DIST is part of the split (live: primary 50 + secondary 50).
        g["perc_dist_sum"] = round(_num(g.get("p_perc_dist")) + sum(_num(x.get("perc_dist")) for x in secs), 4)
    return list(out.values())
