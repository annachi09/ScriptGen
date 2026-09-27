"""
Unit tests for app/core/double_itb.py - pure SQL-text assertions (no DB).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import double_itb as d


def test_pairs_anomalous_with_billed_twin_on_same_service_and_dates():
    sql = d.build_double_itb_query()
    assert "WHERE a.STATUS = 'STTOBILL00'" in sql
    assert "b.STATUS = 'STTOBILL07'" in sql
    assert "b.ID_BILLING_SERVICE = a.ID_BILLING_SERVICE" in sql
    assert "b.INI_DATE = a.INI_DATE" in sql
    assert "b.END_DATE = a.END_DATE" in sql
    assert "b.ID_ITEM_TO_BILL <> a.ID_ITEM_TO_BILL" in sql


def test_ready_usage_summed_from_readings_items_to_bill_for_anomalous_item():
    sql = d.build_double_itb_query()
    assert "FROM OUC_ADMIN.GCCOM_READINGS_ITEMSTOBILL rit" in sql
    assert "WHERE rit.ID_ITEM_TO_BILL = a.ID_ITEM_TO_BILL" in sql
    assert "SUM(rit.READY_USAGE) AS READY_USAGE" in sql


def test_needs_rebilling_flag():
    sql = d.build_double_itb_query()
    assert "CASE WHEN ISNULL(ru.READY_USAGE, 0) <> 0 THEN 1 ELSE 0 END AS NEEDS_REBILLING" in sql


def test_niss_and_billing_period_in_words():
    sql = d.build_double_itb_query()
    assert "ss.NISS AS NISS" in sql
    assert "COALESCE(dict_bp.text, bp.DESCRIPTION) AS BILLING_PERIOD" in sql
    assert "JOIN GCCOM_CONTRACTED_SERVICE cs ON cs.ID_CONTRACTED_SERVICE = bs.ID_CONTRACTED_SERVICE" in sql
