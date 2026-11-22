"""Pfade für Nutzdaten des Plugins (Hermes-Review PR #157345, Empfehlung 1).

Vorher lagen Symbol-Caches, Marker und der Knowledge-Graph hartkodiert unter
``~/.hermes/...``. Damit teilten sich **alle** Hermes-Profile einen Cache, und
``HERMES_HOME`` wurde ignoriert.

Jetzt kommt die Wurzel aus ``hermes_constants.get_hermes_home()`` — dieselbe
Quelle, die Hermes selbst für Profile verwendet — und liegt unter
``<hermes-home>/plugin-data/agentiker-code-intel``. Ist das Host-Modul nicht
importierbar (Standalone-Betrieb, Tests), greift derselbe Default-Pfad wie in
Hermes.

Das Plugin-Usage-Log (``usage.py``) bleibt bewusst im globalen Log-Verzeichnis:
es ist dieselbe Datei und dasselbe Format wie bei den anderen Plugins und wird
zentral ausgewertet.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["plugin_data_dir", "cache_dir"]

PLUGIN_SLUG = "agentiker-code-intel"


def _hermes_home() -> Path:
    """Hermes-Home: ``$HERMES_HOME``, sonst Host-Modul, sonst ``~/.hermes``.

    ``HERMES_HOME`` hat Vorrang, weil der Host selbst so profiliert. Ohne den
    Env-Zweig schrieben Tests und Profilwechsel in das Default-Profil
    (Regression aus Bughunt-Runde 5, F12).
    """
    env = os.environ.get("HERMES_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    try:  # noqa: PLC0415 — Host-Modul, im Standalone-Betrieb nicht vorhanden
        from hermes_constants import get_hermes_home
    except Exception:  # noqa: BLE001 — kein Host = Default
        return Path.home() / ".hermes"
    try:
        home = get_hermes_home()
    except Exception:  # noqa: BLE001
        return Path.home() / ".hermes"
    return Path(home) if home else Path.home() / ".hermes"


def plugin_data_dir(*parts: str) -> Path:
    """``<hermes-home>/plugin-data/agentiker-code-intel[/parts…]``."""
    return _hermes_home().joinpath("plugin-data", PLUGIN_SLUG, *parts)


def cache_dir() -> Path:
    """Verzeichnis für persistente AST-/File-Caches (pro Profil)."""
    return plugin_data_dir("cache")
