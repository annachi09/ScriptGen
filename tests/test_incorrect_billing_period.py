"""
Unit tests for app/core/incorrect_billing_period.py - pure SQL-text
assertions, same style as test_bill_issuance_validator.py (no real DB).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import incorrect_billing_period as ibp


# ---------------- build_detect_query ----------------

def test_build_detect_query_where_clause_matches_rj_exact_sql():
    sql = ibp.build_detect_query()
    assert "WHERE AN.LAST_BILLING_DATE > AN.BILLING_DATE" in sql
    assert "AN.ANOMALOUS_STATUS NOT IN ('ESTAN00004', 'ESTAN00005', 'ESTAN00008', 'ESTAN00003')" in sql


def test_build_detect_query_join_chain_matches_rj_exact_sql():
    sql = ibp.build_detect_query()
    assert "FROM OUC_ADMIN.GCCOM_ANOMALOUS AN" in sql
    assert "JOIN GCCOM_BILLING_SERVICE BS ON BS.ID_BILLING_SERVICE = AN.ID_BILLING_SERVICE" in sql
    assert "JOIN GCCOM_CONTRACTED_SERVICE CS ON CS.ID_CONTRACTED_SERVICE = BS.ID_CONTRACTED_SERVICE" in sql
    assert "JOIN GCCOM_SECTOR_SUPPLY SS ON SS.ID_SECTOR_SUPPLY = CS.ID_SECTOR_SUPPLY" in sql
    assert "LEFT JOIN OUC_ADMIN.GCCOM_ITEMS_TO_BILL ITB ON ITB.ID_ITEM_TO_BILL = AN.ID_ITEM_TO_BILL" in sql
    assert "LEFT JOIN OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL RIT ON RIT.ID_ITEM_TO_BILL = ITB.ID_ITEM_TO_BILL" in sql
    assert "LEFT JOIN OUC_COMMON_ADMIN.GCGT_RE_READING RR ON RR.ID_READING = RIT.ID_READING" in sql
    assert "LEFT JOIN GCCOM_SECTOR_SUPPLY SS1 ON SS1.ID_SECTOR_SUPPLY = RR.ID_SECTOR_SUPPLY" in sql
    assert (
        "LEFT JOIN GCCOM_CONTRACTED_SERVICE CS1 ON CS1.ID_SECTOR_SUPPLY = RR.ID_SECTOR_SUPPLY "
        "AND RR.READING_DATE = CS1.END_DATE" in sql
    )
    assert "LEFT JOIN GCCOM_BILLING_SERVICE BS1 ON BS1.ID_CONTRACTED_SERVICE = CS1.ID_CONTRACTED_SERVICE" in sql


def test_build_detect_query_aliases_avoid_duplicate_column_names():
    # AN/ITB/BS1 all have their own ID_BILLING_SERVICE, SS/SS1 both have
    # NISS - see module docstring's "collide on column name" note.
    sql = ibp.build_detect_query()
    assert "AN.ID_BILLING_SERVICE AS ANOMALY_BILLING_SERVICE" in sql
    assert "ITB.ID_BILLING_SERVICE AS ITEM_BILLING_SERVICE" in sql
    assert "BS1.ID_BILLING_SERVICE AS NEXT_BILLING_SERVICE" in sql
    assert "SS.NISS" in sql
    assert "SS1.NISS AS NISS_AT_READING" in sql


def test_build_detect_query_enrichment_joins_account_and_service_desc():
    sql = ibp.build_detect_query()
    assert "LEFT JOIN GCCOM_PAYMENT_FORM PF ON PF.ID_PAYMENT_FORM = CS.ID_PAYMENT_FORM" in sql
    assert "PF.REFERENCE AS ACCOUNT" in sql
    assert "LEFT JOIN OUC_COMMON_ADMIN.GCCOM_COMPANY_OFFERED_SERVICE OS ON OS.ID_OFFERED_SERVICE = CS.ID_OFFERED_SERVICE" in sql
    assert "OS.NAME_TYPE AS OFFERED_SERVICE_DESC" in sql


def test_build_detect_query_orders_by_offered_service_then_anomaly_id():
    sql = ibp.build_detect_query()
    assert sql.strip().endswith("ORDER BY CS.ID_OFFERED_SERVICE, AN.ID_ANOMALOUS;")


def test_build_detect_query_top_clause_from_limit():
    sql = ibp.build_detect_query(limit=500)
    assert "SELECT TOP (500)" in sql


def test_build_detect_query_no_top_clause_when_limit_none():
    sql = ibp.build_detect_query(limit=None)
    assert "TOP (" not in sql


def test_build_detect_query_excluded_statuses_overridable():
    sql = ibp.build_detect_query(excluded_statuses=("ESTAN00099",))
    assert "AN.ANOMALOUS_STATUS NOT IN ('ESTAN00099')" in sql
    assert "ESTAN00004" not in sql


# ---------------- build_correction_script ----------------

def test_build_correction_script_matches_rj_exact_template():
    result = ibp.build_correction_script([12345])
    sql = result.sql_text
    assert "UPDATE OUC_ADMIN.GCCOM_ANOMALOUS" in sql
    assert "UPDATE_DATE = GETDATE()" in sql
    assert "ANOMALOUS_STATUS = 'ESTAN00005'" in sql
    assert "WHERE ID_ANOMALOUS IN (12345)" in sql
    assert "ANOMALOUS_STATUS IN ('ESTAN00001', 'ESTAN00009')" in sql


def test_build_correction_script_default_audit_values_match_rj_template():
    result = ibp.build_correction_script([12345])
    assert "UPDATE_USER = 'RMA'" in result.sql_text
    assert "UPDATE_PROGRAM = 'RATE_INCORRECT_BILLPERIOD'" in result.sql_text


def test_build_correction_script_overridable_program_and_user():
    result = ibp.build_correction_script([12345], program="JIRA-1", user="ANALYST1")
    assert "UPDATE_USER = 'ANALYST1'" in result.sql_text
    assert "UPDATE_PROGRAM = 'JIRA-1'" in result.sql_text


def test_build_correction_script_single_statement_batched_in_list():
    result = ibp.build_correction_script([1, 2, 3])
    assert result.anomaly_count == 3
    assert result.sql_text.count("UPDATE OUC_ADMIN.GCCOM_ANOMALOUS") == 1
    assert "ID_ANOMALOUS IN (1, 2, 3)" in result.sql_text


def test_build_correction_script_dedupes_and_drops_none_preserving_order():
    result = ibp.build_correction_script([1, None, 2, 1, 2, 3])
    assert result.anomaly_count == 3
    assert "ID_ANOMALOUS IN (1, 2, 3)" in result.sql_text


def test_build_correction_script_empty_ids_warns_and_no_update():
    result = ibp.build_correction_script([])
    assert result.anomaly_count == 0
    assert len(result.warnings) == 1
    assert "nothing to correct" in result.warnings[0].lower()
    assert "UPDATE OUC_ADMIN.GCCOM_ANOMALOUS" not in result.sql_text


def test_build_correction_script_clean_option_strips_comments():
    result = ibp.build_correction_script([12345], clean=True)
    assert "--" not in result.sql_text
    assert "UPDATE OUC_ADMIN.GCCOM_ANOMALOUS" in result.sql_text
