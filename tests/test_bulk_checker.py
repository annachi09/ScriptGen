"""
Pure-logic tests for app/core/bulk_checker.py's query builders - same
convention as tests/test_hierarchy_analysis.py: these only check the
generated SQL TEXT (clauses, literals, table/column names, filter
branches), never touch a real database. The query shape itself is RJ's
own analyst-supplied SQL, ported verbatim from the standalone EWA Bulk
Checker project (see the module's own docstring for the full provenance
note) - these tests guard against the SQL TEXT regressing during the
port (bind params -> inline literals), not against the query's own
business logic being wrong.
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import bulk_checker as bc

DATE_FROM = datetime.date(2026, 9, 1)
DATE_TO = datetime.date(2026, 10, 1)
BILLING_PERIOD = 10000000235
ACCOUNT_NUMBER = "ACC-123/45"  # includes a char that must be escaped if unquoted


# ---------------------------------------------------------------------
# build_pending_bulks_sql
# ---------------------------------------------------------------------

def test_build_pending_bulks_sql_embeds_billing_period_literal_twice():
    sql = bc.build_pending_bulks_sql(BILLING_PERIOD, DATE_FROM, DATE_TO)
    assert sql.count(f"= {BILLING_PERIOD}") == 2  # BDET subquery + BILLAGG subquery


def test_build_pending_bulks_sql_embeds_date_literals():
    sql = bc.build_pending_bulks_sql(BILLING_PERIOD, DATE_FROM, DATE_TO)
    assert sql.count("< '2026-10-01'") == 2  # BILLAGG cs2.FROM_DATE + outer cs.FROM_DATE
    assert sql.count(">= '2026-09-01'") == 2  # BILLAGG cs2.END_DATE + outer cs.END_DATE


def test_build_pending_bulks_sql_default_status_filter_has_no_extra_clause():
    sql = bc.build_pending_bulks_sql(BILLING_PERIOD, DATE_FROM, DATE_TO, "all")
    assert "BDET.file_number IS NULL" not in sql
    assert "has_missing_bill" in sql  # still selected as a column
    assert "ISNULL(BILLAGG.has_missing_bill, 0) = 1" not in sql  # but not filtered on


def test_build_pending_bulks_sql_pending_filter():
    sql = bc.build_pending_bulks_sql(BILLING_PERIOD, DATE_FROM, DATE_TO, "pending")
    assert "AND BDET.file_number IS NULL" in sql


def test_build_pending_bulks_sql_generated_filter():
    sql = bc.build_pending_bulks_sql(BILLING_PERIOD, DATE_FROM, DATE_TO, "generated")
    assert "AND BDET.file_number IS NOT NULL" in sql


def test_build_pending_bulks_sql_missing_bill_filter():
    sql = bc.build_pending_bulks_sql(BILLING_PERIOD, DATE_FROM, DATE_TO, "missing_bill")
    assert "AND ISNULL(BILLAGG.has_missing_bill, 0) = 1" in sql


def test_build_pending_bulks_sql_in_invoicing_filter():
    sql = bc.build_pending_bulks_sql(BILLING_PERIOD, DATE_FROM, DATE_TO, "in_invoicing")
    assert "AND ISNULL(BILLAGG.has_bill_in_invoicing, 0) = 1" in sql


def test_build_pending_bulks_sql_unknown_filter_falls_back_to_all():
    sql_unknown = bc.build_pending_bulks_sql(BILLING_PERIOD, DATE_FROM, DATE_TO, "not_a_real_filter")
    sql_all = bc.build_pending_bulks_sql(BILLING_PERIOD, DATE_FROM, DATE_TO, "all")
    assert sql_unknown == sql_all


def test_build_pending_bulks_sql_key_joins_present():
    sql = bc.build_pending_bulks_sql(BILLING_PERIOD, DATE_FROM, DATE_TO)
    assert "FROM GCCOM_ACCOUNT_BUNCHER ab" in sql
    assert "JOIN GCCOM_PAYMENT_FORM pf ON ab.ID_PAYMENT_FORM = pf.ID_PAYMENT_FORM" in sql
    assert "LEFT JOIN (" in sql  # BDET
    assert "BILLAGG ON BILLAGG.ID_PAYMENT_FORM_BUNCHER = ab.ID_PAYMENT_FORM_BUNCHER" in sql


def test_build_pending_bulks_sql_excludes_ended_accounts_and_cancelled_services():
    sql = bc.build_pending_bulks_sql(BILLING_PERIOD, DATE_FROM, DATE_TO)
    assert "AND ab.END_DATE IS NULL" in sql
    assert "AND cs.STATUS <> 'ESTSC00005'" in sql


# ---------------------------------------------------------------------
# build_bill_detail_sql
# ---------------------------------------------------------------------

def test_build_bill_detail_sql_embeds_account_literal_escaped():
    sql = bc.build_bill_detail_sql(BILLING_PERIOD, DATE_FROM, DATE_TO, ACCOUNT_NUMBER)
    assert f"pf2.reference = '{ACCOUNT_NUMBER}'" in sql


def test_build_bill_detail_sql_escapes_single_quote_in_account_number():
    sql = bc.build_bill_detail_sql(BILLING_PERIOD, DATE_FROM, DATE_TO, "O'BRIEN-1")
    # The embedded quote must be doubled (T-SQL's escape convention), not
    # left bare - a bare quote there would break out of the string literal.
    assert "pf2.reference = 'O''BRIEN-1'" in sql


def test_build_bill_detail_sql_embeds_billing_period_and_dates():
    sql = bc.build_bill_detail_sql(BILLING_PERIOD, DATE_FROM, DATE_TO, ACCOUNT_NUMBER)
    assert f"b.ID_BILLING_PERIOD = {BILLING_PERIOD}" in sql
    assert "cs.FROM_DATE < '2026-10-01'" in sql
    assert "cs.END_DATE >= '2026-09-01'" in sql


def test_build_bill_detail_sql_default_filter_has_no_extra_clause():
    sql = bc.build_bill_detail_sql(BILLING_PERIOD, DATE_FROM, DATE_TO, ACCOUNT_NUMBER, "all")
    assert "AND b.id_bill IS NOT NULL AND BL.FILE_NUMBER IS NULL" not in sql
    assert "AND b.id_bill IS NULL" not in sql


def test_build_bill_detail_sql_pending_filter():
    sql = bc.build_bill_detail_sql(BILLING_PERIOD, DATE_FROM, DATE_TO, ACCOUNT_NUMBER, "pending")
    assert "AND b.id_bill IS NOT NULL AND BL.FILE_NUMBER IS NULL" in sql


def test_build_bill_detail_sql_missing_filter():
    sql = bc.build_bill_detail_sql(BILLING_PERIOD, DATE_FROM, DATE_TO, ACCOUNT_NUMBER, "missing")
    assert "AND b.id_bill IS NULL" in sql


def test_build_bill_detail_sql_qualifies_schema_for_specific_tables():
    sql = bc.build_bill_detail_sql(BILLING_PERIOD, DATE_FROM, DATE_TO, ACCOUNT_NUMBER)
    # Matches the analyst's own original query exactly - only these
    # tables are schema-qualified, the rest deliberately aren't (same
    # convention app.core.hierarchy_analysis documents for its own
    # unqualified GCCOM_SECTOR_SUPPLY join).
    assert "ouc_admin.GCCOM_ACCOUNT_BUNCHER ab" in sql
    assert "ouc_common_admin.GCCOM_PAYMENT_FORM pf1" in sql
    assert "ouc_admin.GCCB_NOTICE_BILL nb" in sql
    assert "FROM GCCOM_ACCOUNT_BUNCHER" not in sql  # must be schema-qualified here, unlike the pending-bulks query


def test_build_bill_detail_sql_selects_sector_supply_for_reading_history_link():
    sql = bc.build_bill_detail_sql(BILLING_PERIOD, DATE_FROM, DATE_TO, ACCOUNT_NUMBER)
    assert "ss.ID_SECTOR_SUPPLY," in sql


def test_build_bill_detail_sql_orders_by_status_then_reference():
    sql = bc.build_bill_detail_sql(BILLING_PERIOD, DATE_FROM, DATE_TO, ACCOUNT_NUMBER)
    assert "ORDER BY d.TEXT ASC, pf2.reference, PF1.REFERENCE, ss.niss, b.ID_BILLING_PERIOD ASC, cs.status" in sql


# ---------------------------------------------------------------------
# Filter/status constant sanity
# ---------------------------------------------------------------------

def test_status_filters_and_bill_filters_constants():
    assert bc.STATUS_FILTERS == ("all", "pending", "generated", "missing_bill", "in_invoicing")
    assert bc.BILL_FILTERS == ("all", "pending", "missing")


def test_recent_billing_periods_sql_is_read_only_top_50():
    assert "TOP 50" in bc.RECENT_BILLING_PERIODS_SQL
    assert "FROM GCCOM_BILLING_PERIOD" in bc.RECENT_BILLING_PERIODS_SQL
    assert "ORDER BY ID_BILLING_PERIOD DESC" in bc.RECENT_BILLING_PERIODS_SQL
