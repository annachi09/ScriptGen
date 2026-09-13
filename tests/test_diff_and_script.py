"""
Pure-logic tests for the diff engine and script generator. No database,
no GUI - runs anywhere with the repo's dependencies installed:

    python -m pytest tests/ -v
"""
import datetime
import decimal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.diff_engine import (
    cell_display,
    coerce_edited_value,
    compute_row_changes,
)
from app.core.script_generator import generate_update_script, generate_rollback_script, build_preview_line
from app.core.sql_format import format_sql_literal, quote_ident, qualify_table


# ---------- sql_format ----------

def test_format_sql_literal_none():
    assert format_sql_literal(None) == "NULL"


def test_format_sql_literal_bool():
    assert format_sql_literal(True) == "1"
    assert format_sql_literal(False) == "0"


def test_format_sql_literal_numbers():
    assert format_sql_literal(42) == "42"
    assert format_sql_literal(3.14) == "3.14"
    assert format_sql_literal(decimal.Decimal("10.50")) == "10.50"


def test_format_sql_literal_string_escapes_quotes():
    assert format_sql_literal("O'Brien") == "'O''Brien'"


def test_format_sql_literal_datetime():
    dt = datetime.datetime(2026, 1, 5, 13, 30, 0)
    assert format_sql_literal(dt) == "'2026-01-05 13:30:00.000'"


def test_format_sql_literal_date():
    d = datetime.date(2026, 1, 5)
    assert format_sql_literal(d) == "'2026-01-05'"


def test_format_sql_literal_bytes():
    assert format_sql_literal(b"\x01\xFF") == "0x01ff"


def test_quote_ident_escapes_bracket():
    assert quote_ident("weird]name") == "[weird]]name]"


def test_qualify_table_with_and_without_schema():
    # Plain names stay unquoted by default - see test_quote_ident_* below
    # for when quote_ident falls back to bracketing.
    assert qualify_table("dbo", "Users") == "dbo.Users"
    assert qualify_table(None, "Users") == "Users"


def test_quote_ident_leaves_plain_identifier_unquoted():
    assert quote_ident("gccom_payment_form") == "gccom_payment_form"
    assert quote_ident("UPDATE_USER") == "UPDATE_USER"


def test_quote_ident_brackets_reserved_word():
    assert quote_ident("user") == "[user]"
    assert quote_ident("key") == "[key]"


def test_quote_ident_brackets_name_with_space():
    assert quote_ident("first name") == "[first name]"


# ---------- diff_engine: coerce_edited_value ----------

def test_coerce_null_sentinel():
    assert coerce_edited_value("NULL", "anything") is None
    assert coerce_edited_value("null", 5) is None


def test_coerce_int_roundtrip():
    assert coerce_edited_value("123", 1) == 123


def test_coerce_float_roundtrip():
    assert coerce_edited_value("1.5", 1.0) == 1.5


def test_coerce_decimal_roundtrip():
    assert coerce_edited_value("9.99", decimal.Decimal("1.00")) == decimal.Decimal("9.99")


def test_coerce_bool_variants():
    assert coerce_edited_value("true", True) is True
    assert coerce_edited_value("0", True) is False


def test_coerce_datetime():
    result = coerce_edited_value("2026-02-01 10:00:00", datetime.datetime(2025, 1, 1))
    assert result == datetime.datetime(2026, 2, 1, 10, 0, 0)


def test_coerce_falls_back_to_string_on_bad_number():
    assert coerce_edited_value("not-a-number", 5) == "not-a-number"


def test_coerce_from_null_original_guesses_numeric():
    assert coerce_edited_value("42", None) == 42
    assert coerce_edited_value("hello", None) == "hello"


def test_cell_display_none_is_empty_string():
    assert cell_display(None) == ""
    assert cell_display(5) == "5"


# ---------- diff_engine: compute_row_changes ----------

