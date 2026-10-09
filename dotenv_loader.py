"""Loads KEY=value lines from a local .env file into the environment (no extra dependency)."""

import os
from pathlib import Path


def load_env(path: Path = Path(__file__).resolve().parent / ".env") -> None:
    if not path.exists():
        return
    # utf-8-sig: Windows Notepad may save the file with an invisible BOM at the start.
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
