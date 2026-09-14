"""
Integration tests for the FastAPI backend (web/server.py) using
FastAPI's TestClient - no real SQL Server, no real browser. Every path
that would normally hit the network (mssql.run_query / test_connection /
get_primary_key_columns) is monkeypatched, same spirit as
test_ai_assist.py's offline short-circuit tests and smoke_launch.py's
monkeypatched AI calls.

The tricky part is filesystem isolation: web_users.json, the session-
cookie signing key, and config.json all live under app.utils.paths.
get_data_dir() by default, which points at the real project's data/
folder. Each test gets its OWN tmp_path instead, via directly
monkeypatching the specific getter functions each module already bound
into its own namespace at import time (patching the origin module's
attribute doesn't retroactively change an already-`from x import y`'d
name elsewhere - see the client() fixture below) - and importing
web.server itself only AFTER those patches are in place, since it reads
the session secret at module import time.
"""
import contextlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import AppConfig, ConnectionConfig, AIConfig
from app.db.mssql import QueryResult


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    import app.config as config_mod
    import app.utils.paths as paths_mod
    import web.auth as auth_mod
    import web.menu_access as menu_access_mod

    monkeypatch.setattr(config_mod, "get_config_path", lambda: data_dir / "config.json")
    monkeypatch.setattr(auth_mod, "get_data_dir", lambda: data_dir)
    monkeypatch.setattr(auth_mod, "get_web_users_path", lambda: data_dir / "web_users.json")
    monkeypatch.setattr(paths_mod, "get_web_session_secret_path", lambda: data_dir / "web_session.key")
    # web.menu_access imports get_menu_access_path BY NAME too (same
    # already-bound-import gotcha as get_web_users_path above) - patch its
    # own copy directly so /api/config/menu-access tests don't touch the
    # real project's data/web_menu_access.json.
    monkeypatch.setattr(menu_access_mod, "get_menu_access_path", lambda: data_dir / "web_menu_access.json")

    # A fake connection so get_active_connection() doesn't return None -
    # actual DB access always goes through monkeypatched mssql functions
    # below, never a real socket.
    fake_conn = ConnectionConfig(name="Test", server="localhost", port=1433, username="test")
    fake_conn.set_password("unused")
    fake_config = AppConfig(
        connections={"default": fake_conn},
        active_connection="default",
        internal_db_path=str(data_dir / "internal.db"),
        ai=AIConfig(),
    )
    monkeypatch.setattr(config_mod, "load_config", lambda: fake_config)
    monkeypatch.setattr(config_mod, "save_config", lambda cfg: None)

    # Imported here (not at module top) so it picks up the patches above -
    # module-level code in web/server.py reads the session secret once,
    # at first import, via web.auth.get_session_secret().
    import importlib
    import web.server as server_mod
    importlib.reload(server_mod)
    # web.server imported load_config/save_config BY REFERENCE at its own
    # module top, so patching app.config's names above doesn't reach them -
    # patch server_mod's own copies directly too.
    monkeypatch.setattr(server_mod, "load_config", lambda: fake_config)
    monkeypatch.setattr(server_mod, "save_config", lambda cfg: None)

    from fastapi.testclient import TestClient

    return TestClient(server_mod.app)


def _login(client, username="admin", password=None):
    if password is None:
        # Pull the freshly-bootstrapped first-run password out of the
        # notice file the same way a real first-time admin would. Uses
        # web.auth's OWN get_data_dir (patched by the fixture directly on
        # that module) rather than app.utils.paths.get_data_dir, which
        # the fixture leaves untouched - see the client() fixture's
        # docstring about already-bound imports not following a patch
        # made on the origin module.
        import web.auth as auth_mod

        notice = auth_mod.get_data_dir() / "web_admin_first_run.txt"
        # Loading the store once triggers bootstrap if it hasn't happened yet.
        auth_mod.WebUserStore.load()
        text = notice.read_text(encoding="utf-8")
        password = [line for line in text.splitlines() if line.startswith("Password: ")][0].split(": ", 1)[1]
    return client.post("/api/login", json={"username": username, "password": password})


# ---------------- Auth ----------------

def test_first_run_bootstraps_admin_and_login_succeeds(client):
    resp = _login(client)
    assert resp.status_code == 200
    assert resp.json()["username"] == "admin"


def test_wrong_password_rejected(client):
    resp = client.post("/api/login", json={"username": "admin", "password": "definitely-wrong"})
    assert resp.status_code == 401


def test_unknown_user_rejected(client):
    resp = client.post("/api/login", json={"username": "nobody", "password": "whatever"})
    assert resp.status_code == 401


def test_first_run_notice_deleted_after_first_login(client):
    import web.auth as auth_mod

    _login(client)
    notice = auth_mod.get_data_dir() / "web_admin_first_run.txt"
    assert not notice.exists()


def test_protected_route_requires_login(client):
    resp = client.get("/api/session")
    assert resp.status_code == 401
    resp = client.post("/api/query/run", json={"sql": "SELECT 1"})
    assert resp.status_code == 401


def test_session_persists_after_login(client):
    _login(client)
    resp = client.get("/api/session")
    assert resp.status_code == 200
    assert resp.json()["username"] == "admin"


def test_logout_clears_session(client):
    _login(client)
    client.post("/api/logout")
    resp = client.get("/api/session")
    assert resp.status_code == 401


# ---------------- Query / format ----------------

def test_format_query_endpoint(client, monkeypatch):
    _login(client)
    resp = client.post("/api/query/format", json={"sql": "select id from dbo.Accounts where id = 1"})
    assert resp.status_code == 200
    formatted = resp.json()["sql"]
    assert "SELECT" in formatted
    assert "\n" in formatted


