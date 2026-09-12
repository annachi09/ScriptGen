"""
Login accounts for the web UI - who's allowed to open the tool at all,
and what they're allowed to do once they're in.

Deliberately separate from app.config's ConnectionConfig: there's still
just ONE SQL Server connection (the shared read-only tunnel account),
configured server-side same as the desktop app always did - a web login
here does NOT carry its own DB credentials, it just gates access to a
tool that runs queries as that one shared account. That's a real change
from the desktop app's trust model (one person, one machine, one locally-
encrypted config) to a "few teammates, one shared server" model, so it
gets its own explicit account list rather than silently reusing anything
DB-related.

Passwords are hashed with bcrypt (a real KDF, unlike the Fernet
"encrypted at rest" treatment config.py gives the DB password/API key -
those need to be recoverable in plaintext to use against SQL Server/
Gemini; a login password only ever needs to be verified, never read
back, so it gets the stronger one-way treatment).

## Roles (RBAC, least-privilege)

Three profiles, each a strict superset of the one below it - picked
because the underlying SQL Server account is ALWAYS read-only (this
tool never executes anything against the source DB regardless of who's
logged in), so role-gating isn't about preventing DB writes - it's
about who can produce a script someone might go run, and who can touch
shared/dangerous configuration:

- **viewer**  - run queries, browse results/Dashboard/history, read-only
  AI actions (Explain). Cannot generate or save any script, cannot
  export snapshots. For "I need to see this, not change anything."
- **editor**  - everything viewer can, PLUS generate Update/Rollback/
  Date-Anomaly-correction scripts, export snapshots, full AI Assist
  (Suggest/Optimize/NL-WHERE/Review). Cannot touch connections, AI
  settings, or other user accounts. The default for a new account.
- **admin**   - everything, plus manage DB connections, AI (Gemini) key/
  model, and other users' accounts (create/deactivate/role/reset
  password). Reserved for whoever is actually responsible for this
  install.

## Other best practices this module implements

- **Deactivate, don't just delete**: `active=False` is the normal way to
  revoke someone's access - `script_history` entries reference a
  username as a plain text field (see app/db/script_history.py), so a
  hard delete would leave "who generated this" pointing at an account
  that silently vanished. A real delete is still available for cleanup
  of a genuine mistake, it's just not the first tool reached for.
- **Can't lock yourself/everyone out**: you can't deactivate, delete, or
  demote yourself, and you can't deactivate/delete/demote the last
  remaining ACTIVE admin - see _guard_not_last_active_admin.
- **Self-service password changes require the OLD password**
  (`change_own_password`); an admin resetting someone ELSE's password
  does not (`admin_reset_password`) but flips `must_change_password` so
  the admin doesn't end up permanently knowing that person's password.
- **New accounts an admin creates also start `must_change_password=True`**
  - the admin picks a temporary password, the person picks their own
  real one on first login (see /api/account/change-password and the
  frontend's forced-change prompt). The bootstrapped first-run admin
  account is the one exception (see _bootstrap) - that flow already
  forces a one-time password handoff via web_admin_first_run.txt.

First run: if no data/web_users.json exists yet, one "admin" account is
created with a randomly generated password, written ONCE to
data/web_admin_first_run.txt (plus logged to stdout) so whoever starts
the server for the first time can log in and add teammate accounts from
there. That file is deleted the first time anyone logs in successfully
- see consume_first_run_notice().
"""
from __future__ import annotations

import json
import secrets
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone

import bcrypt

from app.utils.paths import get_web_users_path, get_data_dir

DEFAULT_ADMIN_USERNAME = "admin"

ROLE_ADMIN = "admin"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"
ROLES = (ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER)
ROLE_RANK = {ROLE_VIEWER: 0, ROLE_EDITOR: 1, ROLE_ADMIN: 2}  # for "at least editor" checks


def is_valid_role(role: str) -> bool:
    return role in ROLES


@dataclass
class WebUser:
    username: str
    password_hash: str
    role: str = ROLE_EDITOR
    active: bool = True
    must_change_password: bool = False
    created_at_utc: str = ""
    created_by: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def to_public_dict(self) -> dict:
        """Never includes password_hash - this is what the /api/users
        list endpoint and the frontend actually see."""
        return {
            "username": self.username,
            "role": self.role,
            "active": self.active,
            "must_change_password": self.must_change_password,
            "created_at_utc": self.created_at_utc,
            "created_by": self.created_by,
        }


