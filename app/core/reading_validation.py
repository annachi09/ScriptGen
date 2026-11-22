"""
Reading Validation/Modif: one window to review (and, in a later round,
edit) every meter reading recorded against a sector supply - primarily
looked up by GCCOM_SECTOR_SUPPLY.NISS, or the raw ID_SECTOR_SUPPLY.

Background (RJ, 2026-09-23, own words + own SQL). RJ's own starting
query, verbatim (parameter placeholders are RJ's own - :PARAM0 = the
sector supply id, :PARAM1/:PARAM2 = an optional measuring-point filter,
:PARAM3/:PARAM4 = an optional device filter, each "0 = :PARAMn OR ..."
pair meaning "skip this filter when not supplied"):

    SELECT
        1 AS ind_detail, r.id_sector_supply, r.id_measuring_point,
        r.id_reading, r.id_device, MOD.model_name,
        d.serial_num AS company_meter_num,
        /*i18n*/rtype.description AS reading_type,
        r.reading_prev_date, r.reading_date, r.reading_time_ts,
        r.usage_type AS usage_code, /*i18n*/consum.name_type AS usage_name,
        /*i18n*/st.description AS read_status, r.prev_value, r.VALUE AS reading,
        /*i18n*/u.name_type AS reading_unit, /*i18n*/cu.name_type AS corrected_unit,
        /*i18n*/bu.name_type AS bill_unit, r.reading_usage AS metered_usage,
        r.corrected_usage, r.ready_usage AS bill_ready_usage, r.ind_estimate,
        r.ind_negative_usage, bp.description as billing_period, ss.niss,
        ss.id_sector_supply, ss.id_supply,
        COALESCE((SELECT NAME FROM GCXS_USER WHERE RACF_USERS_CODE = usd.RACF_USERS_CODE),
                 (SELECT NAME FROM GCXS_USER WHERE RACF_USERS_CODE = usd.RACF_USERS_CODE),
                 R.UPDATE_USER) AS UPDATE_USER,
        (select rf.VALUE from OUC_COMMON_ADMIN.GCCOM_READING_FACTOR rf
         where rf.ID_READING_FACT = (select max(rf2.ID_READING_FACT)
             FROM OUC_COMMON_ADMIN.GCCOM_READING_FACTOR rf2
             where rf2.ID_READING = r.ID_READING AND RF.FACTOR_TYPE = '8000FCTTYP')) AS POWER_FACTOR,
        CASE WHEN r.ID_REBILLING_ACTIVITY IS NOT NULL THEN 1 ELSE 0 END AS IND_MODREB,
        rr.name as reader_name, rst.NAME_TYPE as reading_origin, e.name as digitizer
    FROM gcgt_re_reading r
    inner join gcgt_re_reading_type rtype on rtype.cod_develop = r.reading_type
    inner join gccom_consum_type consum on consum.cod_develop = r.usage_type
    inner join gcgt_re_read_status st on st.cod_develop = r.read_status
    left join GCGT_RE_READING_SOURCE_TYPE rst on rst.COD_DEVELOP = r.READING_SOURCE
    left join gccom_sector_supply ss on ss.id_sector_supply = r.id_sector_supply
    left join gcgt_me_device d on d.id_device = r.id_device
    left join gcgt_me_device_model MOD on MOD.id_model = d.id_model
    left join gccom_units u on u.cod_develop = r.reading_unit
    left join gccom_units cu on cu.cod_develop = r.corrected_usage_unit
    left join gccom_units bu on bu.cod_develop = r.usage_unit
    left join gccom_billing_period bp on bp.id_billing_period = r.ID_BILLING_PERIOD
    left join gcgt_re_route rt on rt.id_route = r.ID_ROUTE
    left join gcgt_re_reader rr on rr.ID_READER = rt.ID_READER
    left join gcxs_users_shadow usd on CONVERT(varchar(10), usd.USER_ID) = r.UPDATE_USER
    left join (select r.id_device, max(r.reading_date) max_date
               from gcgt_re_reading r group by r.id_device) t on t.id_device = r.id_device
    left join gccb_employee e on e.id_employee_cb = r.digitizer
    WHERE (r.id_sector_supply = :PARAM0)
      AND ((0 = :PARAM1) OR (r.id_measuring_point = :PARAM2))
      AND ((0 = :PARAM3) OR (r.id_device = :PARAM4))
      AND (consum.ind_load_curve is null or consum.ind_load_curve = 0)
    ORDER BY r.reading_date DESC, T.max_date DESC, r.reading_time_ts desc,
             d.comp_serial_num desc, r.usage_type

build_readings_query below reproduces this EXACTLY (join chain, the
COALESCE/UPDATE_USER lookup, the correlated POWER_FACTOR subquery -
including its own odd correlated reference to the outer `rf.FACTOR_TYPE`
inside the inner MAX() subquery, which reads unusual but is RJ's own SQL
and is left untouched rather than "corrected", same standing rule this
app's other modules follow for verbatim analyst SQL) - the only changes
are (1) RJ's `:PARAMn` bind-style optional filters are translated to
this app's own "only add the AND clause when a value is actually given"
convention (every other module in app/core does it this way - see e.g.
hierarchy_analysis.build_reading_history_query's min_billing_period),
since this app builds inline literal SQL text rather than binding
parameters, and (2) one alias fix: RJ's own SELECT list has BOTH
`r.id_sector_supply` and `ss.id_sector_supply` with no distinguishing
alias - harmless in SSMS, but this app's row-shaping is `dict(zip(
columns, row))`, so two same-named columns would silently collapse into
one. The second one is aliased `ss_id_sector_supply` (same "give every
duplicate name its own alias" fix this app already applied to Incorrect
Billing Period's own duplicate ID_BILLING_SERVICE/NISS columns). Every
other column also gets its own EXPLICIT lowercase `AS ...` alias now,
even the ones RJ's own SQL left unaliased (e.g. `r.reading_date`) or
gave an uppercase alias (`AS UPDATE_USER`, `AS POWER_FACTOR`, `AS
IND_MODREB`) - deliberately, not a cosmetic pass: an UNALIASED column's
returned name is whatever casing the live GCGT_RE_READING table actually
stores for that column, which this app cannot verify without querying
the tunnel DB (the same reason `_da_col` in web/server.py exists as a
case-insensitive lookup wherever this app doesn't control the alias).
Reading Validation's own frontend, unlike most of this app's other
pages, indexes result rows by a fixed lowercase key list (app.js's
RV_COLUMNS) instead of running everything through `_da_col` per field,
so every column here needs a GUARANTEED, deterministic lowercase name -
hence aliasing all of them explicitly, not just the one genuine
collision.

build_account_niss_query is new (not part of RJ's pasted SQL) - it's the
lookup behind "search by account", which RJ separately asked for:
"we will add a search by account, which will lead to create 2 tabs, we
expect to have 1 or 2 niss for this, either water or electricity when
searching by account". Same PAYMENT_FORM -> CONTRACTED_SERVICE ->
SECTOR_SUPPLY chain this app already uses elsewhere (see date_anomaly.
build_niss_account_query's own docstring for the reverse direction of
this exact join), walked forward from an account instead of backward
from a NISS, plus the OFFERED_SERVICE lookup so each resulting tab can
be labeled "Electricity"/"Water" instead of a bare service-type code.

Deliberately pure logic, same convention as every other app/core module:
this only builds SELECT query text; it never executes anything itself.
The caller (web/server.py) runs it via app.db.mssql.run_query.

Phase 1 scope only (RJ, 2026-09-23: "this is the main query for now"):
display, filter, sort. No UPDATE/MODIF capability yet despite the menu's
own name - that's an explicitly later round, tracked separately, same
incremental-rounds pattern every other menu in this app was built in.

English-only descriptions (RJ, 2026-09-23: "try to show always english
description, you can use table GCTS_DICTIONARY with LOCALE = 'EN'"),
same pattern already established in bulk_checker.build_bill_detail_sql
(`LEFT JOIN GCTS_DICTIONARY d ON d.id = bst.NAME_TYPE_XI18N AND LOCALE
= 'EN'` -> `d.TEXT AS bill_status`). Every column RJ marked `/*i18n*/`
in the pasted query gets its own such join, live-confirmed via this
app's own Workspace INFORMATION_SCHEMA.COLUMNS query against the tunnel
DB (2026-09-23) rather than assumed:
  - GCGT_RE_READING_TYPE.DESCRIPTION_XI18N  (reading_type)
  - GCCOM_CONSUM_TYPE.NAME_TYPE_XI18N       (usage_name)
  - GCGT_RE_READ_STATUS.DESCRIPTION_XI18N   (read_status)
  - GCCOM_UNITS.NAME_TYPE_XI18N             (reading_unit / corrected_unit / bill_unit)
  - GCCOM_BILLING_PERIOD.PERIOD_NAME_XI18N  (billing_period - confirmed
    live that GCCOM_BILLING_PERIOD has NO DESCRIPTION_XI18N companion,
    only PERIOD_NAME_XI18N, so that's the id used to look up the English
    text even though the selected column itself is bp.description)
GCTS_DICTIONARY itself confirmed live as (ID numeric, LOCALE varchar,
TEXT nvarchar, SOURCE_TABLE, SOURCE_COLUMN, ...). Each join is a LEFT
JOIN (never INNER - a missing translation row must not drop the reading
row) and the selected column becomes COALESCE(dict.text, original) so a
reading still shows its raw/native description on the rare row with no
matching GCTS_DICTIONARY entry, rather than going blank.

Reading chain (RJ, 2026-09-23: "each reading is being referenced by
id_reading and id_last_reading, so the id_reading of a row, is the
id_last reading of the next row"). r.id_last_reading is a real,
self-referencing FK - GCGT_RE_READING.ID_LAST_READING ->
GCGT_RE_READING.ID_READING - confirmed via docs/db_schema_reference.md;
it was not in RJ's original pasted SELECT list at all, so it's added
here purely so the frontend can compute the chain highlight client-side
(no extra join needed, it's a plain column on r).
"""
from __future__ import annotations