def test_run_query_populates_grid_and_recent(client, monkeypatch):
    import web.server as server_mod

    fake_result = QueryResult(
        columns=["id", "name", "amount"],
        rows=[[1, "Alice", 10], [2, "Bob", 20]],
        elapsed_ms=4.2,
        source_table="dbo.Accounts",
        source_schema="dbo",
    )
    monkeypatch.setattr(server_mod.mssql, "run_query", lambda conn, sql: fake_result)

    _login(client)
    resp = client.post("/api/query/run", json={"sql": "SELECT * FROM dbo.Accounts"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["columns"] == ["id", "name", "amount"]
    assert body["display_rows"] == [["1", "Alice", "10"], ["2", "Bob", "20"]]
    assert body["row_count"] == 2
    assert body["source_table"] == "dbo.Accounts"

    recent = client.get("/api/query/recent")
    assert "SELECT * FROM dbo.Accounts" in recent.json()["queries"]


def test_run_query_requires_nonblank_sql(client):
    _login(client)
    resp = client.post("/api/query/run", json={"sql": "   "})
    assert resp.status_code == 400


# ---------------- Grid diff + script generation (the WHERE-guard behavior) ----------------

def _run_fake_query(client, monkeypatch):
    import web.server as server_mod

    fake_result = QueryResult(
        columns=["id", "name", "amount"],
        rows=[[1, "Alice", 10], [2, "Bob", 20]],
        elapsed_ms=1.0,
        source_table="dbo.Accounts",
        source_schema="dbo",
    )
    monkeypatch.setattr(server_mod.mssql, "run_query", lambda conn, sql: fake_result)
    _login(client)
    client.post("/api/query/run", json={"sql": "SELECT * FROM dbo.Accounts"})


def test_grid_diff_requires_a_query_first(client):
    _login(client)
    resp = client.post("/api/grid/diff", json={"edited_rows": [], "key_columns": []})
    assert resp.status_code == 400


def test_grid_diff_reports_changed_row_and_preview_line(client, monkeypatch):
    _run_fake_query(client, monkeypatch)
    edited_rows = [["1", "Alice", "999"], ["2", "Bob", "20"]]
    resp = client.post(
        "/api/grid/diff",
        json={"edited_rows": edited_rows, "key_columns": ["id"], "schema": "dbo", "table": "Accounts"},
    )
    assert resp.status_code == 200
    changes = resp.json()["changes"]
    assert len(changes) == 1
    assert changes[0]["row_index"] == 0
    assert changes[0]["changed_columns"] == ["amount"]
    assert "amount = 999" in changes[0]["preview_line"]
    # The WHERE-clause original-value guard should show up in the live
    # preview too, not just the final generated script.
    assert "WHERE id = 1 AND amount = 10" in changes[0]["preview_line"]


def test_generate_update_script_includes_where_guard(client, monkeypatch):
    _run_fake_query(client, monkeypatch)
    edited_rows = [["1", "Alice", "999"], ["2", "Bob", "20"]]
    resp = client.post(
        "/api/script/generate",
        json={
            "edited_rows": edited_rows,
            "key_columns": ["id"],
            "schema": "dbo",
            "table": "Accounts",
            "program": "JIRA-1",
            "kind": "update",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["statement_count"] == 1
    assert "WHERE id = 1 AND amount = 10;" in body["sql_text"]
    assert "update_program = 'JIRA-1'" in body["sql_text"]


def test_generate_rollback_script(client, monkeypatch):
    _run_fake_query(client, monkeypatch)
    edited_rows = [["1", "Alice", "999"], ["2", "Bob", "20"]]
    resp = client.post(
        "/api/script/generate",
        json={
            "edited_rows": edited_rows,
            "key_columns": ["id"],
            "schema": "dbo",
            "table": "Accounts",
            "kind": "rollback",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "SET amount = 10" in body["sql_text"]  # restores the original value
    assert "WHERE id = 1 AND amount = 999;" in body["sql_text"]  # guards on what forward SET it to


def test_generate_script_requires_target_table(client, monkeypatch):
    _run_fake_query(client, monkeypatch)
    resp = client.post(
        "/api/script/generate",
        json={"edited_rows": [["1", "Alice", "10"], ["2", "Bob", "20"]], "key_columns": ["id"], "table": ""},
    )
    assert resp.status_code == 400


# ---------------- Script history ----------------

def test_history_empty_before_any_script_generated(client):
    _login(client)
    resp = client.get("/api/history")
    assert resp.status_code == 200
    assert resp.json()["entries"] == []


def test_generating_a_script_records_history_entry(client, monkeypatch):
    _run_fake_query(client, monkeypatch)
    edited_rows = [["1", "Alice", "999"], ["2", "Bob", "20"]]
    client.post(
        "/api/script/generate",
        json={
            "edited_rows": edited_rows, "key_columns": ["id"], "schema": "dbo",
            "table": "Accounts", "program": "JIRA-1", "kind": "update",
        },
    )
    resp = client.get("/api/history")
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["username"] == "admin"
    assert entry["kind"] == "update"
    assert entry["table_name"] == "Accounts"
    assert entry["schema_name"] == "dbo"
    assert entry["program"] == "JIRA-1"
    assert entry["statement_count"] == 1
    assert entry["source"] == "web"


def test_history_detail_returns_full_sql_text(client, monkeypatch):
    _run_fake_query(client, monkeypatch)
    edited_rows = [["1", "Alice", "999"], ["2", "Bob", "20"]]
    client.post(
        "/api/script/generate",
        json={
            "edited_rows": edited_rows, "key_columns": ["id"], "schema": "dbo",
            "table": "Accounts", "kind": "update",
        },
    )
    entry_id = client.get("/api/history").json()["entries"][0]["id"]
    resp = client.get(f"/api/history/{entry_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == entry_id
    assert "WHERE id = 1" in body["sql_text"]


def test_history_detail_404_for_unknown_id(client):
    _login(client)
    resp = client.get("/api/history/99999")
    assert resp.status_code == 404


def test_history_requires_login(client):
    resp = client.get("/api/history")
    assert resp.status_code == 401
    resp = client.get("/api/history/1")
    assert resp.status_code == 401


def test_history_mine_only_filters_by_current_user(client, monkeypatch):
    import web.auth as auth_mod

    # Seed a second user directly, since login only accepts one account
    # per session in this test client.
    store = auth_mod.WebUserStore.load()
    store.add_user("bob", "bob-password-123", is_admin=False)

    _run_fake_query(client, monkeypatch)
    client.post(
        "/api/script/generate",
        json={
            "edited_rows": [["1", "Alice", "999"], ["2", "Bob", "20"]],
            "key_columns": ["id"], "schema": "dbo", "table": "Accounts", "kind": "update",
        },
    )
    client.post("/api/logout")

    client.post("/api/login", json={"username": "bob", "password": "bob-password-123"})
    resp = client.get("/api/history")
    assert resp.json()["entries"] == []

    resp = client.get("/api/history?mine_only=false")
    entries = resp.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["username"] == "admin"


# ---------------- Config: connections ----------------

def test_list_connections_includes_the_seeded_default(client):
    _login(client)
    resp = client.get("/api/config/connections")
    assert resp.status_code == 200
    conns = resp.json()["connections"]
    assert len(conns) == 1
    assert conns[0]["key"] == "default"
    assert conns[0]["is_active"] is True
    assert "password" not in conns[0]  # never serialized back


def test_create_connection_and_it_appears_in_the_list(client):
    _login(client)
    resp = client.post(
        "/api/config/connections",
        json={"name": "Staging", "server": "stg.example.com", "port": 1433, "username": "svc", "password": "s3cret"},
    )
    assert resp.status_code == 200
    created = resp.json()
    assert created["key"] == "staging"
    assert created["is_active"] is False  # a second connection doesn't steal activeness

    conns = client.get("/api/config/connections").json()["connections"]
    assert {c["key"] for c in conns} == {"default", "staging"}


def test_create_connection_requires_a_name(client):
    _login(client)
    resp = client.post("/api/config/connections", json={"name": "  ", "server": "x"})
    assert resp.status_code == 400


def test_update_connection_leaves_password_unchanged_when_blank(client, monkeypatch):
    _login(client)
    client.post(
        "/api/config/connections",
        json={"name": "Staging", "server": "stg.example.com", "username": "svc", "password": "orig-pass"},
    )
    resp = client.put(
        "/api/config/connections/staging",
        json={"name": "Staging Renamed", "server": "stg2.example.com", "username": "svc", "password": ""},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Staging Renamed"
    assert resp.json()["server"] == "stg2.example.com"

    import web.server as server_mod

    conn = server_mod.load_config().connections["staging"]
    assert conn.get_password() == "orig-pass"


def test_update_unknown_connection_404s(client):
    _login(client)
    resp = client.put("/api/config/connections/nope", json={"name": "X", "server": "x"})
    assert resp.status_code == 404


def test_delete_connection(client):
    _login(client)
    client.post("/api/config/connections", json={"name": "Staging", "server": "s", "password": "p"})
    resp = client.delete("/api/config/connections/staging")
    assert resp.status_code == 200
    conns = client.get("/api/config/connections").json()["connections"]
    assert {c["key"] for c in conns} == {"default"}


def test_delete_active_connection_is_rejected(client):
    _login(client)
    resp = client.delete("/api/config/connections/default")
    assert resp.status_code == 400


def test_activate_connection_switches_active_flag(client):
    _login(client)
    client.post("/api/config/connections", json={"name": "Staging", "server": "s", "password": "p"})
    resp = client.post("/api/config/connections/staging/activate")
    assert resp.status_code == 200
    assert resp.json()["active_connection"] == "staging"

    conns = {c["key"]: c for c in client.get("/api/config/connections").json()["connections"]}
    assert conns["staging"]["is_active"] is True
    assert conns["default"]["is_active"] is False


def test_activate_unknown_connection_404s(client):
    _login(client)
    resp = client.post("/api/config/connections/nope/activate")
    assert resp.status_code == 404


def test_test_connection_endpoint_by_key(client, monkeypatch):
    import web.server as server_mod

    monkeypatch.setattr(server_mod.mssql, "test_connection", lambda conn: (True, "Connected to 'Db'.", 5.0))
    _login(client)
    resp = client.post("/api/config/connections/test", json={"key": "default"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is True
    assert "Connected" in body["message"]


def test_test_connection_endpoint_adhoc_fields(client, monkeypatch):
    import web.server as server_mod

    captured = {}

    def _fake_test(conn):
        captured["server"] = conn.server
        captured["password"] = conn.get_password()
        return False, "nope", 1.0

    monkeypatch.setattr(server_mod.mssql, "test_connection", _fake_test)
    _login(client)
    resp = client.post(
        "/api/config/connections/test",
        json={"name": "Adhoc", "server": "adhoc.example.com", "password": "hunter2"},
    )
    assert resp.status_code == 200
    assert resp.json()["connected"] is False
    assert captured["server"] == "adhoc.example.com"
    assert captured["password"] == "hunter2"


def test_test_connection_unknown_key_404s(client):
    _login(client)
    resp = client.post("/api/config/connections/test", json={"key": "nope"})
    assert resp.status_code == 404


def test_connections_require_login(client):
    assert client.get("/api/config/connections").status_code == 401
    assert client.post("/api/config/connections", json={"name": "x", "server": "x"}).status_code == 401


# ---------------- Config: AI settings ----------------

def test_get_ai_config_defaults(client):
    _login(client)
    resp = client.get("/api/config/ai")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["has_key"] is False


def test_set_ai_config_stores_key_and_enables(client):
    _login(client)
    resp = client.post("/api/config/ai", json={"model": "gemini-3.6-flash", "api_key": "fake-key-123", "enabled": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_key"] is True
    assert body["enabled"] is True
    assert body["model"] == "gemini-3.6-flash"

    again = client.get("/api/config/ai").json()
    assert again["has_key"] is True


def test_set_ai_config_new_key_enables_even_if_form_sent_enabled_false(client):
    # Regression: the Settings form's "Enabled" checkbox reflects the
    # PREVIOUS saved state when the page loads (false, on a brand-new
    # config) - a user pasting in their first key without separately
    # re-ticking the box should still end up enabled, not silently off.
    _login(client)
    resp = client.post("/api/config/ai", json={"model": "gemini-3.6-flash", "api_key": "fake-key-123", "enabled": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_key"] is True
    assert body["enabled"] is True


def test_set_ai_config_blank_key_leaves_existing_key(client):
    _login(client)
    client.post("/api/config/ai", json={"model": "gemini-3.6-flash", "api_key": "fake-key-123", "enabled": True})
    client.post("/api/config/ai", json={"model": "gemini-3.6-flash", "api_key": "", "enabled": True})
    assert client.get("/api/config/ai").json()["has_key"] is True


def test_test_ai_key_endpoint(client, monkeypatch):
    import web.server as server_mod

    monkeypatch.setattr(
        server_mod.ai_assist, "test_api_key",
        lambda api_key, model, timeout=15: server_mod.ai_assist.AIResponse(success=True, text="OK"),
    )
    _login(client)
    resp = client.post("/api/config/ai/test", json={"api_key": "whatever", "model": "gemini-3.6-flash"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True


# ---------------- Saved queries ----------------

def test_saved_queries_round_trip(client):
    _login(client)
    assert client.get("/api/queries/saved").json()["queries"] == []

    resp = client.post("/api/queries/saved", json={"name": "My Widgets", "sql": "SELECT * FROM Widgets"})
    assert resp.status_code == 200

    queries = client.get("/api/queries/saved").json()["queries"]
    assert len(queries) == 1
    assert queries[0]["name"] == "My Widgets"
    assert queries[0]["sql"] == "SELECT * FROM Widgets"

    resp = client.delete("/api/queries/saved/My Widgets")
    assert resp.status_code == 200
    assert client.get("/api/queries/saved").json()["queries"] == []


def test_save_query_requires_name_and_sql(client):
    _login(client)
    resp = client.post("/api/queries/saved", json={"name": "", "sql": "SELECT 1"})
    assert resp.status_code == 400


# ---------------- Schema validation ----------------

def test_schema_validation_requires_a_query_first(client):
    _login(client)
    resp = client.post("/api/schema/validate", json={"schema": "dbo", "table": "Accounts", "key_columns": ["id"]})
    assert resp.status_code == 400


def test_schema_validation_reports_missing_columns(client, monkeypatch):
    import web.server as server_mod

    _run_fake_query(client, monkeypatch)
    monkeypatch.setattr(
        server_mod.mssql, "get_table_columns",
        lambda conn, schema, table: {"id": "int", "name": "varchar"},  # missing "amount"
    )
    resp = client.post("/api/schema/validate", json={"schema": "dbo", "table": "Accounts", "key_columns": ["id"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["table_exists"] is True
    assert body["missing_columns"] == ["amount"]
    assert body["ok"] is False


def test_schema_validation_table_not_found(client, monkeypatch):
    import web.server as server_mod

    _run_fake_query(client, monkeypatch)
    monkeypatch.setattr(server_mod.mssql, "get_table_columns", lambda conn, schema, table: {})
    resp = client.post("/api/schema/validate", json={"schema": "dbo", "table": "Ghost", "key_columns": []})
    assert resp.status_code == 200
    assert resp.json()["table_exists"] is False


# ---------------- Snapshots ----------------

def test_snapshot_export_requires_a_query_first(client):
    _login(client)
    resp = client.post("/api/snapshot/export", json={"name": "snap1"})
    assert resp.status_code == 400


def test_snapshot_export_list_and_delete(client, monkeypatch):
    _run_fake_query(client, monkeypatch)
    resp = client.post("/api/snapshot/export", json={"name": "snap1"})
    assert resp.status_code == 200
    table_name = resp.json()["snapshot_table"]

    snaps = client.get("/api/snapshot/list").json()["snapshots"]
    assert len(snaps) == 1
    assert snaps[0]["display_name"] == "snap1"
    assert snaps[0]["row_count"] == 2

    resp = client.delete(f"/api/snapshot/{table_name}")
    assert resp.status_code == 200
    assert client.get("/api/snapshot/list").json()["snapshots"] == []


def test_snapshot_diff_reports_changes(client, monkeypatch):
    _run_fake_query(client, monkeypatch)
    id_a = client.post("/api/snapshot/export", json={"name": "before"}).json()["snapshot_table"]

    # Edit and re-export as a second, independent snapshot.
    edited = [["1", "Alice", "999"], ["2", "Bob", "20"]]
    client.post("/api/grid/diff", json={"edited_rows": edited, "key_columns": ["id"]})
    # Snapshot export always exports the ORIGINAL query rows (server-side
    # state), so simulate a genuinely different second export by running
    # a fresh fake query with different data instead.
    import web.server as server_mod
    from app.db.mssql import QueryResult

    changed_result = QueryResult(
        columns=["id", "name", "amount"], rows=[[1, "Alice", 999], [3, "Carol", 50]],
        elapsed_ms=1.0, source_table="dbo.Accounts", source_schema="dbo",
    )
    monkeypatch.setattr(server_mod.mssql, "run_query", lambda conn, sql: changed_result)
    client.post("/api/query/run", json={"sql": "SELECT * FROM dbo.Accounts"})
    id_b = client.post("/api/snapshot/export", json={"name": "after"}).json()["snapshot_table"]

    resp = client.post("/api/snapshot/diff", json={"snapshot_a": id_a, "snapshot_b": id_b, "key_columns": ["id"]})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["added"]) == 1     # id 3 (Carol)
    assert len(body["removed"]) == 1   # id 2 (Bob)
    assert len(body["changed"]) == 1   # id 1's amount 10 -> 999


def test_snapshot_diff_rejects_mismatched_columns(client, monkeypatch):
    _run_fake_query(client, monkeypatch)
    id_a = client.post("/api/snapshot/export", json={"name": "snap_a"}).json()["snapshot_table"]

    import web.server as server_mod
    from app.db.mssql import QueryResult

    other_result = QueryResult(
        columns=["id", "other_col"], rows=[[1, "x"]], elapsed_ms=1.0, source_table="dbo.Other", source_schema="dbo",
    )
    monkeypatch.setattr(server_mod.mssql, "run_query", lambda conn, sql: other_result)
    client.post("/api/query/run", json={"sql": "SELECT * FROM dbo.Other"})
    id_b = client.post("/api/snapshot/export", json={"name": "snap_b"}).json()["snapshot_table"]

    resp = client.post("/api/snapshot/diff", json={"snapshot_a": id_a, "snapshot_b": id_b, "key_columns": []})
    assert resp.status_code == 400


# ---------------- AI Assist ----------------

def test_ai_endpoints_require_configuration(client):
    _login(client)
    for path, body in [
        ("/api/ai/suggest", {"sql": "SELECT 1"}),
        ("/api/ai/optimize", {"sql": "SELECT 1"}),
        ("/api/ai/explain", {"sql": "SELECT 1"}),
        ("/api/ai/nl_where", {"request": "recent rows"}),
        ("/api/ai/review", {"sql_text": "UPDATE x SET y = 1"}),
    ]:
        resp = client.post(path, json=body)
        assert resp.status_code == 400, path


def _enable_ai(client):
    resp = client.post("/api/config/ai", json={"model": "gemini-3.6-flash", "api_key": "fake-key", "enabled": True})
    assert resp.status_code == 200


def test_ai_suggest_returns_extracted_sql(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    _enable_ai(client)
    monkeypatch.setattr(
        server_mod.ai_assist, "suggest_snippet",
        lambda api_key, model, sql, intent: server_mod.ai_assist.AIResponse(
            success=True, text="```sql\nSELECT 2\n```", extracted_sql="SELECT 2",
        ),
    )
    resp = client.post("/api/ai/suggest", json={"sql": "SELECT 1", "intent": "double it"})
    assert resp.status_code == 200
    assert resp.json()["extracted_sql"] == "SELECT 2"


def test_ai_nl_where_uses_current_query_columns(client, monkeypatch):
    import web.server as server_mod

    _run_fake_query(client, monkeypatch)
    _enable_ai(client)
    captured = {}

    def _fake_suggest_where(api_key, model, columns, request, table_context=""):
        captured["columns"] = columns
        return server_mod.ai_assist.AIResponse(success=True, text="```sql\namount > 100\n```", extracted_sql="amount > 100")

    monkeypatch.setattr(server_mod.ai_assist, "suggest_where_clause", _fake_suggest_where)
    resp = client.post("/api/ai/nl_where", json={"request": "big amounts"})
    assert resp.status_code == 200
    assert captured["columns"] == ["id", "name", "amount"]
    assert resp.json()["extracted_sql"] == "amount > 100"


def test_ai_review_endpoint(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    _enable_ai(client)
    monkeypatch.setattr(
        server_mod.ai_assist, "review_script",
        lambda api_key, model, sql_text: server_mod.ai_assist.AIResponse(success=True, text="Looks safe"),
    )
    resp = client.post("/api/ai/review", json={"sql_text": "UPDATE x SET y = 1 WHERE id = 1;"})
    assert resp.status_code == 200
    assert resp.json()["text"] == "Looks safe"


# ---------------- Date Anomaly ----------------
# Web port of the desktop app's "🩹 Date Anomaly" tab - state lives
# server-side per session (web/session_store.py's DateAnomalyState), so
# these tests drive the three endpoints in sequence the same way the
# frontend does (Detect -> Resolve -> Generate), monkeypatching
# mssql.run_query to return different fake QueryResults per call by
# inspecting which query text came in.

def _da_fake_run_query(
    monkeypatch, *, anomaly_rows=None, correct_row=None, item_rows=None, item_xml_id_rows=None, xml_rows=None,
    anomalous_rows=None, item_status_rows=None, account_rows=None,
):
    """Wires server_mod.mssql.run_query to answer each of the Date
    Anomaly workflow's distinct queries based on substrings only that
    query contains - mirrors how _run_fake_query stubs the Workspace
    query with one fixed result, just branching on 5 possible queries
    instead of 1. correct_row's READING_DATE is a real datetime.date
    (not a string) - that's what pytds actually returns for a DATE
    column, and app.core.date_anomaly.patch_xml_dates specifically
    branches on datetime.date/datetime.datetime, so a string there
    would test something pytds would never actually hand back.

    anomalous_rows defaults to [] (no open GCCOM_ANOMALOUS records) -
    build_anomalous_query already filters to open statuses server-side,
    so an empty result here is the normal "nothing to cancel" case, not
    a stub gap; pass explicit rows to test Part 5.

    item_status_rows defaults to [] (no item-to-bill row still sitting
    at STTOBILL00) - same reasoning, build_item_status_query already
    filters server-side, so empty means "nothing to advance"; pass
    explicit rows to test Part 3.

    account_rows defaults to one row (build_niss_account_query is a
    best-effort TOP 1 lookup the detect route always fires - see that
    route's own comment - so every Date Anomaly test using this fixture
    needs a branch for it or the fake dispatcher's fallback
    AssertionError fires on every single Detect call); pass [] to test
    the "no resolving contracted-service row" / account-is-None case.

    item_xml_id_rows answers build_item_xml_id_query (RJ, 2026-09-13:
    "you are updating using id_item_to_bill, you need to get first the
    id_xml from gccom_item_to_bill and update with that id") - the
    REQUIRED first lookup before xml_rows is even queried. Defaults to
    item 500 -> id_xml 500 (same value) so existing tests written before
    this fix don't need touching; pass a row where the two differ (or
    override xml_rows to be keyed by the real id_xml) to exercise the
    real fix end to end."""
    import datetime as _datetime

    import web.server as server_mod
    from app.db.mssql import QueryResult

    # 5th column, READING_TYPE, mirrors real build_detect_query output
    # (SELECT r.* - always includes it) - defaults to a plain cycle-type
    # code so existing tests never accidentally trigger Part 6's orphan
    # cleanup; pass a row ending in date_anomaly.READING_TYPE_ORPHAN_USAGE
    # to test that path. 6th/7th columns (READING_TYPE_DESC,
    # IS_CYCLE_READING) mirror the LEFT JOIN + CASE build_detect_query
    # added 2026-09-13 (RJ: "if not all cycle, i need to know if it
    # contains removal or not") - default row uses TIPTL00001
    # ("Installation" per the live GCGT_RE_READING_TYPE lookup), which is
    # non-cycle (IS_CYCLE_READING 0), same as every other type here except
    # TIPTL00003/TIPTL00005.
    anomaly_rows = (
        anomaly_rows if anomaly_rows is not None
        else [[101, 10000000231, "2024-01-01", "6000STSRED", "TIPTL00001", "Installation", 0]]
    )
    correct_row = correct_row if correct_row is not None else [[999, 10000000240, _datetime.date(2024, 3, 1)]]
    item_rows = item_rows if item_rows is not None else [[101, 500]]
    item_xml_id_rows = item_xml_id_rows if item_xml_id_rows is not None else [[500, 500]]
    xml_rows = xml_rows if xml_rows is not None else [
        [500, "<Root><initDate>2024-01-01</initDate><readingFromDate>2024-01-01</readingFromDate></Root>"]
    ]
    anomalous_rows = anomalous_rows if anomalous_rows is not None else []
    item_status_rows = item_status_rows if item_status_rows is not None else []
    account_rows = account_rows if account_rows is not None else [["ACC-001", "OFS-1", "ACTIVE"]]

    def fake_run_query(conn, sql):
        if "AS ACCOUNT" in sql:
            return QueryResult(
                columns=["ACCOUNT", "OFFERED_SERVICE", "CONTRACT_STATUS"], rows=account_rows, elapsed_ms=1.0,
            )
        if "READ_STATUS = '6000STSRED'" in sql:
            return QueryResult(
                columns=[
                    "ID_READING", "ID_BILLING_PERIOD", "READING_DATE", "READ_STATUS", "READING_TYPE",
                    "READING_TYPE_DESC", "IS_CYCLE_READING",
                ],
                rows=anomaly_rows, elapsed_ms=1.0,
            )
        if "READ_STATUS = '7000STSRED'" in sql:
            return QueryResult(
                columns=["ID_READING", "ID_BILLING_PERIOD", "READING_DATE"],
                rows=correct_row, elapsed_ms=1.0,
            )
        if "GCCOM_READINGS_ITEMSTOBILL" in sql:
            return QueryResult(columns=["ID_READING", "ID_ITEM_TO_BILL"], rows=item_rows, elapsed_ms=1.0)
        if "GCCOM_ITEMS_TO_BILL_XML" in sql:
            return QueryResult(columns=["ID_XML", "XML_TO_BILL"], rows=xml_rows, elapsed_ms=1.0)
        # GITB.ID_XML is unique to build_item_xml_id_query (the REQUIRED
        # first lookup, against GCCOM_ITEMS_TO_BILL itself - GITB alias,
        # no _XML suffix - NOT GCCOM_ITEMS_TO_BILL_XML/GITBX, matched
        # above). RJ, 2026-09-13 fix - see this fixture's own docstring.
        if "GITB.ID_XML" in sql:
            return QueryResult(columns=["ID_ITEM_TO_BILL", "ID_XML"], rows=item_xml_id_rows, elapsed_ms=1.0)
        if "GCCOM_ANOMALOUS" in sql:
            return QueryResult(columns=["ID_ITEM_TO_BILL", "ANOMALOUS_STATUS"], rows=anomalous_rows, elapsed_ms=1.0)
        # STTOBILL00 is unique to build_item_status_query's WHERE clause -
        # safe to key off of since no other Date Anomaly query mentions it.
        if "STTOBILL00" in sql:
            return QueryResult(columns=["ID_ITEM_TO_BILL", "STATUS"], rows=item_status_rows, elapsed_ms=1.0)
        raise AssertionError(f"Unexpected query in Date Anomaly test: {sql}")

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    monkeypatch.setattr(server_mod.mssql, "get_table_columns", lambda conn, schema, table: {"ID_READING": "int"})


def test_date_anomaly_detect_includes_reading_type_description(client, monkeypatch):
    # RJ, 2026-09-13: "if not all cycle, i need to know if it contains
    # removal or not" - Detect's rows now carry the actual reading type
    # name (not just the bare TIPTL code) plus an is_cycle_reading flag.
    _login(client)
    _da_fake_run_query(
        monkeypatch,
        anomaly_rows=[[101, 10000000231, "2024-01-01", "6000STSRED", "TIPTL00002", "Removal", 0]],
    )
    resp = client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})
    assert resp.status_code == 200
    row = resp.json()["rows"][0]
    assert row["reading_type"] == "TIPTL00002"
    assert row["reading_type_desc"] == "Removal"
    assert row["is_cycle_reading"] is False


def test_date_anomaly_detect_requires_niss(client):
    _login(client)
    resp = client.post("/api/date-anomaly/detect", json={"niss": "  ", "threshold": 0})
    assert resp.status_code == 400


def test_date_anomaly_detect_finds_anomalies_and_correct_date(client, monkeypatch):
    _login(client)
    _da_fake_run_query(monkeypatch)
    resp = client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["id_reading"] == "101"
    assert body["correct_date"] == "2024-03-01"
    assert body["correct_date_reading"] == "999"


def test_date_anomaly_detect_no_anomalies(client, monkeypatch):
    _login(client)
    _da_fake_run_query(monkeypatch, anomaly_rows=[])
    resp = client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 0})
    assert resp.status_code == 200
    assert resp.json()["rows"] == []


def test_date_anomaly_detect_billing_period_count_single_period(client, monkeypatch):
    # The default fixture's single anomaly row is enough on its own to
    # cover the "1 distinct billing period" (not >1, no orange highlight)
    # case - explicit here since no other test asserts on this field.
    _login(client)
    _da_fake_run_query(monkeypatch)
    resp = client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})
    assert resp.status_code == 200
    assert resp.json()["billing_period_count"] == 1


def test_date_anomaly_detect_billing_period_count_multiple_periods(client, monkeypatch):
    # Two anomalous readings landing in two distinct ID_BILLING_PERIOD
    # values should report a count of 2 - this is what drives the
    # frontend's orange multi-period row highlighting.
    _login(client)
    _da_fake_run_query(
        monkeypatch,
        anomaly_rows=[
            [101, 10000000231, "2024-01-01", "6000STSRED", "TIPTL00001"],
            [102, 10000000232, "2024-02-01", "6000STSRED", "TIPTL00001"],
        ],
    )
    resp = client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})
    assert resp.status_code == 200
    assert resp.json()["billing_period_count"] == 2


def test_date_anomaly_explain_ai_requires_detect_first(client):
    _login(client)
    _enable_ai(client)
    resp = client.post("/api/date-anomaly/explain-ai")
    assert resp.status_code == 400


def test_date_anomaly_explain_ai_requires_ai_configuration(client, monkeypatch):
    _login(client)
    _da_fake_run_query(monkeypatch)
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})
    resp = client.post("/api/date-anomaly/explain-ai")
    assert resp.status_code == 400


def test_date_anomaly_explain_ai_returns_ai_text(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    _enable_ai(client)
    _da_fake_run_query(monkeypatch)
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})

    captured = {}

    def fake_explain(api_key, model, case_context):
        captured["case_context"] = case_context
        return server_mod.ai_assist.AIResponse(success=True, text="Looks like a routine single-NISS case.")

    monkeypatch.setattr(server_mod.ai_assist, "explain_single_niss_case", fake_explain)
    resp = client.post("/api/date-anomaly/explain-ai")
    assert resp.status_code == 200
    assert resp.json()["text"] == "Looks like a routine single-NISS case."
    assert "NISS: 10450618-301" in captured["case_context"]
    assert "Anomalous readings found: 1" in captured["case_context"]


def test_date_anomaly_resolve_requires_detect_first(client):
    _login(client)
    resp = client.post("/api/date-anomaly/resolve")
    assert resp.status_code == 400


def test_date_anomaly_resolve_maps_items_and_xml(client, monkeypatch):
    _login(client)
    _da_fake_run_query(monkeypatch)
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})
    resp = client.post("/api/date-anomaly/resolve")
    assert resp.status_code == 200
    body = resp.json()
    assert body["linked"] == 1
    assert body["total_readings"] == 1
    assert body["distinct_items"] == 1
    assert body["total_items"] == 1
    assert body["xml_count"] == 1
    assert body["anomalous_count"] == 0  # no open GCCOM_ANOMALOUS rows in this fixture


def test_date_anomaly_resolve_and_generate_cancel_open_anomalies(client, monkeypatch):
    # New business rule: cancel every OPEN (ESTAN00009/ESTAN00001)
    # GCCOM_ANOMALOUS record for an item-to-bill this correction touches.
    _login(client)
    _da_fake_run_query(monkeypatch, anomalous_rows=[[500, "ESTAN00009"]])
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})

    resolve_resp = client.post("/api/date-anomaly/resolve")
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["anomalous_count"] == 1

    gen_resp = client.post("/api/date-anomaly/generate", json={"program": "JIRA-1234"})
    assert gen_resp.status_code == 200
    gen_body = gen_resp.json()
    assert gen_body["anomalous_count"] == 1
    assert "GCCOM_ANOMALOUS" in gen_body["sql_text"]
    assert "WHERE ID_ITEM_TO_BILL = 500" in gen_body["sql_text"]
    assert "ESTAN00005" in gen_body["sql_text"]


def test_date_anomaly_resolve_and_generate_advances_item_status(client, monkeypatch):
    # New business rule: item-to-bill rows still at STTOBILL00 advance to
    # STTOBILL01 as part of the correction (Part 3).
    _login(client)
    _da_fake_run_query(monkeypatch, item_status_rows=[[500, "STTOBILL00"]])
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})

    resolve_resp = client.post("/api/date-anomaly/resolve")
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["item_status_count"] == 1

    gen_resp = client.post("/api/date-anomaly/generate", json={"program": "JIRA-1234"})
    assert gen_resp.status_code == 200
    gen_body = gen_resp.json()
    assert gen_body["item_status_count"] == 1
    assert "STTOBILL01" in gen_body["sql_text"]


def test_date_anomaly_detect_returns_account_info(client, monkeypatch):
    _login(client)
    _da_fake_run_query(monkeypatch, account_rows=[["ACC-999", "OFS-2", "ACTIVE"]])
    resp = client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})
    assert resp.status_code == 200
    body = resp.json()
    assert body["account"] == "ACC-999"
    assert body["offered_service"] == "OFS-2"
    assert body["contract_status"] == "ACTIVE"


def test_date_anomaly_detect_account_none_when_no_resolving_row(client, monkeypatch):
    _login(client)
    _da_fake_run_query(monkeypatch, account_rows=[])
    resp = client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})
    assert resp.status_code == 200
    body = resp.json()
    assert body["account"] is None


def test_date_anomaly_generate_cleans_up_orphan_usage_readings(client, monkeypatch):
    # New business rule: an anomalous reading whose READING_TYPE is the
    # non-cycle TIPTL00011 type also gets its GCCOM_READINGS_ITEMSTOBILL
    # link deleted and READ_STATUS reset to 1000STSRED (Part 6) - see
    # date_anomaly.READING_TYPE_ORPHAN_USAGE.
    _login(client)
    _da_fake_run_query(
        monkeypatch,
        anomaly_rows=[[101, 10000000231, "2024-01-01", "6000STSRED", "TIPTL00011"]],
        item_rows=[[101, 500]],
    )
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})
    client.post("/api/date-anomaly/resolve")

    gen_resp = client.post("/api/date-anomaly/generate", json={"program": "JIRA-1234"})
    assert gen_resp.status_code == 200
    gen_body = gen_resp.json()
    assert gen_body["orphan_reading_count"] == 1
    assert "DELETE FROM" in gen_body["sql_text"]
    assert "1000STSRED" in gen_body["sql_text"]
    assert "IND_USAGE_TO_CAL" in gen_body["sql_text"]


