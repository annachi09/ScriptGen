"""
At-rest protection for everything secret ScriptGen stores.

RJ 2026-11-17: "for now encrypt all api keys, ai keys and all password and users".

What is protected, and how:
  - DB password, AI (Gemini) API key, SMTP password - Fernet-encrypted fields (as before).
  - data/config.json and data/web_users.json - the WHOLE file is now Fernet-encrypted
    (prefix "SGENC1:"), so DB / SMTP usernames, e-mail recipients and every web login
    (username, role, bcrypt password hash) are no longer readable in the file.
    Web login passwords themselves stay bcrypt-hashed (one-way) inside that encrypted file.
  - The Fernet master key (data/scriptgen.key) and the session-cookie key
    (data/web_session.key) are wrapped with Windows DPAPI (prefix "DPAPI1:"): only the same
    Windows user account on the same PC can unwrap them. Before this, the master key sat in
    plain text next to the data it protected, so copying the data folder copied the secrets.

Migration is automatic: a plain key / plain JSON file is read once and rewritten protected.

Docker / Linux (no DPAPI): set SCRIPTGEN_MASTER_KEY (a Fernet key) in the environment; the key
file is then not used. Without it the key file stays as it is (unwrapped).

Limits (honest): DPAPI protects against someone copying the files or reading them from another
account; it does not protect against code running as RJ's own Windows user. Moving the data
folder to another PC / Windows user needs the master key exported first
(`python -m app.utils.crypto export-key`), then set as SCRIPTGEN_MASTER_KEY there.
"""
from __future__ import annotations

import base64
import json
import os
import sys

from cryptography.fernet import Fernet, InvalidToken

from .paths import get_key_path

_KEY_PREFIX = b"DPAPI1:"
_FILE_PREFIX = "SGENC1:"
_ENTROPY = b"ScriptGen/RJ-2026-11-17"   # extra DPAPI entropy: other apps of the same user can't unwrap by accident


class SecretStoreError(RuntimeError):
    """Stored secrets exist but cannot be decrypted (wrong Windows user / PC, or a lost key).
    Raised loudly on purpose - silently starting over would wipe the users and settings."""


# ---------------------------------------------------------------------------------------------
# Windows DPAPI (CryptProtectData / CryptUnprotectData) via ctypes - no extra package needed
# ---------------------------------------------------------------------------------------------
def dpapi_available() -> bool:
    return sys.platform == "win32"


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(b: bytes):
        buf = ctypes.create_string_buffer(b, len(b))
        return _Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    blob_in, _keep1 = _blob(data)
    entropy, _keep2 = _blob(_ENTROPY)
    blob_out = _Blob()
    UI_FORBIDDEN = 0x01
    if protect:
        fn = crypt32.CryptProtectData
        fn.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob), ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        fn.restype = wintypes.BOOL
        ok = fn(ctypes.byref(blob_in), "ScriptGen", ctypes.byref(entropy), None, None, UI_FORBIDDEN, ctypes.byref(blob_out))
    else:
        fn = crypt32.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob), ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        fn.restype = wintypes.BOOL
        ok = fn(ctypes.byref(blob_in), None, ctypes.byref(entropy), None, None, UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise OSError(f"DPAPI {'protect' if protect else 'unprotect'} failed (Windows error {ctypes.GetLastError()})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def protect_bytes(data: bytes) -> bytes:
    """DPAPI-wrap for storage (prefixed). Returns the data unchanged where DPAPI isn't available."""
    if not dpapi_available():
        return data
    return _KEY_PREFIX + base64.b64encode(_dpapi(data, True))


def unprotect_bytes(stored: bytes) -> bytes:
    """Reverse of protect_bytes; plain (legacy / non-Windows) data is returned as is."""
    stored = stored.strip()
    if not stored.startswith(_KEY_PREFIX):
        return stored
    return _dpapi(base64.b64decode(stored[len(_KEY_PREFIX):]), False)


def is_protected(stored: bytes) -> bool:
    return stored.strip().startswith(_KEY_PREFIX)


def write_protected_file(path, data: bytes) -> None:
    """Wrap + atomic write, verified by unwrapping before the file is replaced."""
    wrapped = protect_bytes(data)
    if unprotect_bytes(wrapped) != data:
        raise SecretStoreError("DPAPI round-trip check failed - key file left unchanged.")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(wrapped)
    try:   # the old key file is hidden; clear it first so the replace can't be refused
        import ctypes
        if path.exists():
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x80)   # FILE_ATTRIBUTE_NORMAL
    except Exception:
        pass
    os.replace(tmp, path)
    try:   # best effort: hidden file on Windows
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)
    except Exception:
        pass


