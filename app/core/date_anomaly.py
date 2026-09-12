"""
"Diff Date System" anomaly detection + correction script builder.

Background (from the functional spec this was built against): after a
date-handling system change, some readings for a sector supply (NISS)
got stuck with the wrong READING_PREV_DATE - they sit in
GCGT_RE_READING with READ_STATUS = '6000STSRED' (not yet billed /
anomalous) instead of the normal billed status '7000STSRED'. The fix
is: find the reading_date of the most recent reading that WAS billed
correctly for that NISS (above the same billing-period floor), and
reset READING_PREV_DATE on every anomalous reading to that one date -
then cascade the same date into the two downstream tables that were
built from the bad reading: GCCOM_ITEMS_TO_BILL.INI_DATE and the
initDate/readingFromDate nodes inside GCCOM_ITEMS_TO_BILL_XML.XML_TO_BILL.
A fourth step cancels whatever GCCOM_ANOMALOUS records the bad reading
originally raised, since the correction resolves what caused them (see
the "Part 4" section of build_correction_script's docstring below).

Deliberately pure logic, like the rest of app/core: this module only
builds SELECT query text and, given already-fetched rows (the caller -
app/ui/main_window.py - does the actual DB round-trips via app/db/mssql
and passes plain dicts/values back in), builds the reviewable UPDATE
script text. It never executes anything itself, same as
script_generator.py.

Known assumption, called out because it couldn't be verified against a
live schema in this round (the connection tunnel only exists on the
user's own machine): GCCOM_ITEMS_TO_BILL_XML.ID_XML is assumed to be
the SAME value as GCCOM_ITEMS_TO_BILL.ID_ITEM_TO_BILL (a 1:1 "XML
extension" table keyed by the same id) - the functional spec's example
query filtered ID_XML directly by a value that was already known to be
an item-to-bill id, with no join shown. If that turns out to be wrong
when run for real, the "Resolve Bill Links" step in the UI will come
back with zero XML rows for a real item-to-bill id, which is the
signal to fix XML_ID_COLUMN_MATCHES_ITEM_ID below (or wire in the
correct join) rather than silently generating a script against the
wrong rows.
"""
from __future__ import annotations

import datetime
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .sql_format import format_sql_literal, quote_ident
from .script_generator import DEFAULT_AUDIT_PROGRAM, DEFAULT_AUDIT_USER

# --- Fixed business constants (from the functional spec) -----------------
READ_STATUS_ANOMALY = "6000STSRED"   # unbilled / stuck reading
READ_STATUS_BILLED = "7000STSRED"    # correctly billed reading
READING_TYPE_EXCLUDED = "TIPTL00004"

READING_SCHEMA = "OUC_COMMON_ADMIN"
READING_TABLE = "GCGT_RE_READING"
SECTOR_SUPPLY_TABLE = "GCCOM_SECTOR_SUPPLY"  # left unqualified, matching the spec's own query - see module docstring

ADMIN_SCHEMA = "OUC_ADMIN"
READINGS_ITEMSTOBILL_TABLE = "GCCOM_READINGS_ITEMSTOBILL"
ITEMS_TO_BILL_TABLE = "GCCOM_ITEMS_TO_BILL"
ITEMS_TO_BILL_XML_TABLE = "GCCOM_ITEMS_TO_BILL_XML"
ANOMALOUS_TABLE = "GCCOM_ANOMALOUS"

# See the module docstring's "Known assumption" paragraph.
XML_ID_COLUMN_MATCHES_ITEM_ID = True

READING_PREV_DATE_COLUMN = "READING_PREV_DATE"
# NOT "INIT_DATE" - confirmed against the real schema, no T. Easy to get
# wrong since the concept is "init(ial) date" - if a future round sees
# an "Invalid column name" error on this UPDATE, double check this
# constant before assuming something else broke.
INI_DATE_COLUMN = "INI_DATE"
XML_TO_BILL_COLUMN = "XML_TO_BILL"

# initDate/readingFromDate is the pair the spec named explicitly; kept as a
# tuple (not hardcoded inline) so a future round can extend it without
# touching patch_xml_dates' logic.
XML_DATE_NODE_NAMES = ("initDate", "readingFromDate")

# --- GCCOM_ANOMALOUS cancellation (Part 4) --------------------------------
# Open/active anomaly statuses that a date-anomaly correction should
# cancel, since fixing the reading/item-to-bill/XML dates resolves
# whatever originally raised these. ESTAN00005 is "cancelled" - not
# "corrected"/"resolved", per the functional spec's own wording ("by
# setting status to cancelled = ESTAN00005"), so that's what's applied,
# not some other terminal status that might also exist on this table.
# NOT "STATUS" - confirmed against the real schema: GCCOM_ANOMALOUS has no
# plain STATUS column, only ANOMALOUS_STATUS. Found the hard way (a real
# "Invalid column name 'STATUS'" error from Resolve Bill Links) since this
# was never actually exercised against the live DB before that point -
# don't re-guess "STATUS" here if this ever needs touching again.
ANOMALOUS_STATUS_COLUMN = "ANOMALOUS_STATUS"
ANOMALOUS_OPEN_STATUSES = ("ESTAN00009", "ESTAN00001")
ANOMALOUS_STATUS_CANCELLED = "ESTAN00005"

# --- GCCOM_ITEMS_TO_BILL.STATUS transition (Part 3) -----------------------
# Once the reading/INI_DATE correction is applied, the item-to-bill row
# itself should advance from "pending" (STTOBILL00) to STTOBILL01. Only
# rows still sitting at STTOBILL00 are touched - an item that has already
# progressed further in its billing lifecycle is left alone, same
# "SELECT-filtered lookup" safety pattern as GCCOM_ANOMALOUS above. These
# two codes are the analyst's own naming (no functional-spec label was
# given for them, unlike the ESTAN* codes), kept as neutral constants so
# a future round can rename what they mean without touching the query.
ITEM_STATUS_COLUMN = "STATUS"
ITEM_STATUS_FROM = "STTOBILL00"
ITEM_STATUS_TO = "STTOBILL01"

# --- GCCOM_ANOMALOUS "detect all" (bulk cleanup, not tied to one NISS) ----
# GCCOM_ANOMALOUS holds anomalies of more than one kind - ID_PRINCIPAL_
# ANOMALOUS_TYPE_IDS scopes a system-wide scan to just the Diff Date System
# kind this tool handles, so "detect all" doesn't surface unrelated
# anomaly types. The column's description lives in a lookup table:
#   SELECT * FROM GCCOM_BILL_ANOMALY_COMPANY WHERE ID_BILL_ANOM_COMP IN (...)
# and ANOMALOUS_OPEN_STATUSES' own description lives in another:
#   SELECT * FROM GCCOM_ANOMALOUS_STATUS WHERE COD_DEVELOP IN (...)
# (neither lookup table is queried by this tool itself - purely reference
# info the analyst can check manually against the real schema).
#
# UNVERIFIED SPELLING: the analyst supplied this as "id_principal_anomlay"
# (a plain-language description, not copy-pasted from a schema browser) -
# "ID_PRINCIPAL_ANOMALY" is this module's best-guess correction. The
# column name itself has since been exercised for real (the analyst sent
# back a corrected, working query using it - see the billing-service
# enrichment block below for the other corrections that came out of that
# same round), so it's no longer a pure guess, just not independently
# re-verified beyond that one query.
ANOMALOUS_TYPE_COLUMN = "ID_PRINCIPAL_ANOMALY"
# CORRECTED by the analyst directly (was originally guessed as
# (10000000026, 201) from the earlier "GCCOM_BILL_ANOMALY_COMPANY WHERE
# ID_BILL_ANOM_COMP IN (10000000026,201)" example - the real Diff Date
# System type ids are (202, 201), not 10000000026).
ANOMALOUS_DIFF_DATE_TYPE_IDS = (202, 201)

# Two lookup tables give human-readable text for the codes above, so
# "detect all" can show a description instead of a bare code:
#   GCCOM_ANOMALOUS_STATUS.NAME_TYPE, keyed by COD_DEVELOP, describes
#   ANOMALOUS_STATUS_COLUMN's ESTAN* codes.
#   GCCOM_BILL_ANOMALY_COMPANY.DESCRIPTION (plus its own short code,
#   ANOMALY_COD), keyed by ID_BILL_ANOM_COMP, describes
#   ANOMALOUS_TYPE_COLUMN's type ids.
# CORRECTED by the analyst directly (pasted a working query back) after
# an earlier round's first guess - ANOMALOUS_STATUS_DESC_COLUMN was
# originally guessed as "DESCRIPTION" too, which was wrong; NAME_TYPE is
# the real column. GCCOM_BILL_ANOMALY_COMPANY.DESCRIPTION was confirmed
# correct (unchanged from the earlier guess).
ANOMALOUS_STATUS_LOOKUP_TABLE = "GCCOM_ANOMALOUS_STATUS"
ANOMALOUS_STATUS_LOOKUP_KEY_COLUMN = "COD_DEVELOP"
ANOMALOUS_STATUS_DESC_COLUMN = "NAME_TYPE"

ANOMALOUS_TYPE_LOOKUP_TABLE = "GCCOM_BILL_ANOMALY_COMPANY"
ANOMALOUS_TYPE_LOOKUP_KEY_COLUMN = "ID_BILL_ANOM_COMP"
ANOMALOUS_TYPE_DESC_COLUMN = "DESCRIPTION"
ANOMALOUS_TYPE_CODE_COLUMN = "ANOMALY_COD"