def test_date_anomaly_generate_no_orphan_cleanup_for_cycle_reading_types(client, monkeypatch):
    # The default fixture reading is READING_TYPE TIPTL00001 (a plain
    # cycle-ish code, not TIPTL00011) - Part 6 must stay a no-op for it.
    _login(client)
    _da_fake_run_query(monkeypatch)
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})
    client.post("/api/date-anomaly/resolve")

    gen_resp = client.post("/api/date-anomaly/generate", json={"program": "JIRA-1234"})
    assert gen_resp.status_code == 200
    gen_body = gen_resp.json()
    assert gen_body["orphan_reading_count"] == 0
    assert "DELETE FROM" not in gen_body["sql_text"]


def test_date_anomaly_explain_ai_context_includes_account_and_reading_detail(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    _enable_ai(client)
    _da_fake_run_query(monkeypatch, account_rows=[["ACC-777", "OFS-3", "ACTIVE"]])
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})

    captured = {}

    def fake_explain(api_key, model, case_context):
        captured["case_context"] = case_context
        return server_mod.ai_assist.AIResponse(success=True, text="ok")

    monkeypatch.setattr(server_mod.ai_assist, "explain_single_niss_case", fake_explain)
    resp = client.post("/api/date-anomaly/explain-ai")
    assert resp.status_code == 200
    ctx = captured["case_context"]
    assert "Account: ACC-777" in ctx
    assert "ID_READING 101" in ctx
    assert "billing period 10000000231" in ctx
    assert "2024-01-01" in ctx


def test_date_anomaly_explain_ai_context_flags_multiple_billing_periods(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    _enable_ai(client)
    _da_fake_run_query(
        monkeypatch,
        anomaly_rows=[
            [101, 10000000231, "2024-01-01", "6000STSRED", "TIPTL00001"],
            [102, 10000000232, "2024-02-01", "6000STSRED", "TIPTL00001"],
        ],
    )
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})

    captured = {}

    def fake_explain(api_key, model, case_context):
        captured["case_context"] = case_context
        return server_mod.ai_assist.AIResponse(success=True, text="ok")

    monkeypatch.setattr(server_mod.ai_assist, "explain_single_niss_case", fake_explain)
    resp = client.post("/api/date-anomaly/explain-ai")
    assert resp.status_code == 200
    ctx = captured["case_context"]
    assert "Distinct billing periods among them: 2" in ctx
    assert "spans more than one billing period" in ctx


def test_date_anomaly_detect_resolve_generate_include_explanation(client, monkeypatch):
    _login(client)
    _da_fake_run_query(monkeypatch)
    detect_body = client.post(
        "/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230}
    ).json()
    assert "What's wrong" in detect_body["explanation"]

    resolve_body = client.post("/api/date-anomaly/resolve").json()
    assert "What's wrong" in resolve_body["explanation"]

    gen_body = client.post("/api/date-anomaly/generate", json={"program": "JIRA-1234"}).json()
    assert "What's wrong" in gen_body["explanation"]
    assert "What's fixed" in gen_body["explanation"]


def test_date_anomaly_generate_clean_strips_comments(client, monkeypatch):
    _login(client)
    _da_fake_run_query(monkeypatch)
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})
    client.post("/api/date-anomaly/resolve")

    gen_resp = client.post("/api/date-anomaly/generate", json={"program": "JIRA-1234", "clean": True})
    assert gen_resp.status_code == 200
    body = gen_resp.json()
    assert not any(line.strip().startswith("--") for line in body["sql_text"].split("\n"))
    assert "BEGIN TRANSACTION" not in body["sql_text"]  # removed per explicit analyst request
    # Same statement counts as the non-clean version would report -
    # clean only affects sql_text, not the reported counts.
    assert body["reading_count"] == 1


def test_date_anomaly_history_records_detect_and_generate(client, monkeypatch):
    _login(client)
    _da_fake_run_query(monkeypatch)
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})

    history_after_detect = client.get("/api/date-anomaly/history").json()["entries"]
    assert len(history_after_detect) == 1
    entry = history_after_detect[0]
    assert entry["niss"] == "10450618-301"
    assert entry["generated"] is False
    assert entry["item_count"] is None  # not known yet - Resolve hasn't run

    client.post("/api/date-anomaly/resolve")
    client.post("/api/date-anomaly/generate", json={"program": "JIRA-1234"})

    history_after_generate = client.get("/api/date-anomaly/history").json()["entries"]
    assert len(history_after_generate) == 1  # still one row - refined in place, not duplicated
    entry = history_after_generate[0]
    assert entry["generated"] is True
    assert entry["item_count"] == 1
    assert entry["xml_count"] == 1


def test_date_anomaly_history_scopes_to_mine_only_by_default(client, monkeypatch):
    _login(client)
    _da_fake_run_query(monkeypatch)
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})

    _create_and_login_as(client, "otheruser", "editor")
    _da_fake_run_query(monkeypatch)
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-302", "threshold": 0})

    mine = client.get("/api/date-anomaly/history").json()["entries"]
    assert [e["niss"] for e in mine] == ["10450618-302"]

    everyone = client.get("/api/date-anomaly/history?mine_only=false").json()["entries"]
    assert {e["niss"] for e in everyone} == {"10450618-301", "10450618-302"}


def test_date_anomaly_resolve_handles_one_reading_mapped_to_multiple_items(client, monkeypatch):
    # Regression test for the real-world bug report: GCCOM_READINGS_ITEMSTOBILL
    # returned 2 rows for the same ID_READING (one reading rolling up to two
    # separate item-to-bill rows) - a dict[reading]=item overwrite silently
    # dropped the second one. Both must now surface.
    _login(client)
    _da_fake_run_query(
        monkeypatch,
        item_rows=[[101, 500], [101, 501]],
        xml_rows=[
            [500, "<Root><initDate>2024-01-01</initDate></Root>"],
            [501, "<Root><initDate>2024-01-01</initDate></Root>"],
        ],
    )
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})
    resp = client.post("/api/date-anomaly/resolve")
    assert resp.status_code == 200
    body = resp.json()
    assert body["linked"] == 1          # still 1 reading linked...
    assert body["distinct_items"] == 2  # ...but to 2 distinct item-to-bill ids
    assert body["total_items"] == 2
    assert body["xml_count"] == 2

    gen_resp = client.post("/api/date-anomaly/generate", json={"program": "JIRA-1234"})
    assert gen_resp.status_code == 200
    gen_body = gen_resp.json()
    assert gen_body["item_count"] == 2
    assert gen_body["xml_count"] == 2
    assert "WHERE ID_ITEM_TO_BILL = 500" in gen_body["sql_text"]
    assert "WHERE ID_ITEM_TO_BILL = 501" in gen_body["sql_text"]


def test_date_anomaly_generate_requires_detect_first(client):
    _login(client)
    resp = client.post("/api/date-anomaly/generate", json={"program": "JIRA-1"})
    assert resp.status_code == 400


def test_date_anomaly_generate_requires_program(client, monkeypatch):
    _login(client)
    _da_fake_run_query(monkeypatch)
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})
    resp = client.post("/api/date-anomaly/generate", json={"program": "  "})
    assert resp.status_code == 400


