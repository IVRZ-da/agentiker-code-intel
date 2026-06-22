#!/usr/bin/env python3
"""
Benchmark-Script für code_intel Tools.

Misst Durchschnitts-Latenz von code_definition, code_references,
code_hover, code_search, code_symbols über mehrere Läufe.

Exit 0: Alle Tools unter 5s Durchschnitt
Exit 1: Mindestens ein Tool über 5s Durchschnitt
"""
import json
import os
import sys
import time
from pathlib import Path

PLUGIN_DIR = Path(os.path.expanduser("~/.hermes/plugins/code_intel"))
sys.path.insert(0, str(PLUGIN_DIR.parent))

# ── Config ──────────────────────────────────────────────
SAMPLE_FILE = str(PLUGIN_DIR / "code_tools.py")
WARMUP = 2
RUNS = 5
MAX_AVG_MS = 5000  # 5 Sekunden maximal

# ── Results ─────────────────────────────────────────────
results = {}


def timed(label: str, fn) -> float:
    """Run *fn* *RUNS* times (after *WARMUP*) and return avg ms."""
    for _ in range(WARMUP):
        fn()
    laps = []
    for _ in range(RUNS):
        t0 = time.perf_counter()
        fn()
        laps.append((time.perf_counter() - t0) * 1000)
    avg = sum(laps) / len(laps)
    results[label] = {"avg_ms": avg, "min_ms": min(laps), "max_ms": max(laps)}
    return avg


def main():

    # 1. code_symbols (AST — kein LSP nötig)
    os.chdir(str(PLUGIN_DIR))
    from code_intel.code_tools import code_search_tool, code_symbols_tool

    timed("code_symbols", lambda: json.loads(
        code_symbols_tool(SAMPLE_FILE, kind="function")
    ))

    # 2. code_search (AST)
    timed("code_search", lambda: json.loads(
        code_search_tool(SAMPLE_FILE, preset="function_calls")
    ))

    # 3. LSP-Tools (nur wenn verfügbar)
    from code_intel.lsp_bridge import (
        _check_lsp_reqs,
        code_definition_tool,
        code_hover_tool,
        code_references_tool,
    )

    if _check_lsp_reqs():
        # code_hover
        timed("code_hover", lambda: json.loads(
            code_hover_tool(SAMPLE_FILE, line=50)
        ))

        # code_definition
        timed("code_definition", lambda: json.loads(
            code_definition_tool(SAMPLE_FILE, line=50)
        ))

        # code_references
        timed("code_references", lambda: json.loads(
            code_references_tool(SAMPLE_FILE, line=50, group_by_file=True)
        ))
    else:
        pass

    # ── Summary ──────────────────────────────────────────
    all_ok = True
    for label, r in results.items():
        ok = "✅" if r["avg_ms"] < MAX_AVG_MS else "❌"  # noqa: F841
        if r["avg_ms"] >= MAX_AVG_MS:
            all_ok = False

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
