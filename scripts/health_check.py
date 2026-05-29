#!/usr/bin/env python3
"""
code_intel health check — tests tools, LSP bridge, and registry.

Produces a concise health report. Silently exits 0 when healthy.
Only outputs to stdout when issues are found.

Exit 0: all healthy
Exit 1: critical failures
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# ── Config ──────────────────────────────────────────────
HERMES_AGENT = Path(os.path.expanduser("~/.hermes/hermes-agent"))
CODE_INTEL_PY = HERMES_AGENT / "tools" / "code_intel.py"
LSP_BRIDGE_PY = HERMES_AGENT / "tools" / "lsp_bridge.py"

MONOREPO = Path(os.path.expanduser("~/GIT/AgentSelly/monorepo"))
TS_TARGET = None
if MONOREPO.exists():
    for candidate in sorted(MONOREPO.glob("apps/*/app/**/*.ts"), key=lambda p: p.stat().st_size):
        if candidate.stat().st_size < 500_000 and "node_modules" not in str(candidate) \
           and "test" not in candidate.name.lower() and "spec" not in candidate.name.lower():
            TS_TARGET = candidate
            break
    if not TS_TARGET:
        for candidate in sorted(MONOREPO.glob("packages/*/src/**/*.ts"), key=lambda p: p.stat().st_size):
            if candidate.stat().st_size < 500_000:
                TS_TARGET = candidate
                break

LOG_FILE = Path(os.path.expanduser("~/.hermes/logs/errors.log"))
CUTOFF = datetime.now() - timedelta(hours=6)
LSP_TIMEOUT = 15
VENV_PYTHON = HERMES_AGENT / "venv" / "bin" / "python3"

# ── Results ──────────────────────────────────────────────
_issues = []
_ok = []

def issue(severity: str, component: str, detail: str):
    _issues.append({"severity": severity, "component": component, "detail": detail})

def ok(component: str, detail: str = ""):
    _ok.append({"component": component, "detail": detail})


def timed(label: str, fn):
    t0 = time.perf_counter()
    result = fn()
    elapsed = (time.perf_counter() - t0) * 1000
    return result, elapsed


# ── Checks ──────────────────────────────────────────────

def check_file_integrity():
    for path, label in [(CODE_INTEL_PY, "code_intel.py"), (LSP_BRIDGE_PY, "lsp_bridge.py")]:
        if not path.exists():
            issue("critical", "files", f"{label} missing at {path}")
        else:
            size = path.stat().st_size
            if size < 1000:
                issue("critical", "files", f"{label} suspiciously small ({size} bytes)")
            else:
                ok("files", f"{label} OK ({size:,} bytes)")


def check_fast_tools():
    """Run tree-sitter tools (no LSP). Always completes in <1s."""
    os.chdir(str(HERMES_AGENT))
    sys.path.insert(0, str(HERMES_AGENT))

    from tools.code_intel import code_symbols_tool, code_search_tool, code_refactor_tool

    # code_symbols on Python
    r, ms = timed("code_symbols(py)",
        lambda: json.loads(code_symbols_tool(str(CODE_INTEL_PY), kind="function")))
    if isinstance(r, dict) and "error" not in r and r.get("symbol_count", 0) > 0:
        ok("code_symbols", f"{r['symbol_count']} symbols in {ms:.0f}ms (Python)")
    else:
        issue("critical", "code_symbols", f"FAILED: {r.get('error', str(r)[:120])} ({ms:.0f}ms)")

    # code_symbols on TypeScript
    if TS_TARGET and TS_TARGET.exists():
        r, ms = timed("code_symbols(ts)",
            lambda: json.loads(code_symbols_tool(str(TS_TARGET), kind="class")))
        if isinstance(r, dict) and "error" not in r:
            ok("code_symbols", f"{r.get('symbol_count', 0)} classes in {ms:.0f}ms (TypeScript)")
        else:
            issue("warning", "code_symbols", f"TS scan issue: {r.get('error', str(r)[:120])} ({ms:.0f}ms)")

    # code_search
    r, ms = timed("code_search(py)",
        lambda: json.loads(code_search_tool(str(CODE_INTEL_PY), preset="function_calls", pattern="json")))
    if isinstance(r, dict) and "error" not in r:
        ok("code_search", f"AST search OK in {ms:.0f}ms (Python)")
    else:
        issue("critical", "code_search", f"FAILED: {r.get('error', str(r)[:120])} ({ms:.0f}ms)")

    # code_search on TS
    if TS_TARGET and TS_TARGET.exists():
        r, ms = timed("code_search(ts)",
            lambda: json.loads(code_search_tool(str(TS_TARGET), preset="imports")))
        if isinstance(r, dict) and "error" not in r:
            ok("code_search", f"TS import search OK in {ms:.0f}ms")
        else:
            issue("warning", "code_search", f"TS search issue: {r.get('error', str(r)[:120])} ({ms:.0f}ms)")

    # code_refactor dry-run
    r, ms = timed("code_refactor(dry)",
        lambda: json.loads(code_refactor_tool(str(CODE_INTEL_PY), pattern="json.dumps", rewrite="json.dumps")))
    if isinstance(r, dict) and "error" not in r:
        ok("code_refactor", f"dry-run OK in {ms:.0f}ms")
    else:
        issue("critical", "code_refactor", f"FAILED: {r.get('error', str(r)[:120])} ({ms:.0f}ms)")


def _lsp_standalone_test(target_file: str, target_line: int) -> dict:
    """Run LSP goto-definition in a clean isolated subprocess.

    Uses venv python with a self-contained script to avoid import
    side effects from this process (stale module cache, open FDs, etc.).
    """
    # Pre-kill stale pylsp processes
    subprocess.run(["pkill", "-f", "[p]ylsp"], capture_output=True, timeout=2)

    script = f'''
import sys, os, json, time
HERMES = '{HERMES_AGENT}'
os.chdir(HERMES)
sys.path.insert(0, HERMES)

target = '{target_file}'
line = {target_line}

t0 = time.time()
from tools.lsp_bridge import LSPBridge, _find_workspace_root

root = _find_workspace_root(target)
bridge = LSPBridge(command='pylsp', args=[], root_uri=root, language_id='python')

result = {{}}
if bridge.ensure_initialized():
    locs = bridge.goto_definition(target, line - 1, 5)  # 0-based, col ~5
    elapsed = (time.time() - t0) * 1000
    result = {{
        "ok": True,
        "definition_count": len(locs or []),
        "elapsed_ms": int(elapsed),
    }}
else:
    result = {{"ok": False, "error": "LSP init failed"}}

bridge.shutdown()
print(json.dumps(result))
'''

    try:
        proc = subprocess.run(
            [str(VENV_PYTHON), "-c", script],
            capture_output=True, text=True,
            timeout=LSP_TIMEOUT,
            cwd=str(HERMES_AGENT),
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout.strip())
        return {"ok": False, "error": f"No output (rc={proc.returncode})", "stderr": proc.stderr[:200]}
    except subprocess.TimeoutExpired:
        subprocess.run(["pkill", "-9", "-f", "[p]ylsp"], capture_output=True, timeout=2)
        return {"ok": False, "error": "TimeoutExpired", "elapsed_ms": LSP_TIMEOUT * 1000}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def check_lsp():
    """Test LSP bridge via isolated standalone subprocess."""
    # code_definition on the LSP bridge file itself
    r = _lsp_standalone_test(str(CODE_INTEL_PY), 730)
    elapsed = r.get("elapsed_ms", 0)
    if r.get("ok") and r.get("definition_count", 0) > 0:
        ok("code_definition", f"LSP goto-def OK ({r['definition_count']} defs) in {elapsed}ms")
    elif r.get("ok"):
        issue("warning", "code_definition", f"LSP returned 0 definitions ({elapsed}ms)")
    elif "TimeoutExpired" in r.get("error", ""):
        issue("info", "code_definition", f"Timed out after {LSP_TIMEOUT}s")
    else:
        issue("warning", "code_definition", f"LSP failure: {r.get('error', '?')} ({elapsed}ms)")

    # code_references
    refs_script = f'''
import sys, os, json, time
HERMES = '{HERMES_AGENT}'
os.chdir(HERMES)
sys.path.insert(0, HERMES)

target = '{CODE_INTEL_PY}'
line = 730

t0 = time.time()
from tools.lsp_bridge import LSPBridge, _find_workspace_root

root = _find_workspace_root(target)
bridge = LSPBridge(command='pylsp', args=[], root_uri=root, language_id='python')

result = {{}}
if bridge.ensure_initialized():
    locs = bridge.find_references(target, line - 1, 5, True)
    elapsed = (time.time() - t0) * 1000
    result = {{
        "ok": True,
        "reference_count": len(locs or []),
        "elapsed_ms": int(elapsed),
    }}
else:
    result = {{"ok": False, "error": "LSP init failed"}}

bridge.shutdown()
print(json.dumps(result))
'''

    try:
        proc = subprocess.run(
            [str(VENV_PYTHON), "-c", refs_script],
            capture_output=True, text=True,
            timeout=LSP_TIMEOUT + 10,  # references can take longer
            cwd=str(HERMES_AGENT),
        )
        if proc.returncode == 0 and proc.stdout.strip():
            r = json.loads(proc.stdout.strip())
            elapsed = r.get("elapsed_ms", 0)
            ref_count = r.get("reference_count", 0)
            if r.get("ok") and ref_count > 0:
                ok("code_references", f"LSP refs OK ({ref_count} refs) in {elapsed}ms")
            elif r.get("ok"):
                issue("warning", "code_references", f"LSP returned 0 refs ({elapsed}ms)")
            else:
                issue("warning", "code_references", f"LSP refs failure: {r.get('error', '?')}")
        else:
            issue("warning", "code_references", f"Subprocess failed (rc={proc.returncode})")
    except subprocess.TimeoutExpired:
        subprocess.run(["pkill", "-9", "-f", "[p]ylsp"], capture_output=True, timeout=2)
        issue("warning", "code_references", f"Timed out after {LSP_TIMEOUT + 10}s")
    except Exception as e:
        issue("warning", "code_references", str(e)[:150])


def check_registry():
    """Verify code_intel tools are in the tool registry."""
    os.chdir(str(HERMES_AGENT))
    sys.path.insert(0, str(HERMES_AGENT))

    try:
        from model_tools import get_tool_definitions
        tools = get_tool_definitions(enabled_toolsets=["code_intel"])
        names = {t["function"]["name"] for t in tools}
    except Exception as e:
        issue("warning", "registry", f"Cannot query registry: {e}")
        return

    expected = {"code_symbols", "code_search", "code_refactor", "code_definition", "code_references"}
    present = expected & names
    missing = expected - names

    ok("registry", f"{len(present)}/{len(expected)} tools active: {', '.join(sorted(present))}")
    if missing:
        issue("critical", "registry", f"MISSING TOOLS: {', '.join(sorted(missing))}")
    if len(present) < 5:
        issue("warning", "registry", f"Expected 5 tools, found {len(present)}")


ERROR_PATTERNS = [
    (re.compile(r"LSPBridge.*has no attribute (\w+)", re.I), "attribute_error", "LSPBridge missing method"),
    (re.compile(r"code_\w+ dispatch error: (.+)", re.I), "tool_dispatch", "Tool dispatch crash"),
    (re.compile(r"Failed to persist symbol cache: (.+)", re.I), "cache_persist", "Symbol cache persist failure"),
    (re.compile(r"\[ERROR\].*lsp_bridge: (.+)", re.I), "lsp_error", "LSP bridge error"),
    (re.compile(r"\[WARNING\].*code_intel: (.+)", re.I), "code_intel_warn", "code_intel warning"),
    (re.compile(r"textDocument/diagnostic not supported", re.I), "pull_diag_unsupported", "Pull diagnostics unsupported"),
    (re.compile(r"No LSP bridge available for language=(\w+)", re.I), "no_lsp_lang", "No LSP for language"),
    (re.compile(r"tree.sitter.*Impossible pattern", re.I), "ts_query", "Impossible tree-sitter query"),
    (re.compile(r"SgNode.*Error|ast.grep.*error", re.I), "ast_grep", "ast-grep error"),
    (re.compile(r"TypeError.*tree_sitter", re.I), "ts_type_error", "tree-sitter type mismatch"),
    (re.compile(r"code_\w+.*timeout|timed out", re.I), "timeout", "Tool timeout"),
]


def scan_logs():
    if not LOG_FILE.exists():
        return
    try:
        mtime = datetime.fromtimestamp(LOG_FILE.stat().st_mtime)
        if mtime < CUTOFF:
            return
    except OSError:
        return
    try:
        lines = LOG_FILE.read_text(errors="replace").splitlines()
    except OSError:
        return

    findings = []
    for line in lines[-3000:]:
        for pattern, tag, desc in ERROR_PATTERNS:
            m = pattern.search(line)
            if m:
                detail = m.group(1) if m.lastindex else ""
                findings.append({"tag": tag, "desc": desc, "detail": detail[:100]})

    seen = set()
    unique = []
    for f in findings:
        key = (f["tag"], f["detail"][:80])
        if key not in seen:
            seen.add(key)
            unique.append(f)

    if unique:
        non_expected = [f for f in unique if f["tag"] != "pull_diag_unsupported"]
        if non_expected:
            issue("warning", "log_scan", f"{len(non_expected)} recent errors")
            for f in non_expected:
                detail = f" ({f['detail']})" if f["detail"] else ""
                issue("warning", f"log:{f['tag']}", f"{f['desc']}{detail}")


# ── Main ────────────────────────────────────────────────
def main():
    t0 = time.perf_counter()

    check_file_integrity()
    check_fast_tools()
    check_lsp()
    check_registry()
    scan_logs()

    total_ms = (time.perf_counter() - t0) * 1000
    n_critical = sum(1 for i in _issues if i["severity"] == "critical")
    n_warning = sum(1 for i in _issues if i["severity"] == "warning")
    n_info = sum(1 for i in _issues if i["severity"] == "info")

    # Silent when fully healthy
    if n_critical == 0 and n_warning == 0:
        print(f"✅ HEALTHY — {len(_ok)} checks passed ({total_ms:.0f}ms)")
        return 0

    header = f"🔬 code_intel health check — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"

    if n_critical > 0:
        header += f"🔴 DEGRADED — {n_critical} critical, {n_warning} warnings"
    else:
        header += f"🟡 ATTENTION — {n_warning} warnings"

    print(header)
    if _issues:
        print()
        for i in _issues:
            icon = {"critical": "🔴", "warning": "🟡", "info": "ℹ️ "}.get(i["severity"], "  ")
            print(f"  {icon} [{i['component']}] {i['detail']}")

    if _ok:
        print(f"\n  ✅ {len(_ok)} checks passed")

    print(f"\n  Total: {total_ms:.0f}ms | passed={len(_ok)} "
          f"issues={len(_issues)} (critical={n_critical}, warning={n_warning}, info={n_info})")

    return 1 if n_critical > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