# --- "Detect all" billing-service enrichment (account/supply/service) ----
# Analyst-supplied join chain (own words, own join shape), reformatted to
# this module's usual UPPERCASE identifier style - SQL Server identifiers
# are case-insensitive by default (see schema_check.py's own comment on
# this) so this is purely a style match, not a functional change. Left
# UNQUALIFIED (no ADMIN_SCHEMA prefix), matching how SECTOR_SUPPLY_TABLE
# above is already handled, since none of these were schema-qualified in
# the analyst's own tested query either. Original query, for reference:
#   SELECT pf.reference AS account, ss.niss AS supply,
#          cs.ID_OFFERED_SERVICE, cs.status
#   FROM gccom_billing_service bs
#   JOIN GCCOM_CONTRACTED_SERVICE cs ON cs.ID_CONTRACTED_SERVICE = bs.id_contracted_service
#   JOIN gccom_payment_form pf ON pf.id_payment_form = cs.ID_PAYMENT_FORM
#   JOIN GCCOM_SECTOR_SUPPLY ss ON ss.ID_SECTOR_SUPPLY = cs.ID_SECTOR_SUPPLY
#   WHERE id_billing_service = :ID_ITEM_TO_BILL
# Purely for enrichment context on the "detect all" results table (which
# account/supply/offered-service/contract this item-to-bill row belongs
# to) - LEFT JOINed, not INNER, so an anomaly whose billing-service chain
# doesn't resolve still surfaces rather than silently disappearing.
# GCCOM_SECTOR_SUPPLY itself reuses SECTOR_SUPPLY_TABLE above (same table
# the single-NISS queries already join against) rather than a second
# constant for the same name.
# CORRECTED by the analyst directly: the join from GCCOM_BILLING_SERVICE
# back to GCCOM_ANOMALOUS was originally guessed as
# "BS.ID_BILLING_SERVICE = GA.ID_ITEM_TO_BILL" (assuming the ":ID_ITEM_TO_
# BILL" parameter name in the analyst's own example query meant the join
# target was literally ID_ITEM_TO_BILL) - wrong. GCCOM_ANOMALOUS has its
# OWN ID_BILLING_SERVICE column (ANOMALOUS_BILLING_SERVICE_COLUMN below),
# separate from ID_ITEM_TO_BILL, and that's the real join key. Everything
# else in this chain (REFERENCE/NISS/ID_OFFERED_SERVICE/STATUS and every
# other FK column name) was confirmed unchanged.
BILLING_SERVICE_TABLE = "GCCOM_BILLING_SERVICE"
BILLING_SERVICE_ID_COLUMN = "ID_BILLING_SERVICE"
BILLING_SERVICE_CONTRACTED_SERVICE_FK = "ID_CONTRACTED_SERVICE"
CONTRACTED_SERVICE_TABLE = "GCCOM_CONTRACTED_SERVICE"
CONTRACTED_SERVICE_ID_COLUMN = "ID_CONTRACTED_SERVICE"
CONTRACTED_SERVICE_PAYMENT_FORM_FK = "ID_PAYMENT_FORM"
CONTRACTED_SERVICE_SECTOR_SUPPLY_FK = "ID_SECTOR_SUPPLY"
CONTRACTED_SERVICE_OFFERED_SERVICE_COLUMN = "ID_OFFERED_SERVICE"
CONTRACTED_SERVICE_STATUS_COLUMN = "STATUS"
# GCCOM_ANOMALOUS's own FK into GCCOM_BILLING_SERVICE - NOT the same
# column as ID_ITEM_TO_BILL, see the correction note above.
ANOMALOUS_BILLING_SERVICE_COLUMN = "ID_BILLING_SERVICE"
# Analyst-confirmed ordering for "detect all" - newest/oldest by when the
# anomaly was actually detected, not by item-to-bill id.
ANOMALOUS_DETECTION_DATE_COLUMN = "DETECTION_DATE"
PAYMENT_FORM_TABLE = "GCCOM_PAYMENT_FORM"
PAYMENT_FORM_ID_COLUMN = "ID_PAYMENT_FORM"
PAYMENT_FORM_REFERENCE_COLUMN = "REFERENCE"
SECTOR_SUPPLY_NISS_COLUMN = "NISS"

# --- "Detect all" reading-cycle check (ALL_CYCLE column) -----------------
# Analyst-supplied check, own words: "1 more column telling if all are
# cycle or not... select 1 from GCGT_RE_READING where reading_type not in
# ('TIPTL00003', 'TIPTL00005') and id_reading =:id_reading". Read as: a
# reading whose READING_TYPE is OUTSIDE this pair is a non-cycle reading;
# an item-to-bill is "all cycle" only if NONE of its readings are non-
# cycle. GCCOM_ANOMALOUS has no ID_READING column of its own (see the
# ANOMALOUS_BILLING_SERVICE_COLUMN note above - it's scoped by billing
# service/item-to-bill, not by reading), so the bridge from an anomaly row
# to its reading(s) is the SAME table this module already uses for that
# purpose elsewhere: READINGS_ITEMSTOBILL_TABLE (GCCOM_READINGS_ITEMSTOBILL,
# ID_READING -> ID_ITEM_TO_BILL, one reading to possibly-many items - see
# build_item_to_bill_query). UNVERIFIED: the analyst supplied the cycle-
# type codes and the exists-check shape directly, but this module's own
# choice to bridge via GCCOM_READINGS_ITEMSTOBILL (rather than some other
# path from ID_ITEM_TO_BILL to ID_READING) has not been confirmed against
# the live schema - if "detect all" errors on this column or ALL_CYCLE
# looks wrong for a row you can check by hand, this bridge is the first
# thing to re-examine.
CYCLE_READING_TYPES = ("TIPTL00003", "TIPTL00005")
READING_TYPE_COLUMN = "READING_TYPE"

# --- "Detect all" multi-billing-period flag (BILLING_PERIOD_COUNT column) --
# Analyst request, own words: "make the rows orange in case more than 1
# billing period of re_reading is detected for the NISS" - read as: among
# the reading(s) linked to an anomaly row, how many DISTINCT ID_BILLING_
# PERIOD values show up. More than one suggests the anomaly spans more
# than one billing cycle, which is worth an analyst's extra attention
# before generating a correction (the UI colors the row orange for this -
# see app.js). Same bridge as ALL_CYCLE above (GCCOM_ANOMALOUS has no
# ID_READING of its own, so this goes through READINGS_ITEMSTOBILL_TABLE
# the same way) - a correlated scalar subquery, not a JOIN, for the same
# row-multiplication reason ALL_CYCLE's own comment explains. Returns the
# actual count (not just a >1 bit) so the UI can show "3 billing periods"
# in a tooltip rather than a bare yes/no.
BILLING_PERIOD_COLUMN = "ID_BILLING_PERIOD"

# --- Non-cycle "orphan usage" reading cleanup (Part 6 of build_correction_ --
# script) -------------------------------------------------------------------
# Analyst-supplied rule, own words: among the anomalous readings already
# pulled by build_detect_query (which are all READ_STATUS 6000STSRED and
# READING_TYPE <> TIPTL00004 - see READ_STATUS_ANOMALY/READING_TYPE_EXCLUDED
# above), a reading whose READING_TYPE is TIPTL00011 needs its own extra
# cleanup beyond the usual Part 1-5 correction: the GCCOM_READINGS_
# ITEMSTOBILL row linking it to a bill gets DELETED outright (the one
# exception in this module to "cancel via status flip, never delete" - see
# the module docstring's opening paragraph and Part 5's ANOMALOUS_STATUS_
# CANCELLED comment for that usual convention), and the reading itself is
# reset back to READ_STATUS_RESET (1000STSRED - "not yet billed", the state
# a brand new reading starts in) with IND_USAGE_TO_CAL cleared, so the
# normal billing cycle can pick the reading back up and reprocess it from
# scratch rather than leaving it permanently stuck. Framed by the analyst
# as "readings that are not TIPTL00003/TIPTL00005" (the same non-cycle
# framing CYCLE_READING_TYPES/the ALL_CYCLE column already use) narrowed
# further to specifically TIPTL00011 - not every non-cycle reading type
# gets this, only that one. The READ_STATUS_ANOMALY check the caller also
# applies is redundant given build_detect_query's own WHERE clause (every
# row already satisfies it) - kept anyway as an explicit, defensive
# statement of the analyst's actual condition rather than relying on the
# caller to know it's implied. UNVERIFIED against the live schema beyond
# the analyst's own description (same caveat as every other UNVERIFIED
# note in this module).
READING_TYPE_ORPHAN_USAGE = "TIPTL00011"
READ_STATUS_COLUMN = "READ_STATUS"
READ_STATUS_RESET = "1000STSRED"
IND_USAGE_TO_CAL_COLUMN = "IND_USAGE_TO_CAL"

AUDIT_PROGRAM_COL = "update_program"
AUDIT_DATE_COL = "update_date"
AUDIT_USER_COL = "update_user"


def _qualified(schema: str, table: str) -> str:
    return f"{quote_ident(schema)}.{quote_ident(table)}" if schema else quote_ident(table)


def _row_col(row: dict, name: str) -> Any:
    """Case-insensitive dict lookup - same pattern as the identical helper
    duplicated in web/server.py (_da_col) and web/batch_jobs.py (_col),
    kept private/local here since this module otherwise never touches row
    dicts directly (every other function in this module works with plain
    ids, not rows - see lowest_billing_period_rows for the one exception,
    and why it needs this)."""
    for k, v in row.items():
        if k.lower() == name.lower():
            return v
    return None


def lowest_billing_period_rows(
    rows: Iterable[dict], billing_period_key: str = BILLING_PERIOD_COLUMN,
) -> list[dict]:
    """
    Analyst request, own words: "generate the script only for the lowest
    billing period id_reading in case there are multiple billing period" -
    given the anomaly reading rows already fetched by build_detect_query
    (each carrying ID_BILLING_PERIOD/ID_READING/etc, case-insensitive
    keys, same row shape web/server.py's DateAnomalyState.anomaly_rows and
    web/batch_jobs.py's per-NISS `rows` already use), returns only the
    rows belonging to the LOWEST distinct ID_BILLING_PERIOD value found
    among them.

    This is the "generate for lowest billing period only" option's whole
    implementation: the caller (web/server.py's date_anomaly_generate
    route, web/batch_jobs.py's _process_one_niss) runs this BEFORE
    deriving id_readings/orphan_usage_ids for build_correction_script, so
    every downstream part of the script (Parts 1-6) only ever touches the
    earliest billing period's reading(s) - not a separate filter bolted
    onto build_correction_script itself, since that function only ever
    sees bare ids, never full rows with a billing period on them.

    No-op (returns the input as a plain list, unchanged order) when rows
    is empty or every row already shares one billing period - the option
    only actually narrows anything when there's more than one distinct
    value to choose from, same condition the BILLING_PERIOD_COUNT column/
    orange-row highlighting already flags. Rows with a missing/None
    billing period are ignored when finding the minimum (can't compare)
    but still excluded from the result unless they happen to already be
    the single remaining group - consistent with "lowest KNOWN period
    only", not "lowest or unknown".
    """
    rows = list(rows)
    if not rows:
        return rows
    periods = [p for p in (_row_col(r, billing_period_key) for r in rows) if p is not None]
    if not periods:
        return rows
    lowest = min(periods)
    return [r for r in rows if _row_col(r, billing_period_key) == lowest]


