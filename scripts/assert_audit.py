#!/usr/bin/env python3
"""assert_audit.py — Gate fuer die Schema-Token-Budgets des Plugins.

Warum eigenes Skript
--------------------
``scripts/tool_schema_audit.py`` *misst* und druckt JSON; es kennt nur Zeichen-Budgets
und beendet sich ohne Budgets immer mit 0. Als CI-/Pre-Commit-Gate ist das zu weich:
eine Prosa-Zeile ("unveraendert 4.788 Tok") ist keine Assertion. Dieses Skript
formuliert die Budgets in **Tokens** (so, wie Hermes rechnet: chars / 4.0), prueft sie
und bricht mit Exit 1 ab, wenn eines verletzt ist.

Aufruf
------
    python3 scripts/assert_audit.py                        # nur messen, Exit 0
    python3 scripts/assert_audit.py --max-eager 4900
    python3 scripts/assert_audit.py --max-eager 4900 --max-tool-tokens code_edit=1000
    python3 scripts/assert_audit.py --max-total 6000 --json

Exit-Codes
----------
0 = alle Budgets gehalten
1 = Budget verletzt, Tool nicht registriert oder Messung fehlgeschlagen
2 = Aufruffehler (unbekannte Budget-Syntax)

Interpreter
-----------
Das Plugin importiert beim ``register()`` ``hermes_yaml`` (braucht ``ruamel.yaml``).
Das liegt nur im Hermes-venv, nicht im System-Python — daher::

    env -u PYTHONPATH ~/.hermes/hermes-agent/venv/bin/python3 scripts/assert_audit.py --max-eager 4900
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Sequence

CHARS_PER_TOKEN = 4.0
DEFAULT_PLUGIN = Path(__file__).resolve().parent.parent


def _load_audit():
    """``tool_schema_audit`` aus demselben Verzeichnis importieren."""
    path = Path(__file__).resolve().parent / "tool_schema_audit.py"
    spec = importlib.util.spec_from_file_location("_code_intel_schema_audit", path)
    if spec is None or spec.loader is None:  # pragma: no cover - nur bei kaputtem Repo
        raise RuntimeError(f"{path}: Modul konnte nicht geladen werden")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _token_budget(raw: str) -> tuple[str, int]:
    name, sep, limit = raw.partition("=")
    if not sep or not name.strip() or not limit.isdigit():
        raise ValueError(f"erwartet TOOL=TOKENS, bekam {raw!r}")
    return name.strip(), int(limit)


def check(result: dict, *, max_eager: int | None = None,
          max_total: int | None = None,
          max_tool_tokens: Sequence[tuple[str, int]] = ()) -> list[str]:
    """Budget-Verletzungen als Liste von Klartexten (leer = gruen)."""
    violations: list[str] = []
    by_name = {row["name"]: row for row in result.get("tools", [])}

    for name, limit in max_tool_tokens:
        row = by_name.get(name)
        if row is None:
            violations.append(f"{name}: nicht registriert (Budget {limit} Tok nicht pruefbar)")
        elif row["tokens"] > limit:
            violations.append(f"{name}: {row['tokens']:,} > {limit:,} Tokens")

    if max_eager is not None and result.get("eager_tokens", 0) > max_eager:
        violations.append(
            f"eager-Summe: {result['eager_tokens']:,} > {max_eager:,} Tokens "
            f"({result.get('tool_count', 0)} Tools)"
        )

    if max_total is not None and result.get("total_tokens", 0) > max_total:
        violations.append(f"Gesamt-Summe: {result['total_tokens']:,} > {max_total:,} Tokens")

    return violations


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Token-Budget-Gate fuer die Plugin-Tool-Flaeche")
    ap.add_argument("plugin_dir", nargs="?", default=str(DEFAULT_PLUGIN))
    ap.add_argument("--max-eager", type=int, default=None,
                    help="Budget fuer die Summe aller eager Tool-Schemas in Tokens")
    ap.add_argument("--max-total", type=int, default=None,
                    help="Budget fuer die Summe aller Tool-Schemas in Tokens")
    ap.add_argument("--max-tool-tokens", action="append", default=[], metavar="TOOL=N",
                    help="Budget pro Tool in Tokens (mehrfach erlaubt)")
    ap.add_argument("--json", action="store_true", help="Maschinenlesbare Ausgabe")
    args = ap.parse_args(argv)

    try:
        budgets = [_token_budget(raw) for raw in args.max_tool_tokens]
    except ValueError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2

    try:
        audit = _load_audit()
        result = audit.measure(Path(args.plugin_dir).resolve())
    except ModuleNotFoundError as exc:
        # Das Plugin importiert beim register() hermes_yaml (braucht ruamel.yaml).
        # Im System-Python fehlt das; der Hermes-Interpreter hat es.
        print(
            f"FEHLER: {exc}. Das Plugin braucht die Hermes-Umgebung — Aufruf mit "
            "`env -u PYTHONPATH ~/.hermes/hermes-agent/venv/bin/python3 scripts/assert_audit.py`.",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:  # pragma: no cover - Umgebungsfehler
        print(f"FEHLER: Messung fehlgeschlagen: {exc}", file=sys.stderr)
        return 1

    violations = check(result, max_eager=args.max_eager, max_total=args.max_total,
                       max_tool_tokens=budgets)

    if args.json:
        print(json.dumps(
            {
                "plugin": result.get("plugin"),
                "tool_count": result.get("tool_count"),
                "eager_tokens": result.get("eager_tokens"),
                "total_tokens": result.get("total_tokens"),
                "budgets": {
                    "max_eager": args.max_eager,
                    "max_total": args.max_total,
                    "tools": dict(budgets),
                },
                "violations": violations,
                "status": "fail" if violations else "pass",
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        print(
            f"{result.get('plugin')}: {result.get('tool_count')} Tools, "
            f"eager {result.get('eager_tokens'):,} Tok, gesamt {result.get('total_tokens'):,} Tok"
        )
        if args.max_eager is not None:
            print(f"  Budget eager: {args.max_eager:,} Tok")
        for name, limit in budgets:
            print(f"  Budget {name}: {limit:,} Tok")
        print("  Ergebnis: " + ("BUDGET VERLETZT" if violations else "OK"))

    if violations:
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