import datetime
import decimal

from .sql_format import format_sql_literal

# --- Schema / table names -------------------------------------------------
# RJ's own query leaves every table unqualified except the two
# GCCOM_READING_FACTOR references inside the POWER_FACTOR subquery
# (OUC_COMMON_ADMIN.GCCOM_READING_FACTOR) - reproduced exactly as given,
# not "completed" with schema prefixes RJ didn't write, same reasoning
# date_anomaly.py's own SECTOR_SUPPLY_TABLE comment already documents for
# this codebase ("left unqualified, matching the spec's own query").
READING_TABLE = "gcgt_re_reading"

# For build_account_niss_query, which is new (not part of RJ's pasted
# detect SQL) - same schema split as every other module that walks this
# chain (date_anomaly.py, bill_issuance_validator.py).
PAYMENT_FORM_SCHEMA = "OUC_COMMON_ADMIN"
PAYMENT_FORM_TABLE = "GCCOM_PAYMENT_FORM"
CONTRACTED_SERVICE_TABLE = "GCCOM_CONTRACTED_SERVICE"
SECTOR_SUPPLY_TABLE = "GCCOM_SECTOR_SUPPLY"
OFFERED_SERVICE_SCHEMA = "OUC_COMMON_ADMIN"
OFFERED_SERVICE_TABLE = "GCCOM_COMPANY_OFFERED_SERVICE"