# ---------------------------------------------------------------------
# Query builders - text only, run via app.db.mssql.run_query by the caller.
# Values are embedded through format_sql_literal (same helper the script
# generator uses) rather than raw string interpolation, so a NISS with a
# stray quote in it can't break out of the literal.
# ---------------------------------------------------------------------

def build_detect_query(niss: str, threshold: int, sector_supply_table: str = SECTOR_SUPPLY_TABLE) -> str:
    """
    The spec's query 1: every anomalous (READ_STATUS 6000, not a
    TIPTL00004 reading) reading for this NISS above the billing-period
    floor. Selects r.* only (not "r.ID_BILLING_PERIOD, r.*" like the
    spec's original SSMS query) purely to avoid a duplicate
    ID_BILLING_PERIOD column name in the result set - same columns
    either way, ID_BILLING_PERIOD is already in r.*.
    """
    reading_tbl = _qualified(READING_SCHEMA, READING_TABLE)
    return (
        f"SELECT r.*\n"
        f"FROM {reading_tbl} r\n"
        f"JOIN {sector_supply_table} ss ON ss.ID_SECTOR_SUPPLY = r.ID_SECTOR_SUPPLY\n"
        f"WHERE ss.NISS = {format_sql_literal(niss)}\n"
        f"  AND r.ID_BILLING_PERIOD > {format_sql_literal(threshold)}\n"
        f"  AND r.READING_TYPE <> {format_sql_literal(READING_TYPE_EXCLUDED)}\n"
        f"  AND r.READ_STATUS = {format_sql_literal(READ_STATUS_ANOMALY)}\n"
        f"ORDER BY r.ID_BILLING_PERIOD DESC;"
    )


def build_correct_date_query(niss: str, threshold: int, sector_supply_table: str = SECTOR_SUPPLY_TABLE) -> str:
    """
    The spec's query 2: the single reading_date to apply everywhere -
    the most recent correctly-billed (READ_STATUS 7000) reading for
    this NISS/threshold. ID_READING and ID_BILLING_PERIOD are pulled
    back alongside READING_DATE purely so the UI can show which reading
    the "correct" date actually came from, for review before generating
    anything.
    """
    reading_tbl = _qualified(READING_SCHEMA, READING_TABLE)
    return (
        f"SELECT TOP 1 r.ID_READING, r.ID_BILLING_PERIOD, r.READING_DATE\n"
        f"FROM {reading_tbl} r\n"
        f"JOIN {sector_supply_table} ss ON ss.ID_SECTOR_SUPPLY = r.ID_SECTOR_SUPPLY\n"
        f"WHERE ss.NISS = {format_sql_literal(niss)}\n"
        f"  AND r.ID_BILLING_PERIOD > {format_sql_literal(threshold)}\n"
        f"  AND r.READING_TYPE <> {format_sql_literal(READING_TYPE_EXCLUDED)}\n"
        f"  AND r.READ_STATUS = {format_sql_literal(READ_STATUS_BILLED)}\n"
        f"ORDER BY r.ID_BILLING_PERIOD DESC;"
    )


def build_item_to_bill_query(id_readings: Iterable[Any]) -> Optional[str]:
    """Maps each anomalous ID_READING to its ID_ITEM_TO_BILL via
    GCCOM_READINGS_ITEMSTOBILL. Returns None if id_readings is empty -
    nothing to look up."""
    ids = list(id_readings)
    if not ids:
        return None
    tbl = _qualified(ADMIN_SCHEMA, READINGS_ITEMSTOBILL_TABLE)
    id_list = ", ".join(format_sql_literal(i) for i in ids)
    return (
        f"SELECT GRI.ID_READING, GRI.ID_ITEM_TO_BILL\n"
        f"FROM {tbl} GRI\n"
        f"WHERE GRI.ID_READING IN ({id_list});"
    )


def build_xml_lookup_query(id_item_to_bills: Iterable[Any]) -> Optional[str]:
    """Pulls XML_TO_BILL for the item-to-bill ids found above. See the
    module docstring for the ID_XML == ID_ITEM_TO_BILL assumption this
    relies on."""
    ids = list(id_item_to_bills)
    if not ids:
        return None
    tbl = _qualified(ADMIN_SCHEMA, ITEMS_TO_BILL_XML_TABLE)
    id_list = ", ".join(format_sql_literal(i) for i in ids)
    return (
        f"SELECT GITBX.ID_XML, GITBX.{XML_TO_BILL_COLUMN}\n"
        f"FROM {tbl} GITBX\n"
        f"WHERE GITBX.ID_XML IN ({id_list});"
    )


def build_anomalous_query(id_item_to_bills: Iterable[Any]) -> Optional[str]:
    """
    Finds OPEN anomaly records (STATUS ESTAN00009 or ESTAN00001) in
    GCCOM_ANOMALOUS for the item-to-bill ids this correction touches -
    these get cancelled (Part 4 of build_correction_script) since fixing
    the reading/item-to-bill/XML dates resolves whatever raised them.
    Mirrors the functional spec's own example query (`select * from
    OUC_ADMIN.GCCOM_ANOMALOUS GA where ID_ITEM_TO_BILL in (...)`) plus
    the STATUS filter, so only rows actually eligible for cancellation
    come back - an item-to-bill with no anomaly record at all, or one
    already in some other/terminal status, simply won't appear here and
    won't get a Part 4 statement. Returns None if id_item_to_bills is
    empty.
    """
    ids = list(id_item_to_bills)
    if not ids:
        return None
    tbl = _qualified(ADMIN_SCHEMA, ANOMALOUS_TABLE)
    id_list = ", ".join(format_sql_literal(i) for i in ids)
    status_list = ", ".join(format_sql_literal(s) for s in ANOMALOUS_OPEN_STATUSES)
    return (
        f"SELECT GA.ID_ITEM_TO_BILL, GA.{ANOMALOUS_STATUS_COLUMN}\n"
        f"FROM {tbl} GA\n"
        f"WHERE GA.ID_ITEM_TO_BILL IN ({id_list})\n"
        f"  AND GA.{ANOMALOUS_STATUS_COLUMN} IN ({status_list});"
    )


def build_item_status_query(id_item_to_bills: Iterable[Any]) -> Optional[str]:
    """
    Mirrors build_anomalous_query: SELECTs only the item-to-bill rows
    that are still sitting at ITEM_STATUS_FROM (STTOBILL00), so the
    caller can build a Part 3 UPDATE per id that actually needs the
    STTOBILL00 -> STTOBILL01 transition, rather than blindly updating
    every id and risking an accidental overwrite of a row that has
    already moved further along in its billing lifecycle.
    """
    ids = list(id_item_to_bills)
    if not ids:
        return None
    tbl = _qualified(ADMIN_SCHEMA, ITEMS_TO_BILL_TABLE)
    id_list = ", ".join(format_sql_literal(i) for i in ids)
    return (
        f"SELECT GITB.ID_ITEM_TO_BILL, GITB.{ITEM_STATUS_COLUMN}\n"
        f"FROM {tbl} GITB\n"
        f"WHERE GITB.ID_ITEM_TO_BILL IN ({id_list})\n"
        f"  AND GITB.{ITEM_STATUS_COLUMN} = {format_sql_literal(ITEM_STATUS_FROM)};"
    )


def build_niss_account_query(niss: str, sector_supply_table: str = SECTOR_SUPPLY_TABLE) -> str:
    """
    Account/offered-service/contract-status lookup for ONE NISS, used to
    enrich the Single NISS tab's "Analyze with AI" context (see web/
    server.py's date_anomaly_explain_ai route) - the AI second opinion was
    getting only counts and no account/contract context at all, unlike the
    "Detect All" AI explain which already has it via a different query
    path. This is deliberately a SEPARATE query from build_detect_query,
    not a JOIN fused into it: build_detect_query is a one-row-per-reading
    scan that intentionally returns MANY rows for a NISS, while a sector
    supply can plausibly have more than one GCCOM_CONTRACTED_SERVICE row
    (historical/inactive contracts, multiple offered services) - fusing
    this chain into build_detect_query would risk silently multiplying
    every reading row by however many contracted-service rows match,
    which build_detect_query's own callers (build_correction_script's
    Part 1, one UPDATE per anomalous ID_READING) are not written to
    expect. TOP 1 caps it to a single row for the same reason - same
    defensive shape as build_correct_date_query's own TOP 1 above.

    Reuses the same CONTRACTED_SERVICE/PAYMENT_FORM columns the "Detect
    All" enrichment already established (see BILLING_SERVICE_TABLE's own
    comment block for the analyst-supplied original and its "unverified
    against the live schema" caveat), but anchored from GCCOM_SECTOR_
    SUPPLY.NISS directly via CONTRACTED_SERVICE_SECTOR_SUPPLY_FK, instead
    of from GCCOM_ANOMALOUS.ID_BILLING_SERVICE - the single-NISS flow has
    no GCCOM_ANOMALOUS row to anchor from (it queries GCGT_RE_READING
    directly), only a NISS string, so this joins the other direction:
    SECTOR_SUPPLY -> CONTRACTED_SERVICE -> PAYMENT_FORM. GCCOM_BILLING_
    SERVICE itself is not needed on this path (it only exists to bridge
    FROM an anomaly row TO a contracted service; here the contracted
    service is already reached directly from the sector supply).
    """
    return (
        f"SELECT TOP 1 PF.{PAYMENT_FORM_REFERENCE_COLUMN} AS ACCOUNT, "
        f"CS.{CONTRACTED_SERVICE_OFFERED_SERVICE_COLUMN} AS OFFERED_SERVICE, "
        f"CS.{CONTRACTED_SERVICE_STATUS_COLUMN} AS CONTRACT_STATUS\n"
        f"FROM {sector_supply_table} SS\n"
        f"JOIN {CONTRACTED_SERVICE_TABLE} CS ON CS.{CONTRACTED_SERVICE_SECTOR_SUPPLY_FK} = SS.ID_SECTOR_SUPPLY\n"
        f"LEFT JOIN {PAYMENT_FORM_TABLE} PF ON PF.{PAYMENT_FORM_ID_COLUMN} = CS.{CONTRACTED_SERVICE_PAYMENT_FORM_FK}\n"
        f"WHERE SS.{SECTOR_SUPPLY_NISS_COLUMN} = {format_sql_literal(niss)};"
    )