def test_no_changes_detected_when_grid_matches_original():
    columns = ["id", "name"]
    original = [[1, "Alice"], [2, "Bob"]]
    edited = [["1", "Alice"], ["2", "Bob"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    assert changes == []


def test_single_cell_change_detected():
    columns = ["id", "name"]
    original = [[1, "Alice"], [2, "Bob"]]
    edited = [["1", "Alicia"], ["2", "Bob"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    assert len(changes) == 1
    assert changes[0].row_index == 0
    assert changes[0].key_predicate == {"id": 1}
    assert len(changes[0].cell_changes) == 1
    assert changes[0].cell_changes[0].column == "name"
    assert changes[0].cell_changes[0].old_value == "Alice"
    assert changes[0].cell_changes[0].new_value == "Alicia"


def test_multiple_column_changes_same_row():
    columns = ["id", "name", "age"]
    original = [[1, "Alice", 30]]
    edited = [["1", "Alicia", "31"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    assert len(changes) == 1
    changed_cols = {c.column for c in changes[0].cell_changes}
    assert changed_cols == {"name", "age"}


def test_set_to_null_via_sentinel():
    columns = ["id", "name"]
    original = [[1, "Alice"]]
    edited = [["1", "NULL"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    assert changes[0].cell_changes[0].new_value is None


def test_no_key_columns_falls_back_to_full_row_and_flags_it():
    columns = ["name", "age"]
    original = [["Alice", 30]]
    edited = [["Alice", "31"]]
    changes = compute_row_changes(columns, original, edited, key_columns=[])
    assert changes[0].key_is_full_row is True
    assert changes[0].key_predicate == {"name": "Alice", "age": 30}


def test_editing_the_key_column_itself_uses_old_value_in_where():
    columns = ["id", "name"]
    original = [[1, "Alice"]]
    edited = [["2", "Alice"]]  # user renumbers the id
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    assert changes[0].key_predicate == {"id": 1}  # WHERE uses OLD id
    assert changes[0].cell_changes[0].column == "id"
    assert changes[0].cell_changes[0].new_value == 2


def test_false_positive_guard_numeric_display_equal():
    # "3" edited into a cell whose original int value is 3 -> not a real change
    columns = ["id", "qty"]
    original = [[1, 3]]
    edited = [["1", "3"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    assert changes == []


def test_mismatched_row_lengths_raises():
    import pytest
    columns = ["id"]
    original = [[1]]
    edited = [["1"], ["2"]]
    try:
        compute_row_changes(columns, original, edited, key_columns=["id"])
        assert False, "expected ValueError"
    except ValueError:
        pass


# ---------- script_generator ----------

def test_generate_update_script_basic():
    columns = ["id", "name"]
    original = [[1, "Alice"]]
    edited = [["1", "Alicia"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    result = generate_update_script("dbo", "Users", changes, source_sql="SELECT * FROM dbo.Users")

    assert result.statement_count == 1
    assert result.warning_count == 0
    assert "UPDATE dbo.Users" in result.sql_text
    assert "SET name = 'Alicia'" in result.sql_text
    assert "WHERE id = 1" in result.sql_text
    assert "BEGIN TRANSACTION" in result.sql_text
    assert "ROLLBACK TRANSACTION" in result.sql_text


def test_generate_update_script_warns_on_full_row_match():
    columns = ["name", "age"]
    original = [["Alice", 30]]
    edited = [["Alice", "31"]]
    changes = compute_row_changes(columns, original, edited, key_columns=[])
    result = generate_update_script("dbo", "Users", changes)

    assert result.warning_count == 1
    assert "WARNING: no primary key" in result.sql_text


def test_generate_update_script_no_changes():
    result = generate_update_script("dbo", "Users", [])
    assert result.statement_count == 0
    assert "No changes detected" in result.sql_text


def test_generate_update_script_notes_changed_columns():
    columns = ["id", "name", "age"]
    original = [[1, "Alice", 30]]
    edited = [["1", "Alicia", "31"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    result = generate_update_script("dbo", "Users", changes)
    assert "-- Row 1: changed column(s): name, age" in result.sql_text


def test_generate_update_script_null_set():
    columns = ["id", "name"]
    original = [[1, "Alice"]]
    edited = [["1", "NULL"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    result = generate_update_script("dbo", "Users", changes)
    assert "SET name = NULL" in result.sql_text


# ---------- rollback script generation ----------

def test_rollback_script_restores_original_values():
    columns = ["id", "name"]
    original = [[1, "Alice"]]
    edited = [["1", "Alicia"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    result = generate_rollback_script("dbo", "Users", changes)

    assert result.statement_count == 1
    assert "SET name = 'Alice'" in result.sql_text     # restores the OLD value
    assert "WHERE id = 1" in result.sql_text             # id itself never changed
    assert "Rollback for Row 1: restoring column(s): name" in result.sql_text


def test_rollback_script_where_uses_new_key_value_when_key_column_changed():
    # Renumbering the key column itself: the forward script's WHERE used
    # the OLD id (1) to find the row; after that ran, the row's id in the
    # database is now 2 - so the ROLLBACK's WHERE must match on 2, not 1,
    # or it'd match zero rows.
    columns = ["id", "name"]
    original = [[1, "Alice"]]
    edited = [["2", "Alice"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    result = generate_rollback_script("dbo", "Users", changes)

    assert "WHERE id = 2" in result.sql_text
    assert "SET id = 1" in result.sql_text  # restores id back to its original value


def test_forward_script_where_includes_original_value_of_changed_column():
    # A WHERE built from the key column alone (id = 1) isn't enough of a
    # safety check: it'll happily overwrite name no matter what it
    # currently holds. The generated script should also require name to
    # still be its ORIGINAL value (Alice) before overwriting it - a
    # lightweight guard against something else having changed the row
    # first.
    columns = ["id", "name"]
    original = [[1, "Alice"]]
    edited = [["1", "Alicia"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    result = generate_update_script("dbo", "Users", changes)
    assert "WHERE id = 1 AND name = 'Alice';" in result.sql_text


def test_forward_script_where_original_value_guard_handles_null():
    columns = ["id", "name"]
    original = [[1, None]]
    edited = [["1", "Alicia"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    result = generate_update_script("dbo", "Users", changes)
    assert "WHERE id = 1 AND name IS NULL;" in result.sql_text


def test_forward_script_where_guards_every_changed_non_key_column():
    columns = ["id", "name", "age"]
    original = [[1, "Alice", 30]]
    edited = [["1", "Alicia", "31"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    result = generate_update_script("dbo", "Users", changes)
    assert "WHERE id = 1 AND name = 'Alice' AND age = 30;" in result.sql_text


def test_forward_script_where_no_duplicate_when_changed_column_is_the_key():
    # If the changed column IS the key column, it's already in the WHERE
    # (at its original value) via key_predicate - the guard shouldn't add
    # a second, redundant clause for it.
    columns = ["id", "name"]
    original = [[1, "Alice"]]
    edited = [["2", "Alice"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    result = generate_update_script("dbo", "Users", changes)
    assert "WHERE id = 1;" in result.sql_text
    assert result.sql_text.count("id = 1") == 1


def test_rollback_script_where_includes_value_forward_script_set():
    # Symmetric guard on the rollback side: before restoring name back to
    # Alice, the rollback should require name to still be Alicia - the
    # value the FORWARD script just set it to - not silently restore over
    # whatever it currently holds.
    columns = ["id", "name"]
    original = [[1, "Alice"]]
    edited = [["1", "Alicia"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    result = generate_rollback_script("dbo", "Users", changes)
    assert "WHERE id = 1 AND name = 'Alicia';" in result.sql_text


def test_rollback_script_no_changes():
    result = generate_rollback_script("dbo", "Users", [])
    assert result.statement_count == 0
    assert "No changes to roll back" in result.sql_text


def test_rollback_script_warns_on_full_row_match():
    columns = ["name", "age"]
    original = [["Alice", 30]]
    edited = [["Alice", "31"]]
    changes = compute_row_changes(columns, original, edited, key_columns=[])
    result = generate_rollback_script("dbo", "Users", changes)
    assert result.warning_count == 1
    assert "WARNING: no primary key" in result.sql_text


# ---------- audit columns (update_program / update_date / update_user) ----------

def test_forward_script_stamps_audit_columns_with_default_program():
    columns = ["id", "name"]
    original = [[1, "Alice"]]
    edited = [["1", "Alicia"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    result = generate_update_script("dbo", "Users", changes)
    assert "update_program = 'JIRAXXXX'" in result.sql_text
    assert "update_date = GETDATE()" in result.sql_text
    assert "update_user = 'RMA'" in result.sql_text
    assert "-- Program/Jira: JIRAXXXX" in result.sql_text


def test_forward_script_uses_supplied_program_number():
    columns = ["id", "name"]
    original = [[1, "Alice"]]
    edited = [["1", "Alicia"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    result = generate_update_script("dbo", "Users", changes, program="JIRA-4821")
    assert "update_program = 'JIRA-4821'" in result.sql_text
    assert "JIRAXXXX" not in result.sql_text


def test_forward_script_set_clause_shows_original_value_inline():
    columns = ["id", "name"]
    original = [[1, "Alice"]]
    edited = [["1", "Alicia"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    result = generate_update_script("dbo", "Users", changes)
    assert "name = 'Alicia',  -- was: 'Alice'" in result.sql_text


def test_rollback_script_set_clause_shows_value_it_replaces():
    # Rollback restores old_value; its "-- was:" comment should show the
    # value being overwritten AT ROLLBACK TIME, which is new_value (what
    # the forward script had set) - not old_value again.
    columns = ["id", "name"]
    original = [[1, "Alice"]]
    edited = [["1", "Alicia"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    result = generate_rollback_script("dbo", "Users", changes, program="JIRA-4821")
    assert "name = 'Alice',  -- was: 'Alicia'" in result.sql_text
    assert "update_program = 'JIRA-4821'" in result.sql_text
    assert "update_date = GETDATE()" in result.sql_text


def test_preview_line_where_includes_original_value_of_changed_column():
    columns = ["id", "amount"]
    original = [[1, 10]]
    edited = [["1", "999"]]
    changes = compute_row_changes(columns, original, edited, key_columns=["id"])
    line = build_preview_line("dbo", "Accounts", changes[0])
    assert "SET amount = 999" in line
    assert "WHERE id = 1 AND amount = 10;" in line


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