# Same "fully dead contract" exclusion bulk_checker.build_bill_detail_sql
# already uses (`cs.status <> 'ESTSC00005'`) - keeps a long-terminated,
# no-longer-relevant contracted service off the account-search results
# without needing a whole allow-list of "still relevant" statuses.
CONTRACT_STATUS_DEAD = "ESTSC00005"


def build_readings_query(
    id_sector_supply: int,
    *,
    id_measuring_point: int | None = None,
    id_device: int | None = None,
) -> str:
    """Every reading recorded for one sector supply - see module docstring
    for full provenance. id_measuring_point/id_device are optional extra
    narrowing filters (RJ's own :PARAM1-4 pair), each simply omitted from
    the WHERE clause when not supplied, same "None = no filter" convention
    every other query builder in this app already uses."""
    where_clauses = [f"r.id_sector_supply = {format_sql_literal(id_sector_supply)}"]
    if id_measuring_point is not None:
        where_clauses.append(f"r.id_measuring_point = {format_sql_literal(id_measuring_point)}")
    if id_device is not None:
        where_clauses.append(f"r.id_device = {format_sql_literal(id_device)}")
    where_clauses.append("(consum.ind_load_curve IS NULL OR consum.ind_load_curve = 0)")
    where_sql = "\n  AND ".join(where_clauses)
    return f"""
SELECT
    1 AS ind_detail,
    r.id_sector_supply AS id_sector_supply,
    r.id_measuring_point AS id_measuring_point,
    r.id_reading AS id_reading,
    r.id_last_reading AS id_last_reading,
    r.id_device AS id_device,
    MOD.model_name AS model_name,
    d.serial_num AS company_meter_num,
    COALESCE(dict_rtype.text, rtype.description) AS reading_type,
    r.reading_type AS reading_type_code,
    r.reading_prev_date AS reading_prev_date,
    r.reading_date AS reading_date,
    r.reading_time_ts AS reading_time_ts,
    r.usage_type AS usage_code,
    COALESCE(dict_consum.text, consum.name_type) AS usage_name,
    COALESCE(dict_st.text, st.description) AS read_status,
    r.read_status AS read_status_code,          -- RJ 2026-11-18: status dropdown edits the code
    r.ind_usage_to_cal AS ind_usage_to_cal,     -- RJ 2026-11-18: editable 0/1
    r.prev_value AS prev_value,
    r.VALUE AS reading,
    COALESCE(dict_u.text, u.name_type) AS reading_unit,
    COALESCE(dict_cu.text, cu.name_type) AS corrected_unit,
    COALESCE(dict_bu.text, bu.name_type) AS bill_unit,
    r.reading_usage AS metered_usage,
    r.corrected_usage AS corrected_usage,
    r.ready_usage AS bill_ready_usage,
    umult.MULTIPLIER AS usage_multiplier,
    r.ind_estimate AS ind_estimate,
    r.ind_negative_usage AS ind_negative_usage,
    COALESCE(dict_bp.text, bp.description) AS billing_period,
    ss.niss AS niss,
    ss.id_sector_supply AS ss_id_sector_supply,
    ss.id_supply AS id_supply,
    COALESCE(
        (SELECT NAME FROM GCXS_USER WHERE RACF_USERS_CODE = usd.RACF_USERS_CODE),
        (SELECT NAME FROM GCXS_USER WHERE RACF_USERS_CODE = usd.RACF_USERS_CODE),
        R.UPDATE_USER
    ) AS update_user,
    (SELECT rf.VALUE FROM OUC_COMMON_ADMIN.GCCOM_READING_FACTOR rf
     WHERE rf.ID_READING_FACT = (
         SELECT MAX(rf2.ID_READING_FACT) FROM OUC_COMMON_ADMIN.GCCOM_READING_FACTOR rf2
         WHERE rf2.ID_READING = r.ID_READING AND RF.FACTOR_TYPE = '8000FCTTYP'
     )) AS power_factor,
    CASE WHEN r.ID_REBILLING_ACTIVITY IS NOT NULL THEN 1 ELSE 0 END AS ind_modreb,
    rr.name AS reader_name,
    rst.NAME_TYPE AS reading_origin,
    e.name AS digitizer
FROM {READING_TABLE} r
INNER JOIN gcgt_re_reading_type rtype ON rtype.cod_develop = r.reading_type
INNER JOIN gccom_consum_type consum ON consum.cod_develop = r.usage_type
INNER JOIN gcgt_re_read_status st ON st.cod_develop = r.read_status
LEFT JOIN GCGT_RE_READING_SOURCE_TYPE rst ON rst.COD_DEVELOP = r.READING_SOURCE
LEFT JOIN gccom_sector_supply ss ON ss.id_sector_supply = r.id_sector_supply
LEFT JOIN gcgt_me_device d ON d.id_device = r.id_device
LEFT JOIN gcgt_me_device_model MOD ON MOD.id_model = d.id_model
LEFT JOIN gccom_units u ON u.cod_develop = r.reading_unit
LEFT JOIN gccom_units cu ON cu.cod_develop = r.corrected_usage_unit
LEFT JOIN gccom_units bu ON bu.cod_develop = r.usage_unit
LEFT JOIN gccom_billing_period bp ON bp.id_billing_period = r.ID_BILLING_PERIOD
LEFT JOIN gcgt_re_route rt ON rt.id_route = r.ID_ROUTE
LEFT JOIN gcgt_re_reader rr ON rr.ID_READER = rt.ID_READER
LEFT JOIN gcxs_users_shadow usd ON CONVERT(varchar(10), usd.USER_ID) = r.UPDATE_USER
LEFT JOIN {USAGE_MULTIPLIER_TABLE} umult ON umult.ID_DEVICE = r.id_device AND umult.COD_USAGE_TYPE = r.usage_type
LEFT JOIN GCTS_DICTIONARY dict_rtype ON dict_rtype.id = rtype.description_xi18n AND dict_rtype.locale = 'EN'
LEFT JOIN GCTS_DICTIONARY dict_consum ON dict_consum.id = consum.name_type_xi18n AND dict_consum.locale = 'EN'
LEFT JOIN GCTS_DICTIONARY dict_st ON dict_st.id = st.description_xi18n AND dict_st.locale = 'EN'
LEFT JOIN GCTS_DICTIONARY dict_u ON dict_u.id = u.name_type_xi18n AND dict_u.locale = 'EN'
LEFT JOIN GCTS_DICTIONARY dict_cu ON dict_cu.id = cu.name_type_xi18n AND dict_cu.locale = 'EN'
LEFT JOIN GCTS_DICTIONARY dict_bu ON dict_bu.id = bu.name_type_xi18n AND dict_bu.locale = 'EN'
LEFT JOIN GCTS_DICTIONARY dict_bp ON dict_bp.id = bp.period_name_xi18n AND dict_bp.locale = 'EN'
LEFT JOIN (SELECT r.id_device, MAX(r.reading_date) max_date
           FROM {READING_TABLE} r GROUP BY r.id_device) t ON t.id_device = r.id_device
LEFT JOIN gccb_employee e ON e.id_employee_cb = r.digitizer
WHERE {where_sql}
ORDER BY r.reading_date DESC, T.max_date DESC, r.reading_time_ts DESC,
         d.comp_serial_num DESC, r.usage_type
"""


