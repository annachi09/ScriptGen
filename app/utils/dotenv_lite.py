"""
Tiny, dependency-free ".env" file loader.

NOT python-dotenv - adding a new third-party dependency just for this felt
like overkill for a project whose whole philosophy is "minimal deps, no
compiled extensions" (see requirements.txt's own comments on why python-tds
was picked over pymssql). This is ~15 lines instead.

Added as part of moving secrets (the default DB password / Gemini API key)
out of app/config.py and into the environment, so the project could safely
go into git (2026-09-12) - see README's "Security note on stored
credentials". Reads KEY=VALUE lines from a .env file at the project root
(gitignored - see .gitignore) into os.environ, but NEVER overwrites a
variable that's already set in the real environment: a real env var (set in
PowerShell, a Windows service, CI, etc.) always wins over whatever a stray
.env file says.
"""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv_if_present(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
