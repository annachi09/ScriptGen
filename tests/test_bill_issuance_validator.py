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


def test_build_stuck_bills_query_period_count_only_counts_cycle_invoicing_bills():
    # RJ, 2026-09-14, investigating why REFERENCE 1100068871 wasn't
    # detected: "we only check TFGEN0001 and in status INVOICING for case
    # 1" - period_counts used to count EVERY bill sharing the Rate bill's
    # period, so a one-off charge bill (e.g. a Deposito, BILL_TYPE
    # TFGEN10006) or an old already-invoiced/voided cycle bill would wrongly
    # push the count above 1 and disqualify a genuine match. Live-confirmed:
    # 1100068871's own Rate bill shared its period with exactly such a
    # Deposito bill.
    sql = biv.build_stuck_bills_query()
    period_counts_block = sql.split("candidate AS (")[0]
    assert "WHERE BILL_TYPE = 'TFGEN00001'" in period_counts_block
    assert "AND BILLING_STATUS = 'ESTFAC0012'" in period_counts_block


def test_build_stuck_bills_query_next_period_is_plus_one():
    # RJ, 2026-09-14: "the next water or ele bills can be up to 11 billing
    # period ahead" - this assertion was stale from before that widening
    # (checked for an exact "= PERIOD_RATE + 1" match, which the query
    # hasn't emitted since NEXT_PERIOD_MAX_AHEAD_DEFAULT was introduced).
    # Now checks the BETWEEN range's own lower bound instead, which is
    # still exactly PERIOD_RATE + 1 by default.
    sql = biv.build_stuck_bills_query()
    assert "nb.ID_BILLING_PERIOD BETWEEN c.PERIOD_RATE + 1 AND c.PERIOD_RATE + 11" in sql


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


# RJ, 2026-09-14: "enhancing Case 1, I found cases that the next water or
# ele bills can be up to 11 billing period ahead of the rate bills, and
# they are valid. So can you include them if the next bill of water and
# rate is up to 11 billing period ahead, then add a column on how much
# months and then a filter as well" - live-confirmed the old exact-next-
# period-only version currently finds 0 accounts against real data, the
# widened default (11) finds 74 - see build_stuck_bills_query's own
# docstring for the full live numbers.


def test_build_stuck_bills_query_default_range_is_up_to_11_periods_ahead():
    sql = biv.build_stuck_bills_query()
    assert "nb.ID_BILLING_PERIOD BETWEEN c.PERIOD_RATE + 1 AND c.PERIOD_RATE + 11" in sql
    assert biv.NEXT_PERIOD_MAX_AHEAD_DEFAULT == 11


def test_build_stuck_bills_query_max_periods_ahead_is_configurable():
    sql = biv.build_stuck_bills_query(max_periods_ahead=3)
    assert "nb.ID_BILLING_PERIOD BETWEEN c.PERIOD_RATE + 1 AND c.PERIOD_RATE + 3" in sql


def test_build_stuck_bills_query_computes_periods_ahead_column():
    sql = biv.build_stuck_bills_query()
    assert "nb.ID_BILLING_PERIOD - c.PERIOD_RATE AS PERIODS_AHEAD" in sql
    assert "m.PERIOD_NEXT, m.STATUS_NEXT, m.PERIODS_AHEAD," in sql


def test_build_stuck_bills_query_dedupe_prefers_closest_period_then_electricity():
    # RJ, 2026-09-15's original "Electricity first" tiebreak still applies,
    # but now only as a tiebreak WITHIN the same period - the primary sort
    # is the closest (smallest) period first, since more than one period
    # can now qualify per account.
    sql = biv.build_stuck_bills_query()
    assert "ORDER BY nb.ID_BILLING_PERIOD ASC," in sql
    assert "CASE WHEN nb.ID_OFFERED_SERVICE = 1 THEN 0 ELSE 1 END" in sql


# ---------------- Case 1: Generate Release Script ----------------
# RJ, 2026-09-14, own words + own exact SQL template - see the module's
# own comment block (above NOTICE_STATUS_RELEASE) for the full template
# and business meaning.


def test_build_release_notice_script_updates_notice_tmp_table():
    result = biv.build_release_notice_script([1073563109])
    assert result.bill_count == 1
    assert result.sql_text.count("UPDATE OUC_ADMIN.GCCOM_NOTICE_TMP") == 1
    assert "SET ID_NOTICE_TMP" not in result.sql_text  # sanity: not touching PK