def build_account_niss_query(account_number: str) -> str:
    """Account -> every (still-relevant) sector supply/NISS under it, for
    Reading Validation's "search by account" path - RJ: "we will add a
    search by account, which will lead to create 2 tabs, we expect to
    have 1 or 2 niss for this, either water or electricity". One row per
    distinct sector supply (an account's water and electricity services
    are each their own GCCOM_SECTOR_SUPPLY row), with the offered-service
    description so the frontend can label each resulting tab "Water" /
    "Electricity" instead of a bare service-type code."""
    payment_form_tbl = f"{PAYMENT_FORM_SCHEMA}.{PAYMENT_FORM_TABLE}"
    offered_service_tbl = f"{OFFERED_SERVICE_SCHEMA}.{OFFERED_SERVICE_TABLE}"
    account_lit = format_sql_literal(account_number)
    dead_lit = format_sql_literal(CONTRACT_STATUS_DEAD)
    return f"""
SELECT DISTINCT
    SS.ID_SECTOR_SUPPLY,
    SS.NISS,
    CS.ID_OFFERED_SERVICE,
    OS.NAME_TYPE AS OFFERED_SERVICE_DESC,
    CS.STATUS AS CONTRACT_STATUS,
    PF.REFERENCE AS ACCOUNT
FROM {payment_form_tbl} PF
JOIN {CONTRACTED_SERVICE_TABLE} CS ON CS.ID_PAYMENT_FORM = PF.ID_PAYMENT_FORM
JOIN {SECTOR_SUPPLY_TABLE} SS ON SS.ID_SECTOR_SUPPLY = CS.ID_SECTOR_SUPPLY
LEFT JOIN {offered_service_tbl} OS ON OS.ID_OFFERED_SERVICE = CS.ID_OFFERED_SERVICE
WHERE PF.REFERENCE = {account_lit}
  AND CS.STATUS <> {dead_lit}
ORDER BY SS.NISS
"""


def build_reading_types_query() -> str:
    """Every GCGT_RE_READING_TYPE (code, English description) - feeds the
    "Reading Type" dropdown RJ asked for (2026-09-25: "reading_type(make
    it as dropdown, and in the update script you will use the id not the
    description)"). Same GCTS_DICTIONARY LOCALE='EN' pattern as build_
    readings_query's own reading_type lookup - see that function's join
    on dict_rtype for the identical reasoning; COALESCE falls back to the
    table's own native-locale description on the rare code with no
    matching GCTS_DICTIONARY row."""
    return """
SELECT
    rtype.cod_develop AS code,
    COALESCE(dict_rtype.text, rtype.description) AS description
FROM gcgt_re_reading_type rtype
LEFT JOIN GCTS_DICTIONARY dict_rtype ON dict_rtype.id = rtype.description_xi18n AND dict_rtype.locale = 'EN'
ORDER BY COALESCE(dict_rtype.text, rtype.description)
"""


