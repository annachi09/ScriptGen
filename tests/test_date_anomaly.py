"""
Pure-logic tests for app.core.date_anomaly - the "Diff Date Anomaly"
detection query builders, XML date patcher, and correction-script
assembler. No database involved: every DB-shaped input here (row
dicts, id->id maps, xml text) is exactly what app/ui/main_window.py
would have already fetched via app/db/mssql before calling into this
module - see that module's own docstring for why it's split this way.
"""
import datetime
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import date_anomaly as da


# ---------------------------------------------------------------------
# Query builders
# ---------------------------------------------------------------------

def test_build_detect_query_contains_expected_filters():
    sql = da.build_detect_query("10450618-301", 10000000230)
    assert "GCGT_RE_READING" in sql
    assert "GCCOM_SECTOR_SUPPLY" in sql
    assert "N'10450618-301'" in sql
    assert "10000000230" in sql
    assert da.READ_STATUS_ANOMALY in sql
    assert da.READING_TYPE_EXCLUDED in sql
    # READ_STATUS_BILLED should NOT leak into the anomaly-detection query
    assert da.READ_STATUS_BILLED not in sql


def test_build_detect_query_escapes_quotes_in_niss():
    sql = da.build_detect_query("abc'--", 0)
    # format_sql_literal doubles embedded single quotes - a raw f-string
    # interpolation would have let this break out of the literal instead.
    assert "N'abc''--'" in sql


def test_build_correct_date_query_uses_billed_status_and_top_1():
    sql = da.build_correct_date_query("10450618-301", 10000000230)
    assert "TOP 1" in sql
    assert da.READ_STATUS_BILLED in sql
    assert da.READ_STATUS_ANOMALY not in sql


def test_build_item_to_bill_query_empty_returns_none():
    assert da.build_item_to_bill_query([]) is None


def test_build_item_to_bill_query_lists_ids():
    sql = da.build_item_to_bill_query([1044166372, 1044166373])
    assert "GCCOM_READINGS_ITEMSTOBILL" in sql
    assert "1044166372" in sql
    assert "1044166373" in sql


def test_build_xml_lookup_query_empty_returns_none():
    assert da.build_xml_lookup_query([]) is None


def test_build_xml_lookup_query_lists_ids():
    sql = da.build_xml_lookup_query([1034594394])
    assert "GCCOM_ITEMS_TO_BILL_XML" in sql
    assert "1034594394" in sql


# ---------------------------------------------------------------------
# XML date patching
# ---------------------------------------------------------------------

def test_patch_xml_dates_date_only_format():
    xml_text = "<Root><initDate>2024-01-15</initDate><readingFromDate>2024-01-15</readingFromDate></Root>"
    patched, changed = da.patch_xml_dates(xml_text, datetime.date(2024, 3, 1))
    assert set(changed) == {"initDate", "readingFromDate"}
    root = ET.fromstring(patched)
    assert root.find("initDate").text == "2024-03-01"
    assert root.find("readingFromDate").text == "2024-03-01"


def test_patch_xml_dates_preserves_datetime_format_and_separator():
    xml_text = "<Root><initDate>2024-01-15T00:00:00</initDate></Root>"
    patched, changed = da.patch_xml_dates(xml_text, datetime.datetime(2024, 3, 1, 0, 0, 0))
    assert changed == ["initDate"]
    root = ET.fromstring(patched)
    assert root.find("initDate").text == "2024-03-01T00:00:00"


def test_patch_xml_dates_works_through_a_default_namespace():
    xml_text = (
        '<Root xmlns="urn:example:bill">'
        "<Details><initDate>2024-01-15</initDate></Details>"
        "</Root>"
    )
    patched, changed = da.patch_xml_dates(xml_text, datetime.date(2024, 3, 1))
    assert changed == ["initDate"]
    assert "2024-03-01" in patched


def test_patch_xml_dates_no_matching_nodes():
    xml_text = "<Root><somethingElse>2024-01-15</somethingElse></Root>"
    patched, changed = da.patch_xml_dates(xml_text, datetime.date(2024, 3, 1))
    assert changed == []


def test_patch_xml_dates_malformed_raises_parse_error():
    with pytest.raises(ET.ParseError):
        da.patch_xml_dates("<Root><initDate>2024-01-15</Root>", datetime.date(2024, 3, 1))


# ---------------------------------------------------------------------
# Correction script assembly
# ---------------------------------------------------------------------

def _base_kwargs(**overrides):
    kwargs = dict(
        niss="10450618-301",
        threshold=10000000230,
        correct_date=datetime.date(2024, 3, 1),
        correct_date_source_reading=999,
        anomaly_id_readings=[101, 102],
        item_to_bill_map={101: [500], 102: [500]},  # both readings roll up to the same bill item
        xml_rows={500: "<Root><initDate>2024-01-01</initDate><readingFromDate>2024-01-01</readingFromDate></Root>"},
    )
    kwargs.update(overrides)
    return kwargs


