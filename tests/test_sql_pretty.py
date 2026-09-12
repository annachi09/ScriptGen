"""Tests for app.core.sql_pretty (the query editor's "Format" button)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.sql_pretty import prettify_sql


def test_prettify_reindents_select_onto_multiple_lines():
    sql = "select id, name, amount from dbo.gccom_payment_form where id = 13 and status = 'active'"
    result = prettify_sql(sql)
    assert "SELECT" in result
    assert "FROM dbo.gccom_payment_form" in result
    assert "WHERE" in result
    # One column/clause per line is the whole point of "prettify".
    assert result.count("\n") >= 3


def test_prettify_uppercases_keywords():
    sql = "select * from dbo.Accounts where id = 1"
    result = prettify_sql(sql)
    assert "SELECT" in result
    assert "FROM" in result
    assert "WHERE" in result
    assert "select" not in result
    assert "Accounts" in result  # identifiers are left alone, not shouted


def test_prettify_blank_input_returns_unchanged():
    assert prettify_sql("") == ""
    assert prettify_sql("   ") == "   "


def test_prettify_is_idempotent():
    sql = "SELECT id, name FROM dbo.Accounts WHERE id = 1;"
    once = prettify_sql(sql)
    twice = prettify_sql(once)
    assert once == twice


def test_prettify_preserves_meaning_not_just_layout():
    # Purely cosmetic: the same literal values/identifiers must still be
    # present afterward, just laid out differently - this isn't a SQL
    # validator, only a reformatter.
    sql = "select TOP 100 * from INFORMATION_SCHEMA.TABLES;"
    result = prettify_sql(sql)
    assert "TOP 100" in result
    assert "INFORMATION_SCHEMA.TABLES" in result


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