# ---------------------------------------------------------------------------------------------
# Master key
# ---------------------------------------------------------------------------------------------
def _load_or_create_key() -> bytes:
    env_key = os.environ.get("SCRIPTGEN_MASTER_KEY", "").strip()
    if env_key:
        return env_key.encode("ascii")
    key_path = get_key_path()
    if key_path.exists():
        stored = key_path.read_bytes()
        try:
            key = unprotect_bytes(stored)
        except OSError as exc:
            raise SecretStoreError(
                f"Cannot unlock {key_path.name}: it was protected by another Windows user / PC ({exc}). "
                "Run ScriptGen as the Windows user that set it up, or set SCRIPTGEN_MASTER_KEY.") from exc
        if not is_protected(stored) and dpapi_available():
            write_protected_file(key_path, key)   # RJ 2026-11-17: migrate the plain key in place
        return key
    key = Fernet.generate_key()
    if dpapi_available():
        write_protected_file(key_path, key)
    else:
        key_path.write_bytes(key)
    return key


_fernet: Fernet | None = None
import threading as _threading
_KEY_LOCK = _threading.Lock()   # first use can come from several request threads at once


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        with _KEY_LOCK:
            if _fernet is None:
                _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt_text(plain: str) -> str:
    if not plain:
        return ""
    token = _get_fernet().encrypt(plain.encode("utf-8"))
    return token.decode("ascii")


def decrypt_text(token: str) -> str:
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        # Key file was regenerated/lost, or the value wasn't actually
        # encrypted (e.g. hand-edited config.json). Fail soft rather
        # than crash the app - caller sees an empty secret and the UI
        # will prompt to re-enter it.
        return ""


# ---------------------------------------------------------------------------------------------
# Whole-file encryption for JSON stores (config.json, web_users.json)
# ---------------------------------------------------------------------------------------------
def dumps_secure(obj) -> str:
    return _FILE_PREFIX + _get_fernet().encrypt(json.dumps(obj, indent=2).encode("utf-8")).decode("ascii")


def loads_secure(text: str):
    """Returns (object, was_encrypted). Plain JSON (written before 2026-11-17) is accepted so it
    can be migrated; an encrypted file that won't decrypt raises SecretStoreError (never ignored)."""
    t = (text or "").strip()
    if t.startswith(_FILE_PREFIX):
        try:
            plain = _get_fernet().decrypt(t[len(_FILE_PREFIX):].encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise SecretStoreError("An encrypted ScriptGen data file can't be decrypted with this master key.") from exc
        return json.loads(plain), True
    return json.loads(t), False


def is_encrypted_text(text: str) -> bool:
    return (text or "").lstrip().startswith(_FILE_PREFIX)


if __name__ == "__main__":
    # python -m app.utils.crypto export-key   -> prints the master key (for moving to another PC / Docker)
    # python -m app.utils.crypto status       -> shows what is protected
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "export-key":
        print("Keep this secret. Set it as SCRIPTGEN_MASTER_KEY on the new machine:")
        print(_load_or_create_key().decode("ascii"))
    else:
        from .paths import get_config_path, get_web_users_path, get_web_session_secret_path
        kp = get_key_path()
        print(f"master key   : {'DPAPI-protected' if kp.exists() and is_protected(kp.read_bytes()) else 'PLAIN / env'}")
        sp = get_web_session_secret_path()
        print(f"session key  : {'DPAPI-protected' if sp.exists() and is_protected(sp.read_bytes()) else 'PLAIN / missing'}")
        for p in (get_config_path(), get_web_users_path()):
            print(f"{p.name:<13}: {'encrypted' if p.exists() and is_encrypted_text(p.read_text(encoding='utf-8')) else 'PLAIN / missing'}")