def test_build_correction_script_basic_counts():
    result = da.build_correction_script(**_base_kwargs())
    assert result.reading_count == 2
    assert result.item_count == 1  # deduped - both readings map to item 500
    assert result.xml_count == 1
    assert result.statement_count == 4
    assert result.warning_count == 0
    assert "READING_PREV_DATE" in result.sql_text
    assert "INI_DATE" in result.sql_text
    assert "INIT_DATE" not in result.sql_text  # the real column has no T - see INI_DATE_COLUMN's comment
    assert "XML_TO_BILL" in result.sql_text
    assert "WHERE ID_READING = 101" in result.sql_text
    assert "WHERE ID_READING = 102" in result.sql_text
    assert "WHERE ID_ITEM_TO_BILL = 500" in result.sql_text
    assert "WHERE ID_XML = 500" in result.sql_text
    assert "BEGIN TRANSACTION" not in result.sql_text  # removed per explicit analyst request


def test_build_correction_script_without_audit_columns_omits_them():
    result = da.build_correction_script(**_base_kwargs())
    assert "update_program" not in result.sql_text
    assert "update_user" not in result.sql_text


def test_build_correction_script_with_audit_columns_included():
    result = da.build_correction_script(
        **_base_kwargs(reading_has_audit_cols=True, item_has_audit_cols=True, program="JIRA-1234")
    )
    assert result.sql_text.count("update_program") == 2  # one per audited table (reading + item)
    assert "JIRA-1234" in result.sql_text


def test_build_correction_script_flags_unmapped_reading():
    result = da.build_correction_script(
        **_base_kwargs(anomaly_id_readings=[101, 102, 103], item_to_bill_map={101: [500], 102: [500]})
    )
    assert result.reading_count == 3  # reading 103 still gets its own READING_PREV_DATE fix
    assert result.item_count == 1     # but contributes no item/xml statement
    assert any("no GCCOM_READINGS_ITEMSTOBILL entry" in w for w in result.warnings)


def test_build_correction_script_unmapped_reading_treats_empty_list_like_missing_key():
    # A reading present in the map but with an empty list (e.g. a lookup
    # that ran but found nothing) must be treated the same as a reading
    # absent from the map entirely - both mean "no item-to-bill link yet."
    result = da.build_correction_script(
        **_base_kwargs(anomaly_id_readings=[101, 102], item_to_bill_map={101: [500], 102: []})
    )
    assert result.item_count == 1
    assert any("no GCCOM_READINGS_ITEMSTOBILL entry" in w for w in result.warnings)


def test_build_correction_script_one_reading_maps_to_multiple_items():
    # The real-world case this was fixed for: a single anomalous reading
    # rolls up to MORE than one item-to-bill row (e.g. separate
    # energy/demand billing items generated from the same reading). Both
    # items must get their own INI_DATE/XML correction, not just the
    # first one seen.
    result = da.build_correction_script(
        **_base_kwargs(
            anomaly_id_readings=[101],
            item_to_bill_map={101: [500, 501]},
            xml_rows={
                500: "<Root><initDate>2024-01-01</initDate></Root>",
                501: "<Root><initDate>2024-01-01</initDate></Root>",
            },
        )
    )
    assert result.reading_count == 1
    assert result.item_count == 2
    assert result.xml_count == 2
    assert "WHERE ID_ITEM_TO_BILL = 500" in result.sql_text
    assert "WHERE ID_ITEM_TO_BILL = 501" in result.sql_text
    assert "WHERE ID_XML = 500" in result.sql_text
    assert "WHERE ID_XML = 501" in result.sql_text
    assert result.warning_count == 0


def test_build_correction_script_flags_missing_xml_row():
    result = da.build_correction_script(**_base_kwargs(xml_rows={}))
    assert result.xml_count == 0
    assert any("GCCOM_ITEMS_TO_BILL_XML row" in w for w in result.warnings)


# ---------------------------------------------------------------------
# Part 5: GCCOM_ANOMALOUS cancellation
# ---------------------------------------------------------------------

def test_build_anomalous_query_empty_returns_none():
    assert da.build_anomalous_query([]) is None


def test_build_anomalous_query_filters_open_statuses():
    sql = da.build_anomalous_query([1372865])
    assert "GCCOM_ANOMALOUS" in sql
    assert "1372865" in sql
    assert "ESTAN00009" in sql
    assert "ESTAN00001" in sql
    # The already-cancelled status must never appear as a filter value -
    # this query finds what STILL needs cancelling, not what's done.
    assert sql.count("ESTAN00005") == 0


