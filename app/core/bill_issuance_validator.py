"""
Bill Issuance Validator: finds accounts where a "Rate" bill (ID_OFFERED_
SERVICE 176) still being put to collection is blocking the account's next
regular Electricity/Water bill from being issued.

Background (RJ, 2026-09-15, own words + own SQL). RJ's own starting query:

    select distinct pf.id_payment_form, pf.reference, nt.update_date
    from gccom_notice_tmp nt
    join gccom_bill b on b.id_bill = nt.id_bill
    join gccom_payment_form pf on pf.id_payment_form = b.id_payment_form
    where nt.cod_status = '5000NOTEMP'
      and b.billing_status = 'ESTFAC0012'
    order by nt.update_date;

- every payment form (account) with a pending notice (GCCOM_NOTICE_TMP.
COD_STATUS = 5000NOTEMP) for a bill still "in process of being put to
collection" (GCCOM_BILL.BILLING_STATUS = ESTFAC0012, confirmed live via
`SELECT * FROM GCCOM_BILL_STATUS`: "En proceso de puesta al cobro").

RJ's own further instructions, verbatim: "base on this, I wan to get all
records where it has only 1 recored for the billing period and the
id_offered_service is 176, and using the same id_payment_form, check the
table gccom_bill with id_offered_service = 1 or 19 and the billing_period
is 1 more than our bill with 176 id_offered service, next the status of
the bill on the + 1 id_billing_period should be ESTFAC0015" - i.e., among
those accounts:

  1. Narrow to the ones where that account's ID_BILLING_PERIOD has EXACTLY
     ONE GCCOM_BILL row, and that lone row's ID_OFFERED_SERVICE is 176.
     176 = "Rate" (confirmed live via GCCOM_COMPANY_OFFERED_SERVICE).
  2. For the SAME ID_PAYMENT_FORM, look up GCCOM_BILL for the NEXT billing
     period (ID_BILLING_PERIOD + 1) where ID_OFFERED_SERVICE IN (1, 19) -
     1 = Electricity, 19 = Water (also confirmed live, same lookup table).
  3. Keep only the ones where that next-period bill's BILLING_STATUS is
     ESTFAC0015 - confirmed live via GCCOM_BILL_STATUS: "En espera de
     otros servicios" (waiting for other services). This status is the
     whole point of the tool: it's the system's own literal "I'm stuck
     waiting on another service's bill" flag, and the pattern this query
     surfaces is exactly that dependency - the account's Rate (176) bill
     hasn't finished being issued yet, so the very next period's regular
     Electricity/Water bill can't move past ESTFAC0015.

RJ, 2026-09-15, follow-up: "i dont want duplicates, if you find ele, stop
otherwise if it is not found check water" - an account whose next period
has BOTH Electricity and Water stuck at ESTFAC0015 was showing up as TWO
rows; RJ wants ONE row per account, preferring Electricity (1) when it's
itself stuck, falling back to Water (19) only when Electricity ISN'T one
of the stuck rows. Note this is "found stuck", not "found any bill" - an
Electricity bill that exists but already moved past ESTFAC0015 simply
never enters the ESTFAC0015-filtered set to begin with, so it doesn't
block falling through to Water; only an ELECTRICITY ROW THAT IS ITSELF
STUCK takes priority over one for Water. Implemented as a ROW_NUMBER()
PARTITION BY account/period, ORDER BY Electricity-first, keep RN = 1 -
see the `matched` CTE below.

Confirmed live against the tunnel DB (2026-09-15): of ~3800 payment forms
matching RJ's own starting query, ~880 have exactly one bill in their
period and it's a Rate (176) bill, and (after the Electricity-first
dedupe above) ~593 rows - one per distinct account, no duplicates -
match the full chain (576 Electricity-preferred, 17 fell back to Water
because Electricity wasn't itself stuck). Exact counts drift slightly
run to run since this is live production data, not a fixture - the
SHAPE (one row per account, Electricity-first) is what's guaranteed, not
an exact count.

Deliberately pure logic, same convention as every other app/core module:
this only builds SELECT query text; it never executes anything itself.
The caller (web/server.py) runs it via app.db.mssql.run_query.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Iterable

from .script_generator import DEFAULT_AUDIT_PROGRAM, DEFAULT_AUDIT_USER
from .sql_format import format_sql_literal, quote_ident

# --- Schema / table names -------------------------------------------------
# NOTICE_TABLE lives in OUC_ADMIN; BILL/PAYMENT_FORM/OFFERED_SERVICE/
# BILL_STATUS live in OUC_COMMON_ADMIN / OUC_ADMIN as confirmed live via
# INFORMATION_SCHEMA.COLUMNS and the earlier GCCOM_BILL_STATUS query -
# same split this app already has elsewhere (e.g. date_anomaly.py's
# READING_SCHEMA vs. ADMIN_SCHEMA).
NOTICE_SCHEMA = "OUC_ADMIN"
NOTICE_TABLE = "GCCOM_NOTICE_TMP"

BILL_SCHEMA = "OUC_COMMON_ADMIN"
BILL_TABLE = "GCCOM_BILL"
PAYMENT_FORM_TABLE = "GCCOM_PAYMENT_FORM"
OFFERED_SERVICE_TABLE = "GCCOM_COMPANY_OFFERED_SERVICE"

BILL_STATUS_SCHEMA = "OUC_ADMIN"
BILL_STATUS_TABLE = "GCCOM_BILL_STATUS"
BILL_STATUS_LOOKUP_KEY_COLUMN = "COD_DEVELOP"
BILL_STATUS_LOOKUP_DESC_COLUMN = "NAME_TYPE"
OFFERED_SERVICE_LOOKUP_DESC_COLUMN = "NAME_TYPE"

# --- Fixed business constants (RJ's own codes, confirmed live) -----------
NOTICE_STATUS_PENDING = "5000NOTEMP"
# ESTFAC0012 "En proceso de puesta al cobro" (in process of being issued).
BILL_STATUS_ISSUING = "ESTFAC0012"
# ESTFAC0015 "En espera de otros servicios" (waiting for other services) -
# the literal "stuck on a dependency" status this tool surfaces.
BILL_STATUS_WAITING_OTHER_SERVICES = "ESTFAC0015"

# 176 = "Rate" (GCCOM_COMPANY_OFFERED_SERVICE.NAME_TYPE, confirmed live).
OFFERED_SERVICE_RATE = 176
# 1 = Electricity, 19 = Water - the two "regular" services RJ named.
OFFERED_SERVICE_ELECTRICITY = 1
OFFERED_SERVICE_WATER = 19
NEXT_PERIOD_OFFERED_SERVICES = (OFFERED_SERVICE_ELECTRICITY, OFFERED_SERVICE_WATER)

BILL_ISSUANCE_DEFAULT_LIMIT = 2000

# --- Case 2: terminated-account billing-period mismatch ------------------
# RJ, 2026-09-15, own words, first cut of the business rule: accounts
# where EVERY GCCOM_CONTRACTED_SERVICE row is Terminated, but the
# account's bills disagree on ID_BILLING_PERIOD. RJ's own 342702 example:
# 3 such bills, 2 at period 236 (Water, Sanitary) and 1 at period 237
# (Electricity) - the 4th (Rate) bill turned out to already exist, just
# already ESTFAC0005 "Puesta al cobro" (issued), at period 236.
#
# RJ, 2026-09-16, REDESIGN (verbatim): "mostly this will be the final
# bill for each service, so we need to be sure that all service has
# bills in invoicing status, having the same billing date as the
# termination date, or the bill is in status invoiced but also have the
# same termination date as the billing date of gccom_bill. I need a
# filter for this cases where the bill is already invoiced and having
# same termiantion date." Plus two follow-up AskUserQuestion answers:
# termination date = GCCOM_CONTRACTED_SERVICE.END_DATE (confirmed live -
# matches real bills' BILLING_DATE exactly for a real terminated
# service, unlike DROP_DATE which trails END_DATE by ~1 day and never
# matches a bill), and target period = MAX(ID_BILLING_PERIOD) among the
# account's own matched final bills (same as the original rule, just
# now computed from the termination-date-matched set instead of the
# NOTICE_TMP/ESTFAC0012 "pending" set). Third answer, RJ's own words:
# "the idea is i only want to see the accounts, maybe its a drill down
# to show the bills... the filter is for me to know the cases where it
# is complete only that the other service bills are already invoiced" -
# i.e. group by account (one row per account, drill down to services),
# with a filter for "Complete" (every service's final bill already
# invoiced and aligned) vs. "Needs action".
#
# So per terminated service: find its OWN final bill - the one row in
# GCCOM_BILL whose BILLING_DATE equals that service's END_DATE - and
# require it be either ESTFAC0012 (invoicing) or ESTFAC0005 (invoiced).
# No matching bill at all is itself a NEEDS_UPDATE case (nothing to
# align, but worth flagging - see build_terminated_period_mismatch_query
# docstring for the exact NEEDS_UPDATE rule). Target period is the MAX
# ID_BILLING_PERIOD among an account's matched final bills; a service
# not already at that period needs its ID_BILLING_PERIOD moved there.
#
# GCCOM_CONTRACTED_SERVICE lives in OUC_COMMON_ADMIN (confirmed live via
# INFORMATION_SCHEMA.COLUMNS) - same schema as BILL_SCHEMA above.
CONTRACTED_SERVICE_TABLE = "GCCOM_CONTRACTED_SERVICE"
CONTRACTED_SERVICE_PK_COLUMN = "ID_CONTRACTED_SERVICE"
CONTRACTED_SERVICE_STATUS_COLUMN = "STATUS"
CONTRACTED_SERVICE_END_DATE_COLUMN = "END_DATE"
# ESTSC00004 "Baja" (Terminated) - confirmed live via GCCOM_CONTRACT_SERV_
# STATUS and cross-checked against a real terminated account's services.
TERMINATED_SERVICE_STATUS = "ESTSC00004"
# ESTFAC0005 "Puesta al cobro" (issued/invoiced) - confirmed live via
# GCCOM_BILL_STATUS, same lookup table BILL_STATUS_ISSUING (ESTFAC0012)
# already uses above. A terminated service's final bill counts as
# "already handled" only once it reaches this status AT the termination
# date - see CONTRACTED_SERVICE_END_DATE_COLUMN comment above.
BILL_STATUS_INVOICED = "ESTFAC0005"
FINAL_BILL_STATUSES = (BILL_STATUS_ISSUING, BILL_STATUS_INVOICED)

# How far back to look for terminated services by default. RJ's business
# process only ever reviews recent terminations, and this also keeps the
# query fast: GCCOM_CONTRACTED_SERVICE has ~4.7M rows total, ~3M of them
# already Terminated (confirmed live 2026-09-17), so scanning "every
# terminated service ever" is both irrelevant to the analyst and needless
# extra work once scoped by a real date. 60 days keeps the default result
# set to a size an analyst can actually review in one page (a 6-month
# window returned ~94K service rows / 27K accounts live - correct, but
# far more than anyone would page through) while staying well inside
# TERMINATED_PERIOD_DEFAULT_LIMIT's row cap so a normal scan isn't
# truncated mid-account. RJ can widen it via the UI/API `days_back` param
# when they need to look further back.
TERMINATED_PERIOD_LOOKBACK_DAYS_DEFAULT = 60

# GCCOM_BILL's own audit columns (confirmed live via INFORMATION_SCHEMA.
# COLUMNS: CREATE_DATE, UPDATE_DATE, UPDATE_USER, UPDATE_PROGRAM,
# OPTIMIST_LOCK) - uppercase here (unlike date_anomaly.py's lowercase
# AUDIT_*_COL) to match this module's own column-naming convention
# (ID_BILLING_PERIOD, BILLING_STATUS, etc. are all uppercase throughout).
BILL_AUDIT_PROGRAM_COL = "UPDATE_PROGRAM"
BILL_AUDIT_DATE_COL = "UPDATE_DATE"
BILL_AUDIT_USER_COL = "UPDATE_USER"

TERMINATED_PERIOD_DEFAULT_LIMIT = 8000


def _qualified(schema: str, table: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(table)}" if schema else quote_ident(table)


def build_stuck_bills_query(limit: int | None = BILL_ISSUANCE_DEFAULT_LIMIT) -> str:
    """
    The full chain from the module docstring, as one query:

    `period_counts` - one row per (ID_PAYMENT_FORM, ID_BILLING_PERIOD)
    with how many GCCOM_BILL rows share it, so `candidate` can require
    exactly 1.

    `candidate` - RJ's own starting query (notice pending + bill issuing),
    narrowed to accounts whose Rate (176) bill is the ONLY bill in its
    billing period.

    `matched` - joins GCCOM_BILL again for the SAME account's next billing
    period (PERIOD_RATE + 1), restricted to Electricity/Water (1, 19),
    keeping only rows still BILLING_STATUS = ESTFAC0015. A ROW_NUMBER()
    PARTITIONed by (ID_PAYMENT_FORM, PERIOD_RATE), ORDERed so Electricity
    (1) sorts before Water (19), then keeping only RN = 1 in the final
    SELECT, is the "no duplicates - Electricity first, Water only if
    Electricity isn't stuck" rule RJ asked for (2026-09-15) - an account
    with BOTH stuck now surfaces as one row (Electricity), not two.

    Final SELECT - LEFT JOINs to GCCOM_COMPANY_OFFERED_SERVICE and
    GCCOM_BILL_STATUS purely for human-readable descriptions (service
    name, status name) - LEFT so a row still surfaces even if a
    description happens to be missing, same defensive pattern every other
    lookup JOIN in this app uses.

    `limit` adds a `TOP (N)` cap (no NISS/id scoping here, same reasoning
    as build_detect_all_anomalies_query's own `limit` - this is a system-
    wide scan). Pass None to disable the cap entirely.
    """
    notice_tbl = _qualified(NOTICE_SCHEMA, NOTICE_TABLE)
    bill_tbl = _qualified(BILL_SCHEMA, BILL_TABLE)
    pf_tbl = _qualified(BILL_SCHEMA, PAYMENT_FORM_TABLE)
    offered_service_tbl = _qualified(BILL_SCHEMA, OFFERED_SERVICE_TABLE)
    bill_status_tbl = _qualified(BILL_STATUS_SCHEMA, BILL_STATUS_TABLE)
    next_services_list = ", ".join(format_sql_literal(s) for s in NEXT_PERIOD_OFFERED_SERVICES)
    top_clause = f"TOP ({int(limit)}) " if limit else ""
    return (
        f"WITH period_counts AS (\n"
        f"  SELECT ID_PAYMENT_FORM, ID_BILLING_PERIOD, COUNT(*) AS BILL_COUNT\n"
        f"  FROM {bill_tbl}\n"
        f"  GROUP BY ID_PAYMENT_FORM, ID_BILLING_PERIOD\n"
        f"),\n"
        f"candidate AS (\n"
        f"  SELECT DISTINCT pf.ID_PAYMENT_FORM, pf.REFERENCE, nt.UPDATE_DATE AS NOTICE_UPDATE_DATE,\n"
        f"    b.ID_BILL AS ID_BILL_RATE, b.ID_BILLING_PERIOD AS PERIOD_RATE\n"
        f"  FROM {notice_tbl} nt\n"
        f"  JOIN {bill_tbl} b ON b.ID_BILL = nt.ID_BILL\n"
        f"  JOIN {pf_tbl} pf ON pf.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM\n"
        f"  JOIN period_counts pc ON pc.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM\n"
        f"    AND pc.ID_BILLING_PERIOD = b.ID_BILLING_PERIOD\n"
        f"  WHERE nt.COD_STATUS = {format_sql_literal(NOTICE_STATUS_PENDING)}\n"
        f"    AND b.BILLING_STATUS = {format_sql_literal(BILL_STATUS_ISSUING)}\n"
        f"    AND b.ID_OFFERED_SERVICE = {format_sql_literal(OFFERED_SERVICE_RATE)}\n"
        f"    AND pc.BILL_COUNT = 1\n"
        f"),\n"
        f"matched AS (\n"
        f"  SELECT c.ID_PAYMENT_FORM, c.REFERENCE, c.NOTICE_UPDATE_DATE,\n"
        f"    c.ID_BILL_RATE, c.PERIOD_RATE,\n"
        f"    nb.ID_BILL AS ID_BILL_NEXT, nb.ID_OFFERED_SERVICE AS OFFERED_SERVICE_NEXT,\n"
        f"    nb.ID_BILLING_PERIOD AS PERIOD_NEXT, nb.BILLING_STATUS AS STATUS_NEXT,\n"
        f"    ROW_NUMBER() OVER (\n"
        f"      PARTITION BY c.ID_PAYMENT_FORM, c.PERIOD_RATE\n"
        f"      ORDER BY CASE WHEN nb.ID_OFFERED_SERVICE = {format_sql_literal(OFFERED_SERVICE_ELECTRICITY)} THEN 0 ELSE 1 END\n"
        f"    ) AS RN\n"
        f"  FROM candidate c\n"
        f"  JOIN {bill_tbl} nb ON nb.ID_PAYMENT_FORM = c.ID_PAYMENT_FORM\n"
        f"    AND nb.ID_BILLING_PERIOD = c.PERIOD_RATE + 1\n"
        f"    AND nb.ID_OFFERED_SERVICE IN ({next_services_list})\n"
        f"  WHERE nb.BILLING_STATUS = {format_sql_literal(BILL_STATUS_WAITING_OTHER_SERVICES)}\n"
        f")\n"
        f"SELECT {top_clause}m.ID_PAYMENT_FORM, m.REFERENCE, m.NOTICE_UPDATE_DATE,\n"
        f"  m.ID_BILL_RATE, m.PERIOD_RATE,\n"
        f"  m.ID_BILL_NEXT, m.OFFERED_SERVICE_NEXT,\n"
        f"  os.{OFFERED_SERVICE_LOOKUP_DESC_COLUMN} AS OFFERED_SERVICE_NEXT_DESC,\n"
        f"  m.PERIOD_NEXT, m.STATUS_NEXT,\n"
        f"  bs.{BILL_STATUS_LOOKUP_DESC_COLUMN} AS STATUS_NEXT_DESC\n"
        f"FROM matched m\n"
        f"LEFT JOIN {offered_service_tbl} os ON os.ID_OFFERED_SERVICE = m.OFFERED_SERVICE_NEXT\n"
        f"LEFT JOIN {bill_status_tbl} bs ON bs.{BILL_STATUS_LOOKUP_KEY_COLUMN} = m.STATUS_NEXT\n"
        f"WHERE m.RN = 1\n"
        f"ORDER BY m.NOTICE_UPDATE_DATE;"
    )


def build_terminated_period_mismatch_query(
    limit: int | None = TERMINATED_PERIOD_DEFAULT_LIMIT,
    *,
    days_back: int | None = TERMINATED_PERIOD_LOOKBACK_DAYS_DEFAULT,
) -> str:
    """
    Case 2's detection query (2026-09-17 redesign) - one row per
    (account, terminated service), for the caller (web/server.py) to
    group into one row per account. See the module-level Case 2 comment
    block above for the full business-rule narrative, RJ's own 342702
    example, and the three AskUserQuestion answers this shape implements.

    `terminated_services` - every GCCOM_CONTRACTED_SERVICE row that's
    Terminated (STATUS = ESTSC00004), optionally scoped to the last
    `days_back` days by END_DATE (see TERMINATED_PERIOD_LOOKBACK_DAYS_
    DEFAULT's comment for why this matters: ~3M of GCCOM_CONTRACTED_
    SERVICE's ~4.7M rows are already Terminated, so an unscoped scan is
    both irrelevant to the analyst and the single biggest cost in this
    query - confirmed live, COUNT(*) with no date filter: 2,961,122
    rows/1.4s; with a 6-month filter: 95,256 rows/1.0s). Pass
    days_back=None to disable the window entirely (every Terminated
    service, any age).

    `matched` - LEFT JOINs each terminated service to its OWN final bill:
    the GCCOM_BILL row for that SAME contracted service (joined on
    ID_CONTRACTED_SERVICE - the service's own row PK, not ID_PAYMENT_
    FORM + ID_OFFERED_SERVICE; see CONTRACTED_SERVICE_PK_COLUMN's use
    below and the module Case 2 comment) whose BILLING_DATE equals that
    service's own END_DATE (the termination date) and whose status is
    invoicing or already invoiced (FINAL_BILL_STATUSES). ROW_NUMBER()
    PARTITIONed by ID_CONTRACTED_SERVICE picks one bill if more than one
    happens to match (defensive - real data hasn't shown this, but the
    join has no uniqueness guarantee to lean on). LEFT (not INNER) so a
    service with NO matching final bill still surfaces as a row with
    ID_BILL NULL - itself a NEEDS_UPDATE case (see `flagged` below).

    `with_target` - keeps RN = 1 and adds TARGET_PERIOD: MAX(ID_BILLING_
    PERIOD) OVER (PARTITION BY ID_PAYMENT_FORM) among the account's own
    matched final bills - RJ's own confirmed rule (AskUserQuestion #2).

    `flagged` - NEEDS_UPDATE = 1 when a service has no matching final
    bill at all (ID_BILL IS NULL) OR its final bill's ID_BILLING_PERIOD
    doesn't match TARGET_PERIOD; NEEDS_UPDATE = 0 means that service's
    final bill is already exactly where it should be. ACCOUNT_HAS_ISSUE
    is the same flag re-aggregated with MAX(...) OVER (PARTITION BY
    ID_PAYMENT_FORM) - 1 if ANY of the account's services needs action,
    0 only when every service's final bill already agrees (RJ's own
    "Complete" case, AskUserQuestion #3) - the caller uses this to group
    rows into an account-level Complete/Needs-action filter without a
    second query.

    `account_status` - per account (scoped to just the accounts already
    in `terminated_services`, not a full-table scan), whether it has any
    NON-Terminated service left. Only accounts with ACTIVE_COUNT = 0
    (every service Terminated) are Case 2's business - a terminated
    service whose account also has a live service is a different
    situation entirely. Grouped/joined once (not a correlated EXISTS per
    row) - the module Case 2 comment covers why a correlated EXISTS
    against a big table, re-evaluated per outer row, was a real
    performance trap earlier in this same investigation.

    Final SELECT - joins GCCOM_PAYMENT_FORM for REFERENCE. `limit`
    behaves the same as build_stuck_bills_query's own - a `TOP (N)` cap
    (applied to the per-service row count, not per-account), pass None
    to disable.

    Live-confirmed performance (2026-09-17, 6-month window): full query
    (COUNT + account/issue rollup) ran in ~8s over 94,218 terminated-
    service rows / 27,166 distinct accounts, 16,138 of them flagged
    ACCOUNT_HAS_ISSUE = 1 - a huge improvement over every earlier shape
    tried (OR EXISTS, UNION, even this same JOIN shape with an N'...'
    literal) which all hit the app's 120s query timeout. The fix wasn't
    the join shape - see app/core/sql_format.py's 2026-09-17 comment:
    format_sql_literal used to emit N'...' (nvarchar) literals, and
    BILLING_STATUS/STATUS are plain varchar columns, so every one of
    those WHERE/JOIN comparisons was silently defeating its supporting
    index via an implicit CONVERT().
    """
    bill_tbl = _qualified(BILL_SCHEMA, BILL_TABLE)
    pf_tbl = _qualified(BILL_SCHEMA, PAYMENT_FORM_TABLE)
    contracted_service_tbl = _qualified(BILL_SCHEMA, CONTRACTED_SERVICE_TABLE)
    top_clause = f"TOP ({int(limit)}) " if limit else ""
    final_statuses_list = ", ".join(format_sql_literal(s) for s in FINAL_BILL_STATUSES)
    lookback_clause = (
        f"\n    AND cs.{CONTRACTED_SERVICE_END_DATE_COLUMN} >= DATEADD(DAY, -{int(days_back)}, CAST(GETDATE() AS DATE))"
        if days_back
        else ""
    )
    return (
        f"WITH terminated_services AS (\n"
        f"  SELECT cs.{CONTRACTED_SERVICE_PK_COLUMN}, cs.ID_PAYMENT_FORM, cs.ID_OFFERED_SERVICE,\n"
        f"    cs.{CONTRACTED_SERVICE_END_DATE_COLUMN} AS END_DATE\n"
        f"  FROM {contracted_service_tbl} cs\n"
        f"  WHERE cs.{CONTRACTED_SERVICE_STATUS_COLUMN} = {format_sql_literal(TERMINATED_SERVICE_STATUS)}"
        f"{lookback_clause}\n"
        f"),\n"
        f"matched AS (\n"
        f"  SELECT ts.{CONTRACTED_SERVICE_PK_COLUMN}, ts.ID_PAYMENT_FORM, ts.ID_OFFERED_SERVICE, ts.END_DATE,\n"
        f"    b.ID_BILL, b.ID_BILLING_PERIOD, b.BILLING_STATUS,\n"
        f"    ROW_NUMBER() OVER (PARTITION BY ts.{CONTRACTED_SERVICE_PK_COLUMN} ORDER BY b.ID_BILL DESC) AS RN\n"
        f"  FROM terminated_services ts\n"
        f"  LEFT JOIN {bill_tbl} b\n"
        f"    ON b.{CONTRACTED_SERVICE_PK_COLUMN} = ts.{CONTRACTED_SERVICE_PK_COLUMN}\n"
        f"    AND b.BILLING_DATE = ts.END_DATE\n"
        f"    AND b.BILLING_STATUS IN ({final_statuses_list})\n"
        f"),\n"
        f"with_target AS (\n"
        f"  SELECT *, MAX(ID_BILLING_PERIOD) OVER (PARTITION BY ID_PAYMENT_FORM) AS TARGET_PERIOD\n"
        f"  FROM matched\n"
        f"  WHERE RN = 1\n"
        f"),\n"
        f"flagged AS (\n"
        f"  SELECT *,\n"
        f"    CASE WHEN ID_BILL IS NULL THEN 1 WHEN ID_BILLING_PERIOD <> TARGET_PERIOD THEN 1 ELSE 0 END AS NEEDS_UPDATE,\n"
        f"    MAX(CASE WHEN ID_BILL IS NULL THEN 1 WHEN ID_BILLING_PERIOD <> TARGET_PERIOD THEN 1 ELSE 0 END)\n"
        f"      OVER (PARTITION BY ID_PAYMENT_FORM) AS ACCOUNT_HAS_ISSUE\n"
        f"  FROM with_target\n"
        f"),\n"
        f"account_status AS (\n"
        f"  SELECT cs.ID_PAYMENT_FORM,\n"
        f"    SUM(CASE WHEN cs.{CONTRACTED_SERVICE_STATUS_COLUMN} <> {format_sql_literal(TERMINATED_SERVICE_STATUS)} THEN 1 ELSE 0 END) AS ACTIVE_COUNT\n"
        f"  FROM {contracted_service_tbl} cs\n"
        f"  WHERE cs.ID_PAYMENT_FORM IN (SELECT DISTINCT ID_PAYMENT_FORM FROM terminated_services)\n"
        f"  GROUP BY cs.ID_PAYMENT_FORM\n"
        f")\n"
        f"SELECT {top_clause}f.ID_PAYMENT_FORM, pf.REFERENCE, f.ID_OFFERED_SERVICE,\n"
        f"  f.END_DATE, f.ID_BILL, f.ID_BILLING_PERIOD, f.BILLING_STATUS,\n"
        f"  f.TARGET_PERIOD, f.NEEDS_UPDATE, f.ACCOUNT_HAS_ISSUE\n"
        f"FROM flagged f\n"
        f"JOIN account_status acct ON acct.ID_PAYMENT_FORM = f.ID_PAYMENT_FORM AND acct.ACTIVE_COUNT = 0\n"
        f"JOIN {pf_tbl} pf ON pf.ID_PAYMENT_FORM = f.ID_PAYMENT_FORM\n"
        f"ORDER BY f.ID_PAYMENT_FORM, f.ID_OFFERED_SERVICE;"
    )


def _bill_audit_set_fragment(program: str, user: str) -> str:
    return (
        f",\n    {quote_ident(BILL_AUDIT_PROGRAM_COL)} = {format_sql_literal(program)}"
        f",\n    {quote_ident(BILL_AUDIT_DATE_COL)} = GETDATE()"
        f",\n    {quote_ident(BILL_AUDIT_USER_COL)} = {format_sql_literal(user)}"
    )


def _strip_sql_comments(sql_text: str) -> str:
    """
    Local copy of date_anomaly.py's own _strip_sql_comments - same
    line-based "drop any line that's purely a SQL comment, then collapse
    repeated blank lines" behavior, kept as its own copy here (rather
    than importing date_anomaly's private helper) so this module stays
    self-contained, same as every other app/core module in this app.
    """
    kept = [line for line in sql_text.split("\n") if not line.strip().startswith("--")]
    collapsed: list[str] = []
    prev_blank = False
    for line in kept:
        is_blank = not line.strip()
        if is_blank and prev_blank:
            continue
        collapsed.append(line)
        prev_blank = is_blank
    return "\n".join(collapsed).strip("\n") + "\n"


@dataclass
class TerminatedPeriodFixScript:
    """
    Result of build_terminated_period_fix_script - one UPDATE statement
    per (id_bill, target_period) pair the caller passes in, each moving
    that bill's ID_BILLING_PERIOD to the account's target (latest) period.
    """
    sql_text: str
    update_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def statement_count(self) -> int:
        return self.update_count

    @property
    def warning_count(self) -> int:
        return len(self.warnings)


def build_terminated_period_fix_script(
    updates: Iterable[tuple[Any, Any]],
    *,
    program: str = DEFAULT_AUDIT_PROGRAM,
    user: str = DEFAULT_AUDIT_USER,
    clean: bool = False,
) -> TerminatedPeriodFixScript:
    """
    Builds the "generate update script" button's output for Case 2 - RJ's
    own words: "we need to find this cases, and an update on the bill
    with different id_billing_period... will be created, the new
    billing_period will be the latest id_billing_period... so you will
    add a button form me to generate the update script".

    `updates` is an iterable of (id_bill, target_period) pairs - the
    caller (web/server.py) gets these by re-running build_terminated_
    period_mismatch_query right before Generate (same "re-verify at
    generate time" pattern as build_cleanup_script/build_correction_
    script elsewhere in this app, since the detect list may be stale by
    the time the analyst clicks Generate) and keeping only the rows
    where NEEDS_UPDATE = 1, passing (ID_BILL, TARGET_PERIOD) for each.

    A row with NEEDS_UPDATE = 1 but ID_BILL NULL (the 2026-09-17 redesign
    - a terminated service with no bill at all matching its own END_DATE)
    has nothing to UPDATE - there's no bill row to move. This function
    skips those pairs (id_bill is None) rather than emitting a broken
    `WHERE ID_BILL = NULL` statement (always false, silently a no-op, but
    still wrong to generate), and records one warning per skip so the
    analyst sees it needs manual investigation instead of it just
    vanishing.

    Each statement carries a defensive WHERE ID_BILLING_PERIOD <> target
    guard (in addition to WHERE ID_BILL = id) - so re-running the script
    against a bill that's already been corrected some other way (or
    already ran once) is a safe no-op, same "only touch if still in the
    state we expect" guard convention as every other correction script
    in this app (build_correction_script's Part 3, build_cleanup_script's
    Part A, etc).
    """
    bill_tbl = _qualified(BILL_SCHEMA, BILL_TABLE)
    warnings: list[str] = []
    pairs = list(updates)

    stmts: list[str] = []
    skipped = 0
    for id_bill, target_period in pairs:
        if id_bill is None:
            skipped += 1
            continue
        set_clause = f"{quote_ident('ID_BILLING_PERIOD')} = {format_sql_literal(target_period)}"
        set_clause += _bill_audit_set_fragment(program, user)
        stmts.append(
            f"-- Align ID_BILLING_PERIOD for bill {id_bill} -> {target_period}\n"
            f"UPDATE {bill_tbl}\n"
            f"SET {set_clause}\n"
            f"WHERE {quote_ident('ID_BILL')} = {format_sql_literal(id_bill)}\n"
            f"  AND {quote_ident('ID_BILLING_PERIOD')} <> {format_sql_literal(target_period)};"
        )
    if skipped:
        warnings.append(
            f"{skipped} service(s) with no bill matching their own termination "
            "date were skipped - nothing to UPDATE. Investigate manually."
        )

    header = [
        "-- Bill Issuance Validator: Case 2 (terminated account, billing-period",
        "-- mismatch) UPDATE script - generated by ScriptGen",
        f"-- Generated (UTC): {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
        f"-- Bills selected: {len(pairs)}",
        f"-- Program/Jira: {program}",
        "-- WARNING: every service on the affected account(s) is Terminated",
        "-- (GCCOM_CONTRACTED_SERVICE.STATUS = ESTSC00004) - this script only",
        "-- realigns GCCOM_BILL.ID_BILLING_PERIOD so the account's remaining",
        "-- pending/issuing bills share one billing period and can complete",
        "-- issuance together. It does not change BILLING_STATUS, BILLING_DATE,",
        "-- or anything else on the bill.",
        f"-- Statements: {len(stmts)}",
    ]
    for w in warnings:
        header.append(f"-- WARNING: {w}")
    header.append("")

    body = "\n\n".join(stmts) if stmts else "-- Nothing to update."

    footer = [
        "",
        "-- Review the statements above before running them.",
    ]

    sql_text = "\n".join(header) + body + "\n".join(footer)
    if clean:
        sql_text = _strip_sql_comments(sql_text)
    return TerminatedPeriodFixScript(
        sql_text=sql_text,
        update_count=len(stmts),
        warnings=warnings,
    )