DETECT_ALL_DEFAULT_LIMIT = 1000


def build_detect_all_anomalies_query(
    type_ids: Iterable[Any] = ANOMALOUS_DIFF_DATE_TYPE_IDS,
    open_statuses: Iterable[str] = ANOMALOUS_OPEN_STATUSES,
    limit: int | None = DETECT_ALL_DEFAULT_LIMIT,
) -> str:
    """
    System-wide "detect all" scan of GCCOM_ANOMALOUS - unlike every other
    query builder in this module, this one does NOT take a NISS: it finds
    every OPEN Diff Date System anomaly across the whole database in one
    shot, for an analyst who wants to discover pending cases rather than
    already knowing which NISS to investigate.

    LEFT JOINs to GCCOM_ITEMS_TO_BILL so the caller gets that row's
    current STATUS in the same round-trip (needed to know whether Part 3's
    status-advance applies to a given item) without a second query -
    LEFT, not INNER, so an anomaly whose item-to-bill row is somehow
    missing/deleted still surfaces rather than silently vanishing from the
    list (ITEM_STATUS comes back NULL for that row instead). Two more LEFT
    JOINs pull in the human-readable status/type descriptions (see the
    ANOMALOUS_STATUS_LOOKUP_TABLE / ANOMALOUS_TYPE_LOOKUP_TABLE constants)
    so the UI can show text instead of raw ESTAN*/type-id codes - LEFT
    again, so a code with no matching lookup row still surfaces the raw
    code rather than disappearing. A final chain of LEFT JOINs
    (BILLING_SERVICE_TABLE -> CONTRACTED_SERVICE_TABLE ->
    PAYMENT_FORM_TABLE / SECTOR_SUPPLY_TABLE) adds account/supply/offered-
    service/contract-status context, the analyst's own supplied query -
    see that constant block's comment for the original SQL and the
    "unverified against the live schema" caveat.

    Also adds an ALL_CYCLE bit column via a correlated EXISTS: 1 if every
    reading linked to the row's ID_ITEM_TO_BILL (via
    READINGS_ITEMSTOBILL_TABLE) has a READING_TYPE in CYCLE_READING_TYPES,
    0 if at least one linked reading doesn't. See CYCLE_READING_TYPES'
    own comment for the analyst-supplied check this mirrors and the
    bridging assumption behind it (GCCOM_ANOMALOUS has no ID_READING of
    its own, so the reading link goes through GCCOM_READINGS_ITEMSTOBILL,
    same as build_item_to_bill_query elsewhere in this module).

    And a BILLING_PERIOD_COUNT integer column via a correlated scalar
    subquery: COUNT(DISTINCT ID_BILLING_PERIOD) across the same linked
    readings - more than 1 means this anomaly's readings span more than
    one billing period, which the UI flags for extra scrutiny. See
    BILLING_PERIOD_COLUMN's own comment.

    `limit` adds a `TOP (N)` cap (SQL Server has no LIMIT keyword) - unlike
    every other query in this module, this one has no NISS/id-list scoping
    it down, so on a large production database it could otherwise return an
    unbounded number of rows in one shot. Pass None to disable the cap
    entirely (e.g. a future "load more" flow). The caller is expected to
    request `limit + 1` rows-worth of awareness by comparing the returned
    row count to `limit` itself (see web/server.py's detect-all route) to
    know whether the result was actually truncated - this function only
    builds the SQL, it doesn't know how many rows came back.

    Returns a single, argument-less-by-default query string (not
    Optional[str] like the id-list-based builders - there's no "empty
    input" case here, it always runs). type_ids/open_statuses are
    parameters (not hardcoded) purely so a test can exercise the filter
    logic without depending on the real constants' values.
    """
    anomalous_tbl = _qualified(ADMIN_SCHEMA, ANOMALOUS_TABLE)
    item_tbl = _qualified(ADMIN_SCHEMA, ITEMS_TO_BILL_TABLE)
    reading_tbl = _qualified(READING_SCHEMA, READING_TABLE)
    ri_tbl = _qualified(ADMIN_SCHEMA, READINGS_ITEMSTOBILL_TABLE)
    type_list = ", ".join(format_sql_literal(t) for t in type_ids)
    status_list = ", ".join(format_sql_literal(s) for s in open_statuses)
    cycle_type_list = ", ".join(format_sql_literal(t) for t in CYCLE_READING_TYPES)
    top_clause = f"TOP ({int(limit)}) " if limit else ""
    # Correlated EXISTS, not a JOIN - a JOIN against GCCOM_READINGS_
    # ITEMSTOBILL/GCGT_RE_READING here would multiply each anomaly row by
    # however many readings it has (breaking the "one row per anomaly"
    # shape the rest of this query and its caller assume), and detect-all
    # only needs a yes/no per row, not the individual reading rows.
    all_cycle_expr = (
        f"CASE WHEN EXISTS (\n"
        f"    SELECT 1 FROM {ri_tbl} GRI\n"
        f"    JOIN {reading_tbl} GR ON GR.ID_READING = GRI.ID_READING\n"
        f"    WHERE GRI.ID_ITEM_TO_BILL = GA.ID_ITEM_TO_BILL\n"
        f"      AND GR.{READING_TYPE_COLUMN} NOT IN ({cycle_type_list})\n"
        f"  ) THEN 0 ELSE 1 END AS ALL_CYCLE"
    )
    # Scalar subquery (not EXISTS, not a JOIN) - needs an actual count, not
    # a yes/no, and a JOIN here would multiply each anomaly row the same
    # way ALL_CYCLE's own EXISTS avoids.
    billing_period_count_expr = (
        f"(SELECT COUNT(DISTINCT GR2.{BILLING_PERIOD_COLUMN}) FROM {ri_tbl} GRI2\n"
        f"    JOIN {reading_tbl} GR2 ON GR2.ID_READING = GRI2.ID_READING\n"
        f"    WHERE GRI2.ID_ITEM_TO_BILL = GA.ID_ITEM_TO_BILL) AS BILLING_PERIOD_COUNT"
    )
    return (
        f"SELECT {top_clause}GA.ID_ITEM_TO_BILL, GA.{ANOMALOUS_STATUS_COLUMN}, "
        f"GAS.{ANOMALOUS_STATUS_DESC_COLUMN} AS ANOMALOUS_STATUS_DESC, "
        f"GA.{ANOMALOUS_TYPE_COLUMN}, GBAC.{ANOMALOUS_TYPE_CODE_COLUMN} AS ANOMALOUS_TYPE_CODE, "
        f"GBAC.{ANOMALOUS_TYPE_DESC_COLUMN} AS ANOMALOUS_TYPE_DESC, "
        f"GITB.{ITEM_STATUS_COLUMN} AS ITEM_STATUS, "
        f"PF.{PAYMENT_FORM_REFERENCE_COLUMN} AS ACCOUNT, SS.{SECTOR_SUPPLY_NISS_COLUMN} AS SUPPLY, "
        f"CS.{CONTRACTED_SERVICE_OFFERED_SERVICE_COLUMN} AS OFFERED_SERVICE, "
        f"CS.{CONTRACTED_SERVICE_STATUS_COLUMN} AS CONTRACT_STATUS, "
        f"GA.{ANOMALOUS_DETECTION_DATE_COLUMN} AS DETECTION_DATE, "
        f"{all_cycle_expr},\n"
        f"{billing_period_count_expr}\n"
        f"FROM {anomalous_tbl} GA\n"
        f"LEFT JOIN {item_tbl} GITB ON GITB.ID_ITEM_TO_BILL = GA.ID_ITEM_TO_BILL\n"
        f"LEFT JOIN {ANOMALOUS_STATUS_LOOKUP_TABLE} GAS "
        f"ON GAS.{ANOMALOUS_STATUS_LOOKUP_KEY_COLUMN} = GA.{ANOMALOUS_STATUS_COLUMN}\n"
        f"LEFT JOIN {ANOMALOUS_TYPE_LOOKUP_TABLE} GBAC "
        f"ON GBAC.{ANOMALOUS_TYPE_LOOKUP_KEY_COLUMN} = GA.{ANOMALOUS_TYPE_COLUMN}\n"
        f"LEFT JOIN {BILLING_SERVICE_TABLE} BS "
        f"ON BS.{BILLING_SERVICE_ID_COLUMN} = GA.{ANOMALOUS_BILLING_SERVICE_COLUMN}\n"
        f"LEFT JOIN {CONTRACTED_SERVICE_TABLE} CS "
        f"ON CS.{CONTRACTED_SERVICE_ID_COLUMN} = BS.{BILLING_SERVICE_CONTRACTED_SERVICE_FK}\n"
        f"LEFT JOIN {PAYMENT_FORM_TABLE} PF "
        f"ON PF.{PAYMENT_FORM_ID_COLUMN} = CS.{CONTRACTED_SERVICE_PAYMENT_FORM_FK}\n"
        f"LEFT JOIN {SECTOR_SUPPLY_TABLE} SS "
        f"ON SS.ID_SECTOR_SUPPLY = CS.{CONTRACTED_SERVICE_SECTOR_SUPPLY_FK}\n"
        f"WHERE GA.{ANOMALOUS_TYPE_COLUMN} IN ({type_list})\n"
        f"  AND GA.{ANOMALOUS_STATUS_COLUMN} IN ({status_list})\n"
        f"ORDER BY GA.{ANOMALOUS_DETECTION_DATE_COLUMN};"
    )