def test_build_correction_script_cancels_open_anomalies_for_touched_items():
    result = da.build_correction_script(**_base_kwargs(anomalous_item_ids=[500]))
    assert result.anomalous_count == 1
    assert result.statement_count == 5  # 2 reading + 1 item + 1 xml + 1 anomalous
    assert "GCCOM_ANOMALOUS" in result.sql_text
    assert "WHERE ID_ITEM_TO_BILL = 500" in result.sql_text
    assert "ANOMALOUS_STATUS = N'ESTAN00005'" in result.sql_text  # format_sql_literal prefixes string literals with N
    assert "ESTAN00009" in result.sql_text and "ESTAN00001" in result.sql_text


def test_build_correction_script_no_anomalous_ids_means_no_part_5():
    result = da.build_correction_script(**_base_kwargs())  # anomalous_item_ids defaults to ()
    assert result.anomalous_count == 0
    # The header still names Part 5 with a "0 statement(s)" count (same
    # as the other parts always do), but there must be no actual body
    # section or UPDATE statement for it.
    assert "Part 5 (GCCOM_ANOMALOUS cancellation): 0 statement(s)" in result.sql_text
    assert "=== Part 5" not in result.sql_text
    assert "UPDATE " + da._qualified(da.ADMIN_SCHEMA, da.ANOMALOUS_TABLE) not in result.sql_text


def test_build_correction_script_anomalous_item_not_in_seen_items_is_ignored():
    # Defensive scope check (see build_correction_script's Part 5
    # docstring note): an id that never showed up in item_to_bill_map at
    # all must not get a Part 5 statement, even if the caller somehow
    # passed it in anomalous_item_ids.
    result = da.build_correction_script(**_base_kwargs(anomalous_item_ids=[999999]))
    assert result.anomalous_count == 0
    assert "999999" not in result.sql_text


def test_build_correction_script_multiple_items_each_get_own_anomalous_statement():
    result = da.build_correction_script(
        **_base_kwargs(
            anomaly_id_readings=[101],
            item_to_bill_map={101: [500, 501]},
            xml_rows={
                500: "<Root><initDate>2024-01-01</initDate></Root>",
                501: "<Root><initDate>2024-01-01</initDate></Root>",
            },
            anomalous_item_ids=[500, 501],
        )
    )
    assert result.anomalous_count == 2
    assert "WHERE ID_ITEM_TO_BILL = 500" in result.sql_text
    assert "WHERE ID_ITEM_TO_BILL = 501" in result.sql_text
    assert result.sql_text.count("ANOMALOUS_STATUS = N'ESTAN00005'") == 2


def test_build_correction_script_flags_xml_with_no_date_nodes():
    result = da.build_correction_script(
        **_base_kwargs(xml_rows={500: "<Root><somethingElse>x</somethingElse></Root>"})
    )
    assert result.xml_count == 0
    assert any("nothing changed" in w for w in result.warnings)


def test_build_correction_script_applies_single_correct_date_to_all_readings():
    # Confirms the literal reading (per the functional spec): ONE
    # correct_date value, applied to every anomalous reading's
    # READING_PREV_DATE - not a per-row "nearest prior billed reading."
    result = da.build_correction_script(**_base_kwargs())
    assert result.sql_text.count("READING_PREV_DATE = '2024-03-01'") == 2


# ---------------------------------------------------------------------
# Part 6: non-cycle "orphan usage" (TIPTL00011) reading cleanup
# ---------------------------------------------------------------------

def test_build_correction_script_no_orphan_ids_means_no_part_6():
    result = da.build_correction_script(**_base_kwargs())  # orphan_usage_id_readings defaults to ()
    assert result.orphan_reading_count == 0
    assert "Part 6 (non-cycle TIPTL00011 reading cleanup): 0 reading(s), 0 statement(s)" in result.sql_text
    assert "=== Part 6" not in result.sql_text
    assert "DELETE FROM" not in result.sql_text


def test_build_correction_script_orphan_ids_emit_delete_and_update():
    result = da.build_correction_script(**_base_kwargs(orphan_usage_id_readings=[101]))
    assert result.orphan_reading_count == 1
    # Part 6 is 2 statements (1 DELETE + 1 UPDATE) regardless of how many
    # ids share them - not counted into statement_count directly (see
    # CorrectionScript.statement_count's own comment) but reflected via
    # the +2-when-nonzero shortcut.
    assert result.statement_count == 6  # 2 reading + 1 item + 1 xml + 2 orphan
    assert "DELETE FROM" in result.sql_text
    assert da._qualified(da.ADMIN_SCHEMA, da.READINGS_ITEMSTOBILL_TABLE) in result.sql_text
    assert "WHERE ID_READING IN (101)" in result.sql_text
    assert "READ_STATUS = N'1000STSRED'" in result.sql_text
    assert "IND_USAGE_TO_CAL = 0" in result.sql_text
    assert "=== Part 6" in result.sql_text