def test_build_release_notice_script_sets_release_status_and_audit_cols():
    result = biv.build_release_notice_script([1073563109], program="JIRA-99", user="ANALYST1")
    assert "COD_STATUS = '1000NOTEMP'" in result.sql_text
    assert "UPDATE_DATE = GETDATE()" in result.sql_text
    assert "UPDATE_USER = 'ANALYST1'" in result.sql_text
    assert "UPDATE_PROGRAM = 'JIRA-99'" in result.sql_text


def test_build_release_notice_script_default_audit_values_match_rj_template():
    # RJ's own exact literals from the template he provided, kept as the
    # defaults (see RELEASE_SCRIPT_DEFAULT_PROGRAM/USER's own comment).
    result = biv.build_release_notice_script([1073563109])
    assert "UPDATE_USER = 'RMA'" in result.sql_text
    assert "UPDATE_PROGRAM = 'VALIDATION RELEASE_TERMINATED'" in result.sql_text


def test_build_release_notice_script_subquery_joins_notice_to_bill():
    result = biv.build_release_notice_script([1073563109, 1073563200])
    sql = result.sql_text
    assert "JOIN OUC_COMMON_ADMIN.GCCOM_BILL b ON b.ID_BILL = nt.ID_BILL" in sql
    assert "nt.COD_STATUS = '5000NOTEMP'" in sql
    assert "b.BILLING_STATUS = 'ESTFAC0012'" in sql
    assert "b.ID_BILL IN (1073563109, 1073563200)" in sql


def test_build_release_notice_script_has_outer_pending_status_guard():
    # RJ's own template's outer guard (after the closing paren of the
    # subquery) - makes re-running the script a safe no-op against a
    # notice that's already been released.
    result = biv.build_release_notice_script([1073563109])
    assert ")\nAND COD_STATUS = '5000NOTEMP';" in result.sql_text


def test_build_release_notice_script_dedupes_and_drops_none_preserving_order():
    result = biv.build_release_notice_script([1, None, 2, 1, 2, 3])
    assert result.bill_count == 3
    assert "b.ID_BILL IN (1, 2, 3)" in result.sql_text


def test_build_release_notice_script_empty_bill_ids_warns_and_no_update():
    result = biv.build_release_notice_script([])
    assert result.bill_count == 0
    assert len(result.warnings) == 1
    assert "nothing to release" in result.warnings[0].lower()
    assert "UPDATE OUC_ADMIN.GCCOM_NOTICE_TMP" not in result.sql_text
    assert "Nothing to release." in result.sql_text


def test_build_release_notice_script_all_none_bill_ids_treated_as_empty():
    result = biv.build_release_notice_script([None, None])
    assert result.bill_count == 0
    assert len(result.warnings) == 1


def test_build_release_notice_script_clean_option_strips_comments():
    result = biv.build_release_notice_script([1073563109], clean=True)
    assert "--" not in result.sql_text
    assert "UPDATE OUC_ADMIN.GCCOM_NOTICE_TMP" in result.sql_text


def test_build_release_notice_script_does_not_touch_gccom_bill():
    result = biv.build_release_notice_script([1073563109])
    assert "UPDATE OUC_COMMON_ADMIN.GCCOM_BILL" not in result.sql_text


# ---------------- Case 1: New Contract Match ----------------
# RJ, 2026-09-14, later same day, own words + own exact SQL template - see
# the module's own "Case 1: New Contract Match" comment block for the full
# business meaning and the uniqueness-filter fix he explicitly asked for
# ("i did not check that it is the only bill in pending validation").


def test_build_new_contract_match_query_filters_rate_pending_invoicing():
    sql = biv.build_new_contract_match_query()
    assert "b.BILLING_STATUS = 'ESTFAC0012'" in sql
    assert "t.COD_STATUS = '5000NOTEMP'" in sql
    assert "cs.ID_OFFERED_SERVICE = 176" in sql


def test_build_new_contract_match_query_matches_contract_start_to_last_billing_date():
    sql = biv.build_new_contract_match_query()
    assert "b.LAST_BILLING_DATE = cs.FROM_DATE" in sql


