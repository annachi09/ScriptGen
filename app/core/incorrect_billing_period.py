"""
Incorrect Billing Period ("RATE INCORRECT BILLING PERIOD"): finds GCCOM_
ANOMALOUS records whose LAST_BILLING_DATE has drifted past BILLING_DATE -
an anomaly that's still open but whose "last billed" marker is somehow
later than its own billing date, the literal sign this app's other Rate/
billing-period tools look for elsewhere (Bill Issuance Validator's Case 1
being the closest cousin: a Rate service whose billing period bookkeeping
has gone out of sync with the rest of the account).

RJ, 2026-09-23, own words + own SQL (verbatim, reformatted to this app's
usual UPPERCASE/one-join-per-line style only - every table/column/filter
below is exactly what RJ supplied, nothing added to the WHERE clause):

    select AN.ANOMALOUS_STATUS, rr.id_billing_period, cs.ID_OFFERED_SERVICE,
           an.ID_ANOMALOUS, AN.ID_BILLING_SERVICE, ss.niss,
           an.last_billing_date, an.billing_date, itb.id_item_to_bill,
           ITB.ID_BILLING_SERVICE, itb.status, rr.id_reading, rr.read_status,
           rr.reading_type, rr.READY_USAGE, rr.READING_PREV_DATE,
           rr.READING_DATE, ss1.niss, BS1.ID_BILLING_SERVICE
    from GCCOM_ANOMALOUS an
      join GCCOM_BILLING_SERVICE bs on bs.ID_BILLING_SERVICE = an.ID_BILLING_SERVICE
      join GCCOM_CONTRACTED_SERVICE cs on cs.ID_CONTRACTED_SERVICE = bs.ID_CONTRACTED_SERVICE
      join GCCOM_SECTOR_SUPPLY ss on ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
      left join GCCOM_ITEMS_TO_BILL itb on itb.ID_ITEM_TO_BILL = an.id_item_to_bill
      left join GCCOM_READINGS_ITEMSTOBILL rit on rit.id_item_to_bill = itb.id_item_to_bill
      left join GCGT_RE_READING rr on rr.ID_READING = rit.id_reading
      left join GCCOM_SECTOR_SUPPLY ss1 on ss1.ID_SECTOR_SUPPLY = rr.ID_SECTOR_SUPPLY
      left join GCCOM_CONTRACTED_SERVICE cs1
                on cs1.id_Sector_SUPPLY = RR.ID_sECTOR_SUPPLY AND RR.READING_DATE = CS1.END_DATE
      LEFT JOIN GCCOM_BILLING_SERVICE BS1 ON BS1.ID_CONTRACTED_SERVICE = CS1.ID_CONTRACTED_SERVICE
    where an.LAST_BILLING_DATE > an.BILLING_DATE
      and ANOMALOUS_STATUS not in ('ESTAN00004', 'ESTAN00005', 'ESTAN00008', 'ESTAN00003');

The CS1/BS1 tail (reading -> sector supply -> a contracted service whose
END_DATE happens to equal that very reading's own READING_DATE -> that
service's billing service) is RJ's own way of surfacing whether the
anomalous reading landed exactly on a contract's termination date - a
likely root cause for why its billing period ended up wrong. Kept as-is
(not reinterpreted) since only RJ's own domain knowledge explains why that
specific coincidence matters; this module just reproduces the join
faithfully.

Two enrichments added on top of RJ's exact column list (both LEFT JOINs,
so they can't remove a row RJ's own query would have returned):
  - GCCOM_PAYMENT_FORM.REFERENCE (the account number) - every other page
    in this app shows the account, and CS already has the FK to get there.
  - GCCOM_COMPANY_OFFERED_SERVICE.NAME_TYPE (OFFERED_SERVICE_DESC) - RJ,
    same message, follow-up: "Group it with id_offered_service, and can be
    filtered with ID_OFFERED_SERVICE description" - reuses the exact same
    lookup table/column bill_issuance_validator.py already uses for this
    (OFFERED_SERVICE_TABLE/OFFERED_SERVICE_LOOKUP_DESC_COLUMN there), so
    the web layer can group rows by ID_OFFERED_SERVICE and offer a filter
    by its human-readable description rather than a bare numeric code.

Several of RJ's original SELECT items collide on column name once run for
real (AN.ID_BILLING_SERVICE / ITB.ID_BILLING_SERVICE / BS1.ID_BILLING_
SERVICE all named ID_BILLING_SERVICE; SS.NISS / SS1.NISS both named NISS) -
SQL Server itself tolerates duplicate result-set column names, but this
app's own row-shaping (dict(zip(columns, row)) style, same as every other
page) would silently collapse duplicates to whichever one wins the zip.
Every such column is given its own distinct alias below (ANOMALY_BILLING_
SERVICE / ITEM_BILLING_SERVICE / NEXT_BILLING_SERVICE, NISS / NISS_AT_
READING) - same values RJ's own query returns, just addressable.

The correction script (RJ, same message, own exact SQL): "the script to
correct is : update GCCOM_ANOMALOUS SET UPDATE_DATE=GETDATE(),
UPDATE_USER='RMA', UPDATE_PROGRAM='RATE_INCORRECT_BILLPERIOD',
ANOMALOUS_STATUS = 'ESTAN00005' where ID_ANOMALOUS in ("ID_ANOMALOUS
HERE") and ANOMALOUS_STATUS in ('ESTAN00001', 'ESTAN00009'); and can
generate script base on the selected anomalies from the list only" - a
single batched UPDATE over an analyst-picked subset of ID_ANOMALOUS values
(never "every detected row"), same one-statement-with-IN(...)-list shape
as bill_issuance_validator.build_release_notice_script (RJ gave a fixed
literal UPDATE_USER/UPDATE_PROGRAM template there too, not the usual
JIRAXXXX placeholder - same precedent followed here: CORRECTION_SCRIPT_
DEFAULT_PROGRAM/USER below are real, working defaults, not placeholders,
but still ordinary overridable parameters). The re-check `AND ANOMALOUS_
STATUS IN ('ESTAN00001','ESTAN00009')` in RJ's own WHERE clause is kept
verbatim - it's what makes re-running this script against an anomaly
that's already been cancelled (by this script or anything else) a safe
no-op, same "only touch if still in the state we expect" convention every
other correction script in this app already follows.

Deliberately pure logic, same convention as every other app/core module:
this only builds SELECT/UPDATE query text; it never executes anything
itself. The caller (web/server.py) runs it via app.db.mssql.run_query.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Iterable

from .sql_format import format_sql_literal, quote_ident

# --- Schema / table names -------------------------------------------------
# Same OUC_ADMIN/OUC_COMMON_ADMIN split this app already uses everywhere
# else (date_anomaly.py's ADMIN_SCHEMA/READING_SCHEMA, bill_issuance_
# validator.py's own schema constants) - GCCOM_ANOMALOUS/GCCOM_ITEMS_TO_
# BILL/GCCOM_READINGS_ITEMSTOBILL live in OUC_ADMIN, GCGT_RE_READING and
# GCCOM_COMPANY_OFFERED_SERVICE live in OUC_COMMON_ADMIN (confirmed by
# date_anomaly.py/bill_issuance_validator.py's own prior live checks of
# these exact same tables - not re-verified independently this round).
# BILLING_SERVICE_TABLE/CONTRACTED_SERVICE_TABLE/SECTOR_SUPPLY_TABLE/
# PAYMENT_FORM_TABLE are left UNQUALIFIED, matching RJ's own query (none of
# these were schema-prefixed in what he supplied) and date_anomaly.py's own
# identical choice for these same tables.
ADMIN_SCHEMA = "OUC_ADMIN"
ANOMALOUS_TABLE = "GCCOM_ANOMALOUS"
ITEMS_TO_BILL_TABLE = "GCCOM_ITEMS_TO_BILL"
READINGS_ITEMSTOBILL_TABLE = "GCCOM_READINGS_ITEMSTOBILL"

READING_SCHEMA = "OUC_COMMON_ADMIN"
READING_TABLE = "GCGT_RE_READING"
OFFERED_SERVICE_SCHEMA = "OUC_COMMON_ADMIN"
OFFERED_SERVICE_TABLE = "GCCOM_COMPANY_OFFERED_SERVICE"
OFFERED_SERVICE_DESC_COLUMN = "NAME_TYPE"

BILLING_SERVICE_TABLE = "GCCOM_BILLING_SERVICE"
CONTRACTED_SERVICE_TABLE = "GCCOM_CONTRACTED_SERVICE"
SECTOR_SUPPLY_TABLE = "GCCOM_SECTOR_SUPPLY"
PAYMENT_FORM_TABLE = "GCCOM_PAYMENT_FORM"

ANOMALOUS_STATUS_COLUMN = "ANOMALOUS_STATUS"
ANOMALOUS_ID_COLUMN = "ID_ANOMALOUS"

# RJ's own WHERE clause, verbatim - these 4 statuses are excluded from
# detection (already cancelled/closed/rejected in some way that makes them
# not worth surfacing here); NOT the same list as the 2 statuses the
# correction script itself is allowed to touch (ANOMALOUS_CORRECTABLE_
# STATUSES below) - detection is intentionally broader than correction.
ANOMALOUS_DETECT_EXCLUDED_STATUSES = ("ESTAN00004", "ESTAN00005", "ESTAN00008", "ESTAN00003")
# The 2 "still open" statuses the correction script's own re-check guards
# on - same codes date_anomaly.py's ANOMALOUS_OPEN_STATUSES uses for the
# same concept elsewhere in this app.
ANOMALOUS_CORRECTABLE_STATUSES = ("ESTAN00001", "ESTAN00009")
ANOMALOUS_STATUS_CANCELLED = "ESTAN00005"

DETECT_DEFAULT_LIMIT = 2000

# RJ gave a fixed, working UPDATE_USER/UPDATE_PROGRAM template for this
# specific action (own exact SQL, see module docstring) rather than the
# usual JIRAXXXX placeholder - same precedent as bill_issuance_validator.
# RELEASE_SCRIPT_DEFAULT_PROGRAM/USER. Still ordinary overridable
# parameters, just defaulted to RJ's own literals.
CORRECTION_SCRIPT_DEFAULT_PROGRAM = "RATE_INCORRECT_BILLPERIOD"
CORRECTION_SCRIPT_DEFAULT_USER = "RMA"


def _qualified(schema: str, table: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(table)}" if schema else quote_ident(table)


def build_detect_query(
    limit: int | None = DETECT_DEFAULT_LIMIT,
    *,
    excluded_statuses: Iterable[str] = ANOMALOUS_DETECT_EXCLUDED_STATUSES,
) -> str:
    """
    RJ's own RATE INCORRECT BILLING PERIOD query (see module docstring),
    reproduced faithfully - only additions are the PF.REFERENCE/OS.NAME_
    TYPE enrichment LEFT JOINs and distinct aliases for the 3 ID_BILLING_
    SERVICE columns / 2 NISS columns RJ's own SELECT list would otherwise
    collide on (see module docstring's "collide on column name" note).

    `limit` adds a TOP (N) cap (SQL Server has no LIMIT keyword) - this
    query has no NISS/id-list scoping, so like date_anomaly.py's own
    build_detect_all_anomalies_query it could otherwise return an unbounded
    number of rows against a large production database. Pass None to
    disable the cap entirely.

    ORDER BY CS.ID_OFFERED_SERVICE, AN.ID_ANOMALOUS (not in RJ's own query)
    so rows already arrive clustered by offered service - the web layer's
    own grouping (RJ: "Group it with id_offered_service") then just walks
    the rows in order rather than needing a second sort.
    """
    anomalous_tbl = _qualified(ADMIN_SCHEMA, ANOMALOUS_TABLE)
    item_tbl = _qualified(ADMIN_SCHEMA, ITEMS_TO_BILL_TABLE)
    ri_tbl = _qualified(ADMIN_SCHEMA, READINGS_ITEMSTOBILL_TABLE)
    reading_tbl = _qualified(READING_SCHEMA, READING_TABLE)
    offered_service_tbl = _qualified(OFFERED_SERVICE_SCHEMA, OFFERED_SERVICE_TABLE)
    status_list = ", ".join(format_sql_literal(s) for s in excluded_statuses)
    top_clause = f"TOP ({int(limit)}) " if limit else ""

    return (
        f"SELECT {top_clause}\n"
        f"  AN.{ANOMALOUS_ID_COLUMN}, AN.{ANOMALOUS_STATUS_COLUMN},\n"
        f"  AN.ID_BILLING_SERVICE AS ANOMALY_BILLING_SERVICE,\n"
        f"  AN.LAST_BILLING_DATE, AN.BILLING_DATE,\n"
        f"  CS.ID_OFFERED_SERVICE, OS.{OFFERED_SERVICE_DESC_COLUMN} AS OFFERED_SERVICE_DESC,\n"
        f"  PF.REFERENCE AS ACCOUNT, SS.NISS,\n"
        f"  ITB.ID_ITEM_TO_BILL, ITB.ID_BILLING_SERVICE AS ITEM_BILLING_SERVICE, ITB.STATUS AS ITEM_STATUS,\n"
        f"  RR.ID_BILLING_PERIOD, RR.ID_READING, RR.READ_STATUS, RR.READING_TYPE,\n"
        f"  RR.READY_USAGE, RR.READING_PREV_DATE, RR.READING_DATE,\n"
        f"  SS1.NISS AS NISS_AT_READING, BS1.ID_BILLING_SERVICE AS NEXT_BILLING_SERVICE\n"
        f"FROM {anomalous_tbl} AN\n"
        f"JOIN {BILLING_SERVICE_TABLE} BS ON BS.ID_BILLING_SERVICE = AN.ID_BILLING_SERVICE\n"
        f"JOIN {CONTRACTED_SERVICE_TABLE} CS ON CS.ID_CONTRACTED_SERVICE = BS.ID_CONTRACTED_SERVICE\n"
        f"JOIN {SECTOR_SUPPLY_TABLE} SS ON SS.ID_SECTOR_SUPPLY = CS.ID_SECTOR_SUPPLY\n"
        f"LEFT JOIN {PAYMENT_FORM_TABLE} PF ON PF.ID_PAYMENT_FORM = CS.ID_PAYMENT_FORM\n"
        f"LEFT JOIN {offered_service_tbl} OS ON OS.ID_OFFERED_SERVICE = CS.ID_OFFERED_SERVICE\n"
        f"LEFT JOIN {item_tbl} ITB ON ITB.ID_ITEM_TO_BILL = AN.ID_ITEM_TO_BILL\n"
        f"LEFT JOIN {ri_tbl} RIT ON RIT.ID_ITEM_TO_BILL = ITB.ID_ITEM_TO_BILL\n"
        f"LEFT JOIN {reading_tbl} RR ON RR.ID_READING = RIT.ID_READING\n"
        f"LEFT JOIN {SECTOR_SUPPLY_TABLE} SS1 ON SS1.ID_SECTOR_SUPPLY = RR.ID_SECTOR_SUPPLY\n"
        f"LEFT JOIN {CONTRACTED_SERVICE_TABLE} CS1 ON CS1.ID_SECTOR_SUPPLY = RR.ID_SECTOR_SUPPLY "
        f"AND RR.READING_DATE = CS1.END_DATE\n"
        f"LEFT JOIN {BILLING_SERVICE_TABLE} BS1 ON BS1.ID_CONTRACTED_SERVICE = CS1.ID_CONTRACTED_SERVICE\n"
        f"WHERE AN.LAST_BILLING_DATE > AN.BILLING_DATE\n"
        f"  AND AN.{ANOMALOUS_STATUS_COLUMN} NOT IN ({status_list})\n"
        f"ORDER BY CS.ID_OFFERED_SERVICE, AN.{ANOMALOUS_ID_COLUMN};"
    )


@dataclass
class CorrectionScript:
    """Result of build_correction_script - a single UPDATE statement over
    an analyst-picked IN(...) list of ID_ANOMALOUS values, same one-
    statement shape as bill_issuance_validator.ReleaseNoticeScript."""
    sql_text: str
    anomaly_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)


def build_correction_script(
    anomaly_ids: Iterable[Any],
    *,
    program: str = CORRECTION_SCRIPT_DEFAULT_PROGRAM,
    user: str = CORRECTION_SCRIPT_DEFAULT_USER,
    clean: bool = False,
) -> CorrectionScript:
    """
    RJ's own correction script (see module docstring), verbatim - cancels
    (ANOMALOUS_STATUS_CANCELLED = ESTAN00005) every selected, still-open
    (ANOMALOUS_CORRECTABLE_STATUSES) anomaly in ONE batched UPDATE.

    anomaly_ids is the caller's OWN selected id list (whatever the analyst
    checked on the detect list), not something this function re-derives -
    RJ, same message: "can generate script base on the selected anomalies
    from the list only". Duplicates and None entries are dropped (order-
    preserving); an empty list produces a warning and a "nothing to
    correct" placeholder script instead of a syntactically invalid empty
    IN() clause - same guard bill_issuance_validator.build_release_notice_
    script uses for the same situation.
    """
    seen_ids: list[Any] = []
    seen_set: set[Any] = set()
    for aid in anomaly_ids:
        if aid is None or aid in seen_set:
            continue
        seen_set.add(aid)
        seen_ids.append(aid)

    anomalous_tbl = _qualified(ADMIN_SCHEMA, ANOMALOUS_TABLE)
    warnings: list[str] = []

    header = [
        "-- Incorrect Billing Period correction script - generated by ScriptGen",
        f"-- Generated (UTC): {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
        f"-- Anomalies selected: {len(seen_ids)}",
        f"-- Program: {program}",
        f"-- User: {user}",
        "-- Cancels each selected GCCOM_ANOMALOUS record (ANOMALOUS_STATUS ->",
        "-- ESTAN00005) - only touches rows still ESTAN00001/ESTAN00009, so",
        "-- re-running this script against an already-cancelled anomaly is a",
        "-- safe no-op.",
    ]

    if not seen_ids:
        warnings.append("No anomalies were selected - nothing to correct.")
        header.append(f"-- WARNING: {warnings[0]}")
        header.append("-- Statements: 0")
        header.append("")
        sql_text = "\n".join(header) + "-- Nothing to correct.\n"
        if clean:
            sql_text = _strip_sql_comments(sql_text)
        return CorrectionScript(sql_text=sql_text, anomaly_count=0, warnings=warnings)

    id_list = ", ".join(format_sql_literal(i) for i in seen_ids)
    correctable_list = ", ".join(format_sql_literal(s) for s in ANOMALOUS_CORRECTABLE_STATUSES)
    set_clause = (
        f"{quote_ident('UPDATE_DATE')} = GETDATE(),\n"
        f"    {quote_ident('UPDATE_USER')} = {format_sql_literal(user)},\n"
        f"    {quote_ident('UPDATE_PROGRAM')} = {format_sql_literal(program)},\n"
        f"    {quote_ident(ANOMALOUS_STATUS_COLUMN)} = {format_sql_literal(ANOMALOUS_STATUS_CANCELLED)}"
    )
    stmt = (
        f"UPDATE {anomalous_tbl}\n"
        f"SET {set_clause}\n"
        f"WHERE {quote_ident(ANOMALOUS_ID_COLUMN)} IN ({id_list})\n"
        f"  AND {quote_ident(ANOMALOUS_STATUS_COLUMN)} IN ({correctable_list});"
    )

    header.append("-- Statements: 1")
    header.append("")
    sql_text = "\n".join(header) + stmt + "\n\n-- Review the statement above before running it.\n"
    if clean:
        sql_text = _strip_sql_comments(sql_text)
    return CorrectionScript(sql_text=sql_text, anomaly_count=len(seen_ids), warnings=warnings)


def _strip_sql_comments(sql_text: str) -> str:
    """Same "clean script" behavior as every other build_*_script in this
    app - drops full-line `--` comments, keeps the actual statements."""
    lines = [ln for ln in sql_text.splitlines() if not ln.strip().startswith("--")]
    text = "\n".join(lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip() + "\n"