def test_build_correction_script_orphan_ids_batched_into_one_delete_one_update():
    # Multiple orphan ids share a single DELETE + single UPDATE (IN list),
    # not one pair per id - unlike Parts 1/2/3/5 which emit one statement
    # per id.
    result = da.build_correction_script(**_base_kwargs(orphan_usage_id_readings=[101, 102]))
    assert result.orphan_reading_count == 2
    assert result.sql_text.count("DELETE FROM") == 1
    assert result.sql_text.count(f"SET {da.quote_ident(da.READ_STATUS_COLUMN)}") == 1
    assert "WHERE ID_READING IN (101, 102)" in result.sql_text


def test_build_correction_script_orphan_ids_deduped():
    result = da.build_correction_script(**_base_kwargs(orphan_usage_id_readings=[101, 101]))
    assert result.orphan_reading_count == 1
    assert "WHERE ID_READING IN (101)" in result.sql_text


def test_build_correction_script_orphan_ids_include_audit_columns_when_present():
    # Single reading (not the 2-reading _base_kwargs default) so the count
    # is unambiguous: 1 Part 1 reading UPDATE + 1 Part 6 reset UPDATE, both
    # against GCGT_RE_READING and both gated by reading_has_audit_cols (not
    # item_has_audit_cols, left False/default here).
    result = da.build_correction_script(
        **_base_kwargs(
            anomaly_id_readings=[101],
            item_to_bill_map={101: [500]},
            orphan_usage_id_readings=[101],
            reading_has_audit_cols=True,
            program="JIRA-1234",
        )
    )
    assert result.sql_text.count("update_program") == 2
    assert "JIRA-1234" in result.sql_text


def test_build_case_explanation_mentions_orphan_readings_when_present():
    explanation = da.build_case_explanation(
        niss="10450618-301", threshold=0, anomaly_count=2,
        correct_date=datetime.date(2024, 3, 1), item_count=1, orphan_reading_count=1,
    )
    assert "non-cycle" in explanation
    assert "1000STSRED" in explanation


def test_build_niss_account_query_shape():
    sql = da.build_niss_account_query("10450618-301")
    assert "10450618-301" in sql
    assert "GCCOM_CONTRACTED_SERVICE" in sql
    assert "GCCOM_PAYMENT_FORM" in sql
    assert "AS ACCOUNT" in sql
    assert "TOP 1" in sql
    assert "GCCOM_SECTOR_SUPPLY" in sql


# ---------------------------------------------------------------------
# Part 3: GCCOM_ITEMS_TO_BILL.STATUS transition (STTOBILL00 -> STTOBILL01)
# ---------------------------------------------------------------------

def test_build_item_status_query_empty_returns_none():
    assert da.build_item_status_query([]) is None


def test_build_item_status_query_filters_pending_status():
    sql = da.build_item_status_query([500])
    assert "GCCOM_ITEMS_TO_BILL" in sql
    assert "500" in sql
    assert "STTOBILL00" in sql
    # The already-advanced status must never appear as a filter value -
    # this query finds what's STILL pending, not what's done.
    assert sql.count("STTOBILL01") == 0


def test_build_correction_script_advances_status_for_touched_items():
    result = da.build_correction_script(**_base_kwargs(item_status_ids=[500]))
    assert result.item_status_count == 1
    assert result.statement_count == 5  # 2 reading + 1 item + 1 status + 1 xml
    assert "WHERE ID_ITEM_TO_BILL = 500" in result.sql_text
    assert "STATUS = N'STTOBILL01'" in result.sql_text
    assert "STTOBILL00" in result.sql_text  # the defensive WHERE ... AND STATUS = 'STTOBILL00'


def test_build_correction_script_no_item_status_ids_means_no_part_3():
    result = da.build_correction_script(**_base_kwargs())  # item_status_ids defaults to ()
    assert result.item_status_count == 0
    assert "Part 3 (ITEM STATUS STTOBILL00->STTOBILL01): 0 statement(s)" in result.sql_text
    assert "=== Part 3" not in result.sql_text


def test_build_correction_script_item_status_not_in_seen_items_is_ignored():
    # Same defensive-scope reasoning as Part 5's anomalous_item_ids test:
    # an id that never showed up in item_to_bill_map at all must not get
    # a Part 3 statement.
    result = da.build_correction_script(**_base_kwargs(item_status_ids=[999999]))
    assert result.item_status_count == 0
    assert "999999" not in result.sql_text


def test_build_correction_script_status_transition_is_separate_statement_from_ini_date():
    # Deliberate design choice (see build_correction_script's Part 3
    # docstring note): INI_DATE always fixes unconditionally, STATUS
    # only transitions when confirmed still pending - so they must be
    # two separate UPDATE statements, not merged into one.
    result = da.build_correction_script(**_base_kwargs(item_status_ids=[500]))
    assert result.sql_text.count("UPDATE OUC_ADMIN.GCCOM_ITEMS_TO_BILL") == 2


