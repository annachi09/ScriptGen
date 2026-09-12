"""
Lightweight at-rest protection for stored secrets (DB password, AI API key).

IMPORTANT - honest limitation: this is obfuscation against casual
disclosure (shoulder-surfing config.json, accidental screenshots, git
commits), NOT protection against someone who has access to this
machine's filesystem. The decryption key lives in a plain file right
next to the encrypted data (data/scriptgen.key). Anyone who can read
that key file can decrypt everything. For a read-only reporting
account behind a locked-down tunnel this trade-off is reasonable; it
is NOT a substitute for OS-level secrets storage. If you need real
secrecy, wire this up to Windows Credential Manager (`keyring` package)
instead - the call sites in config.py are isolated so that's a
contained change later.
"""
from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from .paths import get_key_path


def _load_or_create_key() -> bytes:
    key_path = get_key_path()
    if key_path.exists():
        return key_path.read_bytes()
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    try:
        # Best-effort: hide the key file on Windows so it doesn't show up
        # in a casual folder listing. Silently no-op on other platforms
        # or if attrib isn't available.
        import ctypes

        ctypes.windll.kernel32.SetFileAttributesW(str(key_path), 0x02)  # FILE_ATTRIBUTE_HIDDEN
    except Exception:
        pass
    return key


_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
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
