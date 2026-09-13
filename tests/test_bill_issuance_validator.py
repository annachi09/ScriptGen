"""
Pure-logic tests for app/core/bill_issuance_validator.py's query builder -
same convention as tests/test_date_anomaly.py and tests/test_hierarchy_
analysis.py: these only check the generated SQL TEXT (clauses, literals,
table/column names), never touch a real database. Live correctness
against the real tunnel DB was confirmed by hand this round (RJ's own
query + rules, plus GCCOM_BILL_STATUS/GCCOM_COMPANY_OFFERED_SERVICE
lookups - see the module's own docstring) - these tests guard against the
SQL TEXT regressing, not against the business rules themselves.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import bill_issuance_validator as biv


def test_build_stuck_bills_query_filters_notice_and_bill_status():
    sql = biv.build_stuck_bills_query()
    assert "nt.COD_STATUS = '5000NOTEMP'" in sql
    assert "b.BILLING_STATUS = 'ESTFAC0012'" in sql


def test_build_stuck_bills_query_filters_offered_service_176_as_int():
    sql = biv.build_stuck_bills_query()
    # format_sql_literal renders ints as plain numbers, not N'...' -
    # see app/core/sql_format.py.
    assert "b.ID_OFFERED_SERVICE = 176" in sql


def test_build_stuck_bills_query_requires_exactly_one_bill_in_period():
    sql = biv.build_stuck_bills_query()
    assert "COUNT(*) AS BILL_COUNT" in sql
    assert "pc.BILL_COUNT = 1" in sql


def test_build_stuck_bills_query_next_period_is_plus_one():
    sql = biv.build_stuck_bills_query()
    assert "nb.ID_BILLING_PERIOD = c.PERIOD_RATE + 1" in sql


def test_build_stuck_bills_query_next_period_services_electricity_and_water():
    sql = biv.build_stuck_bills_query()
    assert "nb.ID_OFFERED_SERVICE IN (1, 19)" in sql


def test_build_stuck_bills_query_filters_waiting_other_services_status():
    sql = biv.build_stuck_bills_query()
    assert "WHERE nb.BILLING_STATUS = 'ESTFAC0015'" in sql


def test_build_stuck_bills_query_dedupes_electricity_first_no_duplicates():
    # RJ, 2026-09-15: "i dont want duplicates, if you find ele, stop
    # otherwise if it is not found check water" - one row per account,
    # via ROW_NUMBER() partitioned by account/period, Electricity (1)
    # sorted first, keeping only RN = 1.
    sql = biv.build_stuck_bills_query()
    assert "ROW_NUMBER() OVER (" in sql
    assert "PARTITION BY c.ID_PAYMENT_FORM, c.PERIOD_RATE" in sql
    assert "ORDER BY CASE WHEN nb.ID_OFFERED_SERVICE = 1 THEN 0 ELSE 1 END" in sql
    assert "WHERE m.RN = 1" in sql


def test_build_stuck_bills_query_joins_lookup_tables_for_descriptions():
    sql = biv.build_stuck_bills_query()
    assert "GCCOM_COMPANY_OFFERED_SERVICE" in sql
    assert "GCCOM_BILL_STATUS" in sql


def test_build_stuck_bills_query_uses_top_clause_for_limit():
    sql = biv.build_stuck_bills_query(limit=500)
    assert "TOP (500)" in sql


def test_build_stuck_bills_query_no_limit_omits_top_clause():
    sql = biv.build_stuck_bills_query(limit=None)
    assert "TOP (" not in sql


def test_build_stuck_bills_query_orders_by_notice_update_date():
    sql = biv.build_stuck_bills_query()
    assert "ORDER BY m.NOTICE_UPDATE_DATE;" in sql


# ---------------- Case 2: terminated-account billing-period mismatch ----------------
# RJ, 2026-09-15, redesigned 2026-09-16/17 - see the module's own Case 2
# comment block (above CONTRACTED_SERVICE_TABLE) for the full business-
# rule narrative, RJ's own 342702 example, and the AskUserQuestion
# answers this shape implements (termination date = END_DATE, target
# period = MAX among matched final bills, account-grouped Complete
# filter). Also see app/core/sql_format.py's 2026-09-17 comment: these
# assertions expect plain '...' literals (no N prefix) - that's the real
# fix that took this query from timing out at 120s to ~8s live.


def test_build_terminated_period_mismatch_query_scopes_terminated_services_by_status_and_date():
    sql = biv.build_terminated_period_mismatch_query()
    assert "FROM OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE cs" in sql
    assert "WHERE cs.STATUS = 'ESTSC00004'" in sql
    assert "AND cs.END_DATE >= DATEADD(DAY, -60, CAST(GETDATE() AS DATE))" in sql


def test_build_terminated_period_mismatch_query_days_back_is_configurable():
    sql = biv.build_terminated_period_mismatch_query(days_back=14)
    assert "DATEADD(DAY, -14, CAST(GETDATE() AS DATE))" in sql


def test_build_terminated_period_mismatch_query_days_back_none_omits_date_filter():
    sql = biv.build_terminated_period_mismatch_query(days_back=None)
    assert "DATEADD(DAY" not in sql


def test_build_terminated_period_mismatch_query_joins_own_final_bill_by_contracted_service():
    sql = biv.build_terminated_period_mismatch_query()
    # The real bug this redesign fixed: joining by ID_PAYMENT_FORM +
    # ID_OFFERED_SERVICE has no supporting index on GCCOM_BILL. Joining
    # by ID_CONTRACTED_SERVICE (the service's own PK) does - see the
    # query builder's own docstring for the live-confirmed index.
    assert "ON b.ID_CONTRACTED_SERVICE = ts.ID_CONTRACTED_SERVICE" in sql
    assert "AND b.BILLING_DATE = ts.END_DATE" in sql
    assert "AND b.BILLING_STATUS IN ('ESTFAC0012', 'ESTFAC0005')" in sql


def test_build_terminated_period_mismatch_query_computes_target_period_per_account():
    sql = biv.build_terminated_period_mismatch_query()
    assert "MAX(ID_BILLING_PERIOD) OVER (PARTITION BY ID_PAYMENT_FORM) AS TARGET_PERIOD" in sql


def test_build_terminated_period_mismatch_query_flags_needs_update_including_no_bill_case():
    sql = biv.build_terminated_period_mismatch_query()
    assert "CASE WHEN ID_BILL IS NULL THEN 1 WHEN ID_BILLING_PERIOD <> TARGET_PERIOD THEN 1 ELSE 0 END AS NEEDS_UPDATE" in sql


def test_build_terminated_period_mismatch_query_flags_account_has_issue():
    sql = biv.build_terminated_period_mismatch_query()
    assert "AS ACCOUNT_HAS_ISSUE" in sql
    assert "OVER (PARTITION BY ID_PAYMENT_FORM) AS ACCOUNT_HAS_ISSUE" in sql


def test_build_terminated_period_mismatch_query_excludes_accounts_with_active_service():
    sql = biv.build_terminated_period_mismatch_query()
    assert "acct.ACTIVE_COUNT = 0" in sql
    assert "cs.STATUS <> 'ESTSC00004'" in sql


def test_build_terminated_period_mismatch_query_joins_payment_form_for_reference():
    sql = biv.build_terminated_period_mismatch_query()
    assert "JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = f.ID_PAYMENT_FORM" in sql


def test_build_terminated_period_mismatch_query_uses_top_clause_for_limit():
    sql = biv.build_terminated_period_mismatch_query(limit=250)
    assert "TOP (250)" in sql


def test_build_terminated_period_mismatch_query_no_limit_omits_top_clause():
    sql = biv.build_terminated_period_mismatch_query(limit=None)
    assert "TOP (" not in sql


def test_build_terminated_period_mismatch_query_orders_by_account_and_service():
    sql = biv.build_terminated_period_mismatch_query()
    assert "ORDER BY f.ID_PAYMENT_FORM, f.ID_OFFERED_SERVICE;" in sql


def test_build_terminated_period_fix_script_emits_one_update_per_pair():
    result = biv.build_terminated_period_fix_script([(555, 237), (556, 237)])
    assert result.update_count == 2
    assert result.sql_text.count("UPDATE OUC_COMMON_ADMIN.GCCOM_BILL") == 2
    assert "WHERE ID_BILL = 555" in result.sql_text
    assert "AND ID_BILLING_PERIOD <> 237" in result.sql_text


def test_build_terminated_period_fix_script_sets_target_period_and_audit_cols():
    result = biv.build_terminated_period_fix_script([(555, 237)], program="JIRA-42", user="RMA")
    assert "SET ID_BILLING_PERIOD = 237" in result.sql_text
    assert "UPDATE_PROGRAM = 'JIRA-42'" in result.sql_text
    assert "UPDATE_DATE = GETDATE()" in result.sql_text
    assert "UPDATE_USER = 'RMA'" in result.sql_text


def test_build_terminated_period_fix_script_empty_updates_says_nothing_to_update():
    result = biv.build_terminated_period_fix_script([])
    assert result.update_count == 0
    assert "Nothing to update." in result.sql_text


def test_build_terminated_period_fix_script_clean_option_strips_comments():
    result = biv.build_terminated_period_fix_script([(555, 237)], clean=True)
    assert "--" not in result.sql_text
    assert "UPDATE OUC_COMMON_ADMIN.GCCOM_BILL" in result.sql_text


def test_build_terminated_period_fix_script_skips_pairs_with_no_bill_and_warns():
    # A service with NEEDS_UPDATE=1 but no matching final bill at all
    # (id_bill is None) has nothing to UPDATE - see the function's own
    # docstring for why this is skipped rather than emitting a broken
    # `WHERE ID_BILL = NULL` statement.
    result = biv.build_terminated_period_fix_script([(555, 237), (None, 237)])
    assert result.update_count == 1
    assert result.sql_text.count("UPDATE OUC_COMMON_ADMIN.GCCOM_BILL") == 1
    assert len(result.warnings) == 1
    assert "no bill matching" in result.warnings[0]