# ---------------------------------------------------------------------
# Case explanation
# ---------------------------------------------------------------------

def test_build_case_explanation_detect_only_no_correct_date():
    text = da.build_case_explanation(
        niss="10450618-301", threshold=0, anomaly_count=3, correct_date=None,
    )
    assert "What's wrong" in text
    assert "3 meter reading(s)" in text
    assert "What's fixed: nothing yet" in text


def test_build_case_explanation_with_correct_date_but_no_further_counts():
    text = da.build_case_explanation(
        niss="10450618-301", threshold=0, anomaly_count=2,
        correct_date=datetime.date(2024, 3, 1), correct_date_source_reading=999,
    )
    assert "What's fixed: the correct date 2024-03-01" in text
    assert "ID_READING 999" in text
    # No item/xml/status/anomalous counts passed - those clauses should
    # simply be absent, not rendered as "None".
    assert "None" not in text


def test_build_case_explanation_full_counts():
    text = da.build_case_explanation(
        niss="10450618-301", threshold=0, anomaly_count=2,
        correct_date=datetime.date(2024, 3, 1), correct_date_source_reading=999,
        item_count=1, xml_count=1, item_status_count=1, anomalous_count=1,
    )
    assert "1 item-to-bill row(s)" in text
    assert "STTOBILL00" in text and "STTOBILL01" in text
    assert "initDate" in text or "readingFromDate" in text
    assert "ESTAN00005" in text


def test_build_correction_script_embeds_explanation_in_header():
    result = da.build_correction_script(**_base_kwargs())
    assert result.explanation
    assert "What's wrong" in result.sql_text
    assert "What's fixed" in result.sql_text


# ---------------------------------------------------------------------
# Clean script option
# ---------------------------------------------------------------------

def test_build_correction_script_clean_strips_comments_keeps_statements():
    normal = da.build_correction_script(**_base_kwargs())
    clean = da.build_correction_script(**_base_kwargs(clean=True))
    assert not any(line.strip().startswith("--") for line in clean.sql_text.split("\n"))
    assert "BEGIN TRANSACTION" not in clean.sql_text  # removed per explicit analyst request
    assert "UPDATE OUC_COMMON_ADMIN.GCGT_RE_READING" in clean.sql_text
    assert "UPDATE OUC_ADMIN.GCCOM_ITEMS_TO_BILL" in clean.sql_text
    # Same statements/counts either way - clean only touches sql_text.
    assert clean.reading_count == normal.reading_count
    assert clean.item_count == normal.item_count
    assert clean.statement_count == normal.statement_count
    assert clean.explanation == normal.explanation


def test_build_correction_script_clean_false_is_default_and_unchanged():
    result = da.build_correction_script(**_base_kwargs())
    assert "-- Diff Date System anomaly correction script" in result.sql_text


# ---------------------------------------------------------------------
# Detect-all / bulk cleanup
# ---------------------------------------------------------------------

def test_build_detect_all_anomalies_query_uses_type_and_status_filters():
    sql = da.build_detect_all_anomalies_query()
    assert "GCCOM_ANOMALOUS" in sql
    assert "GCCOM_ITEMS_TO_BILL" in sql
    assert "LEFT JOIN" in sql
    assert "IN (202, 201)" in sql
    assert "ESTAN00009" in sql and "ESTAN00001" in sql
    # Cancelled anomalies must never be pulled into "detect all."
    assert sql.count("ESTAN00005") == 0


def test_build_detect_all_anomalies_query_accepts_overrides():
    sql = da.build_detect_all_anomalies_query(type_ids=[999], open_statuses=["ESTAN00099"])
    assert "999" in sql
    assert "ESTAN00099" in sql
    assert "IN (202, 201)" not in sql


def test_build_detect_all_anomalies_query_defaults_to_a_row_cap():
    # This is the one Date Anomaly query with no NISS/id-list scoping it
    # down - it must always cap itself unless a caller explicitly opts out.
    sql = da.build_detect_all_anomalies_query()
    assert f"TOP ({da.DETECT_ALL_DEFAULT_LIMIT})" in sql


def test_build_detect_all_anomalies_query_custom_limit():
    sql = da.build_detect_all_anomalies_query(limit=25)
    assert "TOP (25)" in sql