@dataclass
class WebUserStore:
    users: dict[str, WebUser] = field(default_factory=dict)  # keyed by lowercased username

    @classmethod
    def load(cls) -> "WebUserStore":
        path = get_web_users_path()
        if not path.exists():
            return cls._bootstrap()
        raw = json.loads(path.read_text(encoding="utf-8"))
        users = {}
        for u in raw.get("users", []):
            # Back-compat: a record written before roles existed only has
            # "is_admin" (bool) - map it forward instead of erroring out
            # or silently dropping every existing account's admin status.
            role = u.get("role")
            if role is None:
                role = ROLE_ADMIN if u.get("is_admin", False) else ROLE_EDITOR
            if not is_valid_role(role):
                role = ROLE_EDITOR
            username = u["username"]
            users[username.lower()] = WebUser(
                username=username,
                password_hash=u["password_hash"],
                role=role,
                active=u.get("active", True),
                must_change_password=u.get("must_change_password", False),
                created_at_utc=u.get("created_at_utc", ""),
                created_by=u.get("created_by", ""),
            )
        return cls(users=users)

    @classmethod
    def _bootstrap(cls) -> "WebUserStore":
        alphabet = string.ascii_letters + string.digits
        generated_password = "".join(secrets.choice(alphabet) for _ in range(16))
        store = cls()
        store.add_user(
            DEFAULT_ADMIN_USERNAME, generated_password, role=ROLE_ADMIN,
            created_by="(first-run bootstrap)", require_password_change=False,
        )

        notice_path = get_data_dir() / "web_admin_first_run.txt"
        notice_path.write_text(
            "ScriptGen web - first-run admin account\n"
            "=========================================\n"
            f"Username: {DEFAULT_ADMIN_USERNAME}\n"
            f"Password: {generated_password}\n\n"
            "Log in with this once, then add real teammate accounts (and\n"
            "change this password) from Settings > Users in the app, or via:\n"
            "    python -m web.manage_users add <username>\n"
            "    python -m web.manage_users set-password <username>\n"
            "    python -m web.manage_users role <username> <admin|editor|viewer>\n"
            "    python -m web.manage_users deactivate <username>\n"
            "    python -m web.manage_users activate <username>\n"
            "    python -m web.manage_users remove <username>\n"
            "    python -m web.manage_users list\n\n"
            "This file is deleted automatically the first time anyone logs\n"
            "in successfully - copy the password out before then.\n",
            encoding="utf-8",
        )
        print(
            f"\n[ScriptGen web] No login accounts found - created a first-run admin "
            f"account.\n  Username: {DEFAULT_ADMIN_USERNAME}\n  Password: {generated_password}\n"
            f"  (also written to {notice_path})\n",
            flush=True,
        )
        return store

    def save(self) -> None:
        raw = {
            "users": [
                {
                    "username": u.username,
                    "password_hash": u.password_hash,
                    "role": u.role,
                    "active": u.active,
                    "must_change_password": u.must_change_password,
                    "created_at_utc": u.created_at_utc,
                    "created_by": u.created_by,
                }
                for u in self.users.values()
            ]
        }
        get_web_users_path().write_text(json.dumps(raw, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Lockout guards - shared by deactivate/remove/demote below
    # ------------------------------------------------------------------
    def _active_admin_count(self) -> int:
        return sum(1 for u in self.users.values() if u.active and u.role == ROLE_ADMIN)

    def _guard_not_last_active_admin(self, user: "WebUser", action: str) -> None:
        if user.active and user.role == ROLE_ADMIN and self._active_admin_count() <= 1:
            raise ValueError(f"Can't {action} the last remaining active admin - promote another user first.")

    # ------------------------------------------------------------------
    # Create / read
    # ------------------------------------------------------------------
    def add_user(
        self, username: str, password: str, role: str = ROLE_EDITOR,
        created_by: str = "", require_password_change: bool = True,
    ) -> WebUser:
        username = username.strip()
        if not username:
            raise ValueError("Username can't be blank.")
        if not is_valid_role(role):
            raise ValueError(f"Unknown role '{role}' - must be one of {', '.join(ROLES)}.")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        key = username.lower()
        if key in self.users:
            raise ValueError(f"User '{username}' already exists.")
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
        user = WebUser(
            username=username, password_hash=password_hash, role=role, active=True,
            must_change_password=require_password_change,
            created_at_utc=datetime.now(timezone.utc).isoformat(), created_by=created_by,
        )
        self.users[key] = user
        self.save()
        return user

    def get(self, username: str) -> WebUser | None:
        return self.users.get(username.strip().lower())

    def list_users(self) -> list[WebUser]:
        return sorted(self.users.values(), key=lambda u: u.username.lower())

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------
    def set_password(self, username: str, password: str) -> None:
        """Admin-initiated reset - does not require the old password, but
        DOES force the account to pick a new one on next login (see the
        module docstring's "Self-service password changes" note)."""
        key = username.strip().lower()
        if key not in self.users:
            raise ValueError(f"No such user: {username}")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters.")
        user = self.users[key]
        user.password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
        user.must_change_password = True
        self.save()

    def change_own_password(self, username: str, old_password: str, new_password: str) -> None:
        """Self-service - requires proving you know the CURRENT password
        first, unlike an admin's set_password reset."""
        user = self.get(username)
        if user is None:
            raise ValueError("No such user.")
        if not self.verify(username, old_password):
            raise ValueError("Current password is incorrect.")
        if len(new_password) < 8:
            raise ValueError("New password must be at least 8 characters.")
        user.password_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
        user.must_change_password = False
        self.save()

    def set_role(self, username: str, role: str) -> None:
        key = username.strip().lower()
        if key not in self.users:
            raise ValueError(f"No such user: {username}")
        if not is_valid_role(role):
            raise ValueError(f"Unknown role '{role}' - must be one of {', '.join(ROLES)}.")
        user = self.users[key]
        if user.role == ROLE_ADMIN and role != ROLE_ADMIN:
            self._guard_not_last_active_admin(user, "demote")
        user.role = role
        self.save()

    def deactivate(self, username: str, acting_username: str) -> None:
        key = username.strip().lower()
        if key not in self.users:
            raise ValueError(f"No such user: {username}")
        if key == acting_username.strip().lower():
            raise ValueError("You can't deactivate your own account.")
        user = self.users[key]
        self._guard_not_last_active_admin(user, "deactivate")
        user.active = False
        self.save()

    def reactivate(self, username: str) -> None:
        key = username.strip().lower()
        if key not in self.users:
            raise ValueError(f"No such user: {username}")
        self.users[key].active = True
        self.save()

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------
    def remove_user(self, username: str, acting_username: str = "") -> None:
        key = username.strip().lower()
        if key not in self.users:
            raise ValueError(f"No such user: {username}")
        if acting_username and key == acting_username.strip().lower():
            raise ValueError("You can't delete your own account.")
        if len(self.users) == 1:
            raise ValueError("Can't remove the last remaining login account - add another one first.")
        user = self.users[key]
        self._guard_not_last_active_admin(user, "delete")
        del self.users[key]
        self.save()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------
    def verify(self, username: str, password: str) -> bool:
        user = self.users.get(username.strip().lower())
        if user is None or not user.active:
            # Still run a bcrypt comparison even on an unknown/inactive
            # username, so "no such user", "wrong password", and
            # "deactivated" all take the same amount of time - a cheap,
            # standard guard against account enumeration via response
            # timing (and against learning an account is merely
            # deactivated, vs. never having existed, from timing alone).
            bcrypt.checkpw(b"", bcrypt.gensalt())
            return False
        try:
            return bcrypt.checkpw(password.encode("utf-8"), user.password_hash.encode("utf-8"))
        except ValueError:
            return False


def consume_first_run_notice() -> None:
    """Deletes data/web_admin_first_run.txt after its first successful
    use - called on the first login that succeeds, so the generated
    password doesn't sit around in plaintext on disk indefinitely."""
    notice_path = get_data_dir() / "web_admin_first_run.txt"
    notice_path.unlink(missing_ok=True)


def get_session_secret() -> bytes:
    """Signing key for session cookies - generated once, reused across
    restarts (see get_web_session_secret_path's docstring)."""
    from app.utils.paths import get_web_session_secret_path

    path = get_web_session_secret_path()
    if path.exists():
        return path.read_bytes()
    key = secrets.token_bytes(32)
    path.write_bytes(key)
    return key
