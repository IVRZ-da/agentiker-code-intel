"""Tests for the 4 new custom code tools (wave 4 of test migration).

Tools tested (1 normal + 1–2 error cases each = 8+ tests):
  • code_diagram_symbol_tool    — Mermaid call graph via LSP bridge
  • code_explain_tool           — Structured symbol explanation (capsule + complexity)
  • code_docstring_generate_tool — Docstring template from function signature
  • code_dependency_risk_tool   — Dependency health via ImportGraph

External deps are mocked via unittest.mock.patch:
  • bridge (LSP)        → code_intel.lsp.bridge.get_lsp_manager
  • Internal helpers    → code_intel.code_tools.code_capsule_tool / code_complexity_tool
  • ImportGraph          → code_intel._import_graph.ImportGraph
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import code_intel.tools.explain_extractor as _explain_extractor
import pytest
from code_intel.code_tools import (
    code_dependency_risk_tool,
    code_diagram_symbol_tool,
    code_docstring_generate_tool,
    code_explain_tool,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pyfile(code: str) -> str:
    """Create a temporary .py file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".py", prefix="test_ct_", text=True)
    with open(fd, "w", encoding="utf-8") as f:
        f.write(code)
    return path


def _parse(result: str) -> dict:
    """Parse fmt_ok/fmt_err output (mocked to JSON) back to a dict."""
    return json.loads(result)


# ---------------------------------------------------------------------------
# Fixture: ensure _fmt mock has _strip_ansi for code_explain_tool
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ensure_strip_ansi():
    """Add _strip_ansi to the conftest _fmt mock so code_explain_tool can import it."""
    import sys
    fmt_mock = sys.modules.get("code_intel._fmt")
    if fmt_mock is not None and not hasattr(fmt_mock, "_strip_ansi"):
        fmt_mock._strip_ansi = lambda s: s  # noqa: E731
    yield


# ===================================================================
# 1. code_diagram_symbol_tool
# ===================================================================


class TestCodeDiagramSymbolTool:
    """Mermaid call graph via LSP bridge / AST fallback."""

    def test_lsp_returns_call_hierarchy(self):
        """LSP bridge provides incoming + outgoing calls → valid Mermaid diagram."""
        path = _make_pyfile("def greet(name: str) -> str:\n    return f'Hi {name}'\n")
        try:
            # ── Mock LSP bridge ──────────────────────────────────────
            bridge = MagicMock()
            bridge.ensure_initialized.return_value = True
            bridge.command = "pyright"
            bridge.incoming_calls.return_value = [
                {"name": "main", "uri": f"file://{path}"},
            ]
            bridge.outgoing_calls.return_value = [
                {"name": "format_greeting", "uri": f"file://{path}"},
                {"name": "log", "uri": f"file://{path}"},
            ]

            mgr = MagicMock()
            mgr.get_bridge.return_value = bridge

            # get_lsp_manager is imported *inside* the function body,
            # so we patch at its source module
            with patch("code_intel.lsp.bridge.get_lsp_manager", return_value=mgr):
                result = code_diagram_symbol_tool(
                    path=path, line=1, character=5, depth=2, language="python",
                )

            data = _parse(result)
            assert data["status"] == "ok"
            mermaid: str = data["mermaid"]

            # Mermaid header
            assert "graph LR" in mermaid

            # Symbol name extracted from line text
            assert "greet" in mermaid

            # Caller edge
            assert "main" in mermaid
            # Callee edges
            assert "format_greeting" in mermaid
            assert "log" in mermaid

            # Provided metadata
            assert data["depth"] == 2
            assert data["lsp_server"] == "pyright"
            assert data["symbol"] == "greet"
        finally:
            Path(path).unlink(missing_ok=True)

    def test_error_path_not_found(self):
        """Non-existent file path → error."""
        result = code_diagram_symbol_tool(
            path="/nonexistent/dead_beef.py", line=1,
        )
        data = _parse(result)
        assert data["status"] == "error"
        assert "not found" in data["error"].lower()


# ===================================================================
# 2. code_explain_tool
# ===================================================================


class TestCodeExplainTool:
    """Structured symbol explanation (capsule + complexity)."""

    def test_merges_capsule_and_complexity(self):
        """code_capsule_tool + code_complexity_tool data → structured output."""
        path = _make_pyfile("def fib(n: int) -> int:\n    return n if n < 2 else fib(n-1)+fib(n-2)\n")
        try:
            capsule_data = {
                "symbol": "fib",
                "kind": "function",
                "signature": "def fib(n: int) -> int",
                "doc_preview": "Recursive fibonacci.",
                "definition": f"{path}:1",
                "reference_count": 5,
                "files_affected": 3,
                "top_references": ["test_fib.py"],
            }
            complexity_data = {
                "total": 3,
                "rank": "A",
                "breakdown": {
                    "base": 1,
                    "branches": 1,
                    "loops": 0,
                    "exceptions": 0,
                    "early_returns": 1,
                },
            }

            with patch.object(
                _explain_extractor, "code_capsule_tool",
                return_value=json.dumps(capsule_data),
            ):
                with patch.object(
                    _explain_extractor, "code_complexity_tool",
                    return_value=json.dumps(complexity_data),
                ):
                    result = code_explain_tool(
                        path=path, line=1, language="python",
                    )

            data = _parse(result)
            assert data["status"] == "ok"
            assert data["symbol"] == "fib"
            assert data["kind"] == "function"
            assert data["signature"] == "def fib(n: int) -> int"
            assert data["doc_preview"] == "Recursive fibonacci."
            assert data["reference_count"] == 5
            assert data["files_affected"] == 3
            assert data["top_references"] == ["test_fib.py"]

            comp = data["complexity"]
            assert comp["total"] == 3
            assert comp["rank"] == "A"
            assert comp["breakdown"]["base"] == 1
            assert comp["breakdown"]["branches"] == 1
            assert comp["breakdown"]["early_returns"] == 1
        finally:
            Path(path).unlink(missing_ok=True)

    def test_error_path_not_found(self):
        """Non-existent file path → error."""
        result = code_explain_tool(path="/nonexistent/missing.py", line=1)
        data = _parse(result)
        assert data["status"] == "error"
        assert "not found" in data["error"].lower()