def test_build_detect_all_anomalies_query_includes_description_lookups():
    sql = da.build_detect_all_anomalies_query()
    assert "GCCOM_ANOMALOUS_STATUS" in sql
    assert "GCCOM_BILL_ANOMALY_COMPANY" in sql
    assert "ANOMALOUS_STATUS_DESC" in sql
    assert "ANOMALOUS_TYPE_CODE" in sql
    assert "ANOMALOUS_TYPE_DESC" in sql
    # Joined on the confirmed key columns, not guessed ones.
    assert "GAS.COD_DEVELOP = GA.ANOMALOUS_STATUS" in sql
    assert "GBAC.ID_BILL_ANOM_COMP = GA.ID_PRINCIPAL_ANOMALY" in sql
    # Analyst-corrected: GCCOM_ANOMALOUS_STATUS's description column is
    # NAME_TYPE, not DESCRIPTION (an earlier guess that was wrong).
    assert "GAS.NAME_TYPE AS ANOMALOUS_STATUS_DESC" in sql
    assert "GBAC.ANOMALY_COD AS ANOMALOUS_TYPE_CODE" in sql
    assert "GBAC.DESCRIPTION AS ANOMALOUS_TYPE_DESC" in sql


def test_build_detect_all_anomalies_query_includes_billing_service_enrichment():
    sql = da.build_detect_all_anomalies_query()
    assert "GCCOM_BILLING_SERVICE" in sql
    assert "GCCOM_CONTRACTED_SERVICE" in sql
    assert "GCCOM_PAYMENT_FORM" in sql
    assert "GCCOM_SECTOR_SUPPLY" in sql
    assert "AS ACCOUNT" in sql
    assert "AS SUPPLY" in sql
    assert "AS OFFERED_SERVICE" in sql
    assert "AS CONTRACT_STATUS" in sql
    # Analyst-corrected: GCCOM_BILLING_SERVICE joins back to GCCOM_ANOMALOUS
    # on GCCOM_ANOMALOUS's OWN ID_BILLING_SERVICE column, not ID_ITEM_TO_BILL
    # (an earlier guess that was wrong - see date_anomaly.py's comment on
    # ANOMALOUS_BILLING_SERVICE_COLUMN for why).
    assert "BS.ID_BILLING_SERVICE = GA.ID_BILLING_SERVICE" in sql
    assert "BS.ID_BILLING_SERVICE = GA.ID_ITEM_TO_BILL" not in sql


def test_build_detect_all_anomalies_query_orders_by_detection_date():
    sql = da.build_detect_all_anomalies_query()
    assert "ORDER BY GA.DETECTION_DATE;" in sql


def test_build_detect_all_anomalies_query_includes_all_cycle_column():
    sql = da.build_detect_all_anomalies_query()
    assert "AS ALL_CYCLE" in sql
    # Correlated EXISTS bridging GCCOM_ANOMALOUS -> GCCOM_READINGS_
    # ITEMSTOBILL -> GCGT_RE_READING (see CYCLE_READING_TYPES' comment for
    # why this bridge, not a direct ID_READING column on GCCOM_ANOMALOUS).
    assert "GCCOM_READINGS_ITEMSTOBILL" in sql
    assert "GCGT_RE_READING" in sql
    assert "GRI.ID_ITEM_TO_BILL = GA.ID_ITEM_TO_BILL" in sql
    assert "GR.READING_TYPE NOT IN (N'TIPTL00003', N'TIPTL00005')" in sql
    # A correlated EXISTS, not a JOIN in the main FROM chain - must not
    # multiply anomaly rows by however many readings each item has (the
    # reading table should appear exactly once, inside the EXISTS -
    # BILLING_PERIOD_COUNT's own correlated subquery below is the ONLY
    # other place GCGT_RE_READING is allowed to appear, hence 2 not 1).
    assert "CASE WHEN EXISTS (" in sql
    assert sql.count("GCGT_RE_READING") == 2


def test_build_detect_all_anomalies_query_all_cycle_uses_custom_reading_types():
    # CYCLE_READING_TYPES is a module constant, not a parameter - this test
    # just pins the literal values so a future change to that constant is
    # a deliberate, visible diff here rather than a silent behavior change.
    assert da.CYCLE_READING_TYPES == ("TIPTL00003", "TIPTL00005")


def test_build_detect_all_anomalies_query_includes_billing_period_count_column():
    sql = da.build_detect_all_anomalies_query()
    assert "AS BILLING_PERIOD_COUNT" in sql
    assert "COUNT(DISTINCT GR2.ID_BILLING_PERIOD)" in sql
    # Same bridge as ALL_CYCLE (GCCOM_READINGS_ITEMSTOBILL -> GCGT_RE_
    # READING), a correlated SCALAR subquery (not EXISTS, not a JOIN) since
    # this needs an actual count back, not a yes/no.
    assert "GRI2.ID_ITEM_TO_BILL = GA.ID_ITEM_TO_BILL" in sql
    assert "GR2.ID_READING = GRI2.ID_READING" in sql
    assert sql.count("GCCOM_READINGS_ITEMSTOBILL") == 2  # ALL_CYCLE's own EXISTS + this one