# --- Editable columns / update-script generation ---------------------
# RJ, 2026-09-25: "on the same reading update menu, I want to be able to
# update the following columns reading prev date, reading date,
# reading_type (make it as dropdown, and in the update script you will
# use the id not the description), prev_value, value, reading_usage,
# corrected_usage, ready usage. Only this columns." Each entry maps the
# UI-facing column key (what build_readings_query aliases it as, and
# what the frontend's grid/edit code already keys rows by) to the REAL
# GCGT_RE_READING column name the update script must SET, plus a "kind"
# telling the server how to coerce the edited string back to a typed
# Python value for sql_format.format_sql_literal (see coerce_edit_value
# below) - same job diff_engine.coerce_edited_value does for the generic
# Workspace grid, just scoped to this page's fixed, known column set
# instead of guessing from an arbitrary original value's Python type.
#
# reading_type is edited via its RAW CODE (reading_type_code, a column
# build_readings_query added specifically for this - see its own
# comment), never via the translated description column - RJ was
# explicit that the update script must "use the id not the description".
EDITABLE_COLUMNS: dict[str, tuple[str, str]] = {
    "reading_prev_date": ("READING_PREV_DATE", "datetime"),
    "reading_date": ("READING_DATE", "datetime"),
    "reading_type": ("READING_TYPE", "code"),
    "prev_value": ("PREV_VALUE", "decimal"),
    "reading": ("VALUE", "decimal"),
    "metered_usage": ("READING_USAGE", "decimal"),
    "corrected_usage": ("CORRECTED_USAGE", "decimal"),
    "bill_ready_usage": ("READY_USAGE", "decimal"),
    # RJ 2026-11-18: "i want to also be able to update the status, show me dropdown also to choose and the
    # ind_usage_to_cal, show it i want to update it also" - status edited by its code (GCGT_RE_READ_STATUS.
    # COD_DEVELOP, e.g. 4000STSRED), IND_USAGE_TO_CAL as 0/1 (live-checked on GCGT_RE_READING 2026-11-18).
    "read_status": ("READ_STATUS", "code"),
    "ind_usage_to_cal": ("IND_USAGE_TO_CAL", "decimal"),
}


def build_read_statuses_query() -> str:
    """Every GCGT_RE_READ_STATUS (code, English description) for the Read Status edit dropdown
    (RJ 2026-11-18). Same GCTS_DICTIONARY EN pattern as build_reading_types_query."""
    return """
SELECT
    st.cod_develop AS code,
    COALESCE(dict_st.text, st.description) AS description
FROM gcgt_re_read_status st
LEFT JOIN GCTS_DICTIONARY dict_st ON dict_st.id = st.description_xi18n AND dict_st.locale = 'EN'
ORDER BY st.cod_develop
"""

# The four columns RJ called out as participating in the row-to-row
# reading chain ("Any update in the dates and reading values (the one we
# have referencing) should reflect on the referenced value") - reading_
# type/the three usage columns are edited independently and never
# cascade. Kept here (not just in the frontend) so a future server-side
# validation pass has one shared source of truth for which columns are
# chain-linked; the actual cascade logic itself is client-side (see
# app.js's rvCascadeEdit) since it only ever touches in-memory grid state
# before a script is generated, never the database.
CASCADING_COLUMNS = {"reading_prev_date", "reading_date", "prev_value", "reading"}

# RJ, 2026-09-25 (follow-up round): "the corrected usage, reading usage
# and ready usage should automatically be updated as well if you change
# the value. for reading usage, it is equal to the value - prev_value,
# for [corrected usage and ready usage] it is equal to the value - prev
# value, multiplied by the multiplier column in gcgt_me_usage_type meter
# using the id_device of the re_reading and the usage_type". RJ's own
# table name ("gcgt_me_usage_type meter") is one real table -
# GCGT_ME_USAGE_TYPE_METER - live-confirmed (2026-09-25) via this app's
# own Workspace INFORMATION_SCHEMA.COLUMNS query against the tunnel DB:
# it has ID_DEVICE, COD_USAGE_TYPE and MULTIPLIER columns, and joining it
# on (ID_DEVICE, COD_USAGE_TYPE) against a handful of real GCGT_RE_READING
# rows returned exactly one match per reading (no fan-out), so a plain
# LEFT JOIN is safe here without any extra de-dup logic. The actual
# recompute (metered_usage = reading - prev_value; corrected_usage =
# bill_ready_usage = that difference * multiplier) is done client-side in
# app.js's rvRecalcUsage, the same "server supplies the raw ingredient,
# browser does the arithmetic on in-memory grid state" split already used
# for reference-chain highlighting and cascade edits - this multiplier is
# just that ingredient.
USAGE_MULTIPLIER_TABLE = "GCGT_ME_USAGE_TYPE_METER"

_DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
)


# ---------------------------------------------------------------------------------------------
# RJ 2026-11-17: "if the reading modified is sent to bill, we need to also update the value in
# reading items to bill using the id reading, also the consumption in the item to bill xml should
# be update, and ... for water we also need to update the sanitary reading item to bill and xml".
# Live facts (2026-11-17):
#   - GCCOM_READINGS_ITEMSTOBILL has VALUE, PREV_VALUE, READY_USAGE, READING_DATE per (reading, ITB).
#     A water reading has 2 rows: its water ITB and its sanitary ITB (reading 1049334074 -> ITB
#     1042568021 water + 1042568025 sanitary), so "WHERE ID_READING = x" covers sanitary too.
#   - GCCOM_ITEMS_TO_BILL.ID_XML -> GCCOM_ITEMS_TO_BILL_XML.XML_TO_BILL holds
#     <consumptionType><codConsumptionType>TPCONS..</codConsumptionType><consumption>-274.0</consumption>
#     and <consumption> = SUM(READY_USAGE) of the ITB's readings with GCGT_RE_READING.USAGE_TYPE =
#     that code (checked on a 30-reading electricity ITB: TPCONS0001 -257420 / TPCONS0002 189410).
#     So the new consumption = old consumption + (new READY_USAGE - old READY_USAGE) of the edited
#     readings of that usage type. The sanitary XML carries the same water consumption (TPCONS0006).
# ---------------------------------------------------------------------------------------------
READ_STATUS_SENT_TO_BILL = "6000STSRED"
# RJ 2026-11-17 (later): "for sent to bill we need update the readings and reading dates same for the
# billed one, the consumption is just for reading item to bill and xml" - GCGT_RE_READING gets every
# edited column (readings + dates) for both; the consumption sync (RIT + XML) runs for Sent to bill
# AND Billed (7000STSRED).
SYNC_READ_STATUSES = ("6000STSRED", "7000STSRED")
# RJ 2026-11-17 (later): "dont update if uau is affected give a warning" - a reading in 7001STSRED
# (UAU - Billed) is left out of the whole script (no GCGT_RE_READING / RIT / XML update) with a warning.
READ_STATUS_UAU_BILLED = "7001STSRED"


