"""
Pure-logic tests for web.menu_access - see that module's docstring for
why this is a visibility convenience layered on top of web/auth.py's
role gates, not a second security boundary, and why storage is an
explicit per-role allow-list rather than a deny-list.

Filesystem isolation follows the same pattern as test_auth.py: web.
menu_access imports get_menu_access_path BY NAME at its own module top,
so patching app.utils.paths' original wouldn't reach it - patch web.
menu_access's own bound name directly.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import web.menu_access as ma
from web.auth import ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER


@pytest.fixture()
def isolated_path(tmp_path, monkeypatch):
    path = tmp_path / "web_menu_access.json"
    monkeypatch.setattr(ma, "get_menu_access_path", lambda: path)
    return path


def test_default_access_shows_every_menu_to_every_role():
    access = ma.default_access()
    assert set(access.keys()) == {ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER}
    for role in access:
        assert set(access[role]) == set(ma.MENU_IDS)


def test_load_with_no_saved_file_returns_defaults(isolated_path):
    store = ma.MenuAccessStore.load()
    assert store.access == ma.default_access()


def test_set_access_persists_and_reloads(isolated_path):
    ma.set_access({
        "viewer": ["workspace", "dashboard"],
        "editor": list(ma.MENU_IDS),
        "admin": list(ma.MENU_IDS),
    })
    reloaded = ma.MenuAccessStore.load()
    # "overview" is always force-added (ALWAYS_VISIBLE_MENU).
    assert reloaded.allowed_for_role("viewer") == ["workspace", "dashboard", "overview"]
    assert reloaded.is_allowed("viewer", "workspace") is True
    assert reloaded.is_allowed("viewer", "settings") is False


def test_set_access_rejects_unknown_menu_id(isolated_path):
    with pytest.raises(ValueError, match="Unknown menu id"):
        ma.set_access({
            "viewer": ["not_a_real_menu"],
            "editor": list(ma.MENU_IDS),
            "admin": list(ma.MENU_IDS),
        })


def test_set_access_rejects_non_list_value(isolated_path):
    with pytest.raises(ValueError, match="must be a list"):
        ma.set_access({
            "viewer": "workspace",
            "editor": list(ma.MENU_IDS),
            "admin": list(ma.MENU_IDS),
        })


def test_set_access_requires_admin_keep_settings(isolated_path):
    with pytest.raises(ValueError, match="Admin must always keep"):
        ma.set_access({
            "viewer": list(ma.MENU_IDS),
            "editor": list(ma.MENU_IDS),
            "admin": [m for m in ma.MENU_IDS if m != "settings"],
        })


def test_set_access_dedupes_preserving_order(isolated_path):
    ma.set_access({
        "viewer": ["workspace", "workspace", "dashboard"],
        "editor": list(ma.MENU_IDS),
        "admin": list(ma.MENU_IDS),
    })
    reloaded = ma.MenuAccessStore.load()
    assert reloaded.allowed_for_role("viewer") == ["workspace", "dashboard", "overview"]


def test_load_drops_unknown_ids_from_a_hand_edited_file(isolated_path):
    isolated_path.write_text(
        '{"access": {"viewer": ["workspace", "some_removed_menu"], '
        '"editor": ["script"], "admin": ["settings"]}}',
        encoding="utf-8",
    )
    store = ma.MenuAccessStore.load()
    viewer = store.allowed_for_role("viewer")
    assert "workspace" in viewer
    assert "some_removed_menu" not in viewer


def test_load_forces_settings_back_onto_admin_even_if_file_omits_it(isolated_path):
    # Belt-and-suspenders: set_access() itself refuses to save a config
    # missing this, but a hand-edited or future-buggy file shouldn't be
    # able to lock every admin out on load either.
    isolated_path.write_text(
        '{"access": {"viewer": [], "editor": [], "admin": ["workspace"]}}',
        encoding="utf-8",
    )
    store = ma.MenuAccessStore.load()
    assert "settings" in store.allowed_for_role("admin")


def test_legacy_file_without_known_ids_gains_pages_added_since(isolated_path):
    # A config saved before "known_ids" tracking existed (RJ's real case:
    # TNB CYCLE/DISC Analysis stayed hidden after restart).
    isolated_path.write_text(
        '{"access": {"viewer": ["workspace"], "editor": ["workspace"], "admin": ["workspace", "settings"]}}',
        encoding="utf-8",
    )
    store = ma.MenuAccessStore.load()
    for role in ("viewer", "editor", "admin"):
        assert store.is_allowed(role, "tnbcycledisc")
    # ...but pages that existed back then and were left unticked stay hidden.
    assert not store.is_allowed("viewer", "dateanomaly")


def test_saved_config_records_known_ids_and_respects_deliberate_untick(isolated_path):
    ma.set_access({
        "viewer": ["workspace"],  # tnbcycledisc deliberately unticked
        "editor": list(ma.MENU_IDS),
        "admin": list(ma.MENU_IDS),
    })
    import json
    raw = json.loads(isolated_path.read_text(encoding="utf-8"))
    assert set(raw["known_ids"]) == set(ma.MENU_IDS)
    store = ma.MenuAccessStore.load()
    assert not store.is_allowed("viewer", "tnbcycledisc")


def test_page_added_after_save_appears_for_every_role(isolated_path, monkeypatch):
    ma.set_access({"viewer": ["workspace"], "editor": ["workspace"], "admin": ["workspace", "settings"]})
    monkeypatch.setattr(ma, "MENU_IDS", ma.MENU_IDS + ("brandnewpage",))
    store = ma.MenuAccessStore.load()
    for role in ("viewer", "editor", "admin"):
        assert store.is_allowed(role, "brandnewpage")


def test_load_survives_corrupt_json(isolated_path):
    isolated_path.write_text("{not valid json", encoding="utf-8")
    store = ma.MenuAccessStore.load()
    assert store.access == ma.default_access()
