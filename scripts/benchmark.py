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
    print(f"🔬 code_intel Benchmark — {SAMPLE_FILE}")
    print(f"  Warmup: {WARMUP} Läufe, Runs: {RUNS} Läufe\n")

    # 1. code_symbols (AST — kein LSP nötig)
    os.chdir(str(PLUGIN_DIR))
    from code_intel.code_tools import code_symbols_tool, code_search_tool

    timed("code_symbols", lambda: json.loads(
        code_symbols_tool(SAMPLE_FILE, kind="function")
    ))
    print(f"  ✅ code_symbols:  {results['code_symbols']['avg_ms']:7.1f}ms  "
          f"(min={results['code_symbols']['min_ms']:.0f} max={results['code_symbols']['max_ms']:.0f})")

    # 2. code_search (AST)
    timed("code_search", lambda: json.loads(
        code_search_tool(SAMPLE_FILE, preset="function_calls")
    ))
    print(f"  ✅ code_search:   {results['code_search']['avg_ms']:7.1f}ms  "
          f"(min={results['code_search']['min_ms']:.0f} max={results['code_search']['max_ms']:.0f})")

    # 3. LSP-Tools (nur wenn verfügbar)
    from code_intel.lsp_bridge import (
        code_definition_tool, code_references_tool, code_hover_tool,
        _check_lsp_reqs,
    )

    if _check_lsp_reqs():
        # code_hover
        timed("code_hover", lambda: json.loads(
            code_hover_tool(SAMPLE_FILE, line=50)
        ))
        print(f"  ✅ code_hover:    {results['code_hover']['avg_ms']:7.1f}ms  "
              f"(min={results['code_hover']['min_ms']:.0f} max={results['code_hover']['max_ms']:.0f})")

        # code_definition
        timed("code_definition", lambda: json.loads(
            code_definition_tool(SAMPLE_FILE, line=50)
        ))
        print(f"  ✅ code_definition: {results['code_definition']['avg_ms']:7.1f}ms  "
              f"(min={results['code_definition']['min_ms']:.0f} max={results['code_definition']['max_ms']:.0f})")

        # code_references
        timed("code_references", lambda: json.loads(
            code_references_tool(SAMPLE_FILE, line=50, group_by_file=True)
        ))
        print(f"  ✅ code_references: {results['code_references']['avg_ms']:7.1f}ms  "
              f"(min={results['code_references']['min_ms']:.0f} max={results['code_references']['max_ms']:.0f})")
    else:
        print("\n  ⚠️  Kein LSP-Server verfügbar — LSP-Tools übersprungen")
        print("  Installiere pyright-langserver oder pylsp für vollständige Benchmarks.")

    # ── Summary ──────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"{'Tool':<20} {'Avg (ms)':>10} {'Min':>8} {'Max':>8}")
    print(f"{'-'*50}")
    all_ok = True
    for label, r in results.items():
        ok = "✅" if r["avg_ms"] < MAX_AVG_MS else "❌"
        if r["avg_ms"] >= MAX_AVG_MS:
            all_ok = False
        print(f"  {label:<18} {r['avg_ms']:>8.1f}  {r['min_ms']:>6.0f}  {r['max_ms']:>6.0f}  {ok}")
    print(f"{'='*50}")
    print(f"\n  Threshold: {MAX_AVG_MS}ms (5s)")
    print(f"  Result:    {'✅ ALLE OK' if all_ok else '❌ EIN TOOL ZU LANGSAM'}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