# ---------------------------------------------------------------------
# XML patching - namespace-agnostic (matches by local tag name, so it
# works whether or not XML_TO_BILL declares a default namespace) rather
# than assuming a fixed XPath, since the real document shape hasn't
# been seen. Returns the whole XML re-serialized, which is what the
# generated UPDATE statement sets XML_TO_BILL to - safer than a T-SQL
# .modify() call built against a guessed path.
# ---------------------------------------------------------------------

_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?$")


def _local_name(tag: str) -> str:
    # ElementTree renders a namespaced tag as "{uri}localname" - strip
    # the "{uri}" part so matching works regardless of namespace.
    return tag.rsplit("}", 1)[-1]


def _format_like(original_text: str, correct_date: Any) -> str:
    """Renders correct_date in the same shape the node already had
    (date-only vs. full datetime, and the same T/space separator), so
    the rest of the document's date formatting convention is preserved.
    Falls back to a plain YYYY-MM-DD if the original text doesn't match
    either recognized shape."""
    original = (original_text or "").strip()
    if isinstance(correct_date, datetime.datetime):
        as_datetime = correct_date
    elif isinstance(correct_date, datetime.date):
        as_datetime = datetime.datetime.combine(correct_date, datetime.time())
    else:
        as_datetime = correct_date  # let strftime fail loudly if it's some other type

    if _DATETIME_RE.match(original):
        sep = "T" if "T" in original else " "
        return as_datetime.strftime(f"%Y-%m-%d{sep}%H:%M:%S")
    if _DATE_ONLY_RE.match(original):
        return as_datetime.strftime("%Y-%m-%d")
    return as_datetime.strftime("%Y-%m-%d")


def patch_xml_dates(
    xml_text: str, correct_date: Any, node_names: tuple[str, ...] = XML_DATE_NODE_NAMES
) -> tuple[str, list[str]]:
    """
    Parses xml_text, finds every element anywhere in the tree whose
    local name is in node_names, and replaces its text content with
    correct_date formatted to match that node's existing format.

    Returns (patched_xml_text, changed_node_names) - changed_node_names
    lists which of node_names were actually found/changed, so the
    caller can flag "expected initDate but found nothing" rather than
    silently emitting a no-op UPDATE.

    Raises ET.ParseError if xml_text isn't well-formed XML - the caller
    should catch this and surface it as a warning rather than crash,
    since a malformed value in a production table is exactly the kind
    of thing this tool exists to surface, not hide.
    """
    root = ET.fromstring(xml_text)
    changed: list[str] = []
    for elem in root.iter():
        if _local_name(elem.tag) in node_names:
            elem.text = _format_like(elem.text or "", correct_date)
            changed.append(_local_name(elem.tag))
    patched = ET.tostring(root, encoding="unicode")
    return patched, changed


# ---------------------------------------------------------------------
# Correction script assembly
# ---------------------------------------------------------------------

@dataclass
class CorrectionScript:
    sql_text: str
    reading_count: int
    item_count: int
    xml_count: int
    anomalous_count: int = 0
    item_status_count: int = 0
    orphan_reading_count: int = 0  # Part 6: non-cycle (TIPTL00011) readings DELETEd/reset - see READING_TYPE_ORPHAN_USAGE
    billing_period_count: int = 0  # distinct GCGT_RE_READING.ID_BILLING_PERIOD values among this NISS's anomalous readings
    scoped_to_lowest_period: bool = False  # True if the caller pre-filtered anomaly_id_readings to lowest_billing_period_rows() - see build_correction_script's own kwarg
    explanation: str = ""
    warnings: list[str] = field(default_factory=list)

    # Mirrors script_generator.GeneratedScript's field names so this can be
    # passed straight into MainWindow._record_script_history unchanged.
    @property
    def statement_count(self) -> int:
        return (
            self.reading_count
            + self.item_count
            + self.xml_count
            + self.anomalous_count
            + self.item_status_count
            # Part 6 emits 2 statements (DELETE + UPDATE) per orphan_reading_count>0 case,
            # not one per reading - see build_correction_script's Part 6 assembly.
            + (2 if self.orphan_reading_count else 0)
        )

    @property
    def warning_count(self) -> int:
        return len(self.warnings)


def _audit_set_fragment(program: str, user: str) -> str:
    return (
        f",\n    {quote_ident(AUDIT_PROGRAM_COL)} = {format_sql_literal(program)}"
        f",\n    {quote_ident(AUDIT_DATE_COL)} = GETDATE()"
        f",\n    {quote_ident(AUDIT_USER_COL)} = {format_sql_literal(user)}"
    )


def build_case_explanation(
    *,
    niss: str,
    threshold: Any,
    anomaly_count: int,
    correct_date: Any = None,
    correct_date_source_reading: Optional[Any] = None,
    item_count: Optional[int] = None,
    xml_count: Optional[int] = None,
    item_status_count: Optional[int] = None,
    anomalous_count: Optional[int] = None,
    orphan_reading_count: Optional[int] = None,
) -> str:
    """
    Plain-language "what's wrong / what's fixed" narrative for one NISS's
    Diff Date anomaly case. Built from whatever is known so far, so it
    reads sensibly at every stage of the Detect -> Resolve -> Generate
    workflow:
      - right after Detect: only niss/threshold/anomaly_count/correct_date
        are known - every *_count kwarg is left None, so the "what's
        fixed" half stays a general statement of intent rather than
        claiming specific counts that haven't been resolved yet.
      - after Resolve: item_count/xml_count/item_status_count/
        anomalous_count are the *candidate* counts found so far - passed
        in so the explanation can get specific before Generate runs.
      - after Generate: the same kwargs reflect the ACTUAL statement
        counts in the produced script (build_correction_script calls
        this itself with those final numbers - see there).

    Returned directly by the detect/resolve/generate API routes for the
    UI (as plain text, no SQL-comment prefixing), and also folded into
    the generated script's header comment block by build_correction_script
    so the script itself carries a human-readable summary of what it
    does and why - useful for a reviewer who wasn't the one who ran the
    analysis.
    """
    wrong = (
        f"What's wrong: {anomaly_count} meter reading(s) for NISS {niss} are stuck in the Diff Date "
        f"anomaly state (READ_STATUS {READ_STATUS_ANOMALY}) above billing period floor {threshold} - "
        "their billing dates don't line up with a correctly-billed reference reading, which blocks "
        "these readings (and whatever they roll up to) from being billed and keeps an open anomaly "
        "record against them."
    )

    if correct_date is None:
        fixed = (
            "What's fixed: nothing yet - no correctly-billed (READ_STATUS "
            f"{READ_STATUS_BILLED}) reading was found above this billing period floor to source a "
            "correction date from, so a script can't be generated until one is found (try a lower floor)."
        )
        return wrong + "\n\n" + fixed

    source_note = (
        f" (sourced from ID_READING {correct_date_source_reading})" if correct_date_source_reading is not None else ""
    )
    fixed_parts = [
        f"What's fixed: the correct date {correct_date}{source_note} is applied to "
        f"{READING_TABLE}.{READING_PREV_DATE_COLUMN} for each of the {anomaly_count} anomalous reading(s)"
    ]
    if item_count is not None:
        fixed_parts.append(
            f"the linked {ITEMS_TO_BILL_TABLE}.{INI_DATE_COLUMN} is corrected on {item_count} item-to-bill row(s)"
        )
    if item_status_count is not None:
        fixed_parts.append(
            f"{item_status_count} of those item(s) still pending at {ITEM_STATUS_FROM} "
            f"{'is' if item_status_count == 1 else 'are'} advanced to {ITEM_STATUS_TO} so billing can proceed"
        )
    if xml_count is not None:
        fixed_parts.append(
            f"the embedded {'/'.join(XML_DATE_NODE_NAMES)} in {xml_count} {ITEMS_TO_BILL_XML_TABLE} "
            "row(s) is patched to match"
        )
    if anomalous_count is not None:
        fixed_parts.append(
            f"{anomalous_count} now-resolved {ANOMALOUS_TABLE} record(s) "
            f"{'is' if anomalous_count == 1 else 'are'} cancelled ({ANOMALOUS_STATUS_COLUMN} = "
            f"{ANOMALOUS_STATUS_CANCELLED})"
        )
    if orphan_reading_count:
        fixed_parts.append(
            f"{orphan_reading_count} of those reading(s) are non-cycle ({READING_TYPE_COLUMN} "
            f"{READING_TYPE_ORPHAN_USAGE}) and also get their {READINGS_ITEMSTOBILL_TABLE} link "
            f"deleted and {READ_STATUS_COLUMN} reset to {READ_STATUS_RESET} ({IND_USAGE_TO_CAL_COLUMN} = 0) "
            "so the normal billing cycle can reprocess them"
        )

    fixed = "; ".join(fixed_parts) + "."
    return wrong + "\n\n" + fixed