def test_date_anomaly_generate_produces_three_part_script(client, monkeypatch):
    _login(client)
    _da_fake_run_query(monkeypatch)
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})
    client.post("/api/date-anomaly/resolve")
    resp = client.post("/api/date-anomaly/generate", json={"program": "JIRA-1234"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reading_count"] == 1
    assert body["item_count"] == 1
    assert body["xml_count"] == 1
    assert "READING_PREV_DATE" in body["sql_text"]
    assert "INI_DATE" in body["sql_text"]
    assert "XML_TO_BILL" in body["sql_text"]
    assert "JIRA-1234" in body["sql_text"]

    # Also recorded to Script History, same as desktop.
    history = client.get("/api/history").json()["entries"]
    assert any(e["kind"] == "date_anomaly_correction" for e in history)


def test_date_anomaly_generate_without_resolve_still_fixes_readings(client, monkeypatch):
    # Generate is allowed right after Detect (Resolve is a separate,
    # optional-before-generate step in the UI, not a hard prerequisite
    # enforced server-side) - READING_PREV_DATE still gets fixed for
    # every anomalous reading even with no item/XML links resolved yet.
    _login(client)
    _da_fake_run_query(monkeypatch)
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})
    resp = client.post("/api/date-anomaly/generate", json={"program": "JIRA-1234"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reading_count"] == 1
    assert body["item_count"] == 0


def test_date_anomaly_generate_lowest_billing_period_only(client, monkeypatch):
    # Two anomalous readings in two distinct billing periods (231 lower,
    # 232 higher) - with lowest_billing_period_only the script should only
    # cover reading 101 (period 231); billing_period_count still reports
    # the full unfiltered count of 2 (it's computed before scoping).
    _login(client)
    _da_fake_run_query(
        monkeypatch,
        anomaly_rows=[
            [101, 10000000231, "2024-01-01", "6000STSRED", "TIPTL00001"],
            [102, 10000000232, "2024-01-15", "6000STSRED", "TIPTL00001"],
        ],
        item_rows=[[101, 500], [102, 500]],
    )
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})
    client.post("/api/date-anomaly/resolve")
    resp = client.post(
        "/api/date-anomaly/generate",
        json={"program": "JIRA-1234", "lowest_billing_period_only": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["scoped_to_lowest_period"] is True
    assert body["billing_period_count"] == 2
    assert body["reading_count"] == 1
    assert "SCOPED TO LOWEST BILLING PERIOD ONLY" in body["sql_text"]


def test_date_anomaly_generate_covers_all_periods_when_not_scoped(client, monkeypatch):
    # Same multi-period fixture as above, but without the flag - both
    # readings should be corrected, not just the lowest-period one.
    _login(client)
    _da_fake_run_query(
        monkeypatch,
        anomaly_rows=[
            [101, 10000000231, "2024-01-01", "6000STSRED", "TIPTL00001"],
            [102, 10000000232, "2024-01-15", "6000STSRED", "TIPTL00001"],
        ],
        item_rows=[[101, 500], [102, 500]],
    )
    client.post("/api/date-anomaly/detect", json={"niss": "10450618-301", "threshold": 10000000230})
    client.post("/api/date-anomaly/resolve")
    resp = client.post("/api/date-anomaly/generate", json={"program": "JIRA-1234"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["scoped_to_lowest_period"] is False
    assert body["reading_count"] == 2
    assert "SCOPED TO LOWEST BILLING PERIOD ONLY" not in body["sql_text"]


def _da_fake_detect_all_run_query(monkeypatch, *, rows=None):
    """Fake mssql.run_query for /api/date-anomaly/detect-all and
    .../detect-all/generate - keys off the detect-all query's LEFT JOIN
    (unique to that one query) and the item-status query's STTOBILL00
    literal (shared with _da_fake_run_query's identical branch)."""
    import web.server as server_mod
    from app.db.mssql import QueryResult

    rows = rows if rows is not None else [[500, "ESTAN00009", "STTOBILL00"], [501, "ESTAN00001", "STTOBILL01"]]

    def fake_run_query(conn, sql):
        if "LEFT JOIN" in sql:
            return QueryResult(
                columns=["ID_ITEM_TO_BILL", "ANOMALOUS_STATUS", "ITEM_STATUS"], rows=rows, elapsed_ms=1.0,
            )
        if "STTOBILL00" in sql:
            pending = [[r[0], "STTOBILL00"] for r in rows if r[2] == "STTOBILL00"]
            return QueryResult(columns=["ID_ITEM_TO_BILL", "STATUS"], rows=pending, elapsed_ms=1.0)
        raise AssertionError(f"Unexpected query in detect-all test: {sql}")

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    # Includes ID_PRINCIPAL_ANOMALY (date_anomaly.ANOMALOUS_TYPE_COLUMN) so
    # the detect-all route's schema pre-check passes in this fixture's
    # happy-path tests - see test_date_anomaly_detect_all_requires_known_
    # type_column below for the "column missing" path.
    monkeypatch.setattr(
        server_mod.mssql, "get_table_columns",
        lambda conn, schema, table: {"ID_READING": "int", "ID_PRINCIPAL_ANOMALY": "int"},
    )


def test_date_anomaly_detect_all_lists_open_anomalies(client, monkeypatch):
    _login(client)
    _da_fake_detect_all_run_query(monkeypatch)
    resp = client.post("/api/date-anomaly/detect-all")
    assert resp.status_code == 200
    body = resp.json()
    rows = body["rows"]
    assert len(rows) == 2
    assert rows[0]["id_item_to_bill"] == "500"
    assert rows[0]["needs_status_advance"] is True
    assert rows[1]["needs_status_advance"] is False  # already at STTOBILL01
    assert body["possibly_truncated"] is False

    import web.server as server_mod
    assert body["limit"] == server_mod.date_anomaly.DETECT_ALL_DEFAULT_LIMIT


def test_date_anomaly_detect_all_flags_possible_truncation(client, monkeypatch):
    # Exactly `limit` rows back is indistinguishable from "there might be
    # more" without a second query - the route treats it as possibly
    # truncated rather than claiming completeness it can't verify.
    import web.server as server_mod

    _login(client)
    full_rows = [
        [i, "ESTAN00009", "STTOBILL00"] for i in range(server_mod.date_anomaly.DETECT_ALL_DEFAULT_LIMIT)
    ]
    _da_fake_detect_all_run_query(monkeypatch, rows=full_rows)
    resp = client.post("/api/date-anomaly/detect-all")
    assert resp.status_code == 200
    assert resp.json()["possibly_truncated"] is True


def test_date_anomaly_detect_all_requires_known_type_column(client, monkeypatch):
    # If ANOMALOUS_TYPE_COLUMN (ID_PRINCIPAL_ANOMALY - flagged unverified
    # in date_anomaly.py) doesn't actually exist on GCCOM_ANOMALOUS, the
    # route should fail with a clear, actionable message instead of
    # letting a raw "Invalid column name" DB error surface, the way the
    # ANOMALOUS_STATUS bug did in an earlier round.
    import web.server as server_mod

    _login(client)
    monkeypatch.setattr(
        server_mod.mssql, "get_table_columns",
        lambda conn, schema, table: {"ID_ITEM_TO_BILL": "int", "ANOMALOUS_STATUS": "varchar"},
    )

    def fail_if_called(conn, sql):
        raise AssertionError("run_query should not be reached when the schema pre-check fails")

    monkeypatch.setattr(server_mod.mssql, "run_query", fail_if_called)
    resp = client.post("/api/date-anomaly/detect-all")
    assert resp.status_code == 502
    assert "ID_PRINCIPAL_ANOMALY" in resp.json()["detail"]


def test_date_anomaly_detect_all_requires_connection(client, monkeypatch):
    # The `client` fixture always seeds a fake "default" active connection
    # (see its own docstring) so every other test never touches a real
    # socket - this is the one test that needs NO active connection, so it
    # overrides load_config() locally to point at an empty connections
    # dict instead. Without this override, get_active_connection() would
    # return the fixture's fake connection and both this test's own
    # get_table_columns/run_query would need patching too, or the request
    # would try to actually reach localhost:1433 and fail some other way
    # instead of exercising the route's own "no connection" 400 path.
    import web.server as server_mod
    from app.config import AppConfig, AIConfig

    _login(client)
    empty_config = AppConfig(connections={}, active_connection="default", ai=AIConfig())
    monkeypatch.setattr(server_mod, "load_config", lambda: empty_config)
    resp = client.post("/api/date-anomaly/detect-all")
    assert resp.status_code == 400


def test_date_anomaly_detect_all_generate_requires_selection(client, monkeypatch):
    _login(client)
    _da_fake_detect_all_run_query(monkeypatch)
    resp = client.post("/api/date-anomaly/detect-all/generate", json={"item_ids": [], "program": "JIRA-1"})
    assert resp.status_code == 400


def test_date_anomaly_detect_all_generate_produces_cleanup_script(client, monkeypatch):
    _login(client)
    _da_fake_detect_all_run_query(monkeypatch)
    resp = client.post(
        "/api/date-anomaly/detect-all/generate",
        json={"item_ids": ["500", "501"], "program": "JIRA-9999"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["item_status_count"] == 1  # only 500 is still at STTOBILL00
    assert body["anomalous_count"] == 2
    assert "STTOBILL01" in body["sql_text"]
    assert "ESTAN00005" in body["sql_text"]
    assert "READING_PREV_DATE" not in body["sql_text"]
    assert "INI_DATE" not in body["sql_text"]
    assert "NOT a full correction" in body["sql_text"]
    assert "JIRA-9999" in body["sql_text"]

    history = client.get("/api/history").json()["entries"]
    assert any("Bulk anomaly cleanup" in e["table_name"] for e in history)


def test_date_anomaly_detect_all_generate_clean_strips_comments(client, monkeypatch):
    _login(client)
    _da_fake_detect_all_run_query(monkeypatch)
    resp = client.post(
        "/api/date-anomaly/detect-all/generate",
        json={"item_ids": ["500"], "program": "JIRA-1", "clean": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert not any(line.strip().startswith("--") for line in body["sql_text"].split("\n"))
    assert "BEGIN TRANSACTION" not in body["sql_text"]  # removed per explicit analyst request


def test_date_anomaly_detect_all_generate_requires_editor_role(client, monkeypatch):
    _da_fake_detect_all_run_query(monkeypatch)
    _create_and_login_as(client, "viewer2", "viewer")
    resp = client.post(
        "/api/date-anomaly/detect-all/generate", json={"item_ids": ["500"], "program": "JIRA-1"}
    )
    assert resp.status_code == 403


def test_date_anomaly_detect_all_returns_descriptions_and_enrichment(client, monkeypatch):
    # Row 500 has everything the lookup joins can provide; row 501 has
    # NULLs across the board (no matching description, no billing-service
    # chain) - the response should still carry the raw codes for 501 so
    # the frontend can fall back to them instead of showing blank cells.
    import web.server as server_mod
    from app.db.mssql import QueryResult

    _login(client)

    def fake_run_query(conn, sql):
        assert "LEFT JOIN" in sql
        return QueryResult(
            columns=[
                "ID_ITEM_TO_BILL", "ANOMALOUS_STATUS", "ANOMALOUS_STATUS_DESC",
                "ID_PRINCIPAL_ANOMALY", "ANOMALOUS_TYPE_CODE", "ANOMALOUS_TYPE_DESC", "ITEM_STATUS",
                "ACCOUNT", "SUPPLY", "OFFERED_SERVICE", "CONTRACT_STATUS", "DETECTION_DATE",
            ],
            rows=[
                [500, "ESTAN00009", "Pending review", 202, "26", "Diff Date System",
                 "STTOBILL00", "ACC-1", "10450618-301", 900123, "ACTIVO", "2026-08-01"],
                [501, "ESTAN00001", None, 201, None, None, "STTOBILL01", None, None, None, None, None],
            ],
            elapsed_ms=1.0,
        )

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    monkeypatch.setattr(
        server_mod.mssql, "get_table_columns", lambda conn, schema, table: {"ID_PRINCIPAL_ANOMALY": "int"},
    )
    resp = client.post("/api/date-anomaly/detect-all")
    assert resp.status_code == 200
    rows = resp.json()["rows"]

    assert rows[0]["anomalous_status_description"] == "Pending review"
    assert rows[0]["anomalous_type_code"] == "26"
    assert rows[0]["anomalous_type_description"] == "Diff Date System"
    assert rows[0]["account"] == "ACC-1"
    assert rows[0]["supply"] == "10450618-301"
    assert rows[0]["offered_service"] == "900123"
    assert rows[0]["contract_status"] == "ACTIVO"
    assert rows[0]["detection_date"] == "2026-08-01"

    # No description/enrichment found for 501 - codes still come through.
    assert rows[1]["anomalous_status_description"] == ""
    assert rows[1]["anomalous_status"] == "ESTAN00001"
    assert rows[1]["anomalous_type_code"] == ""
    assert rows[1]["anomalous_type_description"] == ""
    assert rows[1]["anomalous_type"] == "201"
    assert rows[1]["account"] == ""
    assert rows[1]["detection_date"] == ""


def test_date_anomaly_detect_all_returns_all_cycle(client, monkeypatch):
    # ALL_CYCLE comes back from SQL as a bit (0/1) - the route should
    # surface it as a real bool, not the string-ified "0"/"1" cell_display
    # gives every other column, and as None (not False) when the column is
    # entirely absent from the result set (a row shape the schema pre-check
    # can't catch, since ALL_CYCLE isn't the column it checks).
    import web.server as server_mod
    from app.db.mssql import QueryResult

    _login(client)

    def fake_run_query(conn, sql):
        assert "LEFT JOIN" in sql
        return QueryResult(
            columns=["ID_ITEM_TO_BILL", "ANOMALOUS_STATUS", "ITEM_STATUS", "ALL_CYCLE"],
            rows=[[500, "ESTAN00009", "STTOBILL00", 1], [501, "ESTAN00001", "STTOBILL01", 0]],
            elapsed_ms=1.0,
        )

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    monkeypatch.setattr(
        server_mod.mssql, "get_table_columns", lambda conn, schema, table: {"ID_PRINCIPAL_ANOMALY": "int"},
    )
    resp = client.post("/api/date-anomaly/detect-all")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert rows[0]["all_cycle"] is True
    assert rows[1]["all_cycle"] is False


def test_date_anomaly_detect_all_all_cycle_none_when_column_missing(client, monkeypatch):
    # The existing _da_fake_detect_all_run_query fixture's rows don't
    # include an ALL_CYCLE column at all (predates this feature) - the
    # route must degrade to all_cycle: None rather than raising or
    # silently reading as False, matching the same "unknown lookup value
    # shouldn't masquerade as a real answer" convention as the other
    # detect-all enrichment fields.
    _login(client)
    _da_fake_detect_all_run_query(monkeypatch)
    resp = client.post("/api/date-anomaly/detect-all")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert all(r["all_cycle"] is None for r in rows)


def test_date_anomaly_detect_all_returns_non_cycle_reading_types(client, monkeypatch):
    # RJ, 2026-09-13: "if not all cycle, i need to know if it contains
    # removal or not" - NON_CYCLE_READING_TYPES comes back from SQL as a
    # comma-separated string (STUFF + FOR XML PATH), possibly NULL when
    # ALL_CYCLE is true. The route surfaces it as "" (not None) either
    # way - a blank cell, not a null placeholder, since "" already reads
    # naturally as "nothing non-cycle to show" in this table column.
    import web.server as server_mod
    from app.db.mssql import QueryResult

    _login(client)

    def fake_run_query(conn, sql):
        return QueryResult(
            columns=["ID_ITEM_TO_BILL", "ANOMALOUS_STATUS", "ITEM_STATUS", "ALL_CYCLE", "NON_CYCLE_READING_TYPES"],
            rows=[
                [500, "ESTAN00009", "STTOBILL00", 0, "Removal, Reconnection"],
                [501, "ESTAN00001", "STTOBILL01", 1, None],
            ],
            elapsed_ms=1.0,
        )

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    monkeypatch.setattr(
        server_mod.mssql, "get_table_columns", lambda conn, schema, table: {"ID_PRINCIPAL_ANOMALY": "int"},
    )
    resp = client.post("/api/date-anomaly/detect-all")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert rows[0]["non_cycle_reading_types"] == "Removal, Reconnection"
    assert rows[1]["non_cycle_reading_types"] == ""


def test_date_anomaly_detect_all_non_cycle_reading_types_blank_when_column_missing(client, monkeypatch):
    # Same "predates this feature" fixture as the all_cycle-missing test
    # above - must degrade to "" rather than raising.
    _login(client)
    _da_fake_detect_all_run_query(monkeypatch)
    resp = client.post("/api/date-anomaly/detect-all")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert all(r["non_cycle_reading_types"] == "" for r in rows)


def test_date_anomaly_detect_all_returns_billing_period_count(client, monkeypatch):
    # BILLING_PERIOD_COUNT comes back from SQL as a real int (COUNT(...)),
    # never NULL for a matched row - the route surfaces it as-is (not
    # cell_display-stringified, same "int, not bool" stance as all_cycle's
    # own comment) and as None only when the column is entirely absent
    # from the result set (older/mismatched query shape).
    import web.server as server_mod
    from app.db.mssql import QueryResult

    _login(client)

    def fake_run_query(conn, sql):
        assert "LEFT JOIN" in sql
        return QueryResult(
            columns=["ID_ITEM_TO_BILL", "ANOMALOUS_STATUS", "ITEM_STATUS", "BILLING_PERIOD_COUNT"],
            rows=[[500, "ESTAN00009", "STTOBILL00", 2], [501, "ESTAN00001", "STTOBILL01", 1]],
            elapsed_ms=1.0,
        )

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    monkeypatch.setattr(
        server_mod.mssql, "get_table_columns", lambda conn, schema, table: {"ID_PRINCIPAL_ANOMALY": "int"},
    )
    resp = client.post("/api/date-anomaly/detect-all")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert rows[0]["billing_period_count"] == 2
    assert rows[1]["billing_period_count"] == 1


def test_date_anomaly_detect_all_billing_period_count_none_when_column_missing(client, monkeypatch):
    # The shared _da_fake_detect_all_run_query fixture's rows don't
    # include a BILLING_PERIOD_COUNT column at all (predates this
    # feature) - the route must degrade to None rather than raising or
    # silently reading as 0, matching all_cycle's own convention above.
    _login(client)
    _da_fake_detect_all_run_query(monkeypatch)
    resp = client.post("/api/date-anomaly/detect-all")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert all(r["billing_period_count"] is None for r in rows)


def test_date_anomaly_detect_all_export_xlsx_requires_rows(client):
    _login(client)
    resp = client.post("/api/date-anomaly/detect-all/export-xlsx", json={"rows": []})
    assert resp.status_code == 400


def test_date_anomaly_detect_all_export_xlsx_returns_workbook(client):
    from io import BytesIO

    import openpyxl

    _login(client)
    resp = client.post(
        "/api/date-anomaly/detect-all/export-xlsx",
        json={
            "rows": [
                {
                    "id_item_to_bill": "500",
                    "account": "ACC-1",
                    "supply": "10450618-301",
                    "offered_service": "19",
                    "contract_status": "ESTSC00002",
                    "anomalous_type": "DIFFDATES — BILLING DATES AND READING DATES ARE DIFFERENT",
                    "anomalous_status": "Pendiente (tras batch)",
                    "item_status": "STTOBILL00",
                    "needs_status_advance": True,
                    "all_cycle": True,
                    "non_cycle_reading_types": "",
                    "billing_period_count": 2,
                },
            ]
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert 'filename="detect_all_anomalies.xlsx"' in resp.headers["content-disposition"]

    wb = openpyxl.load_workbook(BytesIO(resp.content))
    ws = wb.active
    header = [c.value for c in ws[1]]
    # "Needs Status Advance" -> "Status Still Pending (STTOBILL00)" and a
    # new "Non-Cycle Type(s)" column, both RJ 2026-09-13 (see this round's
    # README section on clearer Detect All labels).
    assert header == [
        "ID Item To Bill", "Account", "Supply (NISS)", "Offered Service", "Contract Status",
        "Anomaly Type", "Anomaly Status", "Item Status", "Status Still Pending (STTOBILL00)", "All Cycle",
        "Non-Cycle Type(s)", "Billing Periods",
    ]
    row = [c.value for c in ws[2]]
    assert row == [
        "500", "ACC-1", "10450618-301", "19", "ESTSC00002",
        "DIFFDATES — BILLING DATES AND READING DATES ARE DIFFERENT", "Pendiente (tras batch)",
        "STTOBILL00", "Yes", "Yes", "", 2,
    ]


def test_date_anomaly_detect_all_export_xlsx_handles_unknown_all_cycle_and_billing_count(client):
    from io import BytesIO

    import openpyxl

    _login(client)
    resp = client.post(
        "/api/date-anomaly/detect-all/export-xlsx",
        json={"rows": [{"id_item_to_bill": "500", "needs_status_advance": False}]},
    )
    assert resp.status_code == 200
    wb = openpyxl.load_workbook(BytesIO(resp.content))
    row = [c.value for c in wb.active[2]]
    # needs_status_advance/all_cycle/non_cycle_reading_types/
    # billing_period_count all blank/None when not supplied - mirrors the
    # CSV export's own "" for unknown values.
    assert row[0] == "500"
    assert not row[8]  # Status Still Pending (STTOBILL00) - written as "", falsy either way it round-trips
    assert not row[9]  # All Cycle
    assert not row[10]  # Non-Cycle Type(s)
    assert not row[11]  # Billing Periods


def test_date_anomaly_detect_all_explain_requires_ai_configuration(client):
    _login(client)
    resp = client.post("/api/date-anomaly/detect-all/explain", json={"id_item_to_bill": "500"})
    assert resp.status_code == 400


def test_date_anomaly_detect_all_explain_returns_ai_text(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    _enable_ai(client)
    captured = {}

    def fake_explain(api_key, model, row_context):
        captured["row_context"] = row_context
        return server_mod.ai_assist.AIResponse(success=True, text="Looks like a routine case.")

    monkeypatch.setattr(server_mod.ai_assist, "explain_anomaly_finding", fake_explain)
    resp = client.post(
        "/api/date-anomaly/detect-all/explain",
        json={
            "id_item_to_bill": "500",
            "account": "ACC-1",
            "supply": "10450618-301",
            "anomalous_type_description": "Diff Date System",
            "anomalous_status_description": "Pending review",
            "item_status": "STTOBILL00",
            "needs_status_advance": True,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "Looks like a routine case."
    assert "10450618-301" in captured["row_context"]
    assert "Diff Date System" in captured["row_context"]
    assert "Yes" in captured["row_context"]  # needs_status_advance rendered as Yes/No


@contextlib.contextmanager
def _noop_reuse_connection(conn_cfg):
    """Stand-in for mssql.reuse_connection (added 2026-09-13 so
    web/batch_jobs.py's per-NISS queries share one connection instead of
    opening a fresh one every call - see that function's own docstring).
    Every batch test here fakes mssql.run_query/get_table_columns
    directly and never wants a REAL connection attempted - the real
    reuse_connection calls the real pytds.connect(), which would try to
    dial the fake test ConnectionConfig's "localhost" for real and fail
    the whole job before a single NISS ran. This makes the `with
    mssql.reuse_connection(conn_cfg):` in _run_batch a no-op instead."""
    yield None


def _da_batch_fake_run_query(monkeypatch):
    """Fake mssql.run_query for the batch route: distinguishes NISS by
    the '<niss>' literal in the detect/correct-date queries (item/xml
    lookups are keyed by ID_READING/ID_ITEM_TO_BILL instead, so a single
    shared fake table covers all NISS as long as their reading/item ids
    don't collide).

    Scenario, matching the real bug report shape:
      - N1: 1 anomalous reading (201) that maps to TWO items-to-bill
        (301, 302) - the one-reading-to-many-items case.
      - N2: no anomalous readings at all.
      - N3: anomalous readings exist, but no correctly-billed reading
        above the floor - can't source a correct date, so this NISS
        should come back as an error, not silently skipped.
    """
    import datetime as _datetime

    import web.server as server_mod
    from app.db.mssql import QueryResult

    def fake_run_query(conn, sql):
        if "READ_STATUS = '6000STSRED'" in sql:
            # 5th column, READING_TYPE, mirrors real build_detect_query
            # output (SELECT r.* - always includes it); only N4 below sets
            # it to the non-cycle TIPTL00011 type, to exercise Part 6.
            cols = ["ID_READING", "ID_BILLING_PERIOD", "READING_DATE", "READ_STATUS", "READING_TYPE"]
            if "'N1'" in sql:
                return QueryResult(
                    columns=cols, rows=[[201, 10000000231, "2024-01-01", "6000STSRED", "TIPTL00001"]], elapsed_ms=1.0,
                )
            if "'N2'" in sql:
                return QueryResult(columns=cols, rows=[], elapsed_ms=1.0)
            if "'N3'" in sql:
                return QueryResult(
                    columns=cols, rows=[[901, 10000000231, "2024-01-01", "6000STSRED", "TIPTL00001"]], elapsed_ms=1.0,
                )
            if "'N4'" in sql:
                return QueryResult(
                    columns=cols, rows=[[401, 10000000231, "2024-01-01", "6000STSRED", "TIPTL00011"]], elapsed_ms=1.0,
                )
            raise AssertionError(f"Unexpected NISS in detect query: {sql}")
        if "READ_STATUS = '7000STSRED'" in sql:
            cols = ["ID_READING", "ID_BILLING_PERIOD", "READING_DATE"]
            if "'N1'" in sql:
                return QueryResult(columns=cols, rows=[[999, 10000000240, _datetime.date(2024, 3, 1)]], elapsed_ms=1.0)
            if "'N4'" in sql:
                return QueryResult(columns=cols, rows=[[998, 10000000240, _datetime.date(2024, 3, 1)]], elapsed_ms=1.0)
            # N3 (and anything else) - no correctly-billed reading found.
            return QueryResult(columns=cols, rows=[], elapsed_ms=1.0)
        if "GCCOM_READINGS_ITEMSTOBILL" in sql:
            return QueryResult(columns=["ID_READING", "ID_ITEM_TO_BILL"], rows=[[201, 301], [201, 302]], elapsed_ms=1.0)
        # GITB.ID_XML is unique to build_item_xml_id_query (RJ, 2026-09-13
        # fix - see _da_fake_run_query's own docstring for the full
        # explanation). Items map to themselves here (301->301, 302->302)
        # purely so this fixture's existing XML rows below stay valid.
        if "GITB.ID_XML" in sql:
            return QueryResult(columns=["ID_ITEM_TO_BILL", "ID_XML"], rows=[[301, 301], [302, 302]], elapsed_ms=1.0)
        if "GCCOM_ITEMS_TO_BILL_XML" in sql:
            return QueryResult(
                columns=["ID_XML", "XML_TO_BILL"],
                rows=[
                    [301, "<Root><initDate>2024-01-01</initDate></Root>"],
                    [302, "<Root><initDate>2024-01-01</initDate></Root>"],
                ],
                elapsed_ms=1.0,
            )
        if "GCCOM_ANOMALOUS" in sql:
            # No open anomalies in this fixture by default - Part 5 stays
            # at 0 for every NISS here, same as if the real table simply
            # had nothing to cancel for these item-to-bill ids.
            return QueryResult(columns=["ID_ITEM_TO_BILL", "ANOMALOUS_STATUS"], rows=[], elapsed_ms=1.0)
        if "STTOBILL00" in sql:
            # No item-to-bill rows still pending in this fixture by
            # default - Part 3 stays at 0 for every NISS here.
            return QueryResult(columns=["ID_ITEM_TO_BILL", "STATUS"], rows=[], elapsed_ms=1.0)
        raise AssertionError(f"Unexpected query in batch test: {sql}")

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    monkeypatch.setattr(server_mod.mssql, "get_table_columns", lambda conn, schema, table: {"ID_READING": "int"})
    monkeypatch.setattr(server_mod.mssql, "reuse_connection", _noop_reuse_connection)


def _da_wait_for_batch_job(client, job_id, timeout=5.0):
    """Polls GET /api/date-anomaly/batch/{job_id} until it reaches a
    terminal status - the batch now runs on a background thread (see
    web/batch_jobs.py), so a test has to wait for that thread the same
    way the real frontend polls, instead of getting the whole result
    back from one blocking POST like the earlier synchronous route
    did. The fake mssql.run_query calls are all in-memory/instant, so
    this should resolve in well under a second in practice - the
    timeout is just a safety net against a hang."""
    import time

    deadline = time.time() + timeout
    body = None
    while time.time() < deadline:
        resp = client.get(f"/api/date-anomaly/batch/{job_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] in ("done", "cancelled", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"Batch job {job_id} did not finish within {timeout}s (last body: {body})")


def test_date_anomaly_batch_start_requires_at_least_one_niss(client):
    _login(client)
    resp = client.post("/api/date-anomaly/batch/start", json={"niss_list": ["   ", ""], "program": "JIRA-1"})
    assert resp.status_code == 400


def test_date_anomaly_batch_start_requires_program(client):
    _login(client)
    resp = client.post("/api/date-anomaly/batch/start", json={"niss_list": ["N1"], "program": "  "})
    assert resp.status_code == 400


def test_date_anomaly_batch_start_requires_editor_role(client):
    _create_and_login_as(client, "viewer1", "viewer")
    resp = client.post("/api/date-anomaly/batch/start", json={"niss_list": ["N1"], "program": "JIRA-1"})
    assert resp.status_code == 403


def test_date_anomaly_batch_runs_in_background_and_reports_mixed_results(client, monkeypatch):
    _login(client)
    _da_batch_fake_run_query(monkeypatch)

    start_resp = client.post(
        "/api/date-anomaly/batch/start",
        json={"niss_list": ["N1", "N2", "N3", "N1"], "threshold": 10000000230, "program": "JIRA-1"},
    )
    assert start_resp.status_code == 200
    start_body = start_resp.json()
    assert start_body["niss_total"] == 3  # duplicate "N1" deduped before the job is even created
    assert start_body["status"] in ("queued", "running", "done")  # the thread may already be underway

    job = _da_wait_for_batch_job(client, start_body["job_id"])
    assert job["status"] == "done"
    assert job["processed"] == 3
    assert [r["niss"] for r in job["results"]] == ["N1", "N2", "N3"]

    n1, n2, n3 = job["results"]
    assert n1["status"] == "ok"
    assert n1["reading_count"] == 1
    assert n1["item_count"] == 2  # the one-reading-to-two-items case
    assert n1["xml_count"] == 2
    assert n1["anomalous_count"] == 0  # no open GCCOM_ANOMALOUS rows in this fixture

    assert n2["status"] == "no_anomalies"

    assert n3["status"] == "error"
    assert n3["error"]

    combined = job["combined_sql"]
    assert "NISS: N1" in combined
    assert "N2: no anomalies found" in combined
    assert "N3: ERROR" in combined
    # only N1 contributed an actual script - N2/N3 get a summary line only.
    assert combined.count("-- Diff Date System anomaly correction script") == 1

    # N1's successful generation is recorded to Script History.
    history = client.get("/api/history").json()["entries"]
    assert any(e["kind"] == "date_anomaly_correction" and "N1" in e["table_name"] for e in history)

    # And the job now shows up in "recent runs" for this user.
    jobs_resp = client.get("/api/date-anomaly/batch/jobs")
    assert jobs_resp.status_code == 200
    assert any(j["job_id"] == job["job_id"] for j in jobs_resp.json()["jobs"])


def test_date_anomaly_batch_explain_ai_summarizes_the_run(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    _enable_ai(client)
    _da_batch_fake_run_query(monkeypatch)

    start_resp = client.post(
        "/api/date-anomaly/batch/start",
        json={"niss_list": ["N1", "N2", "N3"], "threshold": 10000000230, "program": "JIRA-1"},
    )
    job = _da_wait_for_batch_job(client, start_resp.json()["job_id"])
    assert job["status"] == "done"

    captured = {}

    def fake_explain(api_key, model, batch_context):
        captured["batch_context"] = batch_context
        return server_mod.ai_assist.AIResponse(success=True, text="Mostly fine, N3 needs a look.")

    monkeypatch.setattr(server_mod.ai_assist, "explain_batch_run", fake_explain)
    resp = client.post(f"/api/date-anomaly/batch/{job['job_id']}/explain-ai")
    assert resp.status_code == 200
    assert resp.json()["text"] == "Mostly fine, N3 needs a look."
    assert "NISS N1: ok" in captured["batch_context"]
    assert "NISS N2: no_anomalies" in captured["batch_context"]
    assert "NISS N3: error" in captured["batch_context"]


def test_date_anomaly_batch_explain_ai_requires_results(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    _enable_ai(client)
    # Create a job directly (bypassing the background thread entirely, to
    # avoid a race against it actually finishing) so it deterministically
    # has zero results - the "nothing to summarize yet" case.
    job = server_mod.batch_jobs.jobs.create(
        created_by="tester", niss_list=["N1"], threshold=0, program="JIRA-1",
    )
    resp = client.post(f"/api/date-anomaly/batch/{job.id}/explain-ai")
    assert resp.status_code == 400


def test_date_anomaly_batch_explain_ai_hidden_from_other_users(client, monkeypatch):
    # Same 404-not-403 boundary as test_date_anomaly_batch_status_hidden_
    # from_other_users above, applied to the new explain-ai route.
    _da_batch_fake_run_query(monkeypatch)
    _create_and_login_as(client, "editor1", "editor")
    start_resp = client.post(
        "/api/date-anomaly/batch/start", json={"niss_list": ["N2"], "threshold": 0, "program": "JIRA-1"},
    )
    job_id = start_resp.json()["job_id"]
    _da_wait_for_batch_job(client, job_id)

    _create_and_login_as(client, "viewer1", "viewer")
    resp = client.post(f"/api/date-anomaly/batch/{job_id}/explain-ai")
    assert resp.status_code == 404


def test_date_anomaly_batch_cancels_open_anomalies(client, monkeypatch):
    import web.server as server_mod
    from app.db.mssql import QueryResult

    _login(client)
    _da_batch_fake_run_query(monkeypatch)

    # Layer an extra branch on top of the shared batch fake so the
    # GCCOM_ANOMALOUS lookup returns one open row for item 301 only.
    base_fake = server_mod.mssql.run_query

    def fake_with_anomalies(conn, sql):
        if "GCCOM_ANOMALOUS" in sql:
            return QueryResult(
                columns=["ID_ITEM_TO_BILL", "ANOMALOUS_STATUS"], rows=[[301, "ESTAN00001"]], elapsed_ms=1.0
            )
        return base_fake(conn, sql)

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_with_anomalies)

    start_resp = client.post(
        "/api/date-anomaly/batch/start", json={"niss_list": ["N1"], "threshold": 10000000230, "program": "JIRA-1"},
    )
    job = _da_wait_for_batch_job(client, start_resp.json()["job_id"])
    n1 = job["results"][0]
    assert n1["status"] == "ok"
    assert n1["anomalous_count"] == 1
    assert "GCCOM_ANOMALOUS" in job["combined_sql"]
    assert "WHERE ID_ITEM_TO_BILL = 301" in job["combined_sql"]
    assert "ESTAN00005" in job["combined_sql"]


def test_date_anomaly_batch_advances_item_status(client, monkeypatch):
    import web.server as server_mod
    from app.db.mssql import QueryResult

    _login(client)
    _da_batch_fake_run_query(monkeypatch)
    base_fake = server_mod.mssql.run_query

    def fake_with_pending_status(conn, sql):
        if "STTOBILL00" in sql:
            return QueryResult(columns=["ID_ITEM_TO_BILL", "STATUS"], rows=[[301, "STTOBILL00"]], elapsed_ms=1.0)
        return base_fake(conn, sql)

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_with_pending_status)

    start_resp = client.post(
        "/api/date-anomaly/batch/start", json={"niss_list": ["N1"], "threshold": 10000000230, "program": "JIRA-1"},
    )
    job = _da_wait_for_batch_job(client, start_resp.json()["job_id"])
    n1 = job["results"][0]
    assert n1["item_status_count"] == 1
    assert "STTOBILL01" in job["combined_sql"]


def test_date_anomaly_batch_cleans_up_orphan_usage_readings(client, monkeypatch):
    # N4 in _da_batch_fake_run_query has one anomalous reading (401) whose
    # READING_TYPE is the non-cycle TIPTL00011 type - Part 6 should fire
    # for it in the batch pipeline too, same as the single-NISS route.
    _login(client)
    _da_batch_fake_run_query(monkeypatch)
    start_resp = client.post(
        "/api/date-anomaly/batch/start", json={"niss_list": ["N4"], "threshold": 10000000230, "program": "JIRA-1"},
    )
    job = _da_wait_for_batch_job(client, start_resp.json()["job_id"])
    n4 = job["results"][0]
    assert n4["status"] == "ok"
    assert n4["orphan_reading_count"] == 1
    assert "DELETE FROM" in job["combined_sql"]
    assert "1000STSRED" in job["combined_sql"]
    assert "non-cycle reading(s) reset" in job["combined_sql"]


def test_date_anomaly_batch_reports_billing_period_count(client, monkeypatch):
    # N1 in _da_batch_fake_run_query has a single anomalous reading (201)
    # in one billing period (10000000231) - the batch pipeline should
    # report billing_period_count: 1 for it, same field/computation the
    # single-NISS detect route reports.
    _login(client)
    _da_batch_fake_run_query(monkeypatch)
    start_resp = client.post(
        "/api/date-anomaly/batch/start", json={"niss_list": ["N1"], "threshold": 10000000230, "program": "JIRA-1"},
    )
    job = _da_wait_for_batch_job(client, start_resp.json()["job_id"])
    assert job["results"][0]["billing_period_count"] == 1


def test_date_anomaly_batch_billing_period_count_multiple_periods(client, monkeypatch):
    # A NISS whose anomalous readings span two distinct ID_BILLING_PERIOD
    # values should report billing_period_count: 2 - this is what the
    # batch results table's orange multi-period highlighting keys off of.
    import datetime as _datetime

    import web.server as server_mod
    from app.db.mssql import QueryResult

    _login(client)

    def fake_run_query(conn, sql):
        if "READ_STATUS = '6000STSRED'" in sql:
            cols = ["ID_READING", "ID_BILLING_PERIOD", "READING_DATE", "READ_STATUS", "READING_TYPE"]
            return QueryResult(
                columns=cols,
                rows=[
                    [601, 10000000231, "2024-01-01", "6000STSRED", "TIPTL00001"],
                    [602, 10000000232, "2024-02-01", "6000STSRED", "TIPTL00001"],
                ],
                elapsed_ms=1.0,
            )
        if "READ_STATUS = '7000STSRED'" in sql:
            cols = ["ID_READING", "ID_BILLING_PERIOD", "READING_DATE"]
            return QueryResult(columns=cols, rows=[[999, 10000000240, _datetime.date(2024, 3, 1)]], elapsed_ms=1.0)
        if "GCCOM_READINGS_ITEMSTOBILL" in sql:
            return QueryResult(
                columns=["ID_READING", "ID_ITEM_TO_BILL"], rows=[[601, 701], [602, 701]], elapsed_ms=1.0,
            )
        if "GITB.ID_XML" in sql:
            return QueryResult(columns=["ID_ITEM_TO_BILL", "ID_XML"], rows=[[701, 701]], elapsed_ms=1.0)
        if "GCCOM_ITEMS_TO_BILL_XML" in sql:
            return QueryResult(
                columns=["ID_XML", "XML_TO_BILL"],
                rows=[[701, "<Root><initDate>2024-01-01</initDate></Root>"]],
                elapsed_ms=1.0,
            )
        if "GCCOM_ANOMALOUS" in sql:
            return QueryResult(columns=["ID_ITEM_TO_BILL", "ANOMALOUS_STATUS"], rows=[], elapsed_ms=1.0)
        if "STTOBILL00" in sql:
            return QueryResult(columns=["ID_ITEM_TO_BILL", "STATUS"], rows=[], elapsed_ms=1.0)
        raise AssertionError(f"Unexpected query in batch test: {sql}")

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    monkeypatch.setattr(server_mod.mssql, "get_table_columns", lambda conn, schema, table: {"ID_READING": "int"})
    monkeypatch.setattr(server_mod.mssql, "reuse_connection", _noop_reuse_connection)

    start_resp = client.post(
        "/api/date-anomaly/batch/start", json={"niss_list": ["N5"], "threshold": 10000000230, "program": "JIRA-1"},
    )
    job = _da_wait_for_batch_job(client, start_resp.json()["job_id"])
    assert job["results"][0]["billing_period_count"] == 2


def test_date_anomaly_batch_lowest_billing_period_only(client, monkeypatch):
    # Same two-billing-period N5 fixture as
    # test_date_anomaly_batch_billing_period_count_multiple_periods, but
    # with lowest_billing_period_only requested - the batch pipeline
    # should scope N5's script to just the 601 reading (period 10000000231,
    # the lower of the two), while still reporting the full
    # billing_period_count of 2.
    import datetime as _datetime

    import web.server as server_mod
    from app.db.mssql import QueryResult

    _login(client)

    def fake_run_query(conn, sql):
        if "READ_STATUS = '6000STSRED'" in sql:
            cols = ["ID_READING", "ID_BILLING_PERIOD", "READING_DATE", "READ_STATUS", "READING_TYPE"]
            return QueryResult(
                columns=cols,
                rows=[
                    [601, 10000000231, "2024-01-01", "6000STSRED", "TIPTL00001"],
                    [602, 10000000232, "2024-02-01", "6000STSRED", "TIPTL00001"],
                ],
                elapsed_ms=1.0,
            )
        if "READ_STATUS = '7000STSRED'" in sql:
            cols = ["ID_READING", "ID_BILLING_PERIOD", "READING_DATE"]
            return QueryResult(columns=cols, rows=[[999, 10000000240, _datetime.date(2024, 3, 1)]], elapsed_ms=1.0)
        if "GCCOM_READINGS_ITEMSTOBILL" in sql:
            return QueryResult(
                columns=["ID_READING", "ID_ITEM_TO_BILL"], rows=[[601, 701], [602, 701]], elapsed_ms=1.0,
            )
        if "GITB.ID_XML" in sql:
            return QueryResult(columns=["ID_ITEM_TO_BILL", "ID_XML"], rows=[[701, 701]], elapsed_ms=1.0)
        if "GCCOM_ITEMS_TO_BILL_XML" in sql:
            return QueryResult(
                columns=["ID_XML", "XML_TO_BILL"],
                rows=[[701, "<Root><initDate>2024-01-01</initDate></Root>"]],
                elapsed_ms=1.0,
            )
        if "GCCOM_ANOMALOUS" in sql:
            return QueryResult(columns=["ID_ITEM_TO_BILL", "ANOMALOUS_STATUS"], rows=[], elapsed_ms=1.0)
        if "STTOBILL00" in sql:
            return QueryResult(columns=["ID_ITEM_TO_BILL", "STATUS"], rows=[], elapsed_ms=1.0)
        raise AssertionError(f"Unexpected query in batch test: {sql}")

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    monkeypatch.setattr(server_mod.mssql, "get_table_columns", lambda conn, schema, table: {"ID_READING": "int"})
    monkeypatch.setattr(server_mod.mssql, "reuse_connection", _noop_reuse_connection)

    start_resp = client.post(
        "/api/date-anomaly/batch/start",
        json={
            "niss_list": ["N5"], "threshold": 10000000230, "program": "JIRA-1",
            "lowest_billing_period_only": True,
        },
    )
    job = _da_wait_for_batch_job(client, start_resp.json()["job_id"])
    result = job["results"][0]
    assert result["scoped_to_lowest_period"] is True
    assert result["billing_period_count"] == 2
    assert result["reading_count"] == 1


def test_date_anomaly_batch_clean_option_strips_comments(client, monkeypatch):
    _login(client)
    _da_batch_fake_run_query(monkeypatch)
    start_resp = client.post(
        "/api/date-anomaly/batch/start",
        json={"niss_list": ["N1"], "threshold": 10000000230, "program": "JIRA-1", "clean": True},
    )
    job = _da_wait_for_batch_job(client, start_resp.json()["job_id"])
    assert not any(line.strip().startswith("--") for line in job["combined_sql"].split("\n"))
    assert "BEGIN TRANSACTION" not in job["combined_sql"]  # removed per explicit analyst request
    # Statement counts are unaffected by clean - only the text changes.
    assert job["results"][0]["reading_count"] == 1


def test_date_anomaly_batch_records_analysis_history(client, monkeypatch):
    _login(client)
    _da_batch_fake_run_query(monkeypatch)
    start_resp = client.post(
        "/api/date-anomaly/batch/start",
        json={"niss_list": ["N1", "N2", "N3"], "threshold": 10000000230, "program": "JIRA-1"},
    )
    _da_wait_for_batch_job(client, start_resp.json()["job_id"])

    entries = client.get("/api/date-anomaly/history").json()["entries"]
    by_niss = {e["niss"]: e for e in entries}
    assert by_niss["N1"]["generated"] is True
    assert by_niss["N1"]["source"] == "batch"
    assert by_niss["N2"]["generated"] is False  # N2 is the no-anomalies NISS in this fixture
    assert by_niss["N2"]["anomaly_count"] == 0
    # N3 (the error NISS in _da_batch_fake_run_query - no correctly-billed
    # reading found) is intentionally not recorded - see batch_jobs.py's
    # _run_batch comment on why.
    assert "N3" not in by_niss


def test_date_anomaly_batch_status_hidden_from_other_users(client, monkeypatch):
    # editor1 starts a job; viewer1 (a different account) must not be
    # able to see it by guessing/reusing the job id - 404, not 403, so
    # this doesn't confirm/deny the job id exists to someone else.
    _da_batch_fake_run_query(monkeypatch)
    _create_and_login_as(client, "editor1", "editor")
    start_resp = client.post(
        "/api/date-anomaly/batch/start", json={"niss_list": ["N2"], "threshold": 0, "program": "JIRA-1"},
    )
    job_id = start_resp.json()["job_id"]
    _da_wait_for_batch_job(client, job_id)

    _create_and_login_as(client, "viewer1", "viewer")
    resp = client.get(f"/api/date-anomaly/batch/{job_id}")
    assert resp.status_code == 404

    # A recent-runs listing for viewer1 must not include editor1's job either.
    jobs_resp = client.get("/api/date-anomaly/batch/jobs")
    assert all(j["job_id"] != job_id for j in jobs_resp.json()["jobs"])


def test_date_anomaly_batch_cancel_rejected_once_job_is_done(client, monkeypatch):
    _login(client)
    _da_batch_fake_run_query(monkeypatch)
    start_resp = client.post(
        "/api/date-anomaly/batch/start", json={"niss_list": ["N2"], "threshold": 0, "program": "JIRA-1"},
    )
    job_id = start_resp.json()["job_id"]
    _da_wait_for_batch_job(client, job_id)  # let it finish first

    resp = client.post(f"/api/date-anomaly/batch/{job_id}/cancel")
    assert resp.status_code == 400  # nothing left to cancel


def test_date_anomaly_batch_cancel_requires_editor_role(client, monkeypatch):
    _da_batch_fake_run_query(monkeypatch)
    _create_and_login_as(client, "editor1", "editor")
    start_resp = client.post(
        "/api/date-anomaly/batch/start", json={"niss_list": ["N2"], "threshold": 0, "program": "JIRA-1"},
    )
    job_id = start_resp.json()["job_id"]

    _create_and_login_as(client, "viewer1", "viewer")
    resp = client.post(f"/api/date-anomaly/batch/{job_id}/cancel")
    # The route checks role before ownership, so a viewer gets 403 even
    # though they also don't own this job.
    assert resp.status_code == 403


# ---------------- Hierarchy Analysis ----------------
# Draft feature (see app/core/hierarchy_analysis.py's module docstring) -
# these tests exercise the route wiring/response shape, not whether the
# underlying business assumptions (MP_TYPE IN PRIMARY_MP_TYPES as
# "primary", MP_STATUS IN MP_STATUS_ALLOWED, the specific READ_STATUS/
# READING_TYPE code sets) are correct - those were checked by hand
# against the live tunnel DB this round (including the analyst's own
# correction from an earlier IND_DIST_PPAL-based draft), not by these
# offline tests.

_HIER_ROW = [
    143332, "1130", "Hierarchy - Difference billed to the main supply", 20000062, "24", "3", 672343, 1344014,
    "0", "TIPEQM0002", "Secundario", "1000STAMPO", "Conectado",
    "10465189-301", 10000000196, "8-August 2026", 1002459827, "2023-03-01", "2023-03-27", "TIPTL00003",
    "Cycle", "6000STSRED", "0", "1", "107", "106", "106", "106", "TPCONS0006", "0", "AnomaliasSistema",
    "2023-05-16", 1344014,
]
_HIER_COLS = [
    "ID_MAIN_MP", "ID_CALCULATION_MODULE", "CALC_MODULE_TYPE", "ID_MEASURING_POINT", "SECONDARY_COUNT",
    "SECONDARIES_NOT_SENT_COUNT", "ID_SECTOR_SUPPLY", "ID_METER",
    "PERC_DIST", "MP_TYPE", "MP_TYPE_DESC", "MP_STATUS", "MP_STATUS_DESC", "NISS", "ID_BILLING_PERIOD",
    "BILLING_PERIOD_DESC", "ID_READING", "READING_PREV_DATE", "READING_DATE", "READING_TYPE",
    "READING_TYPE_DESC", "READ_STATUS", "IND_USAGE_TO_CAL",
    "PREV_VALUE", "VALUE", "READY_USAGE", "READING_USAGE", "CORRECTED_USAGE", "USAGE_TYPE",
    "IND_ESTIMATE", "UPDATE_PROGRAM", "UPDATE_DATE", "ID_DEVICE",
]


def _hier_fake_detect_run_query(monkeypatch, *, rows=None):
    import web.server as server_mod

    rows = rows if rows is not None else [_HIER_ROW]

    def fake_run_query(conn, sql):
        return QueryResult(columns=_HIER_COLS, rows=rows, elapsed_ms=1.0)

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    monkeypatch.setattr(
        server_mod.mssql, "get_table_columns",
        lambda conn, schema, table: {"MP_TYPE": "varchar", "ID_MEASURING_POINT": "numeric"},
    )


def test_hierarchy_analysis_detect_requires_login(client):
    resp = client.post("/api/hierarchy-analysis/detect")
    assert resp.status_code == 401


def test_hierarchy_analysis_detect_returns_rows(client, monkeypatch):
    _login(client)
    _hier_fake_detect_run_query(monkeypatch)
    resp = client.post("/api/hierarchy-analysis/detect")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["id_measuring_point"] == "20000062"
    assert row["niss"] == "10465189-301"
    assert row["read_status"] == "6000STSRED"
    assert row["reading_type"] == "TIPTL00003"
    assert row["mp_type_desc"] == "Secundario"
    assert row["mp_status_desc"] == "Conectado"
    assert row["billing_period_desc"] == "8-August 2026"
    assert row["reading_type_desc"] == "Cycle"
    assert row["secondary_count"] == "24"
    assert row["secondaries_not_sent_count"] == "3"
    assert row["id_calculation_module"] == "1130"
    assert row["calc_module_type"] == "Hierarchy - Difference billed to the main supply"
    assert row["ready_usage"] == "106"


def test_hierarchy_analysis_detect_uses_default_min_billing_period_floor(client, monkeypatch):
    # The route calls build_pending_primaries_query with only `limit`
    # explicitly set, so it should still pick up the module's own
    # MIN_BILLING_PERIOD default rather than silently disabling the
    # floor - confirm the generated SQL actually carries it.
    import web.server as server_mod
    from app.core import hierarchy_analysis

    _login(client)
    captured_sql = {}

    def fake_run_query(conn, sql):
        captured_sql["sql"] = sql
        return QueryResult(columns=_HIER_COLS, rows=[_HIER_ROW], elapsed_ms=1.0)

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    monkeypatch.setattr(
        server_mod.mssql, "get_table_columns",
        lambda conn, schema, table: {"MP_TYPE": "varchar", "ID_MEASURING_POINT": "numeric"},
    )
    resp = client.post("/api/hierarchy-analysis/detect")
    assert resp.status_code == 200
    assert f"AND r.ID_BILLING_PERIOD > {hierarchy_analysis.MIN_BILLING_PERIOD}" in captured_sql["sql"]
    # Pre-existing bug fixed in passing: this referenced an undefined
    # `body` name (never assigned in this test) - a NameError, not a
    # real assertion, so it was silently never actually checking
    # anything. Noticed while adding the test below.
    assert resp.json()["possibly_truncated"] is False


def test_hierarchy_analysis_detect_secondaries_not_sent_count_sql_shape(client, monkeypatch):
    # Confirms the SECONDARIES_NOT_SENT_COUNT subquery (task, 2026-09-11)
    # carries both required conditions: a not-yet-sent status match AND a
    # "no reading at all in scope" fallback (the user's own follow-up -
    # "also no reading in the secondary counts") - not just one or the
    # other.
    import web.server as server_mod
    from app.core import hierarchy_analysis

    _login(client)
    captured_sql = {}

    def fake_run_query(conn, sql):
        captured_sql["sql"] = sql
        return QueryResult(columns=_HIER_COLS, rows=[_HIER_ROW], elapsed_ms=1.0)

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    monkeypatch.setattr(
        server_mod.mssql, "get_table_columns",
        lambda conn, schema, table: {"MP_TYPE": "varchar", "ID_MEASURING_POINT": "numeric"},
    )
    resp = client.post("/api/hierarchy-analysis/detect")
    assert resp.status_code == 200
    sql = captured_sql["sql"]
    assert "SECONDARIES_NOT_SENT_COUNT" in sql
    # Not-yet-sent status branch - the narrower set (excludes 6000STSRED
    # Sent to Bill, unlike PENDING_READ_STATUSES).
    for status in hierarchy_analysis.SECONDARY_NOT_SENT_STATUSES:
        assert f"'{status}'" in sql
    # No-reading-at-all fallback branch.
    assert "NOT EXISTS" in sql
    assert f"r3.ID_BILLING_PERIOD > {hierarchy_analysis.MIN_BILLING_PERIOD}" in sql


def test_hierarchy_analysis_detect_requires_connection(client, monkeypatch):
    import web.server as server_mod
    from app.config import AppConfig, AIConfig

    _login(client)
    empty_config = AppConfig(connections={}, active_connection="default", ai=AIConfig())
    monkeypatch.setattr(server_mod, "load_config", lambda: empty_config)
    resp = client.post("/api/hierarchy-analysis/detect")
    assert resp.status_code == 400


def test_hierarchy_analysis_detect_flags_possible_truncation(client, monkeypatch):
    from app.core import hierarchy_analysis

    rows = [_HIER_ROW for _ in range(hierarchy_analysis.HIERARCHY_DEFAULT_LIMIT)]
    _hier_fake_detect_run_query(monkeypatch, rows=rows)
    _login(client)
    resp = client.post("/api/hierarchy-analysis/detect")
    assert resp.json()["possibly_truncated"] is True


def test_hierarchy_analysis_detect_requires_known_primary_flag_column(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    monkeypatch.setattr(
        server_mod.mssql, "get_table_columns",
        lambda conn, schema, table: {"ID_MEASURING_POINT": "numeric"},  # no MP_TYPE
    )
    resp = client.post("/api/hierarchy-analysis/detect")
    assert resp.status_code == 502
    assert "MP_TYPE" in resp.json()["detail"]


_HIER_DETAIL_ROW = [
    143332, 20000062, 672343, "10465189-301", "1", "0", "TIPEQM0002", "1000STAMPO", 10000000196,
    "8-August 2026", 1002459827, "2023-03-27", "2023-03-01", "TIPTL00003", "Cycle",
    "6000STSRED", "107", "106",
]
_HIER_DETAIL_COLS = [
    "ID_MAIN_MP", "ID_MEASURING_POINT", "ID_SECTOR_SUPPLY", "NISS", "IND_DIST_PPAL", "PERC_DIST",
    "MP_TYPE", "STATUS", "ID_BILLING_PERIOD", "BILLING_PERIOD_DESC", "ID_READING", "READING_DATE",
    "READING_PREV_DATE", "READING_TYPE", "READING_TYPE_DESC", "READ_STATUS", "VALUE", "READY_USAGE",
]


def test_hierarchy_analysis_detail_requires_id(client):
    _login(client)
    resp = client.post("/api/hierarchy-analysis/detail", json={"id_measuring_point": "  "})
    assert resp.status_code == 400


def test_hierarchy_analysis_detail_requires_numeric_id(client):
    _login(client)
    resp = client.post("/api/hierarchy-analysis/detail", json={"id_measuring_point": "not-a-number"})
    assert resp.status_code == 400


def test_hierarchy_analysis_detail_returns_hierarchy_members(client, monkeypatch):
    import web.server as server_mod

    _login(client)

    def fake_run_query(conn, sql):
        assert "20000062" in sql
        return QueryResult(columns=_HIER_DETAIL_COLS, rows=[_HIER_DETAIL_ROW], elapsed_ms=1.0)

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    resp = client.post("/api/hierarchy-analysis/detail", json={"id_measuring_point": "20000062"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["niss"] == "10465189-301"
    assert body["rows"][0]["ind_dist_ppal"] == "1"
    assert body["rows"][0]["billing_period_desc"] == "8-August 2026"
    assert body["rows"][0]["reading_type_desc"] == "Cycle"
    assert body["rows"][0]["ready_usage"] == "106"
    # Needed by the frontend's reading-history popup (task #143) to call
    # /api/hierarchy-analysis/reading-history without a second lookup.
    assert body["rows"][0]["id_sector_supply"] == "672343"


def test_hierarchy_analysis_detail_passes_billing_period_to_query_builder(client, monkeypatch):
    # The frontend now always sends the clicked primary row's own
    # id_billing_period, so children get scoped to that same period -
    # confirm the route wires it through to the query builder rather
    # than silently dropping it.
    import web.server as server_mod

    _login(client)
    captured_sql = {}

    def fake_run_query(conn, sql):
        captured_sql["sql"] = sql
        return QueryResult(columns=_HIER_DETAIL_COLS, rows=[_HIER_DETAIL_ROW], elapsed_ms=1.0)

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    resp = client.post(
        "/api/hierarchy-analysis/detail",
        json={"id_measuring_point": "20000062", "id_billing_period": "10000000196"},
    )
    assert resp.status_code == 200
    assert "AND r.ID_BILLING_PERIOD = 10000000196" in captured_sql["sql"]


def test_hierarchy_analysis_detail_blank_billing_period_is_treated_as_unscoped(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    captured_sql = {}

    def fake_run_query(conn, sql):
        captured_sql["sql"] = sql
        return QueryResult(columns=_HIER_DETAIL_COLS, rows=[_HIER_DETAIL_ROW], elapsed_ms=1.0)

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    resp = client.post(
        "/api/hierarchy-analysis/detail",
        json={"id_measuring_point": "20000062", "id_billing_period": ""},
    )
    assert resp.status_code == 200
    assert "AND r.ID_BILLING_PERIOD =" not in captured_sql["sql"]


def test_hierarchy_analysis_detail_rejects_non_numeric_billing_period(client):
    _login(client)
    resp = client.post(
        "/api/hierarchy-analysis/detail",
        json={"id_measuring_point": "20000062", "id_billing_period": "not-a-number"},
    )
    assert resp.status_code == 400


_HIER_READING_ROW = [
    10000000196, 1002459827, "TIPTL00003", "TPCONS0006", "6000STSRED",
    "2023-03-01", "2023-03-27", "107", "106", "106", "106", "0",
]
_HIER_READING_COLS = [
    "BILLING_PERIOD", "ID_READING", "READING_TYPE", "USAGE_TYPE", "READ_STATUS",
    "PREV DATE", "READING_DATE", "PREV_VALUE", "VALUE", "READING_USAGE", "READY_USAGE",
    "IND_ESTIMATE",
]


def test_hierarchy_analysis_reading_history_requires_login(client):
    resp = client.post("/api/hierarchy-analysis/reading-history", json={"id_sector_supply": "672343"})
    assert resp.status_code == 401


def test_hierarchy_analysis_reading_history_requires_id(client):
    _login(client)
    resp = client.post("/api/hierarchy-analysis/reading-history", json={"id_sector_supply": "  "})
    assert resp.status_code == 400


def test_hierarchy_analysis_reading_history_requires_numeric_id(client):
    _login(client)
    resp = client.post("/api/hierarchy-analysis/reading-history", json={"id_sector_supply": "abc"})
    assert resp.status_code == 400


def test_hierarchy_analysis_reading_history_returns_rows(client, monkeypatch):
    import web.server as server_mod

    _login(client)

    def fake_run_query(conn, sql):
        assert "672343" in sql
        assert "ID_SECTOR_SUPPLY" in sql
        # No join to GCCOM_SECTOR_SUPPLY needed - filters directly on the
        # reading table's own ID_SECTOR_SUPPLY column, per the analyst's
        # own direction (task #143).
        assert "GCCOM_SECTOR_SUPPLY" not in sql
        return QueryResult(columns=_HIER_READING_COLS, rows=[_HIER_READING_ROW], elapsed_ms=1.0)

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    resp = client.post("/api/hierarchy-analysis/reading-history", json={"id_sector_supply": "672343"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["billing_period"] == "10000000196"
    assert row["id_reading"] == "1002459827"
    assert row["reading_type"] == "TIPTL00003"
    assert row["usage_type"] == "TPCONS0006"
    assert row["read_status"] == "6000STSRED"
    assert row["prev_date"] == "2023-03-01"
    assert row["reading_date"] == "2023-03-27"
    assert row["prev_value"] == "107"
    assert row["value"] == "106"
    assert row["reading_usage"] == "106"
    assert row["ready_usage"] == "106"
    assert row["ind_estimate"] == "0"


def test_hierarchy_analysis_reading_history_applies_floor_and_exclusion(client, monkeypatch):
    import web.server as server_mod
    from app.core import hierarchy_analysis

    _login(client)
    captured_sql = {}

    def fake_run_query(conn, sql):
        captured_sql["sql"] = sql
        return QueryResult(columns=_HIER_READING_COLS, rows=[_HIER_READING_ROW], elapsed_ms=1.0)

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    resp = client.post("/api/hierarchy-analysis/reading-history", json={"id_sector_supply": "672343"})
    assert resp.status_code == 200
    sql = captured_sql["sql"]
    assert f"r.ID_BILLING_PERIOD > {hierarchy_analysis.READING_HISTORY_MIN_BILLING_PERIOD}" in sql
    # format_sql_literal renders plain strings as plain '...' (no N prefix -
    # see app/core/sql_format.py's 2026-09-17 comment on why: an N'...'
    # literal against these varchar status/code columns silently defeats
    # index seeks), so the exclusion clause reads <> 'TIPTL00004'.
    assert f"r.READING_TYPE <> '{hierarchy_analysis.READING_HISTORY_EXCLUDED_READING_TYPE}'" in sql
    assert "ORDER BY r.ID_BILLING_PERIOD DESC" in sql


def test_hierarchy_analysis_reading_history_requires_connection(client, monkeypatch):
    import web.server as server_mod
    from app.config import AppConfig, AIConfig

    _login(client)
    empty_config = AppConfig(connections={}, active_connection="default", ai=AIConfig())
    monkeypatch.setattr(server_mod, "load_config", lambda: empty_config)
    resp = client.post("/api/hierarchy-analysis/reading-history", json={"id_sector_supply": "672343"})
    assert resp.status_code == 400


def test_hierarchy_analysis_export_xlsx_requires_rows(client):
    _login(client)
    resp = client.post("/api/hierarchy-analysis/export-xlsx", json={"rows": []})
    assert resp.status_code == 400


def test_hierarchy_analysis_export_xlsx_returns_workbook(client):
    from io import BytesIO

    import openpyxl

    _login(client)
    resp = client.post(
        "/api/hierarchy-analysis/export-xlsx",
        json={
            "rows": [
                {
                    "id_measuring_point": "20000062",
                    "id_main_mp": "143332",
                    "niss": "10465189-301",
                    "mp_type": "TIPEQM0002",
                    "mp_status": "1000STAMPO",
                    "secondaries_not_sent_count": "0",
                    "id_billing_period": "10000000196",
                    "id_reading": "1002459827",
                    "reading_date": "2023-03-27",
                    "reading_type": "TIPTL00003",
                    "read_status": "6000STSRED",
                    "ready_usage": "106",
                    "reading_usage": "106",
                },
            ]
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert 'filename="hierarchy_analysis.xlsx"' in resp.headers["content-disposition"]
    wb = openpyxl.load_workbook(BytesIO(resp.content))
    header = [c.value for c in wb.active[1]]
    assert header[6] == "Sec. Not Sent"
    assert header[16] == "Ready Usage"
    row = [c.value for c in wb.active[2]]
    assert row[0] == "20000062"
    assert row[2] == "10465189-301"
    assert row[6] == "0"
    assert row[16] == "106"
    # Bolded in the data rows too (not just the header) - the analyst's
    # own most-important field on this page should stand out here.
    assert wb.active.cell(row=2, column=17).font.bold is True


# ---------------- Bill Issuance Validator ----------------

_BILLISS_COLS = [
    "ID_PAYMENT_FORM", "REFERENCE", "NOTICE_UPDATE_DATE", "ID_BILL_RATE", "PERIOD_RATE",
    "ID_BILL_NEXT", "OFFERED_SERVICE_NEXT", "OFFERED_SERVICE_NEXT_DESC", "PERIOD_NEXT",
    "STATUS_NEXT", "PERIODS_AHEAD", "STATUS_NEXT_DESC",
]
_BILLISS_ROW = [
    110301288, "1103012884", "2026-08-24 11:28:48", 1073519772, 10000000236,
    1073720257, 1, "Electricity", 10000000237, "ESTFAC0015", 1, "En espera de otros servicios",
]
# RJ, 2026-09-14: "the next water or ele bills can be up to 11 billing
# period ahead" - a row further out than the old exact-next-period
# assumption, to test PERIODS_AHEAD is passed through correctly.
_BILLISS_ROW_FAR_AHEAD = [
    110301289, "1103012885", "2026-08-24 11:28:48", 1073519773, 10000000230,
    1073720258, 19, "Water", 10000000237, "ESTFAC0015", 7, "En espera de otros servicios",
]

# Case 1 "New Contract Match" fixtures - RJ, 2026-09-14 (later same day):
# "incorporate in case 1, the existing case 1 is ok, now i only want to
# add the case that its is only rate which is in pending validation
# notice_tmp and invoicing gccom_bill, the contract start (from_date) of
# gccom_contracted service is same as last_billing_date of gccom_bill".
_BILLISS_NC_COLS = [
    "ID_PAYMENT_FORM", "REFERENCE", "NOTICE_UPDATE_DATE", "ID_BILL", "BILLING_STATUS",
    "BILLING_STATUS_DESC", "LAST_BILLING_DATE", "BILLING_DATE", "ID_BILLING_PERIOD",
    "ID_CONTRACTED_SERVICE", "CONTRACT_FROM_DATE", "CONTRACT_STATUS",
]
_BILLISS_NC_ROW = [
    220400111, "2204001112", "2026-09-10 09:00:00", 1073900001, "ESTFAC0012",
    "En proceso de puesta al cobro", "2026-08-01", "2026-08-01", 10000000236,
    99001, "2026-08-01", "ESTSC00002",
]
# Used by the Case 4 fixture below (_biss4_fake_run_query) to represent
# an account New Contract Match matches - same shape, account 555.
_BILLISS_NC_ROW_ACCOUNT_555 = [
    555, "5550001", "2026-09-10 09:00:00", 9005, "ESTFAC0012",
    "En proceso de puesta al cobro", "2026-08-01", "2026-08-01", 10000000236,
    99005, "2026-08-01", "ESTSC00002",
]


# RJ, 2026-09-14 (later still, same day): "I wanted the 2 cases merged in
# 1 table, maybe you can do union but there will be clear identifier of
# the case that i can use to filter." /api/bill-issuance/detect now calls
# run_query TWICE - once for build_stuck_bills_query, once for build_new_
# contract_match_query - and merges the results. This fake dispatches on
# a distinguishing substring of each query's own SQL, same convention as
# _biss4_fake_run_query below ("ID_BILL_RATE" only appears in the Stuck
# Bills query, "CONTRACT_FROM_DATE" only in the New Contract Match query).
def _billiss_merged_fake_run_query(stuck_rows=None, nc_rows=None, captured=None):
    if stuck_rows is None:
        stuck_rows = [_BILLISS_ROW]
    if nc_rows is None:
        nc_rows = [_BILLISS_NC_ROW]

    def fake(conn, sql):
        if captured is not None:
            captured.setdefault("sqls", []).append(sql)
        if "ID_BILL_RATE" in sql:
            return QueryResult(columns=_BILLISS_COLS, rows=stuck_rows, elapsed_ms=1.0)
        if "CONTRACT_FROM_DATE" in sql:
            return QueryResult(columns=_BILLISS_NC_COLS, rows=nc_rows, elapsed_ms=1.0)
        raise AssertionError(f"unexpected sql passed to run_query: {sql[:200]}")

    return fake


def test_bill_issuance_detect_requires_login(client):
    resp = client.post("/api/bill-issuance/detect")
    assert resp.status_code == 401


def test_bill_issuance_detect_returns_rows(client, monkeypatch):
    import web.server as server_mod
    from app.core import bill_issuance_validator

    _login(client)
    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        _billiss_merged_fake_run_query(stuck_rows=[_BILLISS_ROW], nc_rows=[_BILLISS_NC_ROW]),
    )
    resp = client.post("/api/bill-issuance/detect")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rows"]) == 2
    assert body["stuck_bill_count"] == 1
    assert body["new_contract_match_count"] == 1

    stuck_row = next(r for r in body["rows"] if r["pattern"] == "stuck_bill")
    assert stuck_row["id_payment_form"] == "110301288"
    assert stuck_row["reference"] == "1103012884"
    assert stuck_row["id_bill_rate"] == "1073519772"
    assert stuck_row["period_rate"] == "10000000236"
    assert stuck_row["id_bill_next"] == "1073720257"
    assert stuck_row["offered_service_next"] == "1"
    assert stuck_row["offered_service_next_desc"] == "Electricity"
    assert stuck_row["period_next"] == "10000000237"
    assert stuck_row["status_next"] == "ESTFAC0015"
    assert stuck_row["periods_ahead"] == "1"
    assert stuck_row["status_next_desc"] == "En espera de otros servicios"

    nc_row = next(r for r in body["rows"] if r["pattern"] == "new_contract")
    assert nc_row["id_payment_form"] == "220400111"
    assert nc_row["reference"] == "2204001112"
    assert nc_row["id_bill_rate"] == "1073900001"  # mapped from ID_BILL
    assert nc_row["period_rate"] == "10000000236"  # mapped from ID_BILLING_PERIOD
    assert nc_row["billing_status_desc"] == "En proceso de puesta al cobro"
    assert nc_row["last_billing_date"] == "2026-08-01"
    assert nc_row["contract_from_date"] == "2026-08-01"
    assert nc_row["contract_status"] == "ESTSC00002"

    assert body["possibly_truncated"] is False
    assert body["max_periods_ahead"] == bill_issuance_validator.NEXT_PERIOD_MAX_AHEAD_DEFAULT


def test_bill_issuance_detect_max_periods_ahead_query_param(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    captured = {}
    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        _billiss_merged_fake_run_query(stuck_rows=[_BILLISS_ROW_FAR_AHEAD], captured=captured),
    )
    resp = client.post("/api/bill-issuance/detect?max_periods_ahead=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["max_periods_ahead"] == 5
    stuck_sql = next(s for s in captured["sqls"] if "ID_BILL_RATE" in s)
    assert "c.PERIOD_RATE + 5" in stuck_sql
    stuck_row = next(r for r in body["rows"] if r["pattern"] == "stuck_bill")
    assert stuck_row["periods_ahead"] == "7"  # passed through as-is, server doesn't re-filter


def test_bill_issuance_detect_requires_connection(client, monkeypatch):
    import web.server as server_mod
    from app.config import AppConfig, AIConfig

    _login(client)
    empty_config = AppConfig(connections={}, active_connection="default", ai=AIConfig())
    monkeypatch.setattr(server_mod, "load_config", lambda: empty_config)
    resp = client.post("/api/bill-issuance/detect")
    assert resp.status_code == 400


def test_bill_issuance_detect_flags_possible_truncation(client, monkeypatch):
    import web.server as server_mod
    from app.core import bill_issuance_validator

    _login(client)
    stuck_rows = [_BILLISS_ROW for _ in range(bill_issuance_validator.BILL_ISSUANCE_DEFAULT_LIMIT)]
    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        _billiss_merged_fake_run_query(stuck_rows=stuck_rows, nc_rows=[]),
    )
    resp = client.post("/api/bill-issuance/detect")
    assert resp.json()["possibly_truncated"] is True


def test_bill_issuance_export_xlsx_requires_rows(client):
    _login(client)
    resp = client.post("/api/bill-issuance/export-xlsx", json={"rows": []})
    assert resp.status_code == 400


def test_bill_issuance_export_xlsx_returns_workbook(client):
    # RJ, 2026-09-14: "i noticed that the excel download is also missing" -
    # same pattern as Detect All's own export-xlsx (see that test above).
    # RJ, 2026-09-14 (later same day): rows/columns now cover both merged
    # patterns - Pattern plus the New Contract Match-only columns.
    from io import BytesIO

    import openpyxl

    _login(client)
    resp = client.post(
        "/api/bill-issuance/export-xlsx",
        json={
            "rows": [
                {
                    "reference": "1103012884",
                    "pattern": "stuck_bill",
                    "notice_update_date": "2026-08-24 11:28:48",
                    "id_bill_rate": "1073519772",
                    "period_rate": "10000000236",
                    "id_bill_next": "1073720257",
                    "offered_service_next_desc": "Electricity",
                    "period_next": "10000000237",
                    "periods_ahead": "1",
                    "status_next_desc": "En espera de otros servicios",
                },
            ]
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert 'filename="bill_issuance_case1.xlsx"' in resp.headers["content-disposition"]

    wb = openpyxl.load_workbook(BytesIO(resp.content))
    ws = wb.active
    header = [c.value for c in ws[1]]
    assert header == [
        "Account", "Pattern", "Notice Updated", "Bill", "Billing Period", "Next Bill",
        "Service", "Next Period", "Months Ahead", "Next Status", "Bill Status",
        "Last Billing Date", "Contract Start", "Contract Status",
    ]
    row = [c.value for c in ws[2]]
    assert row == [
        "1103012884", "stuck_bill", "2026-08-24 11:28:48", "1073519772", "10000000236", "1073720257",
        "Electricity", "10000000237", "1", "En espera de otros servicios", "", "", "", "",
    ]


def test_bill_issuance_export_xlsx_requires_login(client):
    resp = client.post("/api/bill-issuance/export-xlsx", json={"rows": []})
    assert resp.status_code == 401


# Case 1 "Generate Release Script" - RJ, 2026-09-18: "for case 1, create
# script for all detected, 'Generate Release script'" with RJ's own exact
# UPDATE GCCOM_NOTICE_TMP template. The route re-runs both build_stuck_
# bills_query AND build_new_contract_match_query fresh (RJ, 2026-09-14,
# later same day: "the 2 cases merged in 1 table") and generates for
# every bill from either pattern.
def test_bill_issuance_generate_release_requires_login(client):
    resp = client.post("/api/bill-issuance/generate-release", json={})
    assert resp.status_code == 401


def test_bill_issuance_generate_release_produces_script_for_all_detected(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        _billiss_merged_fake_run_query(
            stuck_rows=[_BILLISS_ROW, _BILLISS_ROW_FAR_AHEAD], nc_rows=[_BILLISS_NC_ROW],
        ),
    )
    resp = client.post("/api/bill-issuance/generate-release", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["bill_count"] == 3
    assert "b.ID_BILL IN (1073519772, 1073519773, 1073900001)" in body["sql_text"]
    # Defaults match RJ's own literal template exactly.
    assert "UPDATE_USER = 'RMA'" in body["sql_text"]
    assert "UPDATE_PROGRAM = 'VALIDATION RELEASE_TERMINATED'" in body["sql_text"]

    history = client.get("/api/history").json()["entries"]
    assert any("Bill Issuance Case 1 Release" in e["table_name"] for e in history)


def test_bill_issuance_generate_release_scopes_to_selected_bills(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        _billiss_merged_fake_run_query(
            stuck_rows=[_BILLISS_ROW, _BILLISS_ROW_FAR_AHEAD], nc_rows=[_BILLISS_NC_ROW],
        ),
    )
    resp = client.post(
        "/api/bill-issuance/generate-release",
        json={"id_bill_rates": ["1073519772"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bill_count"] == 1
    assert "b.ID_BILL IN (1073519772)" in body["sql_text"]
    assert "1073519773" not in body["sql_text"]
    assert "1073900001" not in body["sql_text"]


def test_bill_issuance_generate_release_custom_program_and_user(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        _billiss_merged_fake_run_query(stuck_rows=[_BILLISS_ROW], nc_rows=[]),
    )
    resp = client.post(
        "/api/bill-issuance/generate-release",
        json={"program": "JIRA-42", "audit_user": "ANALYST1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "UPDATE_PROGRAM = 'JIRA-42'" in body["sql_text"]
    assert "UPDATE_USER = 'ANALYST1'" in body["sql_text"]


def test_bill_issuance_generate_release_no_bills_detected_errors(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        _billiss_merged_fake_run_query(stuck_rows=[], nc_rows=[]),
    )
    resp = client.post("/api/bill-issuance/generate-release", json={})
    assert resp.status_code == 400


def test_bill_issuance_generate_release_clean_strips_comments(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        _billiss_merged_fake_run_query(stuck_rows=[_BILLISS_ROW], nc_rows=[]),
    )
    resp = client.post("/api/bill-issuance/generate-release", json={"clean": True})
    assert resp.status_code == 200
    body = resp.json()
    assert not any(line.strip().startswith("--") for line in body["sql_text"].split("\n"))


def test_bill_issuance_generate_release_requires_editor_role(client, monkeypatch):
    import web.server as server_mod

    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        _billiss_merged_fake_run_query(stuck_rows=[_BILLISS_ROW], nc_rows=[]),
    )
    _create_and_login_as(client, "viewer4", "viewer")
    resp = client.post("/api/bill-issuance/generate-release", json={})
    assert resp.status_code == 403


def test_bill_issuance_generate_release_requires_connection(client, monkeypatch):
    import web.server as server_mod
    from app.config import AppConfig, AIConfig

    _login(client)
    empty_config = AppConfig(connections={}, active_connection="default", ai=AIConfig())
    monkeypatch.setattr(server_mod, "load_config", lambda: empty_config)
    resp = client.post("/api/bill-issuance/generate-release", json={})
    assert resp.status_code == 400


# Case 1 "New Contract Match" standalone route - kept for API-level access
# to just this one pattern (Case 4 "Unclassified" also calls the query
# builder function directly, not through this route, or through the
# merged /detect route). Unaffected by the /detect merge above.
def test_bill_issuance_case1_new_contract_match_detect_requires_login(client):
    resp = client.post("/api/bill-issuance/case1/new-contract-match/detect")
    assert resp.status_code == 401


def test_bill_issuance_case1_new_contract_match_detect_returns_rows(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        lambda conn, sql: QueryResult(columns=_BILLISS_NC_COLS, rows=[_BILLISS_NC_ROW], elapsed_ms=1.0),
    )
    resp = client.post("/api/bill-issuance/case1/new-contract-match/detect")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["id_payment_form"] == "220400111"
    assert row["reference"] == "2204001112"
    assert row["id_bill"] == "1073900001"
    assert row["billing_status"] == "ESTFAC0012"
    assert row["billing_status_desc"] == "En proceso de puesta al cobro"
    assert row["last_billing_date"] == "2026-08-01"
    assert row["contract_from_date"] == "2026-08-01"
    assert row["contract_status"] == "ESTSC00002"
    assert body["possibly_truncated"] is False


def test_bill_issuance_case1_new_contract_match_detect_requires_connection(client, monkeypatch):
    import web.server as server_mod
    from app.config import AppConfig, AIConfig

    _login(client)
    empty_config = AppConfig(connections={}, active_connection="default", ai=AIConfig())
    monkeypatch.setattr(server_mod, "load_config", lambda: empty_config)
    resp = client.post("/api/bill-issuance/case1/new-contract-match/detect")
    assert resp.status_code == 400


def test_bill_issuance_case1_new_contract_match_detect_flags_possible_truncation(client, monkeypatch):
    import web.server as server_mod
    from app.core import bill_issuance_validator

    _login(client)
    rows = [_BILLISS_NC_ROW for _ in range(bill_issuance_validator.NEW_CONTRACT_MATCH_DEFAULT_LIMIT)]
    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        lambda conn, sql: QueryResult(columns=_BILLISS_NC_COLS, rows=rows, elapsed_ms=1.0),
    )
    resp = client.post("/api/bill-issuance/case1/new-contract-match/detect")
    assert resp.json()["possibly_truncated"] is True


# ---------------- Bill Issuance Validator: Case 2 (terminated account, ----
# billing-period mismatch) ----------------
# RJ, 2026-09-15, redesigned 2026-09-16/17 - see app/core/bill_issuance_
# validator.py's Case 2 comment block for the full business-rule
# narrative, RJ's own 342702 example, and the account-grouped/drill-down
# redesign. Fixture below mirrors that example: account 342702 has four
# terminated services - Water and Sanitary stuck at period 236 needing to
# move to target 237, Electricity already correct at 237, and Rate with
# NO matching final bill at all (the NEEDS_UPDATE-but-id_bill-is-None
# case); account 555555 is a separate mismatched account, kept apart to
# test the id_payment_forms scoping on /generate. Columns match build_
# terminated_period_mismatch_query's 2026-09-17 shape: END_DATE not
# BILLING_DATE, ACCOUNT_HAS_ISSUE added, no *_DESC lookup columns
# (server.py maps offered-service names itself via _OFFERED_SERVICE_NAMES).
_BISS2_COLS = [
    "ID_PAYMENT_FORM", "REFERENCE", "ID_OFFERED_SERVICE", "END_DATE", "ID_BILL",
    "ID_BILLING_PERIOD", "BILLING_STATUS", "TARGET_PERIOD", "NEEDS_UPDATE", "ACCOUNT_HAS_ISSUE",
]
_BISS2_ROW_WATER = [
    342702, "3427021", 19, "2026-07-01", 1001,
    10000000236, "ESTFAC0012", 10000000237, 1, 1,
]
_BISS2_ROW_SANITARY = [
    342702, "3427021", 190, "2026-07-01", 1002,
    10000000236, "ESTFAC0012", 10000000237, 1, 1,
]
_BISS2_ROW_ELECTRICITY_OK = [
    342702, "3427021", 1, "2026-07-01", 1003,
    10000000237, "ESTFAC0012", 10000000237, 0, 1,
]
_BISS2_ROW_RATE_NO_BILL = [
    342702, "3427021", 176, "2026-07-01", None,
    None, None, 10000000237, 1, 1,
]
_BISS2_ROW_OTHER_ACCOUNT = [
    555555, "5555551", 19, "2026-06-01", 2001,
    10000000100, "ESTFAC0012", 10000000105, 1, 1,
]
_BISS2_ALL_ROWS = [
    _BISS2_ROW_WATER, _BISS2_ROW_SANITARY, _BISS2_ROW_ELECTRICITY_OK,
    _BISS2_ROW_RATE_NO_BILL, _BISS2_ROW_OTHER_ACCOUNT,
]


def test_bill_issuance_case2_detect_requires_login(client):
    resp = client.post("/api/bill-issuance/case2/detect")
    assert resp.status_code == 401


def test_bill_issuance_case2_detect_returns_account_grouped_rows(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        lambda conn, sql: QueryResult(columns=_BISS2_COLS, rows=_BISS2_ALL_ROWS, elapsed_ms=1.0),
    )
    resp = client.post("/api/bill-issuance/case2/detect")
    assert resp.status_code == 200
    body = resp.json()
    assert body["account_count"] == 2
    acct = next(a for a in body["accounts"] if a["id_payment_form"] == "342702")
    assert acct["reference"] == "3427021"
    assert acct["target_period"] == "10000000237"
    assert acct["service_count"] == 4
    assert acct["needs_update_count"] == 3  # Water, Sanitary, Rate (no bill) - Electricity is OK
    # RJ, 2026-09-14: "you did not add the filter to see the ones that
    # need action where the bill is missing" - Water/Sanitary have a bill
    # (just the wrong period), Rate has none at all, so this account
    # should show 1 missing_bill + 2 period_mismatch, summing to the same
    # needs_update_count as before.
    assert acct["missing_bill_count"] == 1
    assert acct["period_mismatch_count"] == 2
    assert acct["complete"] is False
    water = next(s for s in acct["services"] if s["id_bill"] == "1001")
    assert water["needs_update"] is True
    assert water["id_billing_period"] == "10000000236"
    assert water["reason"] == "period_mismatch"
    rate = next(s for s in acct["services"] if s["id_offered_service"] == "176")
    assert rate["id_bill"] == ""  # no matching final bill at all
    assert rate["needs_update"] is True
    assert rate["reason"] == "missing_bill"
    electricity = next(s for s in acct["services"] if s["id_offered_service"] == "1")
    assert electricity["needs_update"] is False
    assert electricity["reason"] is None
    assert body["accounts_needing_action"] == 2
    # Both accounts need action, but only 342702 has a service with no
    # bill at all (555555's single flagged service has a real bill, just
    # a period mismatch) - accounts_with_missing_bill should be 1, not 2.
    assert body["accounts_with_missing_bill"] == 1
    assert body["possibly_truncated"] is False


def test_bill_issuance_case2_detect_missing_bill_vs_period_mismatch_split(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        lambda conn, sql: QueryResult(columns=_BISS2_COLS, rows=_BISS2_ALL_ROWS, elapsed_ms=1.0),
    )
    resp = client.post("/api/bill-issuance/case2/detect")
    body = resp.json()
    other = next(a for a in body["accounts"] if a["id_payment_form"] == "555555")
    # 555555's only flagged service has a real bill (2001) - a period
    # mismatch, not a missing bill.
    assert other["missing_bill_count"] == 0
    assert other["period_mismatch_count"] == 1
    assert body["accounts_with_missing_bill"] == 1  # only 342702


def test_bill_issuance_case2_detect_complete_account_flagged(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    # An account whose only service is already fully aligned - ACCOUNT_
    # HAS_ISSUE = 0 - should come back complete=True.
    complete_row = [
        900001, "9000011", 1, "2026-05-01", 8001,
        10000000230, "ESTFAC0005", 10000000230, 0, 0,
    ]
    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        lambda conn, sql: QueryResult(columns=_BISS2_COLS, rows=[complete_row], elapsed_ms=1.0),
    )
    resp = client.post("/api/bill-issuance/case2/detect")
    body = resp.json()
    assert body["accounts"][0]["complete"] is True
    assert body["accounts_needing_action"] == 0


def test_bill_issuance_case2_detect_days_back_query_param_overrides_default(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    captured = {}

    def fake_run_query(conn, sql):
        captured["sql"] = sql
        return QueryResult(columns=_BISS2_COLS, rows=[], elapsed_ms=1.0)

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    resp = client.post("/api/bill-issuance/case2/detect?days_back=14")
    assert resp.status_code == 200
    assert resp.json()["days_back"] == 14
    assert "DATEADD(DAY, -14, CAST(GETDATE() AS DATE))" in captured["sql"]


def test_bill_issuance_case2_detect_days_back_zero_means_all_time(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    captured = {}

    def fake_run_query(conn, sql):
        captured["sql"] = sql
        return QueryResult(columns=_BISS2_COLS, rows=[], elapsed_ms=1.0)

    monkeypatch.setattr(server_mod.mssql, "run_query", fake_run_query)
    resp = client.post("/api/bill-issuance/case2/detect?days_back=0")
    assert resp.status_code == 200
    assert resp.json()["days_back"] is None
    assert "DATEADD(DAY" not in captured["sql"]


def test_bill_issuance_case2_detect_requires_connection(client, monkeypatch):
    import web.server as server_mod
    from app.config import AppConfig, AIConfig

    _login(client)
    empty_config = AppConfig(connections={}, active_connection="default", ai=AIConfig())
    monkeypatch.setattr(server_mod, "load_config", lambda: empty_config)
    resp = client.post("/api/bill-issuance/case2/detect")
    assert resp.status_code == 400


def test_bill_issuance_case2_detect_flags_possible_truncation(client, monkeypatch):
    import web.server as server_mod
    from app.core import bill_issuance_validator

    _login(client)
    rows = [_BISS2_ROW_WATER for _ in range(bill_issuance_validator.TERMINATED_PERIOD_DEFAULT_LIMIT)]
    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        lambda conn, sql: QueryResult(columns=_BISS2_COLS, rows=rows, elapsed_ms=1.0),
    )
    resp = client.post("/api/bill-issuance/case2/detect")
    assert resp.json()["possibly_truncated"] is True


def test_bill_issuance_case2_generate_produces_update_script_for_all_flagged(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        lambda conn, sql: QueryResult(columns=_BISS2_COLS, rows=_BISS2_ALL_ROWS, elapsed_ms=1.0),
    )
    resp = client.post(
        "/api/bill-issuance/case2/generate",
        json={"id_payment_forms": [], "program": "JIRA-9999"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # 3 flagged rows have a real bill to move (1001, 1002, 2001); the Rate
    # row (no id_bill) is skipped with a warning, not emitted as an
    # UPDATE - 1003 is already at its target period so it's excluded too.
    assert body["update_count"] == 3
    assert "WHERE ID_BILL = 1001" in body["sql_text"]
    assert "WHERE ID_BILL = 1002" in body["sql_text"]
    assert "WHERE ID_BILL = 2001" in body["sql_text"]
    assert "SET ID_BILLING_PERIOD = 10000000237" in body["sql_text"]
    assert "JIRA-9999" in body["sql_text"]
    assert any("no bill matching" in w for w in body["warnings"])

    history = client.get("/api/history").json()["entries"]
    assert any("Bill Issuance Case 2" in e["table_name"] for e in history)


def test_bill_issuance_case2_generate_scopes_to_selected_accounts(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        lambda conn, sql: QueryResult(columns=_BISS2_COLS, rows=_BISS2_ALL_ROWS, elapsed_ms=1.0),
    )
    resp = client.post(
        "/api/bill-issuance/case2/generate",
        json={"id_payment_forms": ["342702"], "program": "JIRA-1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["update_count"] == 2
    assert "WHERE ID_BILL = 1001" in body["sql_text"]
    assert "WHERE ID_BILL = 1002" in body["sql_text"]
    assert "WHERE ID_BILL = 2001" not in body["sql_text"]


def test_bill_issuance_case2_generate_no_rows_needing_update_errors(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        lambda conn, sql: QueryResult(columns=_BISS2_COLS, rows=[_BISS2_ROW_ELECTRICITY_OK], elapsed_ms=1.0),
    )
    resp = client.post(
        "/api/bill-issuance/case2/generate",
        json={"id_payment_forms": [], "program": "JIRA-1"},
    )
    assert resp.status_code == 400


def test_bill_issuance_case2_generate_clean_strips_comments(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        lambda conn, sql: QueryResult(columns=_BISS2_COLS, rows=_BISS2_ALL_ROWS, elapsed_ms=1.0),
    )
    resp = client.post(
        "/api/bill-issuance/case2/generate",
        json={"id_payment_forms": [], "program": "JIRA-1", "clean": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert not any(line.strip().startswith("--") for line in body["sql_text"].split("\n"))


def test_bill_issuance_case2_generate_requires_editor_role(client, monkeypatch):
    import web.server as server_mod

    monkeypatch.setattr(
        server_mod.mssql, "run_query",
        lambda conn, sql: QueryResult(columns=_BISS2_COLS, rows=_BISS2_ALL_ROWS, elapsed_ms=1.0),
    )
    _create_and_login_as(client, "viewer3", "viewer")
    resp = client.post(
        "/api/bill-issuance/case2/generate",
        json={"id_payment_forms": [], "program": "JIRA-1"},
    )
    assert resp.status_code == 403


# ---------------- Bill Issuance Validator: Case 4 (Unclassified) --------
# RJ, 2026-09-14 (same day), own words: "create a 4th case,
# 'Unclassified' those that are pending validation in notice TMP, and not
# in case 1, case 2, case 3, and any other case that we will add in the
# future." The route calls build_stuck_bills_query, build_new_contract_
# match_query, build_terminated_period_mismatch_query, build_bills_
# complete_query, and build_unclassified_query - this fixture dispatches
# a distinct, minimal fake result to each based on a marker column/text
# unique to that query's own shape, so the test can assert the excluded-
# account logic without needing a real database.
#
# RJ, later same day, asked directly: "was this new rule considered in
# case for unclassified?" - Case 1's own New Contract Match addition
# (build_new_contract_match_query) had NOT been wired into this route's
# exclusion set when it shipped. Account 555 below plays that role: it
# only appears in New Contract Match's own fake result, so it must be
# excluded from Unclassified same as 222/333/444 are.
_BISS3_COLS = [
    "REFERENCE", "ID_PAYMENT_FORM", "CONTRACTED_SERVICES", "BILLS", "MISSING_BILLS",
    "ID_BILLING_PERIOD", "BILLING_PERIOD_NAME", "WITH_ACTIVE_CONTRACT",
]
_BISS4_COLS = [
    "REFERENCE", "ID_PAYMENT_FORM", "ID_BILL", "ID_BILLING_PERIOD", "BILL_TYPE",
    "ID_OFFERED_SERVICE", "OFFERED_SERVICE_DESC", "BILLING_STATUS", "BILLING_STATUS_DESC",
    "BILLING_DATE",
]
# Account 111 has nothing in Case 1/2/3/New-Contract-Match's own fake
# results below, so it's the one row that should survive as Unclassified.
# Accounts 222/333/444/555 each appear in exactly one of those four
# account sets, so each should be excluded from Case 4's output even
# though they also have a row in the general "pending validation"
# universe below.
_BISS4_ROW_UNCLASSIFIED = ["1110001", 111, 9001, 10000000236, "TFGEN00001", 1, "Electricity", "ESTFAC0012", "En proceso de puesta al cobro", "2026-08-01"]
_BISS4_ROW_CASE1_ACCOUNT = ["2220001", 222, 9002, 10000000236, "TFGEN00001", 176, "Rate", "ESTFAC0012", "En proceso de puesta al cobro", "2026-08-01"]
_BISS4_ROW_CASE2_ACCOUNT = ["3330001", 333, 9003, 10000000236, "TFGEN00001", 19, "Water", "ESTFAC0012", "En proceso de puesta al cobro", "2026-08-01"]
_BISS4_ROW_CASE3_ACCOUNT = ["4440001", 444, 9004, 10000000236, "TFGEN00001", 1, "Electricity", "ESTFAC0012", "En proceso de puesta al cobro", "2026-08-01"]
_BISS4_ROW_NEW_CONTRACT_MATCH_ACCOUNT = ["5550001", 555, 9005, 10000000236, "TFGEN00001", 176, "Rate", "ESTFAC0012", "En proceso de puesta al cobro", "2026-08-01"]
_BISS4_ALL_ROWS = [
    _BISS4_ROW_UNCLASSIFIED, _BISS4_ROW_CASE1_ACCOUNT, _BISS4_ROW_CASE2_ACCOUNT, _BISS4_ROW_CASE3_ACCOUNT,
    _BISS4_ROW_NEW_CONTRACT_MATCH_ACCOUNT,
]


def _biss4_fake_run_query(conn, sql):
    if "ID_BILL_RATE" in sql:  # build_stuck_bills_query (Case 1)
        return QueryResult(
            columns=_BILLISS_COLS,
            rows=[[222, "2220001", "2026-08-01", 1, 10000000235, 2, 1, "Electricity", 10000000236, "ESTFAC0015", 1, "En espera de otros servicios"]],
            elapsed_ms=1.0,
        )
    if "CONTRACT_FROM_DATE" in sql:  # build_new_contract_match_query (Case 1: New Contract Match)
        return QueryResult(columns=_BILLISS_NC_COLS, rows=[_BILLISS_NC_ROW_ACCOUNT_555], elapsed_ms=1.0)
    if "ACCOUNT_HAS_ISSUE" in sql:  # build_terminated_period_mismatch_query (Case 2)
        return QueryResult(
            columns=_BISS2_COLS,
            rows=[[333, "3330001", 19, "2026-08-01", 5001, 10000000236, "ESTFAC0012", 10000000236, 1, 1]],
            elapsed_ms=1.0,
        )
    if "WITH_ACTIVE_CONTRACT" in sql:  # build_bills_complete_query (Case 3)
        return QueryResult(
            columns=_BISS3_COLS,
            rows=[["4440001", 444, 3, 3, 0, 10000000236, "August 2026", "YES"]],
            elapsed_ms=1.0,
        )
    return QueryResult(columns=_BISS4_COLS, rows=_BISS4_ALL_ROWS, elapsed_ms=1.0)  # build_unclassified_query


def test_bill_issuance_case4_detect_requires_login(client):
    resp = client.post("/api/bill-issuance/case4/detect")
    assert resp.status_code == 401


def test_bill_issuance_case4_detect_excludes_case123_accounts(client, monkeypatch):
    import web.server as server_mod

    _login(client)
    monkeypatch.setattr(server_mod.mssql, "run_query", _biss4_fake_run_query)
    resp = client.post("/api/bill-issuance/case4/detect")
    assert resp.status_code == 200
    body = resp.json()
    assert body["account_count"] == 1
    assert body["case1_account_count"] == 1
    assert body["new_contract_match_account_count"] == 1
    assert body["case2_account_count"] == 1
    assert body["case3_account_count"] == 1
    acct = body["accounts"][0]
    assert acct["id_payment_form"] == "111"
    assert acct["reference"] == "1110001"
    assert acct["bill_count"] == 1
    assert acct["bills"][0]["id_bill"] == "9001"
    assert acct["bills"][0]["offered_service_desc"] == "Electricity"
    # Accounts 222/333/444/555 each appeared in the general pending
    # universe too, but were each excluded by exactly one of Case
    # 1/New-Contract-Match/2/3 - confirm none of them leaked into the
    # Unclassified account list. 555 specifically is the regression check
    # for "was this new rule considered in case for unclassified?" - New
    # Contract Match's own account set is now wired into the exclusion.
    assert {a["id_payment_form"] for a in body["accounts"]} == {"111"}


def test_bill_issuance_case4_export_xlsx_requires_rows(client):
    _login(client)
    resp = client.post("/api/bill-issuance/case4/export-xlsx", json={"rows": []})
    assert resp.status_code == 400


def test_bill_issuance_case4_export_xlsx_returns_workbook(client):
    import openpyxl
    from io import BytesIO

    _login(client)
    resp = client.post(
        "/api/bill-issuance/case4/export-xlsx",
        json={"rows": [{
            "reference": "1110001", "id_payment_form": "111", "bill_count": "1",
            "offered_services": "Electricity", "billing_periods": "10000000236",
        }]},
    )
    assert resp.status_code == 200
    wb = openpyxl.load_workbook(BytesIO(resp.content))
    ws = wb.active
    header = [c.value for c in ws[1]]
    assert header == ["Account", "Payment Form ID", "Bill Count", "Services", "Billing Periods"]
    row = [c.value for c in ws[2]]
    assert row == ["1110001", "111", "1", "Electricity", "10000000236"]


def test_bill_issuance_case4_export_xlsx_requires_login(client):
    resp = client.post("/api/bill-issuance/case4/export-xlsx", json={"rows": []})
    assert resp.status_code == 401


# ---------------- Dashboard ----------------

def test_dashboard_stats_requires_a_query_first(client):
    _login(client)
    resp = client.get("/api/dashboard/stats")
    assert resp.status_code == 400


def test_dashboard_stats_returns_kpis_and_column_breakdown(client, monkeypatch):
    _run_fake_query(client, monkeypatch)
    resp = client.get("/api/dashboard/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["row_count"] == 2
    assert body["column_count"] == 3
    by_name = {c["name"]: c for c in body["columns"]}
    assert by_name["amount"]["kind"] == "numeric"
    assert by_name["amount"]["mean_value"] == 15.0
    assert by_name["name"]["kind"] == "categorical"


def test_dashboard_stats_includes_median_percentiles_outliers_and_unique_ratio(client, monkeypatch):
    _run_fake_query(client, monkeypatch)
    resp = client.get("/api/dashboard/stats")
    body = resp.json()
    amount = {c["name"]: c for c in body["columns"]}["amount"]
    assert amount["median_value"] == 15.0
    assert amount["p25_value"] == pytest.approx(12.5)
    assert amount["p75_value"] == pytest.approx(17.5)
    assert amount["outlier_count"] == 0
    assert amount["unique_ratio"] == pytest.approx(1.0)  # both amounts (10, 20) are distinct


def test_dashboard_stats_includes_duplicate_row_count_and_quality_flags(client, monkeypatch):
    import web.server as server_mod

    fake_result = QueryResult(
        columns=["id", "status"],
        rows=[[1, "active"], [2, "active"], [1, "active"]],  # row [1, "active"] repeats
        elapsed_ms=1.0, source_table="dbo.Accounts", source_schema="dbo",
    )
    monkeypatch.setattr(server_mod.mssql, "run_query", lambda conn, sql: fake_result)
    _login(client)
    client.post("/api/query/run", json={"sql": "SELECT * FROM dbo.Accounts"})

    resp = client.get("/api/dashboard/stats")
    body = resp.json()
    assert body["duplicate_row_count"] == 1
    flag_kinds_by_column = {(f["kind"], f["column"]) for f in body["quality_flags"]}
    assert ("constant", "status") in flag_kinds_by_column


def test_dashboard_histogram(client, monkeypatch):
    _run_fake_query(client, monkeypatch)
    resp = client.post("/api/dashboard/histogram", json={"column": "amount", "bins": 4})
    assert resp.status_code == 200
    body = resp.json()
    assert sum(body["counts"]) == 2
    assert len(body["labels"]) == len(body["counts"])


def test_dashboard_histogram_unknown_column(client, monkeypatch):
    _run_fake_query(client, monkeypatch)
    resp = client.post("/api/dashboard/histogram", json={"column": "nope"})
    assert resp.status_code == 400


def test_dashboard_correlation(client, monkeypatch):
    import web.server as server_mod
    from app.db.mssql import QueryResult

    corr_result = QueryResult(
        columns=["x", "y"], rows=[[1, 2], [2, 4], [3, 6]], elapsed_ms=1.0,
        source_table="dbo.Points", source_schema="dbo",
    )
    monkeypatch.setattr(server_mod.mssql, "run_query", lambda conn, sql: corr_result)
    _login(client)
    client.post("/api/query/run", json={"sql": "SELECT * FROM dbo.Points"})

    resp = client.post("/api/dashboard/correlation", json={"column_a": "x", "column_b": "y"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["r"] == pytest.approx(1.0)
    assert len(body["points"]) == 3


def test_dashboard_trend_filters_by_table(client, monkeypatch):
    _run_fake_query(client, monkeypatch)
    client.post("/api/snapshot/export", json={"name": "snap1"})

    resp = client.get("/api/dashboard/trend", params={"table": "dbo.Accounts"})
    assert resp.status_code == 200
    assert len(resp.json()["points"]) == 1

    resp = client.get("/api/dashboard/trend", params={"table": "dbo.SomethingElse"})
    assert resp.status_code == 200
    assert resp.json()["points"] == []


# ---------------- RBAC / User management ----------------
# The bootstrapped first-run account _login(client) uses is always role
# "admin" (see web/auth.py's _bootstrap), so most of test_web_api.py's
# existing tests are implicitly "as admin". These tests specifically
# exercise the OTHER two roles and the /api/users management routes -
# see web/auth.py's module docstring for the full role matrix and the
# lockout guards (test_auth.py covers those at the WebUserStore level
# directly; these cover the same guards surfaced through the API).

def _create_and_login_as(client, username, role, password="temporarypw123"):
    """Logs in as the bootstrapped admin, creates a new user with the
    given role, then switches the client's session to that new user -
    same cookie jar, simulating one browser signing out of admin and
    back in as someone else. Returns the login response body."""
    _login(client)
    resp = client.post("/api/users", json={"username": username, "password": password, "role": role})
    assert resp.status_code == 200, resp.text
    client.post("/api/logout")
    resp = client.post("/api/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_session_reports_role_and_forces_password_change_for_new_accounts(client):
    info = _create_and_login_as(client, "viewer1", "viewer")
    assert info["role"] == "viewer"
    assert info["must_change_password"] is True
    session = client.get("/api/session").json()
    assert session["role"] == "viewer"
    assert session["must_change_password"] is True


# ---------------- Menu access (admin-only sidebar visibility config) ----

def test_session_defaults_to_every_menu_visible(client):
    _login(client)
    session = client.get("/api/session").json()
    import web.menu_access as ma
    assert set(session["allowed_menus"]) == set(ma.MENU_IDS)


def test_get_menu_access_requires_admin(client):
    _create_and_login_as(client, "viewer1", "viewer")
    resp = client.get("/api/config/menu-access")
    assert resp.status_code == 403


def test_put_menu_access_requires_admin(client):
    _create_and_login_as(client, "editor1", "editor")
    resp = client.put("/api/config/menu-access", json={"access": {"viewer": [], "editor": [], "admin": ["settings"]}})
    assert resp.status_code == 403


def test_admin_can_view_and_update_menu_access(client):
    _login(client)
    import web.menu_access as ma

    get_resp = client.get("/api/config/menu-access")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert {m["id"] for m in body["menus"]} == set(ma.MENU_IDS)
    assert set(body["access"]["viewer"]) == set(ma.MENU_IDS)  # defaults, nothing saved yet

    put_resp = client.put(
        "/api/config/menu-access",
        json={
            "access": {
                "viewer": ["workspace", "dashboard"],
                "editor": list(ma.MENU_IDS),
                "admin": list(ma.MENU_IDS),
            }
        },
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["access"]["viewer"] == ["workspace", "dashboard"]

    # A fresh GET reflects the save, and a viewer's own /api/session now
    # reports the narrowed list.
    assert client.get("/api/config/menu-access").json()["access"]["viewer"] == ["workspace", "dashboard"]


def test_put_menu_access_rejects_removing_settings_from_admin(client):
    _login(client)
    import web.menu_access as ma

    resp = client.put(
        "/api/config/menu-access",
        json={
            "access": {
                "viewer": list(ma.MENU_IDS),
                "editor": list(ma.MENU_IDS),
                "admin": [m for m in ma.MENU_IDS if m != "settings"],
            }
        },
    )
    assert resp.status_code == 400
    assert "settings" in resp.json()["detail"].lower()


def test_put_menu_access_rejects_unknown_menu_id(client):
    _login(client)
    import web.menu_access as ma

    resp = client.put(
        "/api/config/menu-access",
        json={"access": {"viewer": ["not_real"], "editor": list(ma.MENU_IDS), "admin": list(ma.MENU_IDS)}},
    )
    assert resp.status_code == 400


def test_viewer_session_reflects_saved_menu_access(client):
    _login(client)
    import web.menu_access as ma

    client.put(
        "/api/config/menu-access",
        json={
            "access": {
                "viewer": ["workspace"],
                "editor": list(ma.MENU_IDS),
                "admin": list(ma.MENU_IDS),
            }
        },
    )
    info = _create_and_login_as(client, "viewer2", "viewer")
    assert info["allowed_menus"] == ["workspace"]
    session = client.get("/api/session").json()
    assert session["allowed_menus"] == ["workspace"]


def test_viewer_can_run_queries_but_not_generate_scripts(client, monkeypatch):
    import web.server as server_mod
    from app.db.mssql import QueryResult

    _create_and_login_as(client, "viewer1", "viewer")
    fake_result = QueryResult(columns=["id"], rows=[[1]], elapsed_ms=1.0, source_table="dbo.T", source_schema="dbo")
    monkeypatch.setattr(server_mod.mssql, "run_query", lambda conn, sql: fake_result)

    run_resp = client.post("/api/query/run", json={"sql": "SELECT * FROM dbo.T"})
    assert run_resp.status_code == 200  # read queries stay open to viewers

    gen_resp = client.post(
        "/api/script/generate",
        json={"edited_rows": [["2"]], "key_columns": ["id"], "table": "T", "program": "JIRA-1"},
    )
    assert gen_resp.status_code == 403


def test_editor_can_generate_scripts_but_not_manage_connections(client, monkeypatch):
    import web.server as server_mod
    from app.db.mssql import QueryResult

    _create_and_login_as(client, "editor1", "editor")
    fake_result = QueryResult(columns=["id"], rows=[[1]], elapsed_ms=1.0, source_table="dbo.T", source_schema="dbo")
    monkeypatch.setattr(server_mod.mssql, "run_query", lambda conn, sql: fake_result)
    client.post("/api/query/run", json={"sql": "SELECT * FROM dbo.T"})

    gen_resp = client.post(
        "/api/script/generate",
        json={"edited_rows": [["2"]], "key_columns": ["id"], "table": "T", "program": "JIRA-1"},
    )
    assert gen_resp.status_code == 200

    conn_resp = client.post("/api/config/connections", json={"name": "New", "server": "x"})
    assert conn_resp.status_code == 403


def test_viewer_and_editor_blocked_from_user_management(client):
    _create_and_login_as(client, "viewer1", "viewer")
    assert client.get("/api/users").status_code == 403

    _create_and_login_as(client, "editor1", "editor")
    resp = client.post("/api/users", json={"username": "x", "password": "password123", "role": "viewer"})
    assert resp.status_code == 403


def _read_bootstrap_admin_password() -> str:
    """Reads the first-run admin password directly off disk, WITHOUT
    logging in (which would consume/delete the notice file - see
    consume_first_run_notice). Must be called before the first
    successful admin login in a test that also needs a second admin
    login later (e.g. from a separate TestClient/"browser")."""
    import web.auth as auth_mod

    auth_mod.WebUserStore.load()  # triggers bootstrap if this is the very first call
    notice = auth_mod.get_data_dir() / "web_admin_first_run.txt"
    text = notice.read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.startswith("Password: ")][0].split(": ", 1)[1]


def test_viewer_allowed_ai_explain_but_blocked_ai_suggest(client, monkeypatch):
    import web.server as server_mod

    admin_password = _read_bootstrap_admin_password()
    monkeypatch.setattr(
        server_mod.ai_assist, "explain_query",
        lambda api_key, model, sql: server_mod.ai_assist.AIResponse(success=True, text="explanation"),
    )

    # Log in as admin (password passed explicitly - _login()'s own
    # notice-file lookup would fail second time around, see the helper
    # above) to enable AI, then create and switch to a viewer account.
    _login(client, "admin", admin_password)
    _enable_ai(client)
    resp = client.post("/api/users", json={"username": "viewer1", "password": "temporarypw123", "role": "viewer"})
    assert resp.status_code == 200
    client.post("/api/logout")
    client.post("/api/login", json={"username": "viewer1", "password": "temporarypw123"})

    explain_resp = client.post("/api/ai/explain", json={"sql": "SELECT 1"})
    assert explain_resp.status_code == 200

    suggest_resp = client.post("/api/ai/suggest", json={"sql": "SELECT 1"})
    assert suggest_resp.status_code == 403


def test_users_crud_and_lockout_guards_via_api(client):
    _login(client)  # admin

    create_resp = client.post("/api/users", json={"username": "bob", "password": "password123", "role": "viewer"})
    assert create_resp.status_code == 200
    assert create_resp.json()["role"] == "viewer"

    list_resp = client.get("/api/users")
    usernames = {u["username"] for u in list_resp.json()["users"]}
    assert {"admin", "bob"} <= usernames

    role_resp = client.put("/api/users/bob/role", json={"role": "editor"})
    assert role_resp.status_code == 200
    assert role_resp.json()["role"] == "editor"

    deactivate_resp = client.post("/api/users/bob/deactivate")
    assert deactivate_resp.status_code == 200
    assert deactivate_resp.json()["active"] is False

    reactivate_resp = client.post("/api/users/bob/reactivate")
    assert reactivate_resp.status_code == 200
    assert reactivate_resp.json()["active"] is True

    reset_resp = client.post("/api/users/bob/reset-password", json={"new_password": "brandnewpw1"})
    assert reset_resp.status_code == 200

    # Can't deactivate/delete yourself, and can't demote/deactivate the
    # last active admin (there's only "admin" as an admin here).
    self_deactivate = client.post("/api/users/admin/deactivate")
    assert self_deactivate.status_code == 400
    self_delete = client.delete("/api/users/admin")
    assert self_delete.status_code == 400
    demote_self = client.put("/api/users/admin/role", json={"role": "editor"})
    assert demote_self.status_code == 400

    delete_resp = client.delete("/api/users/bob")
    assert delete_resp.status_code == 200
    assert "bob" not in {u["username"] for u in client.get("/api/users").json()["users"]}


def test_deactivated_users_session_is_rejected_on_next_request(client):
    # Capture the admin password BEFORE any login in this test - logging
    # in (even bob's, via _create_and_login_as) consumes the first-run
    # notice file, and a second, independent "browser" below needs its
    # own admin login afterward.
    admin_password = _read_bootstrap_admin_password()

    _create_and_login_as(client, "bob", "editor")
    # bob is now the logged-in session on `client`. Log in as admin in a
    # SEPARATE TestClient (its own, independent cookie jar) so bob's
    # session on `client` is left completely undisturbed, and deactivate
    # bob from there - simulating a different admin, in a different
    # browser, revoking bob's access.
    from fastapi.testclient import TestClient
    import web.server as server_mod

    admin_client = TestClient(server_mod.app)
    _login(admin_client, "admin", admin_password)
    resp = admin_client.post("/api/users/bob/deactivate")
    assert resp.status_code == 200

    # bob's existing session cookie is still "valid" (signed correctly)
    # but the account behind it is no longer active - the NEXT request
    # on that cookie should be rejected, not silently allowed through.
    still_logged_in = client.get("/api/session")
    assert still_logged_in.status_code == 401


def test_change_own_password_requires_correct_current_password(client):
    _create_and_login_as(client, "bob", "editor")
    wrong = client.post(
        "/api/account/change-password", json={"old_password": "wrong", "new_password": "newpassword1"}
    )
    assert wrong.status_code == 400

    right = client.post(
        "/api/account/change-password",
        json={"old_password": "temporarypw123", "new_password": "newpassword1"},
    )
    assert right.status_code == 200
    session = client.get("/api/session").json()
    assert session["must_change_password"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
