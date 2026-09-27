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
    SUPERSEDED 2026-09-25: saves now record "known_ids", and pages added
    after the last save are shown to every role automatically - see
    MENU_IDS_ADDED_BEFORE_TRACKING. Only a page the admin actually saw and
    unticked stays hidden.
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
MENU_IDS = ("overview", "workspace", "script", "history", "dashboard", "ai", "dateanomaly", "hierarchy", "readingvalidation", "tnbcycledisc", "wrongstuckhierarchy", "billissuance", "incorrectbillingperiod", "bulkchecker", "tools", "settings")

MENU_LABELS = {
    "overview": "Overview",
    "workspace": "Workspace",
    "script": "Script",
    "history": "History",
    "dashboard": "Dashboard",
    "ai": "AI Assist",
    "dateanomaly": "DIFF DATES Anomaly",
    "hierarchy": "Hierarchy Analysis",
    "readingvalidation": "Reading Validation/Modif",
    "tnbcycledisc": "TNB CYCLE/DISC Analysis",
    "wrongstuckhierarchy": "Wrong Stuck in Hierarchy ITB",
    "billissuance": "Bill Issuance Validator",
    "incorrectbillingperiod": "Incorrect Billing Period",
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

# Overview is the app's home/landing page (2026-09-23) - RJ: "i want the
# dashboard to be the first page and all data loaded upon opening". Every
# role, not just admin, must always keep it - unlike a normal new menu id
# (see the module docstring above: a SAVED config only gets filtered on
# load, never auto-gains new ids), so an account that customized its menu
# list BEFORE this page existed silently lost its own landing page: their
# saved list didn't include "overview", so applyMenuVisibilityToUI() in
# app.js hid its nav button and auto-redirected to the first still-visible
# page (Workspace) - exactly the bug RJ hit. Forcing it in both load() and
# set_access() (silently, not an error like ADMIN_REQUIRED_MENU - losing
# your home page by an unchecked box is a mistake, not a deliberate admin
# choice worth blocking) means this can never happen again for any role,
# on this config or a future one.
ALWAYS_VISIBLE_MENU = "overview"

# New pages should just APPEAR (RJ, 2026-09-25: added TNB CYCLE/DISC
# Analysis, restarted, "not showing" - his saved config predated the new
# id, so the old allow-list rule silently kept it hidden for every role).
# Fix: every save now also records "known_ids" = the MENU_IDS that existed
# at save time. On load, any menu id NOT in that list is new since the
# admin last saved, so it's added to every role automatically - while an
# id the admin saw and deliberately unticked stays hidden, same as before.
# Files saved before this tracking existed have no "known_ids"; for those,
# every id except the ones listed here is treated as already known. When
# adding a new page in future, nothing needs to go here - known_ids
# handles it; this tuple only covers ids added before the tracking.
MENU_IDS_ADDED_BEFORE_TRACKING = ("tnbcycledisc", "wrongstuckhierarchy")
# A legacy file is upgraded in place on first load (known_ids written
# back), so from then on new pages are detected purely via known_ids and
# this tuple never needs to grow again.


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
            known = raw.get("known_ids")
            if not isinstance(known, list):
                known = [m for m in MENU_IDS if m not in MENU_IDS_ADDED_BEFORE_TRACKING]
                # Upgrade the legacy file once so later pages are caught by
                # known_ids alone. Best-effort: a read-only file just means
                # we fall back to this same logic on every load.
                try:
                    path.write_text(json.dumps({**raw, "known_ids": known}, indent=2), encoding="utf-8")
                except OSError:
                    pass
            new_since_save = [m for m in MENU_IDS if m not in known]
            for role in ROLES:
                if role in saved and isinstance(saved[role], list):
                    # Filter to known ids only - drops anything left over
                    # from a since-removed menu id rather than surfacing a
                    # dead entry in the admin panel forever.
                    data[role] = [m for m in saved[role] if m in MENU_IDS]
                    # Pages added since this config was saved show up for
                    # every role (see MENU_IDS_ADDED_BEFORE_TRACKING).
                    data[role] += [m for m in new_since_save if m not in data[role]]
        # Belt-and-suspenders even on a saved file that somehow dropped it
        # (hand-edited, or written by a future bug) - never load a state
        # where admin can't see Settings.
        if ADMIN_REQUIRED_MENU not in data[ROLE_ADMIN]:
            data[ROLE_ADMIN].append(ADMIN_REQUIRED_MENU)
        # Every role always keeps the home page, even a config saved before
        # it existed - see ALWAYS_VISIBLE_MENU above.
        for role in ROLES:
            if ALWAYS_VISIBLE_MENU not in data[role]:
                data[role].append(ALWAYS_VISIBLE_MENU)
        return cls(access=data)

    def save(self) -> None:
        raw = {"access": self.access, "known_ids": list(MENU_IDS)}
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
    for role in ROLES:
        if ALWAYS_VISIBLE_MENU not in cleaned[role]:
            cleaned[role].append(ALWAYS_VISIBLE_MENU)
    if ADMIN_REQUIRED_MENU not in cleaned[ROLE_ADMIN]:
        raise ValueError(
            f"Admin must always keep '{MENU_LABELS[ADMIN_REQUIRED_MENU]}' visible - "
            "otherwise no admin could ever get back here to fix it."
        )
    store = MenuAccessStore(access=cleaned)
    store.save()
    return store