def build_read_status_query(reading_ids: list) -> str:
    ids = ", ".join(str(int(decimal.Decimal(str(i)))) for i in reading_ids)
    return ("SELECT RR.ID_READING, RR.READ_STATUS FROM OUC_COMMON_ADMIN.GCGT_RE_READING RR\n"
            f"WHERE RR.ID_READING IN ({ids});")
RIT_TABLE = "OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL"
ITB_TABLE = "OUC_ADMIN.GCCOM_ITEMS_TO_BILL"
ITB_XML_TABLE = "OUC_ADMIN.GCCOM_ITEMS_TO_BILL_XML"
# GCGT_RE_READING column -> GCCOM_READINGS_ITEMSTOBILL column (same names).
# RJ 2026-11-17 (later): "no for xml and the reading item to bill lets just update the consumption" -
# only the consumption (READY_USAGE) is synced; values / dates stay as they are in RIT and the XML.
RIT_SYNC_COLUMNS = ("READY_USAGE",)
# RJ 2026-11-18: "why the reading modify is not updating the Reading item to bill and xml when it is sent
# to bill check 10156214-301" + "also the sanitary" - his script (history #373) only changed READING_DATE
# of Sent-to-bill reading 1049305774, so nothing was synced. Now every RIT column that mirrors the reading
# is synced (live-checked GCCOM_READINGS_ITEMSTOBILL: READING_DATE, VALUE, PREV_VALUE, READY_USAGE), on
# every RIT row of the reading = water AND sanitary ITB. The XML gets the dates too: in the reading's
# <consumptionType> block <readingFromDate> = READING_PREV_DATE, <readingToDate> = READING_DATE (checked on
# 1049305774: 2026-10-25 / 2026-10-24), plus the header <initDate> / <endDate> / <billingDate> when they
# still hold the old dates.
RIT_SYNC_COLUMNS = ("READING_DATE", "VALUE", "PREV_VALUE", "READY_USAGE")
XML_DATE_COLUMNS = ("READING_PREV_DATE", "READING_DATE")
SYNC_TRIGGER_COLUMNS = RIT_SYNC_COLUMNS + ("READING_PREV_DATE",)


def _ymd(v) -> str | None:
    if v is None or v == "":
        return None
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    return str(v)[:10]


def patch_xml_dates(xml_text: str, usage_type: str, from_old, from_new, to_old, to_new) -> tuple[str, list[str]]:
    """Reading date change -> XML: <readingFromDate>/<readingToDate> of the usage type's <consumptionType>,
    and the header <initDate>/<endDate>/<billingDate> only where they still equal the old date."""
    import re
    out, log = xml_text or "", []
    blk = re.compile(r"<consumptionType>(?:(?!</consumptionType>).)*?<codConsumptionType>\s*" + re.escape(usage_type) +
                     r"\s*</codConsumptionType>(?:(?!</consumptionType>).)*?</consumptionType>", re.S)
    m = blk.search(out)
    if m:
        b = m.group(0)
        for tag, new in (("readingFromDate", from_new), ("readingToDate", to_new)):
            if new:
                b2 = re.sub(rf"(<{tag}>)[^<]*(</{tag}>)", rf"\g<1>{new}\g<2>", b, count=1)
                if b2 != b:
                    log.append(f"{tag} -> {new}")
                b = b2
        out = out[:m.start()] + b + out[m.end():]
    for tag, old, new in (("initDate", from_old, from_new), ("endDate", to_old, to_new), ("billingDate", to_old, to_new)):
        if old and new and old != new and f"<{tag}>{old}</{tag}>" in out:
            out = out.replace(f"<{tag}>{old}</{tag}>", f"<{tag}>{new}</{tag}>", 1)
            log.append(f"{tag} {old} -> {new}")
    return out, log


