"""
Command-line admin tool for web login accounts (see web/auth.py for why
these are separate from the SQL Server connection config, and for the
role/RBAC model this operates on). Most day-to-day user maintenance now
also has an in-browser equivalent - Settings > Users, visible to admin
accounts only (see web/server.py's /api/users routes) - but this CLI
stays useful for the very first admin setup (before anyone can log in
yet) and for anyone who'd rather script it.

Usage (run from the project root, same place you'd run main.py):
    python -m web.manage_users add <username>                    (prompts for password; asks for role)
    python -m web.manage_users set-password <username>           (prompts for new password; forces change on next login)
    python -m web.manage_users role <username> <admin|editor|viewer>
    python -m web.manage_users deactivate <username>
    python -m web.manage_users activate <username>
    python -m web.manage_users remove <username>
    python -m web.manage_users list
"""
from __future__ import annotations

import getpass
import sys

from web.auth import ROLE_ADMIN, ROLE_EDITOR, ROLES, WebUserStore


def _prompt_password(label: str = "Password") -> str:
    while True:
        pw1 = getpass.getpass(f"{label}: ")
        if len(pw1) < 8:
            print("Password must be at least 8 characters - try again.")
            continue
        pw2 = getpass.getpass(f"{label} (again): ")
        if pw1 != pw2:
            print("Passwords didn't match - try again.")
            continue
        return pw1


def _prompt_role() -> str:
    while True:
        raw = input(f"Role [{'/'.join(ROLES)}] (default editor): ").strip().lower()
        if not raw:
            return ROLE_EDITOR
        if raw in ROLES:
            return raw
        print(f"Not a valid role - choose one of: {', '.join(ROLES)}")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1

    command = argv[0]
    store = WebUserStore.load()

    if command == "list":
        if not store.users:
            print("(no login accounts)")
        for user in store.list_users():
            status = "active" if user.active else "DEACTIVATED"
            flag = "  [must change password]" if user.must_change_password else ""
            print(f"  {user.username:<20} {user.role:<8} {status}{flag}")
        return 0

    if command == "add":
        if len(argv) < 2:
            print("Usage: python -m web.manage_users add <username>")
            return 1
        username = argv[1]
        password = _prompt_password()
        role = _prompt_role()
        try:
            store.add_user(username, password, role=role, created_by="(CLI)")
        except ValueError as exc:
            print(f"Error: {exc}")
            return 1
        print(f"Added user '{username}' as {role}. They'll be asked to set their own password on first login.")
        return 0

    if command == "set-password":
        if len(argv) < 2:
            print("Usage: python -m web.manage_users set-password <username>")
            return 1
        username = argv[1]
        password = _prompt_password("New password")
        try:
            store.set_password(username, password)
        except ValueError as exc:
            print(f"Error: {exc}")
            return 1
        print(f"Updated password for '{username}' - they'll be asked to change it again on next login.")
        return 0

    if command == "role":
        if len(argv) < 3:
            print(f"Usage: python -m web.manage_users role <username> <{'|'.join(ROLES)}>")
            return 1
        username, role = argv[1], argv[2].lower()
        try:
            store.set_role(username, role)
        except ValueError as exc:
            print(f"Error: {exc}")
            return 1
        print(f"'{username}' is now {role}.")
        return 0

    if command == "deactivate":
        if len(argv) < 2:
            print("Usage: python -m web.manage_users deactivate <username>")
            return 1
        username = argv[1]
        try:
            # No logged-in "acting user" from the CLI - pass a value that
            # can never match a real username so the self-deactivation
            # guard simply doesn't apply here (there's no "self" on the
            # command line, only whichever account you type in).
            store.deactivate(username, acting_username="\0cli")
        except ValueError as exc:
            print(f"Error: {exc}")
            return 1
        print(f"Deactivated '{username}'.")
        return 0

    if command == "activate":
        if len(argv) < 2:
            print("Usage: python -m web.manage_users activate <username>")
            return 1
        username = argv[1]
        try:
            store.reactivate(username)
        except ValueError as exc:
            print(f"Error: {exc}")
            return 1
        print(f"Activated '{username}'.")
        return 0

    if command == "remove":
        if len(argv) < 2:
            print("Usage: python -m web.manage_users remove <username>")
            return 1
        username = argv[1]
        try:
            store.remove_user(username)
        except ValueError as exc:
            print(f"Error: {exc}")
            return 1
        print(f"Removed user '{username}'.")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