def test_build_detect_all_anomalies_query_limit_none_disables_cap():
    sql = da.build_detect_all_anomalies_query(limit=None)
    assert "TOP" not in sql


def test_build_cleanup_script_advances_status_and_cancels_anomaly():
    result = da.build_cleanup_script(item_ids=[500, 501], item_status_ids=[500])
    assert result.item_status_count == 1
    assert result.anomalous_count == 2
    assert result.statement_count == 3
    assert "WHERE ID_ITEM_TO_BILL = 500" in result.sql_text
    assert "STATUS = N'STTOBILL01'" in result.sql_text
    assert "ANOMALOUS_STATUS = N'ESTAN00005'" in result.sql_text
    # Item 501 has no status-advance statement (not in item_status_ids)
    # but still gets an anomaly-cancel statement.
    assert result.sql_text.count("UPDATE OUC_ADMIN.GCCOM_ITEMS_TO_BILL\n") == 1
    assert result.sql_text.count("UPDATE OUC_ADMIN.GCCOM_ANOMALOUS\n") == 2


def test_build_cleanup_script_never_touches_reading_or_xml_tables():
    result = da.build_cleanup_script(item_ids=[500], item_status_ids=[500])
    assert "GCGT_RE_READING" not in result.sql_text
    assert "READING_PREV_DATE" not in result.sql_text
    assert "INI_DATE" not in result.sql_text
    assert "XML_TO_BILL" not in result.sql_text
    assert "NOT a full correction" in result.sql_text


def test_build_cleanup_script_empty_selection_produces_nothing_to_update():
    result = da.build_cleanup_script(item_ids=[])
    assert result.statement_count == 0
    assert "Nothing to update" in result.sql_text


def test_build_cleanup_script_clean_strips_comments_including_warning():
    normal = da.build_cleanup_script(item_ids=[500], item_status_ids=[500])
    clean = da.build_cleanup_script(item_ids=[500], item_status_ids=[500], clean=True)
    assert not any(line.strip().startswith("--") for line in clean.sql_text.split("\n"))
    assert "BEGIN TRANSACTION" not in clean.sql_text  # removed per explicit analyst request
    assert clean.statement_count == normal.statement_count


# ---------------------------------------------------------------------
# Batch / multi-NISS script assembly
# ---------------------------------------------------------------------

def _batch_ok(niss, **overrides):
    kwargs = dict(
        niss=niss, status=da.STATUS_OK, reading_count=1, item_count=1, xml_count=1,
        warnings=[], sql_text=f"-- script for {niss}\nUPDATE OUC_COMMON_ADMIN.GCGT_RE_READING ...\n...",
    )
    kwargs.update(overrides)
    return da.BatchNissResult(**kwargs)


def test_build_batch_script_includes_summary_line_per_niss():
    results = [
        _batch_ok("10450618-301"),
        da.BatchNissResult(niss="10450618-302", status=da.STATUS_NO_ANOMALIES),
        da.BatchNissResult(niss="10450618-303", status=da.STATUS_ERROR, error="connection lost"),
    ]
    text = da.build_batch_script(results, program="JIRA-1")
    assert "10450618-301: OK" in text
    assert "10450618-302: no anomalies found" in text
    assert "10450618-303: ERROR - connection lost" in text
    assert "NISS processed: 3" in text


def test_build_batch_script_includes_each_ok_niss_own_script():
    results = [_batch_ok("A"), _batch_ok("B")]
    text = da.build_batch_script(results)
    assert "-- script for A" in text
    assert "-- script for B" in text
    assert "NISS: A" in text
    assert "NISS: B" in text


def test_build_batch_script_skips_no_anomalies_and_error_from_sql_body():
    results = [
        _batch_ok("A"),
        da.BatchNissResult(niss="B", status=da.STATUS_NO_ANOMALIES),
        da.BatchNissResult(niss="C", status=da.STATUS_ERROR, error="boom"),
    ]
    text = da.build_batch_script(results)
    # Only A's actual script body should appear - B/C get a summary line only.
    assert text.count("-- script for") == 1


def test_build_batch_script_summary_notes_orphan_readings_when_present():
    results = [_batch_ok("10450618-301", orphan_reading_count=2)]
    text = da.build_batch_script(results, program="JIRA-1")
    assert "2 non-cycle reading(s) reset" in text


def test_build_batch_script_summary_omits_orphan_note_when_zero():
    results = [_batch_ok("10450618-301")]  # orphan_reading_count defaults to 0
    text = da.build_batch_script(results, program="JIRA-1")
    assert "non-cycle reading(s) reset" not in text


def test_batch_niss_result_orphan_reading_count_defaults_to_zero():
    result = da.BatchNissResult(niss="A", status=da.STATUS_NO_ANOMALIES)
    assert result.orphan_reading_count == 0