def build_sent_to_bill_links_query(reading_ids: list) -> str:
    """For the edited readings: status / usage type / current READY_USAGE, and every
    reading-item-to-bill row with its ITB, service and XML (one row per RIT row)."""
    ids = ", ".join(str(int(decimal.Decimal(str(i)))) for i in reading_ids)
    return (
        "SELECT RR.ID_READING, RR.READ_STATUS, RR.USAGE_TYPE, RR.READY_USAGE AS RR_READY_USAGE,\n"
        "  RIT.ID_READING_ITEM2BILL, RIT.ID_ITEM_TO_BILL, RIT.VALUE AS RIT_VALUE, RIT.PREV_VALUE AS RIT_PREV_VALUE,\n"
        "  RIT.READY_USAGE AS RIT_READY_USAGE, RIT.READING_DATE AS RIT_READING_DATE,\n"
        "  ITB.STATUS AS ITB_STATUS, ITB.ID_XML, CS.ID_OFFERED_SERVICE, X.XML_TO_BILL,\n"
        "  ITB.INI_DATE AS ITB_INI_DATE, ITB.END_DATE AS ITB_END_DATE, ITB.BILLING_DATE AS ITB_BILLING_DATE\n"
        "FROM OUC_COMMON_ADMIN.GCGT_RE_READING RR\n"
        f"JOIN {RIT_TABLE} RIT ON RIT.ID_READING = RR.ID_READING\n"
        f"JOIN {ITB_TABLE} ITB ON ITB.ID_ITEM_TO_BILL = RIT.ID_ITEM_TO_BILL\n"
        "LEFT JOIN GCCOM_BILLING_SERVICE BS ON BS.ID_BILLING_SERVICE = ITB.ID_BILLING_SERVICE\n"
        "LEFT JOIN GCCOM_CONTRACTED_SERVICE CS ON CS.ID_CONTRACTED_SERVICE = BS.ID_CONTRACTED_SERVICE\n"
        f"LEFT JOIN {ITB_XML_TABLE} X ON X.ID_XML = ITB.ID_XML\n"
        f"WHERE RR.ID_READING IN ({ids})\n"
        "ORDER BY RR.ID_READING, RIT.ID_ITEM_TO_BILL;"
    )


def _fmt_consumption(d: decimal.Decimal) -> str:
    s = format(d.normalize() if d == d.to_integral() else d, "f")
    if "." not in s:
        s += ".0"
    return s


def patch_xml_consumption(xml_text: str, usage_type: str, delta: decimal.Decimal) -> tuple[str, str | None, str | None]:
    """Adds `delta` to <consumption> of the <consumptionType> whose <codConsumptionType> = usage_type.
    Text-level patch (keeps the XML byte-for-byte except that one number). Returns
    (new_xml, old_value, new_value); old/new are None when the block wasn't found."""
    import re
    pat = re.compile(
        r"(<consumptionType>(?:(?!</consumptionType>).)*?<codConsumptionType>\s*" + re.escape(usage_type) +
        r"\s*</codConsumptionType>(?:(?!</consumptionType>).)*?<consumption>)([^<]*)(</consumption>)", re.S)
    m = pat.search(xml_text or "")
    if not m:
        return xml_text, None, None
    try:
        old = decimal.Decimal(m.group(2).strip())
    except decimal.InvalidOperation:
        return xml_text, None, None
    new = _fmt_consumption(old + delta)
    return xml_text[:m.start(2)] + new + xml_text[m.end(2):], m.group(2), new


def _sql_val(v) -> str:
    return "NULL" if v is None or v == "" else format_sql_literal(v)


