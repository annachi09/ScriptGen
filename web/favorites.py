"""
Favorite menus per user (RJ 2026-11-18): "i also want an option to add favorite menus and saved by user
and option to show only favorites".

Stored in data/web_favorites.json, encrypted like web_users.json (crypto.dumps_secure), keyed by the
lower-cased username: {"rj": {"favorites": ["billissuance", ...], "only": false}}.
Only known menu ids (web.menu_access.MENU_IDS) are kept.
"""
from __future__ import annotations

import os
import threading

from app.utils import crypto
from app.utils.paths import get_data_dir
from web.menu_access import MENU_IDS

_LOCK = threading.Lock()


def _path():
    return get_data_dir() / "web_favorites.json"


def _load_all() -> dict:
    p = _path()
    if not p.exists():
        return {}
    data, _enc = crypto.loads_secure(p.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def get(username: str) -> dict:
    with _LOCK:
        rec = _load_all().get((username or "").lower(), {})
    favs = [m for m in rec.get("favorites", []) if m in MENU_IDS]
    return {"favorites": favs, "only": bool(rec.get("only")) and bool(favs)}


def put(username: str, favorites: list, only: bool) -> dict:
    favs = list(dict.fromkeys(m for m in (favorites or []) if isinstance(m, str) and m in MENU_IDS))
    with _LOCK:
        data = _load_all()
        data[(username or "").lower()] = {"favorites": favs, "only": bool(only)}
        p = _path()
        tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
        tmp.write_text(crypto.dumps_secure(data), encoding="utf-8")
        os.replace(tmp, p)
    return {"favorites": favs, "only": bool(only) and bool(favs)}
