"""
Application configuration: named SQL Server connections, the active
connection, internal SQLite export path, and AI (Gemini) settings.

Stored as JSON at data/config.json next to the exe. Passwords / API
keys are Fernet-encrypted at rest via app.utils.crypto (see that
module's docstring for the honest limitations of that approach).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from .utils import crypto
from .utils.dotenv_lite import load_dotenv_if_present
from .utils.paths import get_app_dir, get_config_path, get_internal_db_path

CONFIG_VERSION = 1
RECENT_QUERIES_MAX = 15

# A real DB password and a real Gemini API key used to be hardcoded here
# directly (both baked into the seed default below) - moved out when this
# project went into git (2026-09-12): a secret in source is a secret in
# git history forever, unlike data/config.json (gitignored, never
# tracked). See README's "Security note on stored credentials".
#
# load_dotenv_if_present reads a gitignored .env file at the project root
# into os.environ first (if one exists), so a real env var OR a local
# .env both work - see app/utils/dotenv_lite.py. Leave both unset to
# start with a blank connection/AI key and fill them in later via the
# app's own Settings/Config screen instead - nothing below requires
# either to be set.
load_dotenv_if_present(get_app_dir() / ".env")

DEFAULT_DB_PASSWORD = os.environ.get("SCRIPTGEN_DB_PASSWORD", "")
DEFAULT_AI_API_KEY = os.environ.get("SCRIPTGEN_AI_API_KEY", "")
DEFAULT_AI_MODEL = "gemini-3.6-flash"
_RETIRED_AI_MODELS = {"gemini-2.0-flash"}


@dataclass
class ConnectionConfig:
    name: str = "Default"
    server: str = "localhost"
    port: int = 1433
    database: str = ""  # blank = server default database for this login
    username: str = ""
    password_enc: str = ""  # Fernet-encrypted; never store plaintext
    timeout_seconds: int = 10
    # Purely informational - reminds the user this needs their tunnel up.
    notes: str = ""

    def get_password(self) -> str:
        return crypto.decrypt_text(self.password_enc)

    def set_password(self, plain: str) -> None:
        self.password_enc = crypto.encrypt_text(plain)


@dataclass
class AIConfig:
    provider: str = "gemini"          # currently only "gemini" is wired up
    api_key_enc: str = ""
    model: str = "gemini-3.6-flash"
    enabled: bool = False

    def get_api_key(self) -> str:
        return crypto.decrypt_text(self.api_key_enc)

    def set_api_key(self, plain: str) -> None:
        self.api_key_enc = crypto.encrypt_text(plain)
        self.enabled = bool(plain)


@dataclass
class SavedQuery:
    """A deliberately-named, kept-until-deleted query - distinct from
    recent_queries (a rolling, unnamed history). You save one on purpose;
    it doesn't fall off after 15 more queries."""
    name: str
    sql: str
    created_at_utc: str = ""


@dataclass
class AppConfig:
    version: int = CONFIG_VERSION
    connections: dict[str, ConnectionConfig] = field(default_factory=dict)
    active_connection: str = "default"
    internal_db_path: str = ""
    ai: AIConfig = field(default_factory=AIConfig)
    theme: str = "flatly"   # ttkbootstrap theme name; see app/ui/theme.py for the light/dark pair
    recent_queries: list[str] = field(default_factory=list)  # most-recent-first, capped at RECENT_QUERIES_MAX
    saved_queries: list[SavedQuery] = field(default_factory=list)

    def get_active_connection(self) -> Optional[ConnectionConfig]:
        return self.connections.get(self.active_connection)

    def add_recent_query(self, sql: str) -> None:
        sql = sql.strip()
        if not sql:
            return
        self.recent_queries = [sql] + [q for q in self.recent_queries if q != sql]
        del self.recent_queries[RECENT_QUERIES_MAX:]

    def add_connection(self, key: str, conn: ConnectionConfig) -> None:
        self.connections[key] = conn

    def remove_connection(self, key: str) -> None:
        if key == self.active_connection:
            raise ValueError("Can't remove the active connection - switch to another one first.")
        self.connections.pop(key, None)

    def save_query(self, name: str, sql: str) -> None:
        """Upsert by name - saving again under an existing name replaces it."""
        name = name.strip()
        sql = sql.strip()
        if not name or not sql:
            return
        self.saved_queries = [q for q in self.saved_queries if q.name != name]
        self.saved_queries.append(SavedQuery(name=name, sql=sql, created_at_utc=datetime.now(timezone.utc).isoformat()))

    def delete_query(self, name: str) -> None:
        self.saved_queries = [q for q in self.saved_queries if q.name != name]


