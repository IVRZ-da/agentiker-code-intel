"""Nutzungs-Messung: eine JSONL-Zeile pro code_intel-Aufruf.

Gegenstück zu ``scout/shared/usage.py`` — dieselbe Datei, dasselbe Format, damit
eine Auswertung beide Plugins in einem Durchlauf zählt.

Pfad: ``~/.hermes/logs/plugin-usage.jsonl`` (überschreibbar via ``PLUGIN_USAGE_LOG``).
Die Datei ist append-only, Zeilen < 4 KiB; `O_APPEND` ist atomar, kein Locking nötig.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_ENV = "PLUGIN_USAGE_LOG"
#: Log-Datei der Nutzungsmessung. Bewusst GLOBAL (nicht unter plugin-data):
#: dieselbe Datei und dasselbe Format wie bei den anderen Plugins, damit die
#: Auswertung zentral bleibt (siehe _paths.py).
_DEFAULT_LOG = Path.home() / ".hermes" / "logs" / "plugin-usage.jsonl"

# Die sieben Dispatcher und die 70 Legacy-Einzel-Tools teilen sich das Präfix.
_TOOL_PREFIX = "code_"


def log_path() -> Path:
    """Ziel-Datei; ``PLUGIN_USAGE_LOG`` gewinnt (Tests, Umzüge)."""
    return Path(os.environ.get(LOG_ENV) or _DEFAULT_LOG)


def is_code_intel_tool(name: str) -> bool:
    """True für die sieben Dispatcher und die Legacy-Einzelnamen."""
    return name.startswith(_TOOL_PREFIX)


#: Schluessel, unter denen der Host die Tool-Argumente uebergibt. ``args`` ist
#: der echte Vertrag (``model_tools._emit_post_tool_call_hook``); die anderen
#: bleiben fuer aeltere Aufrufer und fremde Plugins.
_ARGS_KEYS = ("args", "arguments", "tool_args")

#: ``status``-Werte, die keinen Fehler bedeuten.
_OK_STATUS = frozenset({"ok", "success", "completed", "succeeded", ""})


def _op_of(kwargs: dict[str, Any]) -> str | None:
    """``op`` aus den Tool-Argumenten, egal ob dict oder JSON-String."""
    args: Any = next((kwargs[key] for key in _ARGS_KEYS if kwargs.get(key) is not None), {})
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return None
    if isinstance(args, dict):
        op = args.get("op")
        return op if isinstance(op, str) else None
    return None



def _is_error(kwargs: dict[str, Any]) -> bool:
    """Fehlerflag aus dem Host-Vertrag ableiten.

    Der Host liefert ``status`` (``ok``/``error``) plus ``error_type`` und
    ``error_message`` — kein ``error``/``is_error``. Vor 0.6.41 stand deshalb
    in jeder Zeile ``op=null`` und ``error=false`` (Bughunt-Runde 5, F16).
    """
    for key in ("is_error", "error"):
        value = kwargs.get(key)
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in _OK_STATUS
    status = kwargs.get("status")
    if isinstance(status, str):
        return status.strip().lower() not in _OK_STATUS
    return bool(kwargs.get("error_type") or kwargs.get("error_message"))



def on_post_tool_call(**kwargs: Any) -> None:
    """Schreibt eine Messzeile für jeden code_intel-Aufruf; bricht nie einen Turn."""
    name = str(kwargs.get("tool_name") or "")
    if not is_code_intel_tool(name):
        return
    try:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "plugin": "code_intel",
            "tool": name,
            "op": _op_of(kwargs),
            "session_id": kwargs.get("session_id"),
            "error": _is_error(kwargs),
            "status": kwargs.get("status") if isinstance(kwargs.get("status"), str) else None,
        }
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — Messung darf nie einen Turn brechen
        pass