def test_build_new_contract_match_query_requires_cycle_bill_and_active_contract():
    sql = biv.build_new_contract_match_query()
    assert "b.BILL_TYPE = 'TFGEN00001'" in sql
    assert "cs.STATUS = 'ESTSC00002'" in sql


def test_build_new_contract_match_query_joins_notice_bill_contract_payment_form():
    sql = biv.build_new_contract_match_query()
    assert "JOIN OUC_COMMON_ADMIN.GCCOM_BILL b ON b.ID_BILL = t.ID_BILL" in sql
    assert "JOIN OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE cs ON cs.ID_CONTRACTED_SERVICE = b.ID_CONTRACTED_SERVICE" in sql
    assert "JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM" in sql


def test_build_new_contract_match_query_adds_uniqueness_filter_rj_did_not_have():
    # RJ's own words: "i did not check that it is the only bill in
    # pending validation... add a filter for this cases" - the
    # pending_counts CTE + PENDING_COUNT = 1 guard is the fix.
    sql = biv.build_new_contract_match_query()
    assert "pending_counts AS (" in sql
    assert "COUNT(*) AS PENDING_COUNT" in sql
    assert "GROUP BY b2.ID_PAYMENT_FORM" in sql
    assert "pc.PENDING_COUNT = 1" in sql


def test_build_new_contract_match_query_pending_counts_scoped_by_account_only():
    # pending_counts (the "only bill in pending validation" guard) counts
    # every pending GCCOM_NOTICE_TMP row for the account, any period -
    # deliberately different scope than the NEW period_counts CTE below,
    # which counts bills sharing one specific billing period.
    sql = biv.build_new_contract_match_query()
    pending_counts_block = sql.split("period_counts AS (")[0]
    assert "GROUP BY b2.ID_PAYMENT_FORM, b2.ID_BILLING_PERIOD" not in pending_counts_block
    assert "GROUP BY b2.ID_PAYMENT_FORM" in pending_counts_block


def test_build_new_contract_match_query_requires_only_rate_bill_in_period():
    # RJ, real-data correction (account 1102978994): "it should only be
    # rate bill for that specific billing period" - a period_counts CTE,
    # same shape as Case 1 Stuck Bills' own, requiring the Rate bill be
    # the ONLY cycle+invoicing bill in its billing period.
    sql = biv.build_new_contract_match_query()
    assert "period_counts AS (" in sql
    period_counts_block = sql.split("period_counts AS (")[1].split("matched AS (")[0]
    assert "WHERE BILL_TYPE = 'TFGEN00001'" in period_counts_block
    assert "AND BILLING_STATUS = 'ESTFAC0012'" in period_counts_block
    assert "GROUP BY ID_PAYMENT_FORM, ID_BILLING_PERIOD" in period_counts_block
    assert "JOIN period_counts prc ON prc.ID_PAYMENT_FORM = b.ID_PAYMENT_FORM" in sql
    assert "prc.BILL_COUNT = 1" in sql


def test_build_new_contract_match_query_joins_lookup_for_status_description():
    sql = biv.build_new_contract_match_query()
    assert "LEFT JOIN OUC_ADMIN.GCCOM_BILL_STATUS bs ON bs.COD_DEVELOP = m.BILLING_STATUS" in sql
    assert "BILLING_STATUS_DESC" in sql


def test_build_new_contract_match_query_uses_top_clause_for_limit():
    sql = biv.build_new_contract_match_query(limit=500)
    assert "SELECT TOP (500)" in sql


def test_build_new_contract_match_query_no_limit_omits_top_clause():
    sql = biv.build_new_contract_match_query(limit=None)
    assert "TOP (" not in sql


def test_build_new_contract_match_query_orders_by_notice_update_date():
    sql = biv.build_new_contract_match_query()
    assert sql.strip().endswith("ORDER BY m.NOTICE_UPDATE_DATE;")


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