def test_build_batch_script_all_failed_still_returns_summary_only():
    results = [
        da.BatchNissResult(niss="A", status=da.STATUS_NO_ANOMALIES),
        da.BatchNissResult(niss="B", status=da.STATUS_ERROR, error="boom"),
    ]
    text = da.build_batch_script(results)
    assert "Nothing to correct across this batch." in text
    assert "-- script for" not in text


# ---------------------------------------------------------------------
# lowest_billing_period_rows / "generate for lowest billing period only"
# ---------------------------------------------------------------------

def test_lowest_billing_period_rows_empty_input_returns_empty():
    assert da.lowest_billing_period_rows([]) == []


def test_lowest_billing_period_rows_single_period_returns_all_unchanged():
    rows = [{"ID_READING": 1, "ID_BILLING_PERIOD": 500}, {"ID_READING": 2, "ID_BILLING_PERIOD": 500}]
    assert da.lowest_billing_period_rows(rows) == rows


def test_lowest_billing_period_rows_keeps_only_the_lowest_period():
    rows = [
        {"ID_READING": 1, "ID_BILLING_PERIOD": 700},
        {"ID_READING": 2, "ID_BILLING_PERIOD": 500},
        {"ID_READING": 3, "ID_BILLING_PERIOD": 600},
        {"ID_READING": 4, "ID_BILLING_PERIOD": 500},
    ]
    result = da.lowest_billing_period_rows(rows)
    assert [r["ID_READING"] for r in result] == [2, 4]


def test_lowest_billing_period_rows_is_case_insensitive_on_key_lookup():
    # Mirrors how pytds/mssql row dicts come back - see _row_col's own
    # comment on why this matches _da_col/_col elsewhere in the codebase.
    rows = [{"id_reading": 1, "id_billing_period": 700}, {"id_reading": 2, "id_billing_period": 500}]
    result = da.lowest_billing_period_rows(rows)
    assert [r["id_reading"] for r in result] == [2]


def test_lowest_billing_period_rows_ignores_rows_with_none_period_when_finding_min():
    rows = [
        {"ID_READING": 1, "ID_BILLING_PERIOD": None},
        {"ID_READING": 2, "ID_BILLING_PERIOD": 500},
        {"ID_READING": 3, "ID_BILLING_PERIOD": 600},
    ]
    result = da.lowest_billing_period_rows(rows)
    assert [r["ID_READING"] for r in result] == [2]


def test_lowest_billing_period_rows_all_none_periods_returns_all_unchanged():
    rows = [{"ID_READING": 1, "ID_BILLING_PERIOD": None}, {"ID_READING": 2, "ID_BILLING_PERIOD": None}]
    assert da.lowest_billing_period_rows(rows) == rows


def test_build_correction_script_scoped_to_lowest_period_flag_on_result():
    result = da.build_correction_script(**_base_kwargs(billing_period_count=2, scoped_to_lowest_period=True))
    assert result.scoped_to_lowest_period is True


def test_build_correction_script_scoped_note_appears_when_multi_period_and_scoped():
    result = da.build_correction_script(**_base_kwargs(billing_period_count=2, scoped_to_lowest_period=True))
    assert "SCOPED TO LOWEST BILLING PERIOD ONLY" in result.sql_text


def test_build_correction_script_scoped_note_omitted_when_single_period():
    # scoped_to_lowest_period=True but billing_period_count<=1 means the
    # option had nothing to actually narrow - no point claiming a scope
    # that didn't do anything.
    result = da.build_correction_script(**_base_kwargs(billing_period_count=1, scoped_to_lowest_period=True))
    assert "SCOPED TO LOWEST BILLING PERIOD ONLY" not in result.sql_text


def test_build_correction_script_scoped_note_omitted_when_flag_false():
    result = da.build_correction_script(**_base_kwargs(billing_period_count=2, scoped_to_lowest_period=False))
    assert "SCOPED TO LOWEST BILLING PERIOD ONLY" not in result.sql_text
    assert result.scoped_to_lowest_period is False


def test_batch_niss_result_scoped_to_lowest_period_defaults_to_false():
    result = da.BatchNissResult(niss="A", status=da.STATUS_NO_ANOMALIES)
    assert result.scoped_to_lowest_period is False


def test_build_batch_script_summary_notes_scoped_to_lowest_period():
    results = [_batch_ok("10450618-301", billing_period_count=2, scoped_to_lowest_period=True)]
    text = da.build_batch_script(results, program="JIRA-1")
    assert "scoped to lowest of 2 billing periods" in text


def test_build_batch_script_summary_omits_scoped_note_when_single_period():
    results = [_batch_ok("10450618-301", billing_period_count=1, scoped_to_lowest_period=True)]
    text = da.build_batch_script(results, program="JIRA-1")
    assert "scoped to lowest" not in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
