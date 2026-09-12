"""
Pure-logic tests for web.auth's role model (RBAC) and lockout guards -
see that module's docstring for the full role matrix and the reasoning
behind each guard rail. No FastAPI/HTTP involved here (that's
test_web_api.py's job for the /api/users routes) - these tests drive
WebUserStore directly.

Filesystem isolation follows the same pattern test_web_api.py's client()
fixture documents: web.auth imported get_web_users_path/get_data_dir BY
NAME at its own module top, so patching app.utils.paths' originals
wouldn't reach it - patch web.auth's own bound names directly.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import web.auth as auth_mod
from web.auth import ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER, WebUserStore


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    users_path = tmp_path / "web_users.json"
    monkeypatch.setattr(auth_mod, "get_web_users_path", lambda: users_path)
    monkeypatch.setattr(auth_mod, "get_data_dir", lambda: tmp_path)
    return WebUserStore()  # empty - NOT ._bootstrap(), so tests control exactly what exists


def _add(store, username, password="password123", role=ROLE_EDITOR, **kw):
    return store.add_user(username, password, role=role, **kw)


# ---------------- add_user ----------------

def test_add_user_requires_valid_role(isolated_store):
    with pytest.raises(ValueError, match="Unknown role"):
        _add(isolated_store, "bob", role="superuser")


def test_add_user_requires_min_password_length(isolated_store):
    with pytest.raises(ValueError, match="8 characters"):
        _add(isolated_store, "bob", password="short")


def test_add_user_rejects_duplicate_username_case_insensitive(isolated_store):
    _add(isolated_store, "Bob")
    with pytest.raises(ValueError, match="already exists"):
        _add(isolated_store, "bob")


def test_add_user_defaults_to_must_change_password_true(isolated_store):
    user = _add(isolated_store, "bob")
    assert user.must_change_password is True


def test_bootstrap_admin_does_not_require_password_change(isolated_store):
    # _bootstrap explicitly passes require_password_change=False - the
    # first-run notice file IS the one-time handoff for that account.
    user = isolated_store.add_user(
        "admin", "generatedpw123", role=ROLE_ADMIN, require_password_change=False,
    )
    assert user.must_change_password is False


# ---------------- role changes & the "last active admin" guard ----------------

def test_set_role_changes_role(isolated_store):
    _add(isolated_store, "admin1", role=ROLE_ADMIN)
    _add(isolated_store, "bob", role=ROLE_VIEWER)
    isolated_store.set_role("bob", ROLE_EDITOR)
    assert isolated_store.get("bob").role == ROLE_EDITOR


def test_cannot_demote_the_last_active_admin(isolated_store):
    _add(isolated_store, "solo-admin", role=ROLE_ADMIN)
    with pytest.raises(ValueError, match="last remaining active admin"):
        isolated_store.set_role("solo-admin", ROLE_EDITOR)


def test_can_demote_an_admin_when_another_active_admin_exists(isolated_store):
    _add(isolated_store, "admin1", role=ROLE_ADMIN)
    _add(isolated_store, "admin2", role=ROLE_ADMIN)
    isolated_store.set_role("admin1", ROLE_EDITOR)
    assert isolated_store.get("admin1").role == ROLE_EDITOR
    assert isolated_store.get("admin2").role == ROLE_ADMIN


def test_active_admin_count_ignores_deactivated_admins(isolated_store):
    _add(isolated_store, "admin1", role=ROLE_ADMIN)
    _add(isolated_store, "admin2", role=ROLE_ADMIN)
    isolated_store.deactivate("admin2", acting_username="admin1")
    # Only one ACTIVE admin remains (admin1) even though two admin
    # records exist - demoting admin1 now should be blocked.
    with pytest.raises(ValueError, match="last remaining active admin"):
        isolated_store.set_role("admin1", ROLE_EDITOR)


# ---------------- deactivate / reactivate ----------------

def test_cannot_deactivate_own_account(isolated_store):
    _add(isolated_store, "admin1", role=ROLE_ADMIN)
    _add(isolated_store, "admin2", role=ROLE_ADMIN)
    with pytest.raises(ValueError, match="own account"):
        isolated_store.deactivate("admin1", acting_username="admin1")


def test_cannot_deactivate_the_last_active_admin(isolated_store):
    _add(isolated_store, "solo-admin", role=ROLE_ADMIN)
    _add(isolated_store, "someone-else", role=ROLE_EDITOR)
    with pytest.raises(ValueError, match="last remaining active admin"):
        isolated_store.deactivate("solo-admin", acting_username="someone-else")


def test_deactivate_then_reactivate_round_trips(isolated_store):
    _add(isolated_store, "admin1", role=ROLE_ADMIN)
    _add(isolated_store, "bob", role=ROLE_EDITOR)
    isolated_store.deactivate("bob", acting_username="admin1")
    assert isolated_store.get("bob").active is False
    isolated_store.reactivate("bob")
    assert isolated_store.get("bob").active is True


def test_deactivated_user_fails_verify(isolated_store):
    _add(isolated_store, "admin1", role=ROLE_ADMIN)
    _add(isolated_store, "bob", "password123", role=ROLE_EDITOR)
    assert isolated_store.verify("bob", "password123") is True
    isolated_store.deactivate("bob", acting_username="admin1")
    assert isolated_store.verify("bob", "password123") is False


# ---------------- remove_user ----------------

def test_cannot_remove_own_account(isolated_store):
    _add(isolated_store, "admin1", role=ROLE_ADMIN)
    _add(isolated_store, "admin2", role=ROLE_ADMIN)
    with pytest.raises(ValueError, match="own account"):
        isolated_store.remove_user("admin1", acting_username="admin1")


def test_cannot_remove_the_last_remaining_account(isolated_store):
    _add(isolated_store, "solo", role=ROLE_ADMIN)
    with pytest.raises(ValueError, match="last remaining login account"):
        isolated_store.remove_user("solo", acting_username="someone-else")


def test_cannot_remove_the_last_active_admin_even_with_other_users(isolated_store):
    _add(isolated_store, "solo-admin", role=ROLE_ADMIN)
    _add(isolated_store, "viewer1", role=ROLE_VIEWER)
    with pytest.raises(ValueError, match="last remaining active admin"):
        isolated_store.remove_user("solo-admin", acting_username="viewer1")


def test_remove_user_succeeds_when_not_last_admin(isolated_store):
    _add(isolated_store, "admin1", role=ROLE_ADMIN)
    _add(isolated_store, "bob", role=ROLE_EDITOR)
    isolated_store.remove_user("bob", acting_username="admin1")
    assert isolated_store.get("bob") is None


# ---------------- passwords ----------------

def test_change_own_password_requires_correct_old_password(isolated_store):
    _add(isolated_store, "bob", "originalpw1", role=ROLE_EDITOR)
    with pytest.raises(ValueError, match="incorrect"):
        isolated_store.change_own_password("bob", "wrongpw", "newpassword1")


def test_change_own_password_clears_must_change_flag(isolated_store):
    user = _add(isolated_store, "bob", "originalpw1", role=ROLE_EDITOR)
    assert user.must_change_password is True
    isolated_store.change_own_password("bob", "originalpw1", "newpassword1")
    updated = isolated_store.get("bob")
    assert updated.must_change_password is False
    assert isolated_store.verify("bob", "newpassword1") is True
    assert isolated_store.verify("bob", "originalpw1") is False


def test_admin_set_password_forces_must_change_password(isolated_store):
    user = _add(isolated_store, "bob", "originalpw1", role=ROLE_EDITOR)
    isolated_store.change_own_password("bob", "originalpw1", "newpassword1")
    assert isolated_store.get("bob").must_change_password is False
    isolated_store.set_password("bob", "resetbyadmin1")
    updated = isolated_store.get("bob")
    assert updated.must_change_password is True
    assert isolated_store.verify("bob", "resetbyadmin1") is True


# ---------------- load()/save() round trip + back-compat ----------------

def test_save_then_load_round_trips_all_fields(tmp_path, monkeypatch):
    users_path = tmp_path / "web_users.json"
    monkeypatch.setattr(auth_mod, "get_web_users_path", lambda: users_path)
    monkeypatch.setattr(auth_mod, "get_data_dir", lambda: tmp_path)

    store = WebUserStore()
    _add(store, "admin1", "adminpassword1", role=ROLE_ADMIN, created_by="(test)")

    reloaded = WebUserStore.load()
    user = reloaded.get("admin1")
    assert user is not None
    assert user.role == ROLE_ADMIN
    assert user.created_by == "(test)"
    assert user.must_change_password is True
    assert reloaded.verify("admin1", "adminpassword1") is True


def test_load_maps_legacy_is_admin_true_to_admin_role(tmp_path, monkeypatch):
    import json

    users_path = tmp_path / "web_users.json"
    monkeypatch.setattr(auth_mod, "get_web_users_path", lambda: users_path)
    monkeypatch.setattr(auth_mod, "get_data_dir", lambda: tmp_path)

    # A record shaped like the pre-RBAC format (just username/password_hash/is_admin).
    users_path.write_text(json.dumps({
        "users": [{"username": "legacy_admin", "password_hash": "x", "is_admin": True}]
    }), encoding="utf-8")

    store = WebUserStore.load()
    user = store.get("legacy_admin")
    assert user.role == ROLE_ADMIN
    assert user.active is True  # defaults to active for a record with no "active" field


def test_load_maps_legacy_is_admin_false_to_editor_role(tmp_path, monkeypatch):
    import json

    users_path = tmp_path / "web_users.json"
    monkeypatch.setattr(auth_mod, "get_web_users_path", lambda: users_path)
    monkeypatch.setattr(auth_mod, "get_data_dir", lambda: tmp_path)

    users_path.write_text(json.dumps({
        "users": [{"username": "legacy_user", "password_hash": "x", "is_admin": False}]
    }), encoding="utf-8")

    store = WebUserStore.load()
    assert store.get("legacy_user").role == ROLE_EDITOR


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
