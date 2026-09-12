"""
Filesystem locations for ScriptGen.

When frozen by PyInstaller (--onefile), sys.frozen is True and
sys.executable points at the running .exe. We keep all user data
(config, encryption key, internal SQLite db, exported scripts) in a
folder NEXT TO the exe, so the whole thing stays "standalone" and
portable - copy the folder, copy the app.

In dev mode (running main.py directly with a normal Python interpreter)
we use the project root instead.
"""
from __future__ import annotations

import sys
from pathlib import Path


def get_app_dir() -> Path:
    """Directory the app's persistent files should live in."""
    if getattr(sys, "frozen", False):
        # Running as a PyInstaller-built .exe
        base = Path(sys.executable).resolve().parent
    else:
        # Running from source: project root is two levels up from this file
        # (app/utils/paths.py -> app/ -> project root)
        base = Path(__file__).resolve().parent.parent.parent
    return base


def get_data_dir() -> Path:
    """Where config.json, the encryption key, and the internal db live."""
    data_dir = get_app_dir() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_exports_dir() -> Path:
    exports_dir = get_app_dir() / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    return exports_dir


def get_config_path() -> Path:
    return get_data_dir() / "config.json"


def get_key_path() -> Path:
    return get_data_dir() / "scriptgen.key"


def get_internal_db_path() -> Path:
    return get_data_dir() / "scriptgen_internal.db"


def get_log_path() -> Path:
    return get_data_dir() / "scriptgen.log"


def get_web_users_path() -> Path:
    """Login accounts for the web UI (separate from DB connections in
    config.json - these gate who can open the tool at all, not which
    SQL Server account it runs queries as; see web/auth.py)."""
    return get_data_dir() / "web_users.json"


def get_menu_access_path() -> Path:
    """Per-role sidebar menu visibility config for the web UI (see
    web/menu_access.py) - a separate small JSON file next to web_users.json
    rather than a new field on AppConfig, since it's web-UI-only (the
    desktop app has no equivalent) and edited through its own admin panel,
    not the shared connection/AI settings dialog."""
    return get_data_dir() / "web_menu_access.json"


def get_web_session_secret_path() -> Path:
    """Signing key for the web UI's session cookies. Same treatment as
    scriptgen.key: a plain file next to the data it protects, generated
    once and reused - losing/rotating it just logs everyone out."""
    return get_data_dir() / "web_session.key"
