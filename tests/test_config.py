"""
Pure-logic tests for AppConfig.add_recent_query - dedup/ordering/cap
behavior for the "Recent" query menu - plus save_query/delete_query
(the Tools-tab Saved Queries list) and add_connection/remove_connection
(the Tools-tab Environment switcher / future multi-connection UI).
Doesn't touch disk (load_config/save_config aren't exercised here - see
smoke_launch.py for that, which already runs against a throwaway data/
directory).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import AppConfig, ConnectionConfig, SavedQuery, RECENT_QUERIES_MAX


def test_add_recent_query_prepends_newest_first():
    cfg = AppConfig()
    cfg.add_recent_query("SELECT 1")
    cfg.add_recent_query("SELECT 2")
    assert cfg.recent_queries == ["SELECT 2", "SELECT 1"]


def test_add_recent_query_dedupes_and_moves_to_front():
    cfg = AppConfig()
    cfg.add_recent_query("SELECT 1")
    cfg.add_recent_query("SELECT 2")
    cfg.add_recent_query("SELECT 1")
    assert cfg.recent_queries == ["SELECT 1", "SELECT 2"]


def test_add_recent_query_ignores_blank():
    cfg = AppConfig()
    cfg.add_recent_query("   ")
    assert cfg.recent_queries == []


def test_add_recent_query_caps_at_max():
    cfg = AppConfig()
    for i in range(RECENT_QUERIES_MAX + 5):
        cfg.add_recent_query(f"SELECT {i}")
    assert len(cfg.recent_queries) == RECENT_QUERIES_MAX
    # newest survive, oldest fall off the end
    assert cfg.recent_queries[0] == f"SELECT {RECENT_QUERIES_MAX + 4}"


# ---------- save_query / delete_query ----------

def test_save_query_adds_new_entry():
    cfg = AppConfig()
    cfg.save_query("My Query", "SELECT * FROM Accounts")
    assert len(cfg.saved_queries) == 1
    assert cfg.saved_queries[0].name == "My Query"
    assert cfg.saved_queries[0].sql == "SELECT * FROM Accounts"
    assert cfg.saved_queries[0].created_at_utc  # stamped, non-empty


def test_save_query_upserts_by_name():
    cfg = AppConfig()
    cfg.save_query("My Query", "SELECT 1")
    cfg.save_query("My Query", "SELECT 2")
    assert len(cfg.saved_queries) == 1
    assert cfg.saved_queries[0].sql == "SELECT 2"


def test_save_query_ignores_blank_name_or_sql():
    cfg = AppConfig()
    cfg.save_query("", "SELECT 1")
    cfg.save_query("Name only", "   ")
    assert cfg.saved_queries == []


def test_delete_query_removes_by_name():
    cfg = AppConfig()
    cfg.save_query("Keep", "SELECT 1")
    cfg.save_query("Drop", "SELECT 2")
    cfg.delete_query("Drop")
    assert [q.name for q in cfg.saved_queries] == ["Keep"]


def test_delete_query_missing_name_is_a_no_op():
    cfg = AppConfig()
    cfg.save_query("Keep", "SELECT 1")
    cfg.delete_query("Does Not Exist")
    assert [q.name for q in cfg.saved_queries] == ["Keep"]


# ---------- add_connection / remove_connection ----------

def test_add_connection_stores_under_key():
    cfg = AppConfig()
    conn = ConnectionConfig(name="Staging", server="stg.example.com")
    cfg.add_connection("staging", conn)
    assert cfg.connections["staging"] is conn


def test_add_connection_overwrites_existing_key():
    cfg = AppConfig()
    cfg.add_connection("staging", ConnectionConfig(name="Old"))
    cfg.add_connection("staging", ConnectionConfig(name="New"))
    assert cfg.connections["staging"].name == "New"


def test_remove_connection_deletes_non_active():
    cfg = AppConfig(active_connection="default")
    cfg.add_connection("default", ConnectionConfig(name="Default"))
    cfg.add_connection("staging", ConnectionConfig(name="Staging"))
    cfg.remove_connection("staging")
    assert "staging" not in cfg.connections
    assert "default" in cfg.connections


def test_remove_connection_refuses_to_remove_active():
    cfg = AppConfig(active_connection="default")
    cfg.add_connection("default", ConnectionConfig(name="Default"))
    try:
        cfg.remove_connection("default")
        assert False, "expected ValueError"
    except ValueError:
        pass
    assert "default" in cfg.connections


def test_remove_connection_missing_key_is_a_no_op():
    cfg = AppConfig(active_connection="default")
    cfg.add_connection("default", ConnectionConfig(name="Default"))
    cfg.remove_connection("does-not-exist")
    assert "default" in cfg.connections


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
