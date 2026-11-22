"""Regression: Write-Guard fail-closed (Bughunt Runde 5, F5).

``code_batch_refactor`` prüfte vor dem Schreiben jeden Zielpfad — aber nur über
die Trefferliste des Vorab-Scans. Lieferte der Scan ``None``/``[]``, lief die
Schleife nie, und ``_ast_grep_apply`` schrieb ungeprüft weiter.
"""

from __future__ import annotations

import pytest

from code_intel.tools import batch


def test_scan_returns_none_when_ast_grep_fails(monkeypatch):
    class _Result:
        returncode = 2
        stdout = ""
        stderr = "error: unknown flag"

    monkeypatch.setattr(batch.subprocess, "run", lambda *a, **kw: _Result())
    assert batch._ast_grep_scan("/usr/bin/ast-grep", ".", "x", "*.py", "python") is None


def test_apply_is_not_called_when_scan_fails(monkeypatch, tmp_path):
    target = tmp_path / "mod.py"
    target.write_text("old_name(1)\n", encoding="utf-8")

    called = {"apply": 0}

    monkeypatch.setattr(batch, "_find_ast_grep", lambda: "/usr/bin/ast-grep")
    monkeypatch.setattr(batch, "_ast_grep_scan", lambda *a, **kw: None)

    def _boom(*a, **kw):
        called["apply"] += 1
        return []

    monkeypatch.setattr(batch, "_ast_grep_apply", _boom)

    out = batch.code_batch_refactor_tool(
        path=str(tmp_path),
        pattern="old_name($A)",
        rewrite="new_name($A)",
        file_glob="*.py",
        dry_run=False,
        language="python",
    )
    assert called["apply"] == 0
    assert "fail closed" in out
    assert target.read_text(encoding="utf-8") == "old_name(1)\n"


@pytest.mark.skipif(batch._find_ast_grep() is None, reason="ast-grep CLI fehlt")
def test_real_apply_writes_through_guard(tmp_path):
    """Positivfall: mit echtem ast-grep wird geschrieben (F4 war nicht nur Theorie)."""
    target = tmp_path / "mod.py"
    target.write_text("old_name(1)\nold_name(2)\n", encoding="utf-8")
    batch.code_batch_refactor_tool(
        path=str(tmp_path),
        pattern="old_name($A)",
        rewrite="new_name($A)",
        file_glob="*.py",
        dry_run=False,
        language="python",
    )
    assert target.read_text(encoding="utf-8") == "new_name(1)\nnew_name(2)\n"
