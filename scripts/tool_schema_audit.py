#!/usr/bin/env python3
"""tool_schema_audit.py — Schema-Token-Audit der eigenen Tool-Flaeche.

Registriert das Plugin gegen einen Stub-PluginContext und misst, was Hermes pro Turn
wirklich in den Tool-Prompt legen wuerde, wenn alle Tools eager waeren. Hermes rechnet
Tokens als ``chars / 4.0`` (``tools/tool_search_catalog.py: CHARS_PER_TOKEN``).

Nur eager Tools kosten Schema-Tokens pro Turn; deferred Tools kosten lediglich einen
Katalognamen. Deshalb markiert das Skript die Tools aus ``EAGER_TOOLS`` (falls das Plugin
so eine Liste exportiert) und weist die Eager-Summe separat aus.

Aufruf
------
    python3 scripts/tool_schema_audit.py                      # Plugin dieses Repos
    python3 scripts/tool_schema_audit.py /pfad/zum/plugin
    python3 scripts/tool_schema_audit.py . --max-chars plan_create=3000
    python3 scripts/tool_schema_audit.py . --max-eager-chars 9000
    python3 scripts/tool_schema_audit.py . --json

Exit-Code: 0 = alle Budgets eingehalten, 1 = Budget verletzt oder Registrierung
fehlgeschlagen (CI-taugliche Negativprobe). ``--json`` gibt Maschinenlesbares aus.

Hinweis: Das Skript braucht die Python-Abhaengigkeiten des Plugins. Im Hermes-Host
liegen sie in der Plugin-Runtime; ausserhalb z. B.:
    env -u PYTHONPATH ~/.hermes/hermes-agent/venv/bin/python3 scripts/tool_schema_audit.py .
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

CHARS_PER_TOKEN = 4.0


class StubContext:
    """Minimaler PluginContext: sammelt nur, was register() anmeldet."""

    def __init__(self) -> None:
        self.tools: list[tuple[str, dict, str]] = []
        self.hooks: list[str] = []

    def register_tool(
        self, name, toolset=None, schema=None, handler=None, description=None, **kwargs
    ):  # noqa: ANN001
        self.tools.append((name or "", schema or {}, description or ""))

    def register_hook(self, hook_name, callback=None, **kwargs):  # noqa: ANN001
        self.hooks.append(hook_name)

    def __getattr__(self, item):  # noqa: ANN001
        def _noop(*args, **kwargs):
            return None

        return _noop


def _ensure_hermes_root() -> None:
    """Hermes-Install an den Suchpfad haengen (fuer ``hermes_cli``/``hermes_yaml``).

    ``code_intel.register()`` importiert ``hermes_cli.plugins``; das zieht ``utils``
    und ``hermes_yaml`` nach. Ausserhalb des Hosts fehlen die. Bewusst **ans Ende**
    gehaengt: dieses Plugin bringt ein eigenes ``tools/``-Paket mit, das Vorrang
    behalten muss (sonst kapert ``hermes-agent/tools`` die Importe).
    """
    root = Path.home() / ".hermes" / "hermes-agent"
    if root.is_dir() and str(root) not in sys.path:
        sys.path.append(str(root))


def load_plugin(plugin_dir: Path) -> Any:
    """Importiert das Plugin-Paket unabhaengig vom Hermes-Plugin-Manager."""
    _ensure_hermes_root()
    pkg = plugin_dir.name
    if str(plugin_dir.parent) not in sys.path:
        sys.path.insert(0, str(plugin_dir.parent))
    for mod in [m for m in list(sys.modules) if m == pkg or m.startswith(pkg + ".")]:
        del sys.modules[mod]
    spec = importlib.util.spec_from_file_location(
        pkg, plugin_dir / "__init__.py", submodule_search_locations=[str(plugin_dir)]
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"{pkg}: Paket konnte nicht geladen werden")
    module = importlib.util.module_from_spec(spec)
    sys.modules[pkg] = module
    spec.loader.exec_module(module)
    return module


def measure(plugin_dir: Path) -> dict[str, Any]:
    module = load_plugin(plugin_dir)
    ctx = StubContext()
    getattr(module, "register")(ctx)

    eager_names = set(getattr(module, "EAGER_TOOLS", ()) or ())
    rows = []
    for name, schema, desc in ctx.tools:
        chars = len(json.dumps({"schema": schema, "description": desc}, ensure_ascii=False))
        rows.append(
            {
                "name": name,
                "chars": chars,
                "tokens": round(chars / CHARS_PER_TOKEN),
                "eager": name in eager_names if eager_names else True,
            }
        )
    rows.sort(key=lambda r: r["chars"], reverse=True)
    total = sum(r["chars"] for r in rows)
    eager_chars = sum(r["chars"] for r in rows if r["eager"])
    return {
        "plugin": plugin_dir.name,
        "tools": rows,
        "tool_count": len(rows),
        "total_chars": total,
        "total_tokens": round(total / CHARS_PER_TOKEN),
        "eager_chars": eager_chars,
        "eager_tokens": round(eager_chars / CHARS_PER_TOKEN),
        "eager_known": bool(eager_names),
        "hooks": ctx.hooks,
    }


def parse_budgets(values: list[str] | None) -> dict[str, int]:
    budgets: dict[str, int] = {}
    for raw in values or []:
        name, _, limit = raw.partition("=")
        if not name or not limit.isdigit():
            raise SystemExit(f"--max-chars erwartet TOOL=ZEICHEN, bekam: {raw!r}")
        budgets[name.strip()] = int(limit)
    return budgets


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Schema-Token-Audit der Plugin-Tool-Flaeche")
    ap.add_argument("plugin_dir", nargs="?", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument(
        "--max-chars",
        action="append",
        metavar="TOOL=N",
        help="Budget fuer ein einzelnes Tool-Schema in Zeichen (mehrfach erlaubt)",
    )
    ap.add_argument(
        "--max-eager-chars",
        type=int,
        default=None,
        help="Budget fuer die Summe aller eager Tool-Schemas in Zeichen",
    )
    ap.add_argument("--json", action="store_true", help="Maschinenlesbare Ausgabe")
    args = ap.parse_args(argv)

    plugin_dir = Path(args.plugin_dir).resolve()
    if not (plugin_dir / "__init__.py").is_file():
        print(f"FEHLER: {plugin_dir} ist kein Plugin-Verzeichnis", file=sys.stderr)
        return 1

    result = measure(plugin_dir)
    budgets = parse_budgets(args.max_chars)
    violations: list[str] = []

    by_name = {r["name"]: r for r in result["tools"]}
    for name, limit in budgets.items():
        row = by_name.get(name)
        if row is None:
            violations.append(f"{name}: nicht registriert (Budget {limit} nicht pruefbar)")
        elif row["chars"] > limit:
            violations.append(f"{name}: {row['chars']:,} > {limit:,} Zeichen")
    if args.max_eager_chars is not None and result["eager_chars"] > args.max_eager_chars:
        violations.append(
            f"eager-Summe: {result['eager_chars']:,} > {args.max_eager_chars:,} Zeichen"
        )

    if args.json:
        print(json.dumps({**result, "violations": violations}, ensure_ascii=False, indent=2))
    else:
        print(f"\n{result['plugin']}: {result['tool_count']} Tools, "
              f"{result['total_chars']:,} Zeichen ≈ {result['total_tokens']:,} Tokens (alle eager)")
        if result["eager_known"]:
            print(f"eager laut EAGER_TOOLS: {result['eager_chars']:,} Zeichen "
                  f"≈ {result['eager_tokens']:,} Tokens pro Turn")
        else:
            print("kein EAGER_TOOLS exportiert — alle Tools als eager gerechnet (obere Schranke)")
        print(f"Hooks: {', '.join(result['hooks']) or 'keine'}\n")
        print(f"  {'tool':<28}{'chars':>9}{'~tok':>7}  eager")
        for row in result["tools"]:
            print(f"  {row['name']:<28}{row['chars']:>9,}{row['tokens']:>7,}  "
                  f"{'ja' if row['eager'] else 'nein'}")

    if violations:
        print("\nBUDGET VERLETZT:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