def test_build_terminated_period_mismatch_query_requires_pending_validation_notice():
    # RJ, 2026-09-17: "we are only checking where the accounts exists in
    # gccom_notic_tmp where it is in status pending validation" - confirmed
    # live, COD_DEVELOP 5000NOTEMP = NAME_TYPE "For validation" in GCCOM_
    # NOTICE_TMP_STATUS, the same status Case 1 already keys off. Scopes by
    # account (ID_PAYMENT_FORM), not by bill - a terminated account can have
    # several bills, unlike Case 1's single "the" bill.
    sql = biv.build_terminated_period_mismatch_query()
    assert "AND EXISTS (" in sql
    assert "SELECT 1 FROM OUC_ADMIN.GCCOM_NOTICE_TMP nt" in sql
    assert "JOIN OUC_COMMON_ADMIN.GCCOM_BILL nb ON nb.ID_BILL = nt.ID_BILL" in sql
    assert "WHERE nt.ID_PAYMENT_FORM = cs.ID_PAYMENT_FORM" in sql
    assert "AND nt.COD_STATUS = '5000NOTEMP'" in sql


def test_build_terminated_period_mismatch_query_notice_bill_must_still_be_invoicing():
    # RJ, 2026-09-17, real-data correction: account 3134 (REFERENCE
    # 1076362704) had a 5000NOTEMP notice but its OWN linked bill was
    # already ESTFAC0007 "Anulada" (voided) - a stale notice, not a live
    # "needs review" signal. The EXISTS must also require that notice's
    # linked bill still be ESTFAC0012 (invoicing), same combined pattern
    # Case 1 already uses (notice pending + bill invoicing).
    sql = biv.build_terminated_period_mismatch_query()
    assert "AND nb.BILLING_STATUS = 'ESTFAC0012'" in sql


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
    assert "AND b.BILLING_STATUS IN ('ESTFAC0012', 'ESTFAC0005')" in sql


def test_build_terminated_period_mismatch_query_matches_final_bill_by_date_only():
    sql = biv.build_terminated_period_mismatch_query()
    # 2026-09-13 real bug fix: GCCOM_CONTRACTED_SERVICE.END_DATE can carry
    # a non-midnight time-of-day component (confirmed live, ~59% of Rate/
    # 176 terminations in a 60-day sample) while GCCOM_BILL.BILLING_DATE
    # is always midnight - an exact datetime equality join could never
    # match those services' real final bill. Compares by DATE via a range
    # on the un-wrapped BILLING_DATE column (not a CAST() on it) so an
    # index on BILLING_DATE stays usable.
    assert "AND b.BILLING_DATE >= CAST(ts.END_DATE AS DATE)" in sql
    assert "AND b.BILLING_DATE < DATEADD(DAY, 1, CAST(ts.END_DATE AS DATE))" in sql
    assert "AND b.BILLING_DATE = ts.END_DATE" not in sql


def test_build_terminated_period_mismatch_query_requires_cycle_bill_type():
    sql = biv.build_terminated_period_mismatch_query()
    # RJ, 2026-09-13: "we need cycle bills TFGEN0001 something in
    # bill_type of gccom_bill" - a one-off charge bill (deposit,
    # reconnection fee, etc.) sharing the termination date shouldn't be
    # mistaken for the service's real final bill.
    assert "AND b.BILL_TYPE = 'TFGEN00001'" in sql


def test_build_terminated_period_mismatch_query_computes_target_period_per_account():
    sql = biv.build_terminated_period_mismatch_query()
    assert "MAX(ID_BILLING_PERIOD) OVER (PARTITION BY ID_PAYMENT_FORM) AS TARGET_PERIOD" in sql


def test_build_terminated_period_mismatch_query_flags_needs_update_including_no_bill_case():
    sql = biv.build_terminated_period_mismatch_query()
    assert "WHEN ID_BILL IS NULL THEN 1" in sql
    assert "AS NEEDS_UPDATE" in sql


def test_build_terminated_period_mismatch_query_needs_update_only_when_still_issuing():
    sql = biv.build_terminated_period_mismatch_query()
    # RJ, 2026-09-13, real account example (REFERENCE 1059650711): a
    # final bill already BILL_STATUS_INVOICED (ESTFAC0005) in a period
    # that disagrees with TARGET_PERIOD is NOT a needs-update case - the
    # bill's already gone out and can't be usefully "corrected". Only a
    # bill still ESTFAC0012 (in process) with a period mismatch counts.
    assert "WHEN ID_BILLING_PERIOD <> TARGET_PERIOD AND BILLING_STATUS = 'ESTFAC0012' THEN 1" in sql
    assert "WHEN ID_BILLING_PERIOD <> TARGET_PERIOD THEN 1 ELSE 0 END AS NEEDS_UPDATE" not in sql


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


