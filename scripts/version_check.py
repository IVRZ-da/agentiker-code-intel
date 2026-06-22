#!/usr/bin/env python3
"""
Version Consistency Check für agentiker-code-intel-plugin.

Prüft ob plugin.yaml, pyproject.toml und CHANGELOG.md die gleiche Version führen.
Exit-Codes:
  0 — Konsistent
  1 — Drift erkannt
  2 — Fehler beim Lesen

Usage:
  python3 scripts/version_check.py          # Prüft + Ausgabe
  python3 scripts/version_check.py --quiet   # Nur Exit-Code, keine Ausgabe
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def extract_version(filepath: str, pattern: str) -> str | None:
    """Extrahiert eine Version via Regex aus einer Datei."""
    path = REPO_ROOT / filepath
    if not path.exists():
        return None
    try:
        text = path.read_text("utf-8")
        m = re.search(pattern, text, re.MULTILINE)
        return m.group(1) if m else None
    except (OSError, UnicodeDecodeError):
        return None


def check() -> int:
    """Prüft Version-Konsistenz. Returns 0=ok, 1=drift, 2=error."""
    versions = {
        "plugin.yaml": extract_version("plugin.yaml", r'version:\s*([\d.]+)'),
        "pyproject.toml": extract_version(
            "pyproject.toml", r'version\s*=\s*"([\d.]+)"'
        ),
        "CHANGELOG.md": extract_version(
            "CHANGELOG.md", r"^## \[([\d.]+)\]"
        ),
    }

    # Filter None (Datei nicht gefunden)
    found = {k: v for k, v in versions.items() if v is not None}
    if not found:
        return 2

    unique = set(found.values())
    if len(unique) > 1:
        quiet = "--quiet" in sys.argv
        if not quiet:
            for file, ver in versions.items():
                status = f"  {file}: {ver or 'NICHT GEFUNDEN'}"
                if ver and ver != list(unique)[0]:
                    status += "  ← DRIFT"
        return 1

    ver = next(iter(unique))
    quiet = "--quiet" in sys.argv
    if not quiet:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(check())
