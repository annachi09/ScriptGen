"""
Per-role sidebar menu visibility for the web UI.

Lets an admin decide which top-level nav pages (Workspace, Script,
History, Dashboard, AI Assist, DIFF DATES Anomaly, Tools, Settings) each
role's sidebar shows at all - e.g. a shop that only ever wants viewers
running ad-hoc queries could hide Batch/Detect-All-heavy pages they'd
never need, without touching what viewers are individually allowed to DO
on the pages they still see (that's still web/auth.py's ROLE_RANK gates).

Deliberately NOT a new security boundary. This only controls what the
sidebar renders and which page a nav click reveals - it does NOT gate any
API route. A role with a menu hidden here still hits the exact same
require_editor/require_admin 403 it would today if it called that page's
underlying route directly (hiding the button is just UX declutter, same
"the button existing/not existing is not the real gate" principle
documented in web/auth.py's own module docstring for EDITOR_ONLY_IDS).
Hiding "Settings" from a role, for instance, does NOT stop that role's
account-level actions (self-service password change is still reachable
via /api/account/change-password) from working if called directly - it
only removes the page most people would use to reach them.

Storage: a small standalone JSON file (get_menu_access_path(), same
directory/pattern as web_users.json), keyed by role, each an explicit
ALLOW-LIST of menu ids - not a deny-list. Two consequences of that
choice, both deliberate:
  - No saved file yet (fresh install, or nobody's touched this panel) =
    every role sees every menu - today's behavior is the implicit
    default, nothing changes until an admin opens this panel.
  - A role's list, once saved, is exactly what's visible - if a FUTURE
    round adds a new MENU_ID and an admin already has a customized list
    saved for some role, that brand-new page will NOT appear for that
    role until the admin explicitly re-checks it. This is the safer
    failure mode for an admin who deliberately trimmed a role's menu
    down (a new page silently reappearing for a role someone restricted
    would be more surprising than it silently staying hidden) - but it
    does mean "add a new page to the sidebar" isn't purely additive for
    accounts that already have a customized config; worth remembering if
    a future round adds an app.py-level page/nav item.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.utils.paths import get_menu_access_path
from web.auth import ROLES, ROLE_ADMIN

# Must match web/static/index.html's `data-page="..."` values exactly -
# these ARE the ids used everywhere (server config, the admin panel, and
# the frontend's own nav-item lookup), so there's exactly one place
# (here) that has to change if a page is ever added/renamed/removed.
MENU_IDS = ("workspace", "script", "history", "dashboard", "ai", "dateanomaly", "hierarchy", "billissuance", "bulkchecker", "tools", "settings")

MENU_LABELS = {
    "workspace": "Workspace",
    "script": "Script",
    "history": "History",
    "dashboard": "Dashboard",
    "ai": "AI Assist",
    "dateanomaly": "DIFF DATES Anomaly",
    "hierarchy": "Hierarchy Analysis",
    "billissuance": "Bill Issuance Validator",
    "bulkchecker": "Bulk Checker",
    "tools": "Tools",
    "settings": "Settings",
}

# The one menu every admin account must always keep, regardless of what's
# submitted - this IS this module's one real guard, mirroring web/auth.py's
# "can't deactivate/demote the last active admin" philosophy: without it,
# an admin could save a config that hides Settings from admin itself and
# lock every admin out of the only page that can undo it (no CLI escape
# hatch for this config the way manage_users.py is for accounts).
ADMIN_REQUIRED_MENU = "settings"


def default_access() -> dict[str, list[str]]:
    """Every role sees every menu - today's behavior, and what a fresh
    install (no saved file) returns."""
    return {role: list(MENU_IDS) for role in ROLES}


@dataclass
class MenuAccessStore:
    access: dict[str, list[str]]

    @classmethod
    def load(cls) -> "MenuAccessStore":
        path = get_menu_access_path()
        data = default_access()
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = {}
            saved = raw.get("access", {})
            for role in ROLES:
                if role in saved and isinstance(saved[role], list):
                    # Filter to known ids only - drops anything left over
                    # from a since-removed menu id rather than surfacing a
                    # dead entry in the admin panel forever.
                    data[role] = [m for m in saved[role] if m in MENU_IDS]
        # Belt-and-suspenders even on a saved file that somehow dropped it
        # (hand-edited, or written by a future bug) - never load a state
        # where admin can't see Settings.
        if ADMIN_REQUIRED_MENU not in data[ROLE_ADMIN]:
            data[ROLE_ADMIN].append(ADMIN_REQUIRED_MENU)
        return cls(access=data)

    def save(self) -> None:
        raw = {"access": self.access}
        get_menu_access_path().write_text(json.dumps(raw, indent=2), encoding="utf-8")

    def allowed_for_role(self, role: str) -> list[str]:
        return list(self.access.get(role, MENU_IDS))

    def is_allowed(self, role: str, menu_id: str) -> bool:
        return menu_id in self.access.get(role, MENU_IDS)


def set_access(new_access: dict[str, list[str]]) -> MenuAccessStore:
    """Validates and saves a full role->menu-id-list replacement (the
    admin panel always submits all three roles' lists together, not a
    partial patch - simpler to validate/reason about than merging a
    partial update role-by-role). Raises ValueError on anything invalid;
    the caller (web/server.py's route) turns that into a 400."""
    if not isinstance(new_access, dict):
        raise ValueError("Menu access must be an object keyed by role.")
    cleaned: dict[str, list[str]] = {}
    for role in ROLES:
        ids = new_access.get(role, [])
        if not isinstance(ids, list) or not all(isinstance(m, str) for m in ids):
            raise ValueError(f"Menu list for role '{role}' must be a list of menu ids.")
        unknown = [m for m in ids if m not in MENU_IDS]
        if unknown:
            raise ValueError(f"Unknown menu id(s) for role '{role}': {', '.join(unknown)}")
        cleaned[role] = list(dict.fromkeys(ids))  # dedupe, preserve order
    if ADMIN_REQUIRED_MENU not in cleaned[ROLE_ADMIN]:
        raise ValueError(
            f"Admin must always keep '{MENU_LABELS[ADMIN_REQUIRED_MENU]}' visible - "
            "otherwise no admin could ever get back here to fix it."
        )
    store = MenuAccessStore(access=cleaned)
    store.save()
    return store