def build_sent_to_bill_sync(edited_rows: list[dict], links: list[dict], *, program: str, user: str,
                            rollback: bool = False) -> tuple[str, int, list[str]]:
    """edited_rows: [{"id_reading": int, "changes": {DB_COL: (old, new)}}] (GCGT_RE_READING columns).
    links: rows of build_sent_to_bill_links_query (lower-case keys).
    Returns (sql, statement_count, notes). Only readings in 6000STSRED (Sent to bill) are synced.
    Rollback: puts back the RIT values and XML exactly as they are now (live)."""
    usr, prog = format_sql_literal(user), format_sql_literal(program + (" ROLLBACK" if rollback else ""))
    stamp = f"UPDATE_DATE = GETDATE(), UPDATE_USER = {usr}, UPDATE_PROGRAM = {prog}"
    by_reading: dict[str, list[dict]] = {}
    for l in links:
        by_reading.setdefault(str(l.get("id_reading")), []).append(l)
    parts, notes, n = [], [], 0
    xml_deltas: dict[str, dict] = {}   # id_xml -> {"xml": original, "itb": .., "svc": .., "deltas": {usage_type: Decimal}}
    for row in edited_rows:
        rid = str(row["id_reading"])
        ls = by_reading.get(rid, [])
        if not ls or str(ls[0].get("read_status")) not in SYNC_READ_STATUSES:
            continue
        status_txt = "Sent to bill" if str(ls[0].get("read_status")) == READ_STATUS_SENT_TO_BILL else "Billed"
        changes = {c: v for c, v in row["changes"].items() if c in RIT_SYNC_COLUMNS}
        if changes:
            if rollback:
                for l in ls:   # back to the live RIT values, row by row
                    sets = ", ".join(f"{c} = {_sql_val(l.get('rit_' + c.lower()))}" for c in changes)
                    parts.append(f"UPDATE {RIT_TABLE}\nSET {sets},\n    {stamp}\n"
                                 f"WHERE ID_READING_ITEM2BILL = {l.get('id_reading_item2bill')};\n")
                    n += 1
            else:
                sets = ", ".join(f"{c} = {_sql_val(new)}" for c, (_old, new) in changes.items())
                itbs = ", ".join(f"{l.get('id_item_to_bill')} ({_svc_name(l.get('id_offered_service'))})" for l in ls)
                parts.append(f"-- reading {rid} is {status_txt}: its reading-item-to-bill rows (ITB {itbs})\n"
                             f"UPDATE {RIT_TABLE}\nSET {sets},\n    {stamp}\nWHERE ID_READING = {rid};\n")
                n += 1
        # RJ 2026-11-18: date changes go into every linked XML (water + sanitary ITB)
        dchg = {c: row["changes"][c] for c in XML_DATE_COLUMNS if c in row["changes"]}
        if dchg:
            ut = str(ls[0].get("usage_type") or "")
            # the ITB row itself carries the same dates (live: ITB 1042261498 INI_DATE 2026-10-25 /
            # END_DATE 2026-10-24 / BILLING_DATE 2026-10-24 = the reading's prev date / date) - moved only
            # where they still equal the reading's old date
            fo, fn = (_ymd(x) for x in dchg.get("READING_PREV_DATE", (None, None)))
            to, tn = (_ymd(x) for x in dchg.get("READING_DATE", (None, None)))
            for l in ls:
                sets, guards = [], []
                for col, old, new in (("INI_DATE", fo, fn), ("END_DATE", to, tn), ("BILLING_DATE", to, tn)):
                    cur = _ymd(l.get("itb_" + col.lower()))
                    if old and new and old != new and cur == old:
                        # rollback: put the old date back, guarded on the new one
                        sets.append(f"{col} = '{(old if rollback else new)}'")
                        guards.append(f"{col} = '{(new if rollback else old)}'")
                if sets:
                    parts.append(f"-- {'ROLLBACK ' if rollback else ''}dates of ITB {l.get('id_item_to_bill')} "
                                 f"({_svc_name(l.get('id_offered_service'))})\n"
                                 f"UPDATE {ITB_TABLE}\nSET {', '.join(sets)},\n    {stamp}\n"
                                 f"WHERE ID_ITEM_TO_BILL = {l.get('id_item_to_bill')} AND {' AND '.join(guards)};\n")
                    n += 1
            for l in ls:
                xid = str(l.get("id_xml") or "").strip()
                if not xid:
                    notes.append(f"reading {rid}: ITB {l.get('id_item_to_bill')} has no XML")
                    continue
                e = xml_deltas.setdefault(xid, {"xml": l.get("xml_to_bill") or "", "itb": l.get("id_item_to_bill"),
                                                "svc": l.get("id_offered_service"), "deltas": {}})
                e.setdefault("dates", []).append((ut, dchg))
        if "READY_USAGE" in row["changes"]:
            old, new = row["changes"]["READY_USAGE"]
            try:
                delta = decimal.Decimal(str(new if new is not None else 0)) - decimal.Decimal(str(old if old is not None else 0))
            except decimal.InvalidOperation:
                notes.append(f"reading {rid}: READY_USAGE not numeric - XML consumption left unchanged")
                continue
            ut = str(ls[0].get("usage_type") or "")
            for l in ls:
                xid = str(l.get("id_xml") or "").strip()
                if not xid:
                    notes.append(f"reading {rid}: ITB {l.get('id_item_to_bill')} has no XML")
                    continue
                e = xml_deltas.setdefault(xid, {"xml": l.get("xml_to_bill") or "", "itb": l.get("id_item_to_bill"),
                                                "svc": l.get("id_offered_service"), "deltas": {}})
                e["deltas"][ut] = e["deltas"].get(ut, decimal.Decimal(0)) + delta
    for xid, e in xml_deltas.items():
        if all(d == 0 for d in e["deltas"].values()) and not e.get("dates"):
            continue
        new_xml, changes_txt = e["xml"], []
        for ut, d in e["deltas"].items():
            if d == 0:
                continue
            new_xml, o, nv = patch_xml_consumption(new_xml, ut, d)
            if o is None:
                notes.append(f"ITB {e['itb']}: no <consumptionType> {ut} in its XML - not changed")
            else:
                changes_txt.append(f"consumption {ut} {o} -> {nv}")
        for ut, dchg in e.get("dates", []):
            fo, fn = (_ymd(x) for x in dchg.get("READING_PREV_DATE", (None, None)))
            to, tn = (_ymd(x) for x in dchg.get("READING_DATE", (None, None)))
            new_xml, dlog = patch_xml_dates(new_xml, ut, fo, fn, to, tn)
            if dlog:
                changes_txt.extend(dlog)
            else:
                notes.append(f"ITB {e['itb']}: reading dates not found in its XML - dates not changed")
        if new_xml == e["xml"]:
            continue
        target = e["xml"] if rollback else new_xml   # rollback = the XML exactly as it is now
        # guard: only touch the XML if it is still exactly what it was when the script was made
        # (running the update twice can't add the difference twice; rollback only undoes this patch)
        guard = new_xml if rollback else e["xml"]
        label = "ROLLBACK " if rollback else ""
        parts.append(f"-- {label}XML of ITB {e['itb']} ({_svc_name(e['svc'])}): {'; '.join(changes_txt)}\n"
                     f"UPDATE {ITB_XML_TABLE}\nSET XML_TO_BILL = {format_sql_literal(target)},\n    {stamp}\n"
                     f"WHERE ID_XML = {xid}\n  AND XML_TO_BILL = {format_sql_literal(guard)};\n")
        n += 1
    return "\n".join(parts), n, notes


def _svc_name(svc) -> str:
    return {"1": "Electricity", "19": "Water", "190": "Sanitary"}.get(str(svc), f"service {svc}")


def coerce_edit_value(kind: str, raw: str):
    """Turns one edited-grid string (old OR new - both arrive as display
    strings, see diff_engine.cell_display) into a properly-typed Python
    value for sql_format.format_sql_literal. Falls back to the raw string
    on a parse failure - the resulting SQL is then just a quoted string
    literal, always syntactically valid even if not the ideal type,
    same fallback stance diff_engine.coerce_edited_value takes."""
    if raw is None or raw.strip() == "":
        return None
    stripped = raw.strip()
    if kind == "decimal":
        try:
            return decimal.Decimal(stripped)
        except decimal.InvalidOperation:
            return raw
    if kind == "datetime":
        for fmt in _DATETIME_FORMATS:
            try:
                return datetime.datetime.strptime(stripped, fmt)
            except ValueError:
                continue
        return raw
    # "code" (reading_type) and anything else: kept as the raw string.
    return raw