# ---------------- Case 3: All Contract Status - Bills Complete ----------------
# RJ, 2026-09-14, own verbatim SQL + "repeat this for all the billing
# period of 2026 from gccom_billing_period, it is very important that you
# use the billing period 1 by 1 to obtain the correct result" - see the
# module's own Case 3 comment block (above CONTRACT_STATUS_ACTIVE) for the
# full translation/optimization rationale and the live equivalence check
# (2026-09-14: a literal per-period-IN(...) translation of RJ's own query
# and this module's optimized CTE rewrite returned byte-for-byte identical
# rows against the real tunnel DB).


def test_build_bills_complete_query_requires_year_or_period_ids():
    import pytest
    with pytest.raises(ValueError):
        biv.build_bills_complete_query(year=None, billing_period_ids=None)


def test_build_bills_complete_query_scopes_period_by_year_subquery():
    sql = biv.build_bills_complete_query(year=2026)
    assert "WHERE YEAR(INITIAL_DATE) = 2026" in sql
    assert "FROM OUC_COMMON_ADMIN.GCCOM_BILLING_PERIOD" in sql


def test_build_bills_complete_query_explicit_period_ids_override_year():
    sql = biv.build_bills_complete_query(year=2026, billing_period_ids=[10000000237])
    assert "B.ID_BILLING_PERIOD IN (10000000237)" in sql
    # No live subquery against GCCOM_BILLING_PERIOD when explicit ids are given.
    assert "WHERE YEAR(INITIAL_DATE)" not in sql


def test_build_bills_complete_query_all_contract_statuses_includes_terminated():
    # RJ's own cs subquery: STATUS in ('ESTSC00002','ESTSC00007','ESTSC00003','ESTSC00004')
    # - "All Contract Status" really does include Terminated (Baja).
    sql = biv.build_bills_complete_query(billing_period_ids=[10000000237])
    assert "STATUS IN ('ESTSC00002', 'ESTSC00007', 'ESTSC00003', 'ESTSC00004')" in sql


def test_build_bills_complete_query_with_active_contract_excludes_terminated():
    # RJ's own EXISTS CASE: WITH_ACTIVE_CONTRACT checks only ESTSC00002/7/3,
    # deliberately leaving out ESTSC00004 (Terminated).
    sql = biv.build_bills_complete_query(billing_period_ids=[10000000237])
    assert "STATUS IN ('ESTSC00002', 'ESTSC00007', 'ESTSC00003')\n" in sql
    assert "WITH_ACTIVE_CONTRACT" in sql


def test_build_bills_complete_query_requires_cycle_bill_type_and_issuing_status():
    sql = biv.build_bills_complete_query(billing_period_ids=[10000000237])
    assert "B.BILL_TYPE = 'TFGEN00001'" in sql
    assert "B.BILLING_STATUS = 'ESTFAC0012'" in sql


def test_build_bills_complete_query_requires_pending_notice():
    sql = biv.build_bills_complete_query(billing_period_ids=[10000000237])
    assert "tmp.COD_STATUS = '5000NOTEMP'" in sql
    assert "b.BILLING_STATUS = 'ESTFAC0012'" in sql


def test_build_bills_complete_query_missing_bills_is_contracted_minus_bills():
    sql = biv.build_bills_complete_query(billing_period_ids=[10000000237])
    assert "cc.CONTRACTED_SERVICES - pb.BILLS AS MISSING_BILLS" in sql


def test_build_bills_complete_query_equality_filter_keeps_only_complete_accounts():
    sql = biv.build_bills_complete_query(billing_period_ids=[10000000237])
    assert "WHERE cc.CONTRACTED_SERVICES = pb.BILLS" in sql


def test_build_bills_complete_query_active_contract_filter_true():
    sql = biv.build_bills_complete_query(billing_period_ids=[10000000237], with_active_contract=True)
    assert "AND f.WITH_ACTIVE_CONTRACT = 'YES'" in sql


def test_build_bills_complete_query_active_contract_filter_false():
    sql = biv.build_bills_complete_query(billing_period_ids=[10000000237], with_active_contract=False)
    assert "AND f.WITH_ACTIVE_CONTRACT = 'NO'" in sql