def _default_config() -> AppConfig:
    """
    Seeded with the connection the user gave us for their local tunnel -
    server/port/username aren't secret on their own (a read-only account
    name behind a locked-down tunnel), so those stay as plain defaults;
    the password comes from DEFAULT_DB_PASSWORD (SCRIPTGEN_DB_PASSWORD
    env var / .env, see module docstring above) and is blank if that's
    not set - open Settings and enter it there instead in that case.
    Editable any time from the Config menu; nothing here is hardcoded
    into the app logic, it's just the initial value written to
    data/config.json the first time the app runs.
    """
    default_conn = ConnectionConfig(
        name="Default (Local Tunnel)",
        server="localhost",
        port=1433,
        database="",
        username="ouc_read_only",
        notes="Requires your local tunnel to be open before connecting.",
    )
    default_conn.set_password(DEFAULT_DB_PASSWORD)

    default_ai = AIConfig(model=DEFAULT_AI_MODEL)
    default_ai.set_api_key(DEFAULT_AI_API_KEY)

    cfg = AppConfig(
        connections={"default": default_conn},
        active_connection="default",
        internal_db_path=str(get_internal_db_path()),
        ai=default_ai,
    )
    return cfg


def load_config() -> AppConfig:
    path = get_config_path()
    if not path.exists():
        cfg = _default_config()
        save_config(cfg)
        return cfg

    raw = json.loads(path.read_text(encoding="utf-8"))
    connections = {
        key: ConnectionConfig(**val) for key, val in raw.get("connections", {}).items()
    }
    ai_raw = raw.get("ai", {})
    ai = AIConfig(**ai_raw) if ai_raw else AIConfig()
    # Auto-upgrade path for configs written before the gemini-2.0-flash
    # retirement: a blank/retired model is swapped for the current
    # default, and a blank key is seeded with DEFAULT_AI_API_KEY (from
    # SCRIPTGEN_AI_API_KEY / .env, see module docstring) - but only when
    # there's actually something to seed it WITH; a key the user already
    # set themselves is never touched either way.
    ai_upgraded = False
    if not ai.model or ai.model in _RETIRED_AI_MODELS:
        ai.model = DEFAULT_AI_MODEL
        ai_upgraded = True
    if not ai.get_api_key() and DEFAULT_AI_API_KEY:
        ai.set_api_key(DEFAULT_AI_API_KEY)
        ai_upgraded = True

    cfg = AppConfig(
        version=raw.get("version", CONFIG_VERSION),
        connections=connections,
        active_connection=raw.get("active_connection", "default"),
        internal_db_path=raw.get("internal_db_path", str(get_internal_db_path())),
        ai=ai,
        theme=raw.get("theme", "flatly"),
        recent_queries=raw.get("recent_queries", []),
        saved_queries=[SavedQuery(**q) for q in raw.get("saved_queries", [])],
    )
    if ai_upgraded:
        save_config(cfg)
    return cfg


def save_config(cfg: AppConfig) -> None:
    raw = {
        "version": cfg.version,
        "connections": {key: asdict(val) for key, val in cfg.connections.items()},
        "active_connection": cfg.active_connection,
        "internal_db_path": cfg.internal_db_path,
        "ai": asdict(cfg.ai),
        "theme": cfg.theme,
        "recent_queries": cfg.recent_queries,
        "saved_queries": [asdict(q) for q in cfg.saved_queries],
    }
    get_config_path().write_text(json.dumps(raw, indent=2), encoding="utf-8")
