"""
Unit tests for app/core/reading_validation.py - pure SQL-text assertions,
same style as test_bill_issuance_validator.py / test_incorrect_billing_
period.py (no real DB).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import reading_validation as rv


# ---------------- build_readings_query ----------------

def test_build_readings_query_filters_on_sector_supply():
    sql = rv.build_readings_query(12345)
    assert "WHERE r.id_sector_supply = 12345" in sql


def test_build_readings_query_join_chain_matches_rj_exact_sql():
    sql = rv.build_readings_query(1)
    assert "FROM gcgt_re_reading r" in sql
    assert "INNER JOIN gcgt_re_reading_type rtype ON rtype.cod_develop = r.reading_type" in sql
    assert "INNER JOIN gccom_consum_type consum ON consum.cod_develop = r.usage_type" in sql
    assert "INNER JOIN gcgt_re_read_status st ON st.cod_develop = r.read_status" in sql
    assert "LEFT JOIN GCGT_RE_READING_SOURCE_TYPE rst ON rst.COD_DEVELOP = r.READING_SOURCE" in sql
    assert "LEFT JOIN gccom_sector_supply ss ON ss.id_sector_supply = r.id_sector_supply" in sql
    assert "LEFT JOIN gcgt_me_device d ON d.id_device = r.id_device" in sql
    assert "LEFT JOIN gcgt_me_device_model MOD ON MOD.id_model = d.id_model" in sql
    assert "LEFT JOIN gccom_units u ON u.cod_develop = r.reading_unit" in sql
    assert "LEFT JOIN gccom_units cu ON cu.cod_develop = r.corrected_usage_unit" in sql
    assert "LEFT JOIN gccom_units bu ON bu.cod_develop = r.usage_unit" in sql
    assert "LEFT JOIN gccom_billing_period bp ON bp.id_billing_period = r.ID_BILLING_PERIOD" in sql
    assert "LEFT JOIN gcgt_re_route rt ON rt.id_route = r.ID_ROUTE" in sql
    assert "LEFT JOIN gcgt_re_reader rr ON rr.ID_READER = rt.ID_READER" in sql
    assert "LEFT JOIN gcxs_users_shadow usd ON CONVERT(varchar(10), usd.USER_ID) = r.UPDATE_USER" in sql
    assert "LEFT JOIN gccb_employee e ON e.id_employee_cb = r.digitizer" in sql


def test_build_readings_query_dedupes_sector_supply_alias_collision():
    # RJ's own SELECT list has both r.id_sector_supply and ss.id_sector_
    # supply unaliased - a real collision under this app's dict(zip(
    # columns, row)) row-shaping. The second one must get its own alias.
    sql = rv.build_readings_query(1)
    assert "r.id_sector_supply AS id_sector_supply,\n" in sql
    assert "ss.id_sector_supply AS ss_id_sector_supply" in sql


def test_build_readings_query_reproduces_update_user_coalesce():
    sql = rv.build_readings_query(1)
    assert "COALESCE(" in sql
    assert "WHERE RACF_USERS_CODE = usd.RACF_USERS_CODE" in sql
    assert "R.UPDATE_USER" in sql


def test_build_readings_query_reproduces_power_factor_subquery_verbatim():
    sql = rv.build_readings_query(1)
    assert "OUC_COMMON_ADMIN.GCCOM_READING_FACTOR rf" in sql
    assert "OUC_COMMON_ADMIN.GCCOM_READING_FACTOR rf2" in sql
    assert "RF.FACTOR_TYPE = '8000FCTTYP'" in sql
    assert "AS power_factor" in sql


def test_build_readings_query_selects_id_last_reading():
    sql = rv.build_readings_query(1)
    assert "r.id_last_reading AS id_last_reading" in sql


def test_build_readings_query_gcts_dictionary_english_joins():
    sql = rv.build_readings_query(1)
    assert "LEFT JOIN GCTS_DICTIONARY dict_rtype ON dict_rtype.id = rtype.description_xi18n AND dict_rtype.locale = 'EN'" in sql
    assert "LEFT JOIN GCTS_DICTIONARY dict_consum ON dict_consum.id = consum.name_type_xi18n AND dict_consum.locale = 'EN'" in sql
    assert "LEFT JOIN GCTS_DICTIONARY dict_st ON dict_st.id = st.description_xi18n AND dict_st.locale = 'EN'" in sql
    assert "LEFT JOIN GCTS_DICTIONARY dict_u ON dict_u.id = u.name_type_xi18n AND dict_u.locale = 'EN'" in sql
    assert "LEFT JOIN GCTS_DICTIONARY dict_cu ON dict_cu.id = cu.name_type_xi18n AND dict_cu.locale = 'EN'" in sql
    assert "LEFT JOIN GCTS_DICTIONARY dict_bu ON dict_bu.id = bu.name_type_xi18n AND dict_bu.locale = 'EN'" in sql
    assert "LEFT JOIN GCTS_DICTIONARY dict_bp ON dict_bp.id = bp.period_name_xi18n AND dict_bp.locale = 'EN'" in sql
    assert "COALESCE(dict_rtype.text, rtype.description) AS reading_type" in sql
    assert "COALESCE(dict_consum.text, consum.name_type) AS usage_name" in sql
    assert "COALESCE(dict_st.text, st.description) AS read_status" in sql
    assert "COALESCE(dict_u.text, u.name_type) AS reading_unit" in sql
    assert "COALESCE(dict_cu.text, cu.name_type) AS corrected_unit" in sql
    assert "COALESCE(dict_bu.text, bu.name_type) AS bill_unit" in sql
    assert "COALESCE(dict_bp.text, bp.description) AS billing_period" in sql


def test_build_readings_query_optional_measuring_point_filter():
    sql = rv.build_readings_query(1, id_measuring_point=99)
    assert "r.id_measuring_point = 99" in sql


def test_build_readings_query_no_measuring_point_filter_when_none():
    sql = rv.build_readings_query(1)
    assert "id_measuring_point =" not in sql


def test_build_readings_query_optional_device_filter():
    sql = rv.build_readings_query(1, id_device=55)
    assert "r.id_device = 55" in sql


def test_build_readings_query_excludes_load_curve_consumption():
    sql = rv.build_readings_query(1)
    assert "(consum.ind_load_curve IS NULL OR consum.ind_load_curve = 0)" in sql


def test_build_readings_query_orders_matches_rj_exact_sql():
    sql = rv.build_readings_query(1)
    assert "ORDER BY r.reading_date DESC, T.max_date DESC, r.reading_time_ts DESC," in sql
    assert "d.comp_serial_num DESC, r.usage_type" in sql


# ---------------- build_account_niss_query ----------------

def test_build_account_niss_query_filters_on_account():
    sql = rv.build_account_niss_query("ACC-123")
    assert "WHERE PF.REFERENCE = 'ACC-123'" in sql


def test_build_account_niss_query_excludes_dead_contracts():
    sql = rv.build_account_niss_query("ACC-123")
    assert "CS.STATUS <> 'ESTSC00005'" in sql


def test_build_account_niss_query_join_chain():
    sql = rv.build_account_niss_query("ACC-123")
    assert "FROM OUC_COMMON_ADMIN.GCCOM_PAYMENT_FORM PF" in sql
    assert "JOIN GCCOM_CONTRACTED_SERVICE CS ON CS.ID_PAYMENT_FORM = PF.ID_PAYMENT_FORM" in sql
    assert "JOIN GCCOM_SECTOR_SUPPLY SS ON SS.ID_SECTOR_SUPPLY = CS.ID_SECTOR_SUPPLY" in sql
    assert "LEFT JOIN OUC_COMMON_ADMIN.GCCOM_COMPANY_OFFERED_SERVICE OS ON OS.ID_OFFERED_SERVICE = CS.ID_OFFERED_SERVICE" in sql


def test_build_account_niss_query_selects_niss_and_offered_service_desc():
    sql = rv.build_account_niss_query("ACC-123")
    assert "SS.NISS" in sql
    assert "OS.NAME_TYPE AS OFFERED_SERVICE_DESC" in sql
    assert "PF.REFERENCE AS ACCOUNT" in sql


def test_build_account_niss_query_escapes_quotes():
    sql = rv.build_account_niss_query("O'Brien")
    assert "O''Brien" in sql


# ---------------- reading_type_code column (raw code for editing) -----

def test_build_readings_query_selects_raw_reading_type_code():
    sql = rv.build_readings_query(1)
    assert "r.reading_type AS reading_type_code" in sql


# ---------------- build_reading_types_query ----------------

def test_build_reading_types_query_selects_code_and_english_description():
    sql = rv.build_reading_types_query()
    assert "rtype.cod_develop AS code" in sql
    assert "COALESCE(dict_rtype.text, rtype.description) AS description" in sql
    assert "FROM gcgt_re_reading_type rtype" in sql
    assert "dict_rtype.locale = 'EN'" in sql


# ---------------- EDITABLE_COLUMNS / CASCADING_COLUMNS ----------------

def test_editable_columns_has_exactly_the_eight_rj_asked_for():
    assert set(rv.EDITABLE_COLUMNS.keys()) == {
        "reading_prev_date", "reading_date", "reading_type",
        "prev_value", "reading", "metered_usage", "corrected_usage", "bill_ready_usage",
    }


def test_editable_columns_map_to_real_db_column_names():
    assert rv.EDITABLE_COLUMNS["reading_prev_date"] == ("READING_PREV_DATE", "datetime")
    assert rv.EDITABLE_COLUMNS["reading_date"] == ("READING_DATE", "datetime")
    assert rv.EDITABLE_COLUMNS["reading_type"] == ("READING_TYPE", "code")
    assert rv.EDITABLE_COLUMNS["prev_value"] == ("PREV_VALUE", "decimal")
    assert rv.EDITABLE_COLUMNS["reading"] == ("VALUE", "decimal")
    assert rv.EDITABLE_COLUMNS["metered_usage"] == ("READING_USAGE", "decimal")
    assert rv.EDITABLE_COLUMNS["corrected_usage"] == ("CORRECTED_USAGE", "decimal")
    assert rv.EDITABLE_COLUMNS["bill_ready_usage"] == ("READY_USAGE", "decimal")


def test_cascading_columns_are_only_the_dates_and_reading_values():
    assert rv.CASCADING_COLUMNS == {"reading_prev_date", "reading_date", "prev_value", "reading"}
    # reading_type/the usage columns never cascade
    assert "reading_type" not in rv.CASCADING_COLUMNS
    assert "metered_usage" not in rv.CASCADING_COLUMNS


# ---------------- coerce_edit_value ----------------

def test_coerce_edit_value_decimal():
    import decimal
    assert rv.coerce_edit_value("decimal", "41") == decimal.Decimal("41")
    assert rv.coerce_edit_value("decimal", "41.5") == decimal.Decimal("41.5")


def test_coerce_edit_value_decimal_falls_back_to_raw_on_bad_input():
    assert rv.coerce_edit_value("decimal", "not-a-number") == "not-a-number"


def test_coerce_edit_value_datetime_full_timestamp():
    import datetime
    assert rv.coerce_edit_value("datetime", "2026-08-15 00:00:00") == datetime.datetime(2026, 8, 15, 0, 0, 0)


def test_coerce_edit_value_datetime_date_only():
    import datetime
    assert rv.coerce_edit_value("datetime", "2026-08-15") == datetime.datetime(2026, 8, 15)


def test_coerce_edit_value_datetime_falls_back_to_raw_on_bad_input():
    assert rv.coerce_edit_value("datetime", "not-a-date") == "not-a-date"


def test_coerce_edit_value_code_kept_as_raw_string():
    assert rv.coerce_edit_value("code", "TPREAD0001") == "TPREAD0001"


def test_coerce_edit_value_empty_string_is_none():
    assert rv.coerce_edit_value("decimal", "") is None
    assert rv.coerce_edit_value("decimal", "   ") is None


# ---------------- usage multiplier (auto-calc usage columns) ---------

def test_build_readings_query_selects_usage_multiplier():
    sql = rv.build_readings_query(1)
    assert "umult.MULTIPLIER AS usage_multiplier" in sql


def test_build_readings_query_joins_usage_type_meter_on_device_and_usage_type():
    sql = rv.build_readings_query(1)
    assert "LEFT JOIN GCGT_ME_USAGE_TYPE_METER umult ON umult.ID_DEVICE = r.id_device AND umult.COD_USAGE_TYPE = r.usage_type" in sql
