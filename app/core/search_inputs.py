"""
Shared "Find" input parsing (RJ 2026-11-26: "why no search screen" - every page built today gets a Search screen:
type / paste codes, one per line or comma separated, instead of only Detect all).

parse() keeps only safe tokens (letters, digits, '-', '_', '.') so they can be inlined as SQL string literals;
sql_list() renders them as a quoted IN-list. Pure logic.
"""
from __future__ import annotations

import re

MAX_INPUTS = 500
_TOKEN = re.compile(r"[A-Za-z0-9_.\-]+")


def parse(text) -> list[str]:
    if isinstance(text, (list, tuple)):
        text = " ".join(str(t) for t in text)
    out, seen = [], set()
    for tok in _TOKEN.findall(str(text or "")):
        tok = tok.strip(".-_")
        if tok and tok.upper() not in seen:
            seen.add(tok.upper())
            out.append(tok)
    if len(out) > MAX_INPUTS:
        raise ValueError(f"Max {MAX_INPUTS} values per search ({len(out)} given).")
    return out


def sql_list(tokens: list[str]) -> str:
    return ", ".join("'" + t.replace("'", "''") + "'" for t in tokens)
