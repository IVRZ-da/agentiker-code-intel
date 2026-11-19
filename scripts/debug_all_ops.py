#!/usr/bin/env python3
"""debug_all_ops.py — Live-Debug aller 70 code_intel-Ops.

Warum
-----
Das Plugin registriert 7 Namespace-Dispatcher mit 70 Ops (``code_<op>``). Ein
pytest-Lauf deckt die Handler isoliert ab, aber nicht den Live-Pfad: echte
``_fmt``-Panels (Rich/ANSI), echte LSP-Server, echtes Git-Repo, echte
Dateisystem-Rechte. Genau dort sassen die letzten Bugs (JSONDecodeError auf
ANSI-Output, nested JSON-Schema im Dispatch).

Was
----
Baut den echten Registrierungs-Catalog (``namespaces.build_catalog``), erzeugt
für jeden op aus ``tools.op_meta`` die Argumente aus den deklarierten
Pflicht-/Optionalfeldern und ruft den Handler.

Zwei Ebenen
-----------
``--mode handler``   Handler direkt (``catalog["code_<op>"]["handler"](args)``).
``--mode namespace`` durch den Dispatcher (``code_search`` …) — testet op-Enum,
                     Pflichtfeld-Validierung und ``op=help``.
``--mode both``      beides (default).

Sicherheit
----------
Alle schreibenden Ops laufen mit ``dry_run=True``. Vor und nach dem Lauf wird
``git status`` verglichen; Abweichung landet als ``worktree_dirty`` im Report.

Aufruf
------
    python scripts/debug_all_ops.py --out /tmp/ops_report.json
    python scripts/debug_all_ops.py --only migration,refactor --mode handler -v
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

PLUGIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_DIR.parent))  # damit `import code_intel` greift

FIXTURE_FILE = PLUGIN_DIR / "namespaces.py"
TARGET_FILE = PLUGIN_DIR / "lsp" / "extra" / "actions.py"

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# Ops, deren `path` ein Verzeichnis sein darf/muss.
DIR_OPS = {
    "metrics",
    "index",
    "dependency_risk",
    "workspace_summary",
    "hot_paths",
    "duplicates",
    "unused_finder",
    "security_scan",
    "review_assistant",
    "diff_analysis",
    "pr_impact",
    "export",
    "dependency_graph",
    "cycle_detector",
    "graph_query",
}

# Pro-Feld-Defaults. None = Feld bewusst nicht setzen (Tool soll selbst raten).
FIELD_DEFAULTS: dict[str, object] = {
    "path": str(FIXTURE_FILE),
    "source": str(FIXTURE_FILE),
    "target": str(TARGET_FILE),
    "line": None,  # dynamisch: Zeile von namespaces_enabled
    "character": None,
    "symbol": "namespaces_enabled",
    "new_name": "namespaces_enabled_v2",
    "new_body": "def namespaces_enabled() -> bool:\n    return True\n",
    "code": "def _probe_helper() -> int:\n    return 42\n",
    "pattern": "logger.debug($ARG)",
    "rewrite": "logger.info($ARG)",
    "rules": [{"pattern": "logger.debug($ARG)", "rewrite": "logger.info($ARG)"}],
    "error": "ImportError",
    "query": "build_catalog",
    "intent": "where is build_catalog defined",
    "dry_run": True,
    "language": "python",
    "format": "text",
    "fmt": "json",
    "kind": "all",
    "direction": "both",
    "limit": 5,
    "max_results": 20,
    "top_n": 5,
    "max_files": 40,
    "max_findings": 20,
    "max_count": 5,
    "max_commits": 5,
    "max_depth": 2,
    "max_callers_per_level": 5,
    "max_cycles": 5,
    "depth": 2,
    "timeout": 60,
    "min_lines": 5,
    "similarity_threshold": 0.8,
    "context_lines": 2,
    "start_line": 1,
    "end_line": 40,
    "base": "HEAD~1",
    "head": "HEAD",
    "base_branch": "main",
    "auto_detect": True,
    "staged": False,
    "include_declaration": True,
    "group_by_file": False,
    "include_body": False,
    "include_tests": False,
    "include_decorators": True,
    "newline": True,
    "force_rescan": False,
    "force": False,
    "module_level": False,
    "test_coverage": True,
    "directory": False,
    "style": "google",
    "framework": "auto",
    "severity": "all",
}

# Op-spezifische Argumente, die sich nicht aus Feldnamen ableiten lassen.
OP_OVERRIDES: dict[str, dict[str, object]] = {
    "graph_query": {"path": str(PLUGIN_DIR), "query": "callers", "symbol": "namespaces_enabled"},
    "workspace_symbols": {"query": "build_catalog"},
    "query": {"intent": "where is build_catalog defined", "path": str(PLUGIN_DIR)},
    "search_by_error": {"path": str(PLUGIN_DIR), "error": "ImportError"},
    "search": {"path": str(PLUGIN_DIR), "pattern": "build_catalog", "preset": "function_calls"},
    "review_assistant": {"path": str(PLUGIN_DIR), "base": "HEAD~1", "head": "HEAD"},
    "diff_analysis": {"path": str(PLUGIN_DIR), "base": "HEAD~1", "head": "HEAD"},
    "complexity": {"path": str(FIXTURE_FILE)},
    "export": {"path": str(PLUGIN_DIR), "kind": "all", "fmt": "json"},
    "index": {"path": str(PLUGIN_DIR), "depth": 2},
    "metrics": {"path": str(PLUGIN_DIR), "depth": 2},
    "duplicates": {"path": str(PLUGIN_DIR), "max_files": 40, "min_lines": 8},
    "unused_finder": {"path": str(PLUGIN_DIR), "depth": 2, "max_files": 40},
    "security_scan": {"path": str(PLUGIN_DIR), "max_files": 40},
    "hot_paths": {"path": str(PLUGIN_DIR), "depth": 2, "top_n": 5},
    "dependency_graph": {"path": str(PLUGIN_DIR), "depth": 2, "format": "mermaid"},
    "cycle_detector": {"path": str(PLUGIN_DIR), "depth": 2},
    "workspace_summary": {"path": str(PLUGIN_DIR), "depth": 2},
    "dependency_risk": {"path": str(FIXTURE_FILE)},
    "migration": {
        "path": str(PLUGIN_DIR),
        "rules": [{"pattern": "logger.debug($ARG)", "rewrite": "logger.info($ARG)"}],
        "dry_run": True,
    },
    "refactor": {
        "path": str(PLUGIN_DIR / "lsp"),
        "pattern": "logger.debug($ARG)",
        "rewrite": "logger.info($ARG)",
        "dry_run": True,
        "file_glob": "*.py",
    },
    "batch_refactor": {
        "path": str(PLUGIN_DIR / "lsp"),
        "pattern": "logger.debug($ARG)",
        "rewrite": "logger.info($ARG)",
        "dry_run": True,
        "file_glob": "*.py",
    },
    "action": {"path": str(FIXTURE_FILE), "line": None, "apply_index": None},
    "format": {"path": str(FIXTURE_FILE), "dry_run": True},
}


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def fixture_line() -> int:
    """Zeile von ``namespaces_enabled`` — stabiler Anker für line-basierte Ops."""
    tree = ast.parse(FIXTURE_FILE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "namespaces_enabled":
            return node.lineno
    raise SystemExit("fixture_line: namespaces_enabled nicht gefunden")


def build_args(op: str, meta, line: int) -> dict[str, object]:
    fields = list(meta.required) + list(meta.optional)
    args: dict[str, object] = {}
    for field in fields:
        if field in OP_OVERRIDES.get(op, {}):
            continue
        value = FIELD_DEFAULTS.get(field)
        if value is None and field == "line":
            value = line
        if value is None:
            continue
        if field == "path" and op in DIR_OPS:
            value = str(PLUGIN_DIR)
        args[field] = value
    args.update({k: v for k, v in OP_OVERRIDES.get(op, {}).items() if v is not None})
    if "line" in fields and "line" not in args:
        args["line"] = line
    return args


class _Timeout(Exception):
    pass


def _alarm(signum, frame):  # noqa: ARG001
    raise _Timeout()


def call_handler(handler, args: dict, timeout: int) -> tuple[str, str | None, float]:
    """Handler aufrufen. Gibt (output, exception, dauer_ms) zurück."""
    start = time.monotonic()
    exc_text: str | None = None
    out = ""
    old = signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        out = handler(dict(args))
    except _Timeout:
        exc_text = f"TimeoutError: op haengt >{timeout}s"
    except BaseException as exc:  # noqa: BLE001 — jede Exception ist ein Befund
        exc_text = f"{type(exc).__name__}: {exc}"
        if os.environ.get("DEBUG_OPS_TRACE"):
            exc_text += "\n" + traceback.format_exc(limit=6)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
    return ("" if out is None else str(out)), exc_text, (time.monotonic() - start) * 1000


# Ops, deren Rueckgabe JSON ist (fmt_json) — muss parsbar ankommen.
def semantic_check(out: str) -> list[str]:
    """Stille Bugs finden: JSON-Vertrag gebrochen oder Pflichtfelder None.

    ``ok`` im Status heisst nur "kein Fehler-Panel". Ein Tool kann
    "✅ Success" melden und trotzdem lauter None-Felder liefern (genau das tat
    ``explain``, weil zwei interne Aufrufe Panel-Text als JSON parsten).
    """
    problems: list[str] = []
    plain = strip_ansi(out).strip()
    if plain.startswith(("{", "[")):
        try:
            data = json.loads(plain)
        except ValueError as exc:
            problems.append(f"JSON-Vertrag gebrochen: {exc}")
        else:
            if isinstance(data, dict):
                nulls = [k for k, v in data.items() if v is None]
                if nulls and not str(data.get("status", "")).lower() == "error":
                    problems.append(f"None-Felder: {', '.join(nulls[:5])}")
    # Panel-Rueckgaben (fmt_ok) mit leeren Kernfeldern
    for match in re.finditer(r"│\s+(symbol|signature|kind|function|file)\s+│\s+None", plain):
        problems.append(f"{match.group(1)}=None im Panel")
    return problems


ERROR_MARKERS = ("[tool_error]", "traceback (most recent call last)", '"status": "error"', '"status":"error"', "op '")

# Panels, die einen Fehler tragen, ohne dass der Handler crasht.
SOFT_MARKERS = ("❌", "╳", "error:", "fehler:")


def classify(out: str, exc: str | None) -> tuple[str, list[str]]:
    """Status aus Handler-Output ableiten.

    Wichtig: nur der *Anfang* des Outputs zaehlt. Tools, die fremden Quelltext
    zitieren (``security_scan``, ``duplicates``), enthalten sonst beliebige
    Fehler-Strings aus dem gescannten Code — das erzeugt False Positives.
    """
    if exc:
        return ("timeout" if exc.startswith("TimeoutError") else "error"), [exc.split("\n")[0]]
    plain = strip_ansi(out).strip()
    if not plain:
        return "empty", ["leerer Output"]

    # JSON-Tools: echtes status-Feld auswerten
    if plain.startswith("{"):
        try:
            data = json.loads(plain)
        except ValueError:
            data = None
        if isinstance(data, dict) and str(data.get("status", "")).lower() == "error":
            return "error", [f"status=error: {str(data.get('error') or data.get('message'))[:120]}"]
        if isinstance(data, dict):
            return "ok", []

    # Panel-Tools: nur die ersten 600 Zeichen (Titel + erste Zeilen) pruefen
    head = plain[:600].lower()
    hits = [m for m in ERROR_MARKERS if m in head]
    if hits:
        return "error", hits
    soft = [m for m in SOFT_MARKERS if m in head]
    if soft and len(plain) < 3000:
        return "soft_error", soft
    return "ok", []


def git_state() -> str:
    try:
        return subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=PLUGIN_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except Exception:  # noqa: BLE001
        return "<git nicht verfuegbar>"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="/tmp/ops_report.json")
    ap.add_argument("--mode", choices=["handler", "namespace", "both"], default="both")
    ap.add_argument("--only", default="", help="Komma-Liste von Ops")
    ap.add_argument("--skip", default="", help="Komma-Liste von Ops")
    ap.add_argument("--timeout", type=int, default=180, help="Sekunden pro Op")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    import code_intel  # type: ignore[reportMissingImports]
    from code_intel.namespaces import (  # type: ignore[reportMissingImports]
        build_catalog,
        build_tool_specs,
    )
    from code_intel.tools import op_meta  # type: ignore[reportMissingImports]

    def _reg_ast(target):
        code_intel._register_ast_tools(target)

    def _reg_lsp(target):
        from code_intel.lsp_bridge import register_lsp_tools  # type: ignore[reportMissingImports]

        register_lsp_tools(target)

    catalog = build_catalog(_reg_ast, _reg_lsp)
    line = fixture_line()

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    ops = [op for op in op_meta.OP_META if (not only or op in only) and op not in skip]

    # Dispatcher-Handler je Gruppe (Namespace-Ebene)
    ns_handlers: dict[str, Any] = {}
    if args.mode in ("namespace", "both"):
        # Die Dispatcher entstehen erst in register_namespaces() — build_tool_specs
        # liefert exakt die Handler, die der Host registriert (op-Enum + Pflichtcheck).
        for spec in build_tool_specs(catalog):
            ns_handlers[spec["name"]] = spec["handler"]

    dirty_before = git_state()
    results: list[dict[str, Any]] = []
    order = {"ok": 0, "empty": 1, "soft_error": 2, "error": 3, "timeout": 4}

    for op in ops:
        meta = op_meta.OP_META[op]
        tool = catalog.get(f"code_{op}")
        call_args = build_args(op, meta, line)
        entry: dict[str, Any] = {
            "op": op,
            "group": meta.group,
            "tier": meta.tier,
            "required": list(meta.required),
            "args": sorted(call_args.keys()),
        }

        # 1) Handler-Ebene
        if args.mode in ("handler", "both"):
            if tool is None or tool.get("handler") is None:
                entry["handler"] = {"status": "error", "detail": ["kein Handler im Catalog"]}
            else:
                out, exc, dur = call_handler(tool["handler"], call_args, args.timeout)
                status, why = classify(out, exc)
                entry["handler"] = {
                    "status": status,
                    "why": why,
                    "duration_ms": round(dur),
                    "output_len": len(strip_ansi(out)),
                    "head": strip_ansi(out)[:400],
                    "semantic": semantic_check(out),
                }

        # 2) Namespace-Ebene (echter Dispatcher-Pfad inkl. Pflichtfeld-Check)
        if args.mode in ("namespace", "both"):
            handler = ns_handlers.get(meta.group)
            if handler is None:
                entry["namespace"] = {"status": "error", "detail": [f"Dispatcher {meta.group} fehlt"]}
            else:
                ns_args = {"op": op, **{k: v for k, v in call_args.items() if k != "op"}}
                out, exc, dur = call_handler(handler, ns_args, args.timeout)
                status, why = classify(out, exc)
                entry["namespace"] = {
                    "status": status,
                    "why": why,
                    "duration_ms": round(dur),
                    "output_len": len(strip_ansi(out)),
                    "head": strip_ansi(out)[:400],
                    "semantic": semantic_check(out),
                }

        # Semantik-Befunde beider Ebenen zusammenfuehren
        sem: list[str] = []
        for key in ("handler", "namespace"):
            level = entry.get(key)
            if isinstance(level, dict):
                for item in level.get("semantic") or []:
                    if item not in sem:
                        sem.append(item)
        if sem:
            entry["semantic"] = sem

        # Gesamtstatus = schlechtester der beiden Ebenen
        statuses = [entry[k]["status"] for k in ("handler", "namespace") if isinstance(entry.get(k), dict)]
        entry["status"] = max(statuses, key=lambda s: order.get(s, 9)) if statuses else "error"

        results.append(entry)
        if args.verbose or entry["status"] not in ("ok",):
            print(f"[{entry['status']:>10}] {meta.group}.{op}", flush=True)

    dirty_after = git_state()
    summary: dict[str, int] = {}
    suspect = 0
    for entry in results:
        summary[entry["status"]] = summary.get(entry["status"], 0) + 1
        if entry.get("semantic"):
            suspect += 1
    summary["semantisch_auffaellig"] = suspect

    report = {
        "meta": {
            "plugin_version": (PLUGIN_DIR / "VERSION").read_text(encoding="utf-8").strip()
            if (PLUGIN_DIR / "VERSION").exists()
            else "?",
            "catalog_size": len(catalog),
            "op_count": len(op_meta.OP_META),
            "mode": args.mode,
            "timeout_s": args.timeout,
            "fixture": str(FIXTURE_FILE),
            "fixture_line": line,
            "python": sys.version.split()[0],
            "worktree_dirty": dirty_before != dirty_after,
            "dirty_diff": ""
            if dirty_before == dirty_after
            else f"before={dirty_before!r} after={dirty_after!r}",
        },
        "summary": summary,
        "results": results,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")

    print()
    print(f"Report: {out_path}  ({len(results)} Ops, mode={args.mode})")
    print("Status:", dict(sorted(summary.items())))
    if report["meta"]["worktree_dirty"]:
        print("!! WORKTREE VERAENDERT — dry_run-Verletzung pruefen:", report["meta"]["dirty_diff"])
    bad = [r for r in results if r["status"] in ("error", "timeout")]
    if bad:
        print(f"\nRote Ops ({len(bad)}):")
        for r in bad:
            levels = {k: r[k] for k in ("handler", "namespace") if isinstance(r.get(k), dict)}
            worst = max(levels, key=lambda k: order.get(levels[k].get("status"), 9))
            detail = levels[worst].get("why") or levels[worst].get("detail") or ["?"]
            print(f"  - {r['group']}.{r['op']} [{worst}={levels[worst]['status']}]: {detail[0]}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