def _strip_sql_comments(sql_text: str) -> str:
    """
    Post-processing step for the "clean script" option: drops every
    line that is purely a SQL line comment (starts with "--" once
    leading whitespace is trimmed) - the header block, every per-
    statement "-- Reading 101" / "-- Item to bill 500" / etc comment,
    the case explanation block, and the "review before running" footer
    note all go. What's left is just the UPDATE/DELETE statements
    themselves, untouched - this never rewrites or re-derives the SQL,
    just filters lines, so a clean script and its commented counterpart
    are always statement-for-statement identical. NOTE: there is
    deliberately no BEGIN TRANSACTION/COMMIT/ROLLBACK wrapper in this
    module's output (removed per explicit analyst request - each
    statement runs and commits on its own, autocommit-style; wrap it
    yourself if you want transactional all-or-nothing behavior).

    Deliberately line-based rather than a real SQL comment parser: this
    codebase never emits a "--" mid-line (comments are always their own
    line - see every f"-- ..." in build_correction_script/
    build_batch_script), so there's nothing more sophisticated to get
    right here, and a line-based filter can't misparse a "--" that
    happens to appear inside a string literal value.

    Also collapses the runs of blank lines that stripping comments
    tends to leave behind (a comment line sitting between two blank
    lines becomes two blank lines in a row) down to one, purely for
    readability - doesn't change what would execute either way.
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
    return "\n".join(collapsed).strip() + "\n"


def build_correction_script(
    *,
    niss: str,
    threshold: Any,
    correct_date: Any,
    correct_date_source_reading: Optional[Any],
    anomaly_id_readings: Iterable[Any],
    item_to_bill_map: dict[Any, list[Any]],
    xml_rows: dict[Any, str],
    anomalous_item_ids: Iterable[Any] = (),
    item_status_ids: Iterable[Any] = (),
    orphan_usage_id_readings: Iterable[Any] = (),
    billing_period_count: int = 0,
    scoped_to_lowest_period: bool = False,
    reading_has_audit_cols: bool = False,
    item_has_audit_cols: bool = False,
    program: str = DEFAULT_AUDIT_PROGRAM,
    user: str = DEFAULT_AUDIT_USER,
    clean: bool = False,
) -> CorrectionScript:
    """
    Builds the five-part correction script:
      1. GCGT_RE_READING.READING_PREV_DATE, one UPDATE per anomalous
         ID_READING - always generated, since every anomalous reading
         needs this fix regardless of whether it's linked to a bill yet.
      2. GCCOM_ITEMS_TO_BILL.INI_DATE, one UPDATE per DISTINCT
         ID_ITEM_TO_BILL found via item_to_bill_map (readings with no
         entry - or an empty list - in item_to_bill_map are skipped
         here and noted as a warning, not an error - not every
         anomalous reading is necessarily linked to a bill item yet).

    item_to_bill_map is ID_READING -> list[ID_ITEM_TO_BILL], NOT a
    single id: GCCOM_READINGS_ITEMSTOBILL is a one-reading-to-MANY-
    items relationship in practice (e.g. one meter reading can spawn
    separate energy/demand/power-factor billing items), so a single
    reading can legitimately need more than one INI_DATE/XML
    correction. An earlier version of this function took a single id
    per reading and silently dropped every item past the first one for
    a given reading - fixed after a real NISS came back with fewer
    items-to-bill than the analyst expected.
      3. GCCOM_ITEMS_TO_BILL.STATUS, one UPDATE per item-to-bill id in
         item_status_ids (already filtered by the caller's
         build_item_status_query to only rows still at ITEM_STATUS_FROM
         / STTOBILL00), setting STATUS = ITEM_STATUS_TO (STTOBILL01).
         Emitted as its OWN statement, separate from Part 2's INI_DATE
         UPDATE - not merged into one - so INI_DATE always gets fixed
         unconditionally (preserving existing behavior) while the
         STATUS transition only fires when the row is confirmed to
         still be pending, protecting against silently reverting an
         item that has already advanced further in its billing
         lifecycle. Restricted to ids also present in Part 2's
         seen_items, same defensive-scope reasoning as Part 5 below. An
         item-to-bill not still at STTOBILL00 simply gets no Part 3
         statement - that's the normal case, not a warning.
      4. GCCOM_ITEMS_TO_BILL_XML.XML_TO_BILL, one UPDATE per xml_rows
         entry, with initDate/readingFromDate patched via
         patch_xml_dates and the WHOLE resulting document written back
         (see that function's docstring for why, over a targeted
         .modify()).
      5. GCCOM_ANOMALOUS.STATUS, one UPDATE per item-to-bill id in
         anomalous_item_ids (already filtered by the caller's
         build_anomalous_query to only OPEN anomalies - STATUS
         ESTAN00009 or ESTAN00001), setting STATUS = ESTAN00005
         (cancelled). Restricted to ids also present in Part 2's
         seen_items - anomalous_item_ids should already only ever
         contain ids from the same item_to_bill_map, so this is a
         defensive scope, not a real-world filter. An item-to-bill with
         no open anomaly (none at all, or already in some other status)
         simply gets no Part 5 statement - that's the normal case, not
         a warning.
      6. Non-cycle "orphan usage" reading cleanup, for readings in
         orphan_usage_id_readings (READING_TYPE TIPTL00011 at READ_STATUS
         6000STSRED - see READING_TYPE_ORPHAN_USAGE's own comment block
         for the analyst's rule and why this is the one DELETE in this
         module): a single DELETE FROM GCCOM_READINGS_ITEMSTOBILL WHERE
         ID_READING IN (...), plus a single UPDATE GCGT_RE_READING SET
         READ_STATUS = '1000STSRED', IND_USAGE_TO_CAL = 0 WHERE ID_READING
         IN (...) - batched as one DELETE + one UPDATE covering every
         orphan id, not one statement per id like Parts 1-5, since these
         two statements don't need a distinct WHERE per row (no per-row
         date/value to embed). Empty (the common case) produces neither
         statement.

    anomaly_id_readings / item_to_bill_map / xml_rows / anomalous_item_ids /
    item_status_ids / orphan_usage_id_readings are plain data the caller
    already fetched from the DB (app/ui/main_window.py or web/server.py,
    via app/db/mssql.run_query) - this function does no DB access itself.

    billing_period_count is purely informational, passed straight through
    to CorrectionScript/the header comment/the case explanation - the
    caller computes it (distinct ID_BILLING_PERIOD values across the same
    anomaly rows already fetched for anomaly_id_readings) since this
    function only ever sees bare ids, not the full rows. More than 1
    means this NISS's anomalous readings span more than one billing
    period - the web UI colors the row(s) orange for this (see
    app.js's daRowClassForBillingPeriods()) as a flag worth the analyst's
    extra attention before generating, same idea as the ALL_CYCLE/
    BILLING_PERIOD_COUNT column on the Detect All page.

    scoped_to_lowest_period is purely informational for the header comment
    (analyst request: "generate the script only for the lowest billing
    period id_reading in case there are multiple billing period") - set it
    to True when the CALLER already pre-filtered anomaly_id_readings (and
    orphan_usage_id_readings) via lowest_billing_period_rows() before
    calling this function, so the header can say so explicitly rather than
    silently generating a smaller script with no explanation. This
    function does no filtering itself - by the time anomaly_id_readings
    arrives here it's just a flat list of ids with no billing period
    attached to filter by (see lowest_billing_period_rows' own docstring
    for why that has to happen one level up, against the full rows).

    clean=True strips every comment line (header, case explanation,
    per-statement labels, footer reminder) from the returned sql_text
    via _strip_sql_comments, leaving just the UPDATE/DELETE statements -
    for an analyst who wants to paste straight into a query tool without
    the annotations. CorrectionScript.explanation and .warnings are
    unaffected either way - clean only touches sql_text, so the UI's
    separate explanation panel keeps working the same. No BEGIN
    TRANSACTION/COMMIT/ROLLBACK wrapper either way - see _strip_sql_
    comments' own docstring for why that was removed.
    """
    id_readings = list(anomaly_id_readings)
    warnings: list[str] = []
    date_lit = format_sql_literal(correct_date)

    reading_tbl = _qualified(READING_SCHEMA, READING_TABLE)
    item_tbl = _qualified(ADMIN_SCHEMA, ITEMS_TO_BILL_TABLE)
    xml_tbl = _qualified(ADMIN_SCHEMA, ITEMS_TO_BILL_XML_TABLE)
    anomalous_tbl = _qualified(ADMIN_SCHEMA, ANOMALOUS_TABLE)

    # --- Part 1: readings -------------------------------------------------
    reading_stmts: list[str] = []
    for id_reading in id_readings:
        set_clause = f"{quote_ident(READING_PREV_DATE_COLUMN)} = {date_lit}"
        if reading_has_audit_cols:
            set_clause += _audit_set_fragment(program, user)
        reading_stmts.append(
            f"-- Reading {id_reading}\n"
            f"UPDATE {reading_tbl}\n"
            f"SET {set_clause}\n"
            f"WHERE ID_READING = {format_sql_literal(id_reading)};"
        )

    # --- Part 2: items to bill (deduped, order-preserving) -----------------
    # One reading can map to MULTIPLE items-to-bill (see docstring) - so
    # this collects every item id across every reading's list, not just
    # the first one.
    seen_items: list[Any] = []
    unmapped_readings: list[Any] = []
    for id_reading in id_readings:
        item_ids_for_reading = item_to_bill_map.get(id_reading) or []
        if not item_ids_for_reading:
            unmapped_readings.append(id_reading)
            continue
        for item_id in item_ids_for_reading:
            if item_id not in seen_items:
                seen_items.append(item_id)
    if unmapped_readings:
        warnings.append(
            f"{len(unmapped_readings)} anomalous reading(s) have no GCCOM_READINGS_ITEMSTOBILL entry "
            f"(no item-to-bill / XML correction generated for them): {unmapped_readings}"
        )

    item_stmts: list[str] = []
    for item_id in seen_items:
        set_clause = f"{quote_ident(INI_DATE_COLUMN)} = {date_lit}"
        if item_has_audit_cols:
            set_clause += _audit_set_fragment(program, user)
        item_stmts.append(
            f"-- Item to bill {item_id}\n"
            f"UPDATE {item_tbl}\n"
            f"SET {set_clause}\n"
            f"WHERE ID_ITEM_TO_BILL = {format_sql_literal(item_id)};"
        )

    # --- Part 3: item-to-bill STATUS transition (STTOBILL00 -> STTOBILL01) --
    # Restricted to seen_items (Part 2's ids) as a defensive scope - see
    # the docstring's Part 3 note. item_status_ids is expected to already
    # be a subset of/equal to seen_items in normal use.
    item_status_id_set = set(item_status_ids)
    item_status_stmts: list[str] = []
    if item_status_id_set:
        for item_id in seen_items:
            if item_id not in item_status_id_set:
                continue
            set_clause = f"{quote_ident(ITEM_STATUS_COLUMN)} = {format_sql_literal(ITEM_STATUS_TO)}"
            item_status_stmts.append(
                f"-- Advance status for item to bill {item_id}\n"
                f"UPDATE {item_tbl}\n"
                f"SET {set_clause}\n"
                f"WHERE ID_ITEM_TO_BILL = {format_sql_literal(item_id)}\n"
                f"  AND {quote_ident(ITEM_STATUS_COLUMN)} = {format_sql_literal(ITEM_STATUS_FROM)};"
            )

    # --- Part 4: XML -----------------------------------------------------
    xml_stmts: list[str] = []
    missing_xml = [item_id for item_id in seen_items if item_id not in xml_rows]
    if missing_xml:
        warnings.append(
            f"{len(missing_xml)} item-to-bill id(s) had no matching GCCOM_ITEMS_TO_BILL_XML row "
            f"(see the module docstring's ID_XML assumption if this is unexpected): {missing_xml}"
        )
    for id_xml, xml_text in xml_rows.items():
        try:
            patched, changed_nodes = patch_xml_dates(xml_text, correct_date)
        except ET.ParseError as exc:
            warnings.append(f"XML for ID_XML={id_xml} did not parse ({exc}) - skipped, needs manual review.")
            continue
        if not changed_nodes:
            warnings.append(
                f"No {'/'.join(XML_DATE_NODE_NAMES)} node found in XML for ID_XML={id_xml} - "
                f"nothing changed, needs manual review."
            )
            continue
        comment = f"-- XML {id_xml}: updated node(s): {', '.join(changed_nodes)}"
        xml_stmts.append(
            f"{comment}\n"
            f"UPDATE {xml_tbl}\n"
            f"SET {quote_ident(XML_TO_BILL_COLUMN)} = {format_sql_literal(patched)}\n"
            f"WHERE ID_XML = {format_sql_literal(id_xml)};"
        )

    # --- Part 5: cancel open GCCOM_ANOMALOUS records ---------------------
    # Restricted to seen_items (Part 2's ids) as a defensive scope - see
    # the docstring's Part 4 note. anomalous_item_ids is expected to
    # already be a subset of/equal to seen_items in normal use.
    anomalous_item_id_set = set(anomalous_item_ids)
    anomalous_stmts: list[str] = []
    if anomalous_item_id_set:
        status_in_list = ", ".join(format_sql_literal(s) for s in ANOMALOUS_OPEN_STATUSES)
        for item_id in seen_items:
            if item_id not in anomalous_item_id_set:
                continue
            set_clause = f"{quote_ident(ANOMALOUS_STATUS_COLUMN)} = {format_sql_literal(ANOMALOUS_STATUS_CANCELLED)}"
            anomalous_stmts.append(
                f"-- Cancel open anomalies for item to bill {item_id}\n"
                f"UPDATE {anomalous_tbl}\n"
                f"SET {set_clause}\n"
                f"WHERE ID_ITEM_TO_BILL = {format_sql_literal(item_id)}\n"
                f"  AND {quote_ident(ANOMALOUS_STATUS_COLUMN)} IN ({status_in_list});"
            )

    # --- Part 6: non-cycle "orphan usage" reading cleanup (TIPTL00011) ---
    # See READING_TYPE_ORPHAN_USAGE's comment block for the rule. Deduped,
    # order-preserving, same as every other id list in this function.
    orphan_ids: list[Any] = []
    for id_reading in orphan_usage_id_readings:
        if id_reading not in orphan_ids:
            orphan_ids.append(id_reading)

    orphan_stmts: list[str] = []
    if orphan_ids:
        ri_tbl = _qualified(ADMIN_SCHEMA, READINGS_ITEMSTOBILL_TABLE)
        id_list = ", ".join(format_sql_literal(i) for i in orphan_ids)
        orphan_stmts.append(
            f"-- Remove {READINGS_ITEMSTOBILL_TABLE} link(s) for non-cycle "
            f"({READING_TYPE_COLUMN} {READING_TYPE_ORPHAN_USAGE}) reading(s): {orphan_ids}\n"
            f"DELETE FROM {ri_tbl}\n"
            f"WHERE ID_READING IN ({id_list});"
        )
        reset_set_clause = (
            f"{quote_ident(READ_STATUS_COLUMN)} = {format_sql_literal(READ_STATUS_RESET)},\n"
            f"    {quote_ident(IND_USAGE_TO_CAL_COLUMN)} = 0"
        )
        if reading_has_audit_cols:
            reset_set_clause += _audit_set_fragment(program, user)
        orphan_stmts.append(
            f"-- Reset non-cycle reading(s) back to {READ_STATUS_RESET}: {orphan_ids}\n"
            f"UPDATE {reading_tbl}\n"
            f"SET {reset_set_clause}\n"
            f"WHERE ID_READING IN ({id_list});"
        )

    # --- Assemble ------------------------------------------------------
    explanation = build_case_explanation(
        niss=niss,
        threshold=threshold,
        anomaly_count=len(id_readings),
        correct_date=correct_date,
        correct_date_source_reading=correct_date_source_reading,
        item_count=len(seen_items),
        xml_count=len(xml_stmts),
        item_status_count=len(item_status_stmts),
        anomalous_count=len(anomalous_stmts),
        orphan_reading_count=len(orphan_ids),
    )

    header = [
        "-- Diff Date System anomaly correction script - generated by ScriptGen",
        f"-- Generated (UTC): {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
        f"-- NISS: {niss}    Billing period floor: {threshold}",
        f"-- Correct date applied: {correct_date}"
        + (f"  (from ID_READING {correct_date_source_reading})" if correct_date_source_reading is not None else ""),
        f"-- Program/Jira: {program}",
        f"-- Part 1 (READING_PREV_DATE): {len(reading_stmts)} statement(s)",
        f"-- Part 2 (INI_DATE): {len(item_stmts)} statement(s)",
        f"-- Part 3 (ITEM STATUS {ITEM_STATUS_FROM}->{ITEM_STATUS_TO}): {len(item_status_stmts)} statement(s)",
        f"-- Part 4 (XML_TO_BILL): {len(xml_stmts)} statement(s)",
        f"-- Part 5 (GCCOM_ANOMALOUS cancellation): {len(anomalous_stmts)} statement(s)",
        f"-- Part 6 (non-cycle {READING_TYPE_ORPHAN_USAGE} reading cleanup): "
        f"{len(orphan_ids)} reading(s), {len(orphan_stmts)} statement(s)",
    ]
    if billing_period_count > 1:
        header.append(
            f"-- NOTE: anomalous readings span {billing_period_count} distinct billing periods "
            "for this NISS - double-check before running."
        )
        if scoped_to_lowest_period:
            header.append(
                f"-- SCOPED TO LOWEST BILLING PERIOD ONLY: this script covers just {len(reading_stmts)} of "
                f"the reading(s) above (the ones from the earliest of the {billing_period_count} billing "
                "periods) - readings from later billing periods were intentionally excluded, per request."
            )
    header += [
        "--",
        "-- --- Case explanation --------------------------------------------",
    ]
    for line in explanation.split("\n"):
        header.append(f"-- {line}" if line else "--")
    header.append("-- --------------------------------------------------------------")
    for w in warnings:
        header.append(f"-- WARNING: {w}")
    header.append("")

    body_parts = []
    if reading_stmts:
        body_parts.append("-- === Part 1: GCGT_RE_READING.READING_PREV_DATE ===\n" + "\n\n".join(reading_stmts))
    if item_stmts:
        body_parts.append("-- === Part 2: GCCOM_ITEMS_TO_BILL.INI_DATE ===\n" + "\n\n".join(item_stmts))
    if item_status_stmts:
        body_parts.append(
            f"-- === Part 3: GCCOM_ITEMS_TO_BILL.STATUS {ITEM_STATUS_FROM}->{ITEM_STATUS_TO} ===\n"
            + "\n\n".join(item_status_stmts)
        )
    if xml_stmts:
        body_parts.append("-- === Part 4: GCCOM_ITEMS_TO_BILL_XML.XML_TO_BILL ===\n" + "\n\n".join(xml_stmts))
    if anomalous_stmts:
        body_parts.append("-- === Part 5: GCCOM_ANOMALOUS cancellation ===\n" + "\n\n".join(anomalous_stmts))
    if orphan_stmts:
        body_parts.append(
            f"-- === Part 6: non-cycle {READING_TYPE_ORPHAN_USAGE} reading cleanup ===\n"
            + "\n\n".join(orphan_stmts)
        )
    body = "\n\n".join(body_parts) if body_parts else "-- Nothing to update."

    footer = [
        "",
        "-- Review the statements above before running them.",
    ]

    sql_text = "\n".join(header) + body + "\n".join(footer)
    if clean:
        sql_text = _strip_sql_comments(sql_text)
    return CorrectionScript(
        sql_text=sql_text,
        reading_count=len(reading_stmts),
        item_count=len(item_stmts),
        xml_count=len(xml_stmts),
        anomalous_count=len(anomalous_stmts),
        item_status_count=len(item_status_stmts),
        orphan_reading_count=len(orphan_ids),
        billing_period_count=billing_period_count,
        scoped_to_lowest_period=scoped_to_lowest_period,
        explanation=explanation,
        warnings=warnings,
    )


@dataclass
class CleanupScript:
    """
    Result of build_cleanup_script - deliberately a SEPARATE, smaller
    shape from CorrectionScript rather than reusing it with
    reading_count/item_count/xml_count pinned to 0: this script only ever
    covers two of the five correction parts, and giving it its own type
    makes that scope limitation visible in the code (an IDE/reviewer
    seeing "CleanupScript" can't mistake it for a full five-part result),
    not just in a comment.
    """
    sql_text: str
    item_status_count: int = 0
    anomalous_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def statement_count(self) -> int:
        return self.item_status_count + self.anomalous_count

    @property
    def warning_count(self) -> int:
        return len(self.warnings)


def build_cleanup_script(
    *,
    item_ids: Iterable[Any],
    item_status_ids: Iterable[Any] = (),
    item_has_audit_cols: bool = False,
    program: str = DEFAULT_AUDIT_PROGRAM,
    user: str = DEFAULT_AUDIT_USER,
    clean: bool = False,
) -> CleanupScript:
    """
    Builds a bulk-cleanup script for a caller-selected set of item-to-bill
    ids discovered via build_detect_all_anomalies_query - the "detect
    all pending anomalies" workflow's counterpart to build_correction_
    script, but intentionally narrower in scope:

      - GCCOM_ITEMS_TO_BILL.STATUS: one UPDATE per id in item_status_ids
        (STTOBILL00 -> STTOBILL01), same "only touch if still confirmed
        pending" WHERE-clause guard as build_correction_script's Part 3.
      - GCCOM_ANOMALOUS: one UPDATE per id in item_ids, cancelling any
        still-open anomaly (ANOMALOUS_STATUS -> ESTAN00005), same
        set-based WHERE-clause guard as build_correction_script's Part 5.

    Deliberately does NOT touch GCGT_RE_READING.READING_PREV_DATE,
    GCCOM_ITEMS_TO_BILL.INI_DATE, or GCCOM_ITEMS_TO_BILL_XML.XML_TO_BILL -
    unlike build_correction_script, this function has no NISS and no
    correct_date to work from (build_detect_all_anomalies_query starts
    from GCCOM_ANOMALOUS directly, not from a NISS's anomalous readings),
    so it cannot derive what those three fixes should be. This is a
    "close out already-fixed items" tool for cases where the underlying
    date correction happened some other way (or will happen separately
    via the NISS-based Detect/Resolve/Generate workflow above) - NOT a
    substitute for that workflow. The generated script's header says so
    explicitly, so nobody mistakes this for a full correction.

    item_ids is every selected id (drives the GCCOM_ANOMALOUS cancel);
    item_status_ids is the subset of those still confirmed at
    ITEM_STATUS_FROM (drives the STATUS advance) - the caller gets this
    by re-running build_item_status_query(item_ids) right before
    generating, same "re-verify at generate time" pattern used
    elsewhere in this module, since the detect-all list may be stale by
    the time the analyst clicks Generate.
    """
    ids = list(item_ids)
    warnings: list[str] = []
    item_tbl = _qualified(ADMIN_SCHEMA, ITEMS_TO_BILL_TABLE)
    anomalous_tbl = _qualified(ADMIN_SCHEMA, ANOMALOUS_TABLE)

    item_status_id_set = set(item_status_ids)
    item_status_stmts: list[str] = []
    for item_id in ids:
        if item_id not in item_status_id_set:
            continue
        set_clause = f"{quote_ident(ITEM_STATUS_COLUMN)} = {format_sql_literal(ITEM_STATUS_TO)}"
        if item_has_audit_cols:
            set_clause += _audit_set_fragment(program, user)
        item_status_stmts.append(
            f"-- Advance status for item to bill {item_id}\n"
            f"UPDATE {item_tbl}\n"
            f"SET {set_clause}\n"
            f"WHERE ID_ITEM_TO_BILL = {format_sql_literal(item_id)}\n"
            f"  AND {quote_ident(ITEM_STATUS_COLUMN)} = {format_sql_literal(ITEM_STATUS_FROM)};"
        )

    anomalous_stmts: list[str] = []
    if ids:
        status_in_list = ", ".join(format_sql_literal(s) for s in ANOMALOUS_OPEN_STATUSES)
        for item_id in ids:
            set_clause = f"{quote_ident(ANOMALOUS_STATUS_COLUMN)} = {format_sql_literal(ANOMALOUS_STATUS_CANCELLED)}"
            anomalous_stmts.append(
                f"-- Cancel open anomalies for item to bill {item_id}\n"
                f"UPDATE {anomalous_tbl}\n"
                f"SET {set_clause}\n"
                f"WHERE ID_ITEM_TO_BILL = {format_sql_literal(item_id)}\n"
                f"  AND {quote_ident(ANOMALOUS_STATUS_COLUMN)} IN ({status_in_list});"
            )

    header = [
        "-- Diff Date System anomaly BULK CLEANUP script - generated by ScriptGen",
        f"-- Generated (UTC): {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
        f"-- Item-to-bill ids selected: {len(ids)}",
        f"-- Program/Jira: {program}",
        "-- WARNING: this is a bulk-cleanup script, NOT a full correction. It only",
        "-- advances GCCOM_ITEMS_TO_BILL.STATUS and cancels GCCOM_ANOMALOUS records -",
        "-- it does NOT touch READING_PREV_DATE, INI_DATE, or XML_TO_BILL. Use the",
        "-- NISS-based Detect / Resolve Bill Links / Generate Correction Script",
        "-- workflow above if those still need fixing for these items.",
        f"-- Part A (ITEM STATUS {ITEM_STATUS_FROM}->{ITEM_STATUS_TO}): {len(item_status_stmts)} statement(s)",
        f"-- Part B (GCCOM_ANOMALOUS cancellation): {len(anomalous_stmts)} statement(s)",
    ]
    for w in warnings:
        header.append(f"-- WARNING: {w}")
    header.append("")

    body_parts = []
    if item_status_stmts:
        body_parts.append(
            f"-- === Part A: GCCOM_ITEMS_TO_BILL.STATUS {ITEM_STATUS_FROM}->{ITEM_STATUS_TO} ===\n"
            + "\n\n".join(item_status_stmts)
        )
    if anomalous_stmts:
        body_parts.append("-- === Part B: GCCOM_ANOMALOUS cancellation ===\n" + "\n\n".join(anomalous_stmts))
    body = "\n\n".join(body_parts) if body_parts else "-- Nothing to update."

    footer = [
        "",
        "-- Review the statements above before running them.",
    ]

    sql_text = "\n".join(header) + body + "\n".join(footer)
    if clean:
        sql_text = _strip_sql_comments(sql_text)
    return CleanupScript(
        sql_text=sql_text,
        item_status_count=len(item_status_stmts),
        anomalous_count=len(anomalous_stmts),
        warnings=warnings,
    )


# ---------------------------------------------------------------------
# Batch / multi-NISS processing - runs the same Detect -> Resolve ->
# Generate pipeline across several sector supplies (NISS) at once, for
# an analyst who already knows they have a list of affected supplies to
# fix rather than reviewing one at a time. Deliberately pure/testable
# here too: the caller (web/server.py's date_anomaly_batch route) does
# every DB round-trip per NISS and hands back one BatchNissResult per
# NISS; this module only assembles the combined, reviewable script.
# ---------------------------------------------------------------------

STATUS_OK = "ok"
STATUS_NO_ANOMALIES = "no_anomalies"
STATUS_ERROR = "error"


@dataclass
class BatchNissResult:
    niss: str
    status: str  # STATUS_OK / STATUS_NO_ANOMALIES / STATUS_ERROR
    reading_count: int = 0
    item_count: int = 0
    xml_count: int = 0
    anomalous_count: int = 0
    item_status_count: int = 0
    orphan_reading_count: int = 0  # Part 6: non-cycle (TIPTL00011) readings DELETEd/reset for this NISS
    billing_period_count: int = 0  # distinct ID_BILLING_PERIOD values among this NISS's anomalous readings
    scoped_to_lowest_period: bool = False  # True if this NISS's script was generated for its lowest billing period only - see build_correction_script's kwarg of the same name
    explanation: str = ""
    warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None
    # This NISS's own complete, self-contained script (statements run
    # and commit on their own, autocommit-style - no BEGIN TRANSACTION
    # wrapper, see _strip_sql_comments' docstring) - None unless
    # status == STATUS_OK, so it can be reviewed and run independently
    # of every other NISS in the batch.
    sql_text: Optional[str] = None


def build_batch_script(
    results: Iterable[BatchNissResult], program: str = DEFAULT_AUDIT_PROGRAM, clean: bool = False,
) -> str:
    """
    Combines per-NISS correction scripts (already built by
    build_correction_script, one call per NISS) into a single
    reviewable document: a leading summary line per NISS (status +
    counts), then each STATUS_OK NISS's own already-self-contained
    script, separated by a banner comment. A NISS with no anomalies or
    an error contributes only its summary line - no empty/broken SQL
    section - so a long batch stays reviewable at a glance before
    anyone runs anything.

    clean=True strips comments from the WHOLE combined document via
    _strip_sql_comments, including this function's own summary/banner
    lines - not just whatever comments happen to already be in each
    per-NISS r.sql_text (batch_jobs.py always builds those with
    clean=False, so Script History and Analysis History keep the fully
    annotated version regardless of what the analyst picks here; only
    the final combined output the analyst downloads/copies is
    affected). That means a clean batch script loses the per-NISS
    status summary (no-anomalies / error lines are comments too) - a
    deliberate trade for "safe to paste and run", not a bug: the
    non-clean version is always available for review first.
    """
    results = list(results)
    lines = [
        "-- ============================================================",
        "-- Diff Date System anomaly correction - BATCH script",
        f"-- Generated (UTC): {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
        f"-- Program/Jira: {program}",
        f"-- NISS processed: {len(results)}",
        "-- ============================================================",
        "-- Summary:",
    ]
    for r in results:
        if r.status == STATUS_OK:
            warn_note = f", {len(r.warnings)} warning(s)" if r.warnings else ""
            orphan_note = f", {r.orphan_reading_count} non-cycle reading(s) reset" if r.orphan_reading_count else ""
            period_note = (
                f", scoped to lowest of {r.billing_period_count} billing periods"
                if r.scoped_to_lowest_period and r.billing_period_count > 1 else ""
            )
            lines.append(
                f"--   {r.niss}: OK - {r.reading_count} reading, {r.item_count} item, "
                f"{r.item_status_count} status-transition, {r.xml_count} XML, "
                f"{r.anomalous_count} anomaly-cancel statement(s){orphan_note}{period_note}{warn_note}"
            )
        elif r.status == STATUS_NO_ANOMALIES:
            lines.append(f"--   {r.niss}: no anomalies found - nothing to correct.")
        else:
            lines.append(f"--   {r.niss}: ERROR - {r.error}")

    ok_results = [r for r in results if r.status == STATUS_OK and r.sql_text]
    if not ok_results:
        lines.append("")
        lines.append("-- Nothing to correct across this batch.")
        combined = "\n".join(lines)
        return _strip_sql_comments(combined) if clean else combined

    body_parts = [
        (
            f"-- ============================================================\n"
            f"-- NISS: {r.niss}\n"
            f"-- ============================================================\n"
            f"{r.sql_text}"
        )
        for r in ok_results
    ]
    combined = "\n".join(lines) + "\n\n" + "\n\n".join(body_parts)
    return _strip_sql_comments(combined) if clean else combined