# ===================================================================
# 3. code_docstring_generate_tool
# ===================================================================


class TestCodeDocstringGenerateTool:
    """Docstring template from function signature (regex-based)."""

    def test_google_style_with_params(self):
        """Function with typed params → Google-style docstring template."""
        path = _make_pyfile(
            "def connect(host: str, port: int = 8080) -> bool:\n"
            '    """Connect to a remote host."""\n'
            "    return True\n"
        )
        try:
            result = code_docstring_generate_tool(path=path, line=1, style="google")
            data = _parse(result)
            assert data["status"] == "ok"
            assert data["function"] == "connect"
            assert data["return_type"] == "bool"
            assert len(data["parameters"]) == 2
            assert data["parameters"][0] == {"name": "host", "type": "str"}
            assert data["parameters"][1] == {"name": "port", "type": "int"}
            assert data["style"] == "google"

            doc = data["docstring"]
            assert "Args:" in doc
            assert "host (str):" in doc
            assert "port (int):" in doc
            assert "Returns:" in doc
            assert "bool:" in doc
        finally:
            Path(path).unlink(missing_ok=True)

    def test_numpy_style(self):
        """NumPy-style docstring generation."""
        path = _make_pyfile("def parse(text: str) -> list:\n    return []\n")
        try:
            result = code_docstring_generate_tool(path=path, line=1, style="numpy")
            data = _parse(result)
            assert data["status"] == "ok"
            assert data["function"] == "parse"

            doc = data["docstring"]
            assert "Parameters" in doc
            assert "----------" in doc
            assert "text : str" in doc
            assert "Returns" in doc
            assert "-------" in doc
            assert "list" in doc
        finally:
            Path(path).unlink(missing_ok=True)

    def test_error_path_not_found(self):
        """Non-existent path → error."""
        result = code_docstring_generate_tool(path="/invalid/path.py", line=1)
        data = _parse(result)
        assert data["status"] == "error"
        assert "not found" in data["error"].lower()

    def test_error_no_function_at_line(self):
        """File without a function definition at/near the given line → error."""
        path = _make_pyfile("# just a comment\nx = 42\n")
        try:
            result = code_docstring_generate_tool(path=path, line=1)
            data = _parse(result)
            assert data["status"] == "error"
            assert "No function" in data["error"]
        finally:
            Path(path).unlink(missing_ok=True)


# ===================================================================
# 4. code_dependency_risk_tool
# ===================================================================


class TestCodeDependencyRiskTool:
    """Dependency health analysis via ImportGraph."""

    def test_clean_project_low_risk(self):
        """No cycles, few imports → low risk score."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "main.py").write_text("import os\n")


            graph = MagicMock()
            graph.find_cycles.return_value = []
            graph.find_hot_paths.return_value = []
            graph.graph = {}
            graph.files = [Path(tmpdir) / "main.py"]

            # Patch ImportGraph directly in the function's __globals__
            # (function lives in tools/export.py but its globals may differ
            #  from the module's __dict__ due to conftest mocking)
            _orig_ig = code_dependency_risk_tool.__globals__.get("ImportGraph")
            code_dependency_risk_tool.__globals__["ImportGraph"] = MagicMock(return_value=graph)
            try:
                result = code_dependency_risk_tool(path=tmpdir)
            finally:
                code_dependency_risk_tool.__globals__["ImportGraph"] = _orig_ig

            data = _parse(result)
            assert data["status"] == "ok"
            assert data["risk_score"] < 3
            assert data["risk_level"] == "low"
            assert data["files_scanned"] == 1
            assert data["import_edges"] == 0
            assert data["factors"] == []

    def test_project_with_cycles_medium_risk(self):
        """Cyclic dependencies and hot paths → medium/high risk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "main.py").write_text("import a\n")


            graph = MagicMock()
            # 3 cycles → medium severity (n_cycles > 2)
            graph.find_cycles.return_value = [
                ("a.py", "b.py", "a.py"),
                ("c.py", "d.py", "e.py", "c.py"),
                ("x.py", "y.py", "x.py"),
            ]
            # Hot path with high caller count
            graph.find_hot_paths.return_value = [
                {"file": "core.py", "caller_count": 30},
            ]
            graph.graph = {
                ("a.py", "b.py"): None,
                ("b.py", "a.py"): None,
            }
            graph.files = [Path(tmpdir) / "main.py", Path(tmpdir) / "a.py"]

            # Patch ImportGraph directly in the function's __globals__
            _orig_ig = code_dependency_risk_tool.__globals__.get("ImportGraph")
            code_dependency_risk_tool.__globals__["ImportGraph"] = MagicMock(return_value=graph)
            try:
                result = code_dependency_risk_tool(path=tmpdir)
            finally:
                code_dependency_risk_tool.__globals__["ImportGraph"] = _orig_ig

            data = _parse(result)
            assert data["status"] == "ok"
            assert data["risk_score"] >= 1
            assert "factors" in data
            factor_names = [f["factor"] for f in data["factors"]]
            assert "cyclic_dependencies" in factor_names

    def test_error_path_not_found(self):
        """Non-existent path → error."""
        result = code_dependency_risk_tool(path="/nonexistent/project")
        data = _parse(result)
        assert data["status"] == "error"
        assert "not found" in data["error"].lower()
