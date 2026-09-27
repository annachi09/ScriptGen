"""
Unit tests for app/core/disconnection_tnb.py - pure SQL-text assertions
(no real DB).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import disconnection_tnb as dt


def test_filters_disconnection_tnb_with_ready_usage():
    sql = dt.build_disconnection_tnb_query()
    assert "r.READING_TYPE = 'TIPTL00010'" in sql
    assert "r.READ_STATUS = '8000STSRED'" in sql
    assert "ISNULL(r.READY_USAGE, 0) <> 0" in sql


def test_requested_columns_present():
    sql = dt.build_disconnection_tnb_query()
    for alias in ("NISS", "BILLING_PERIOD", "READING_PREV_DATE", "READING_DATE", "PREV_VALUE",
                  "VALUE", "READING_USAGE", "CORRECTED_USAGE", "READY_USAGE", "READ_STATUS", "IND_ESTIMATE"):
        assert f"AS {alias}" in sql


def test_nearest_contract_same_sector_supply_by_end_date():
    sql = dt.build_disconnection_tnb_query()
    assert "OUTER APPLY (" in sql
    assert "SELECT TOP 1 cs.ID_CONTRACTED_SERVICE" in sql
    assert "FROM OUC_COMMON_ADMIN.GCCOM_CONTRACTED_SERVICE cs" in sql
    assert "WHERE cs.ID_SECTOR_SUPPLY = r.ID_SECTOR_SUPPLY" in sql
    assert "ORDER BY CASE WHEN cs.END_DATE IS NULL THEN 1 ELSE 0 END" in sql
    assert "ABS(DATEDIFF(day, cs.END_DATE, r.READING_DATE))" in sql


def test_contract_columns_and_day_gap():
    sql = dt.build_disconnection_tnb_query()
    assert "ncs.END_DATE AS CONTRACT_END_DATE" in sql
    assert "ncs.ID_CONTRACTED_SERVICE AS ID_CONTRACTED_SERVICE" in sql
    assert "DATEDIFF(day, ncs.END_DATE, r.READING_DATE) AS DAYS_FROM_END" in sql


def test_english_descriptions():
    sql = dt.build_disconnection_tnb_query()
    assert "dict_bp.id = bp.PERIOD_NAME_XI18N AND dict_bp.locale = 'EN'" in sql
    assert "dict_st.id = st.DESCRIPTION_XI18N AND dict_st.locale = 'EN'" in sql