def test_build_bills_complete_query_no_active_contract_filter_by_default():
    sql = biv.build_bills_complete_query(billing_period_ids=[10000000237])
    assert "WITH_ACTIVE_CONTRACT = 'YES'" not in sql.split("WHERE 1 = 1")[-1]
    assert "WITH_ACTIVE_CONTRACT = 'NO'" not in sql.split("WHERE 1 = 1")[-1]


def test_build_bills_complete_query_uses_top_clause_for_limit():
    sql = biv.build_bills_complete_query(billing_period_ids=[10000000237], limit=500)
    assert "TOP (500)" in sql


def test_build_bills_complete_query_no_limit_omits_top_clause():
    sql = biv.build_bills_complete_query(billing_period_ids=[10000000237], limit=None)
    assert "TOP (" not in sql


def test_build_bills_complete_query_orders_by_period_and_reference():
    sql = biv.build_bills_complete_query(billing_period_ids=[10000000237])
    assert "ORDER BY f.ID_BILLING_PERIOD, f.REFERENCE;" in sql


def test_build_bills_complete_query_joins_payment_form_for_reference():
    sql = biv.build_bills_complete_query(billing_period_ids=[10000000237])
    assert "JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = cc.ID_PAYMENT_FORM" in sql


def test_build_bills_complete_query_left_joins_billing_period_for_name():
    sql = biv.build_bills_complete_query(billing_period_ids=[10000000237])
    assert "LEFT JOIN OUC_COMMON_ADMIN.GCCOM_BILLING_PERIOD bp ON bp.ID_BILLING_PERIOD = pb.ID_BILLING_PERIOD" in sql
    assert "bp.PERIOD_NAME AS BILLING_PERIOD_NAME" in sql


# ---------------- Case 4: Unclassified ----------------
# RJ, 2026-09-14 (same day), own words: "create a 4th case,
# 'Unclassified' those that are pending validation in notice TMP, and not
# in case 1, case 2, case 3, and any other case that we will add in the
# future." build_unclassified_query only builds the general pending-
# validation universe and its bill detail - the actual "not in Case
# 1/2/3" exclusion happens in web/server.py's case4/detect route (tested
# separately in test_web_api.py), so these tests only cover what this
# query builder itself is responsible for.


def test_build_unclassified_query_requires_pending_notice_on_issuing_bill():
    sql = biv.build_unclassified_query()
    assert "tmp.COD_STATUS = '5000NOTEMP'" in sql
    assert "b.BILLING_STATUS = 'ESTFAC0012'" in sql


def test_build_unclassified_query_selects_only_currently_issuing_bills():
    sql = biv.build_unclassified_query()
    assert "JOIN OUC_COMMON_ADMIN.GCCOM_BILL b ON b.ID_PAYMENT_FORM = pa.ID_PAYMENT_FORM\n  AND b.BILLING_STATUS = 'ESTFAC0012'" in sql


def test_build_unclassified_query_joins_payment_form_for_reference():
    sql = biv.build_unclassified_query()
    assert "JOIN OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM pf ON pf.ID_PAYMENT_FORM = pa.ID_PAYMENT_FORM" in sql


def test_build_unclassified_query_left_joins_lookup_descriptions():
    sql = biv.build_unclassified_query()
    assert "LEFT JOIN OUC_COMMON_ADMIN.GCCOM_COMPANY_OFFERED_SERVICE os ON os.ID_OFFERED_SERVICE = b.ID_OFFERED_SERVICE" in sql
    assert "LEFT JOIN OUC_ADMIN.GCCOM_BILL_STATUS bs ON bs.COD_DEVELOP = b.BILLING_STATUS" in sql


def test_build_unclassified_query_uses_top_clause_for_limit():
    sql = biv.build_unclassified_query(limit=500)
    assert "TOP (500)" in sql


def test_build_unclassified_query_no_limit_omits_top_clause():
    sql = biv.build_unclassified_query(limit=None)
    assert "TOP (" not in sql


def test_build_unclassified_query_orders_by_reference_and_period():
    sql = biv.build_unclassified_query()
    assert "ORDER BY pf.REFERENCE, b.ID_BILLING_PERIOD;" in sql
