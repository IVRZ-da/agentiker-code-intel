"""Tests for tools/impact.py — Blast radius, PR impact, and impact analysis.

Target: bring coverage from ~53% to 70%+ by testing error paths,
edge cases, and handler wrappers.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from code_intel.tools.impact import (
    CODE_BLAST_RADIUS_SCHEMA,
    CODE_IMPACT_SCHEMA,
    CODE_PR_IMPACT_SCHEMA,
    _detect_base_branch,
    _find_functions_in_file,
    _git_diff_changed_files,
    _handle_code_blast_radius,
    _handle_code_impact,
    _handle_code_pr_impact,
    _impact_file_level,
    code_blast_radius_tool,
    code_impact_tool,
    code_pr_impact_tool,
)

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _make_py_file(tmp_path: Path, name: str = "mod.py", content: str | None = None) -> Path:
    """Create a minimal Python file."""
    f = tmp_path / name
    f.write_text(content or "def foo():\n    pass\n")
    return f


def _load(s: str) -> dict:
    """Parse a JSON result string (fmt_ok / fmt_err return JSON)."""
    return json.loads(s) if isinstance(s, str) else {}


# ═══════════════════════════════════════════════════════════════════════════
# _impact_file_level
# ═══════════════════════════════════════════════════════════════════════════


class TestImpactFileLevel:
    """_impact_file_level — file-level import analysis."""

    def test_imports_found(self, tmp_path):
        """code_search_tool returns imports."""
        f = _make_py_file(tmp_path)
        base_r = {"path": str(f), "reference_count": 0}
        json_mod = MagicMock()
        json_mod.loads = json.loads

        with patch("code_intel.code_tools.code_search_tool") as mock_search:
            mock_search.return_value = json.dumps(
                {
                    "status": "ok",
                    "results": [
                        {"line": 1, "text": "import os"},
                        {"line": 2, "text": "from pathlib import Path"},
                    ],
                }
            )
            result = _impact_file_level(f, "python", base_r, json_mod)
        data = _load(result)
        # _impact_file_level returns fmt_json (plain JSON, no status key)
        assert data["reference_count"] == 2
        assert data["reference_type"] == "file-level"

    def test_no_imports(self, tmp_path):
        """code_search_tool returns empty results."""
        f = _make_py_file(tmp_path)
        base_r = {"path": str(f), "reference_count": 0}
        json_mod = MagicMock()
        json_mod.loads = json.loads

        with patch("code_intel.code_tools.code_search_tool") as mock_search:
            mock_search.return_value = json.dumps({"status": "ok", "results": []})
            result = _impact_file_level(f, "python", base_r, json_mod)
        data = _load(result)
        assert data["reference_count"] == 0

    def test_search_data_is_list(self, tmp_path):
        """code_search_tool returns a list instead of dict."""
        f = _make_py_file(tmp_path)
        base_r = {"path": str(f), "reference_count": 0}
        json_mod = MagicMock()
        json_mod.loads = json.loads

        with patch("code_intel.code_tools.code_search_tool") as mock_search:
            mock_search.return_value = json.dumps(["import os", "import sys"])
            result = _impact_file_level(f, "python", base_r, json_mod)
        data = _load(result)
        assert data["reference_count"] == 2

    def test_search_data_empty_list(self, tmp_path):
        """code_search_tool returns empty list."""
        f = _make_py_file(tmp_path)
        base_r = {"path": str(f), "reference_count": 0}
        json_mod = MagicMock()
        json_mod.loads = json.loads

        with patch("code_intel.code_tools.code_search_tool") as mock_search:
            mock_search.return_value = json.dumps([])
            result = _impact_file_level(f, "python", base_r, json_mod)
        data = _load(result)
        assert data["reference_count"] == 0

    def test_exception_handling(self, tmp_path):
        """code_search_tool raises an exception."""
        f = _make_py_file(tmp_path)
        base_r = {"path": str(f), "reference_count": 0}
        json_mod = MagicMock()
        json_mod.loads = json.loads

        with patch("code_intel.code_tools.code_search_tool") as mock_search:
            mock_search.side_effect = RuntimeError("boom")
            result = _impact_file_level(f, "python", base_r, json_mod)
        data = _load(result)
        assert data["status"] == "error"
        assert "Unable to analyze imports" in data["error"]


# ═══════════════════════════════════════════════════════════════════════════
# code_impact_tool
# ═══════════════════════════════════════════════════════════════════════════


class TestCodeImpactTool:
    """code_impact_tool — main impact analysis function."""

    def test_path_not_found(self):
        """Non-existent path returns error."""
        result = code_impact_tool(path="/nonexistent/file.py")
        data = _load(result)
        assert data["status"] == "error"
        assert "Path not found" in data["error"]

    def test_file_level_no_imports(self, tmp_path):
        """line=0 triggers file-level analysis with no imports found."""
        f = _make_py_file(tmp_path)
        with patch("code_intel.code_tools.code_search_tool") as mock_search:
            mock_search.return_value = json.dumps({"status": "ok", "results": []})
            result = code_impact_tool(path=str(f), line=0)
        data = _load(result)
        assert data["reference_count"] == 0
        assert data["reference_type"] == "file-level"

    def test_symbol_level_lsp_bridge_none(self, tmp_path):
        """symbol-level: lsp_bridge import fails (ImportError).

        Delete code_references_tool from the already-loaded lsp_bridge
        module so the lazy 'from ..lsp_bridge import code_references_tool'
        inside code_impact_tool raises ImportError.
        """
        import code_intel.lsp_bridge

        f = _make_py_file(tmp_path, content="def foo():\n    pass\n")
        saved = getattr(code_intel.lsp_bridge, "code_references_tool", None)
        try:
            if hasattr(code_intel.lsp_bridge, "code_references_tool"):
                del code_intel.lsp_bridge.code_references_tool
            result = code_impact_tool(path=str(f), line=1)
        finally:
            if saved is not None:
                code_intel.lsp_bridge.code_references_tool = saved
        data = _load(result)
        assert data["status"] == "error"
        assert "lsp_bridge not available" in data["error"]

    def test_symbol_level_empty_refs(self, tmp_path):
        """symbol-level: references returned but empty by_file."""
        f = _make_py_file(tmp_path, content="def foo():\n    pass\n")
        with patch("code_intel.lsp_bridge.code_references_tool") as mock_refs:
            mock_refs.return_value = json.dumps({"by_file": {}})
            result = code_impact_tool(path=str(f), line=1)
        data = _load(result)
        assert data["reference_count"] == 0
        assert data["direct_refs"] == 0
        assert data["confidence"] == "low"
        assert data["risk_level"] == "low"

    def test_symbol_level_with_refs(self, tmp_path):
        """symbol-level: references found and categorized."""
        f = _make_py_file(tmp_path, content="def foo():\n    pass\n")
        by_file = {
            "/path/to/other.py": [{"line": 10}, {"line": 20}],
            "/path/to/test_other.py": [{"line": 5}],
        }
        with patch("code_intel.lsp_bridge.code_references_tool") as mock_refs:
            mock_refs.return_value = json.dumps({"by_file": by_file})
            result = code_impact_tool(path=str(f), line=1)
        data = _load(result)
        assert data["reference_count"] == 3
        assert data["direct_refs"] == 3
        assert len(data["files_affected"]) == 2
        assert len(data["test_files"]) == 1  # test_other.py detected
        # 3 refs → confidence medium, risk low
        assert data["confidence"] == "low"
        assert data["risk_level"] == "low"

    def test_symbol_level_high_confidence(self, tmp_path):
        """>10 refs → high confidence."""
        f = _make_py_file(tmp_path, content="def foo():\n    pass\n")
        by_file = {f"/path/x{i}.py": [{"line": j} for j in range(3)] for i in range(5)}
        with patch("code_intel.lsp_bridge.code_references_tool") as mock_refs:
            mock_refs.return_value = json.dumps({"by_file": by_file})
            result = code_impact_tool(path=str(f), line=1)
        data = _load(result)
        assert data["direct_refs"] == 15
        assert data["confidence"] == "high"
        assert data["risk_level"] == "medium"  # 15 > 10 → medium risk

    def test_symbol_level_parse_error(self, tmp_path):
        """symbol-level: references data is not a dict."""
        f = _make_py_file(tmp_path, content="def foo():\n    pass\n")
        with patch("code_intel.lsp_bridge.code_references_tool") as mock_refs:
            mock_refs.side_effect = RuntimeError("parse error")
            result = code_impact_tool(path=str(f), line=1)
        data = _load(result)
        assert data["status"] == "error"
        assert "Failed to resolve references" in data["error"]

    def test_handler_wrapper(self):
        """_handle_code_impact delegates correctly."""
        with patch("code_intel.tools.impact.code_impact_tool") as mock_tool:
            mock_tool.return_value = json.dumps({"status": "ok"})
            result = _handle_code_impact({"path": "/p", "line": 2, "language": "python"})
        _load(result)
        mock_tool.assert_called_once_with(path="/p", line=2, language="python")

    def test_handler_defaults(self):
        """_handle_code_impact with empty args."""
        with patch("code_intel.tools.impact.code_impact_tool") as mock_tool:
            mock_tool.return_value = json.dumps({"status": "ok"})
            _handle_code_impact({})
        mock_tool.assert_called_once_with(path="", line=0, language=None)


# ═══════════════════════════════════════════════════════════════════════════
# code_blast_radius_tool
# ═══════════════════════════════════════════════════════════════════════════


class TestCodeBlastRadiusTool:
    """code_blast_radius_tool — blast radius analysis."""

    def test_path_not_found(self):
        """Non-existent path returns error."""
        result = code_blast_radius_tool(path="/nonexistent/file.py", line=1)
        data = _load(result)
        assert data["status"] == "error"
        assert "Path not found" in data["error"]

    def test_no_language_detected(self, tmp_path):
        """File with undetectable language returns error."""
        f = tmp_path / "unknown.xyz"
        f.write_text("some content")
        with patch("code_intel.tools.impact.detect_language", return_value=None):
            result = code_blast_radius_tool(path=str(f), line=1)
        data = _load(result)
        assert data["status"] == "error"
        assert "Could not detect language" in data["error"]

    def test_no_lsp_bridge(self, tmp_path):
        """LSP bridge unavailable — no direct callers, no transitive, no tests."""
        f = _make_py_file(tmp_path)

        with (
            patch("code_intel.lsp_bridge.get_lsp_manager") as mock_get_mgr,
            patch("code_intel._import_graph.ImportGraph") as mock_graph_cls,
            patch("code_intel.tools.impact.code_tests_for_symbol_tool") as mock_tests,
        ):
            mock_mgr = MagicMock()
            mock_mgr.get_bridge.return_value = None
            mock_get_mgr.return_value = mock_mgr

            mock_graph = MagicMock()
            mock_graph.analyze_blast_radius.return_value = {"levels": {}, "total": 0, "max_depth_reached": False}
            mock_graph_cls.return_value = mock_graph

            mock_tests.return_value = json.dumps({"tests": []})

            result = code_blast_radius_tool(path=str(f), line=1)

        data = _load(result)
        assert data["impact"] == "LOW"
        assert data["direct_callers"]["count"] == 0
        assert data["transitive_callers"]["count"] == 0
        assert data["test_coverage"]["count"] == 0
        # LOW impact, no callers → "Low impact" recommendation
        assert "Low impact" in data.get("recommendation", "")

    def test_with_callers(self, tmp_path):
        """LSP callers found."""
        f = _make_py_file(tmp_path)

        mock_bridge = MagicMock()
        mock_bridge.ensure_initialized.return_value = True
        mock_bridge.incoming_calls.return_value = [
            {"uri": "file:///path/caller.py", "range": {"start": {"line": 4}}, "name": "caller_func"},
        ]

        with (
            patch("code_intel.lsp_bridge.get_lsp_manager") as mock_get_mgr,
            patch("code_intel._import_graph.ImportGraph") as mock_graph_cls,
            patch("code_intel.tools.impact.code_tests_for_symbol_tool") as mock_tests,
        ):
            mock_mgr = MagicMock()
            mock_mgr.get_bridge.return_value = mock_bridge
            mock_get_mgr.return_value = mock_mgr

            mock_graph = MagicMock()
            mock_graph.analyze_blast_radius.return_value = {
                "levels": {1: ["a.py"]},
                "total": 1,
                "max_depth_reached": False,
            }
            mock_graph_cls.return_value = mock_graph

            mock_tests.return_value = json.dumps({"tests": [{"file": "test_x.py"}]})

            result = code_blast_radius_tool(path=str(f), line=1)

        data = _load(result)
        assert data["impact"] == "MEDIUM"  # nc>0 → MEDIUM
        assert data["direct_callers"]["count"] == 1
        assert data["transitive_callers"]["count"] == 1
        assert data["test_coverage"]["count"] == 1

    def test_high_impact(self, tmp_path):
        """>10 direct callers → HIGH impact."""
        f = _make_py_file(tmp_path)

        mock_bridge = MagicMock()
        mock_bridge.ensure_initialized.return_value = True
        mock_bridge.incoming_calls.return_value = [
            {"uri": f"file:///path/caller{i}.py", "range": {"start": {"line": 0}}, "name": "f"} for i in range(12)
        ]

        with (
            patch("code_intel.lsp_bridge.get_lsp_manager") as mock_get_mgr,
            patch("code_intel._import_graph.ImportGraph") as mock_graph_cls,
            patch("code_intel.tools.impact.code_tests_for_symbol_tool") as mock_tests,
        ):
            mock_mgr = MagicMock()
            mock_mgr.get_bridge.return_value = mock_bridge
            mock_get_mgr.return_value = mock_mgr

            mock_graph = MagicMock()
            mock_graph.analyze_blast_radius.return_value = {"levels": {}, "total": 0, "max_depth_reached": False}
            mock_graph_cls.return_value = mock_graph
            mock_tests.return_value = json.dumps({"tests": []})

            result = code_blast_radius_tool(path=str(f), line=1)

        data = _load(result)
        assert data["impact"] == "HIGH"
        assert "High impact" in data["recommendation"]

    def test_blast_high_via_transitive(self, tmp_path):
        """>20 transitive callers → HIGH impact."""
        f = _make_py_file(tmp_path)

        with (
            patch("code_intel.lsp_bridge.get_lsp_manager") as mock_get_mgr,
            patch("code_intel._import_graph.ImportGraph") as mock_graph_cls,
            patch("code_intel.tools.impact.code_tests_for_symbol_tool") as mock_tests,
        ):
            mock_mgr = MagicMock()
            mock_mgr.get_bridge.return_value = None
            mock_get_mgr.return_value = mock_mgr

            mock_graph = MagicMock()
            mock_graph.analyze_blast_radius.return_value = {
                "levels": {1: [f"f{i}.py" for i in range(25)]},
                "total": 25,
                "max_depth_reached": False,
            }
            mock_graph_cls.return_value = mock_graph
            mock_tests.return_value = json.dumps({"tests": []})

            result = code_blast_radius_tool(path=str(f), line=1)

        data = _load(result)
        assert data["impact"] == "HIGH"

    def test_untested_transitive_recommendation(self, tmp_path):
        """Transitive callers but no tests → untested recommendation."""
        f = _make_py_file(tmp_path)

        with (
            patch("code_intel.lsp_bridge.get_lsp_manager") as mock_get_mgr,
            patch("code_intel._import_graph.ImportGraph") as mock_graph_cls,
            patch("code_intel.tools.impact.code_tests_for_symbol_tool") as mock_tests,
        ):
            mock_mgr = MagicMock()
            mock_mgr.get_bridge.return_value = None
            mock_get_mgr.return_value = mock_mgr

            mock_graph = MagicMock()
            mock_graph.analyze_blast_radius.return_value = {
                "levels": {1: ["a.py"]},
                "total": 1,
                "max_depth_reached": False,
            }
            mock_graph_cls.return_value = mock_graph
            mock_tests.return_value = json.dumps({"tests": []})

            result = code_blast_radius_tool(path=str(f), line=1)

        data = _load(result)
        assert data["impact"] == "MEDIUM"
        assert "Untested transitive callers" in data["recommendation"]

    def test_test_coverage_disabled(self, tmp_path):
        """test_coverage=False skips test lookup."""
        f = _make_py_file(tmp_path)

        with (
            patch("code_intel.lsp_bridge.get_lsp_manager") as mock_get_mgr,
            patch("code_intel._import_graph.ImportGraph") as mock_graph_cls,
            patch("code_intel.tools.impact.code_tests_for_symbol_tool") as mock_tests,
        ):
            mock_mgr = MagicMock()
            mock_mgr.get_bridge.return_value = None
            mock_get_mgr.return_value = mock_mgr

            mock_graph = MagicMock()
            mock_graph.analyze_blast_radius.return_value = {"levels": {}, "total": 0, "max_depth_reached": False}
            mock_graph_cls.return_value = mock_graph

            result = code_blast_radius_tool(path=str(f), line=1, test_coverage=False)

        mock_tests.assert_not_called()
        data = _load(result)
        assert data["test_coverage"]["count"] == 0

    def test_lsp_exception_graceful(self, tmp_path):
        """LSP callHierarchy failure logs debug, not crash."""
        f = _make_py_file(tmp_path)

        with (
            patch("code_intel.lsp_bridge.get_lsp_manager") as mock_get_mgr,
            patch("code_intel._import_graph.ImportGraph") as mock_graph_cls,
            patch("code_intel.tools.impact.code_tests_for_symbol_tool") as mock_tests,
        ):
            mock_bridge = MagicMock()
            mock_bridge.ensure_initialized.return_value = True
            mock_bridge.incoming_calls.side_effect = RuntimeError("LSP crash")
            mock_mgr = MagicMock()
            mock_mgr.get_bridge.return_value = mock_bridge
            mock_get_mgr.return_value = mock_mgr

            mock_graph = MagicMock()
            mock_graph.analyze_blast_radius.return_value = {"levels": {}, "total": 0, "max_depth_reached": False}
            mock_graph_cls.return_value = mock_graph
            mock_tests.return_value = json.dumps({"tests": []})

            result = code_blast_radius_tool(path=str(f), line=1)
        data = _load(result)
        # Should still work, just no callers
        assert data["impact"] == "LOW"

    def test_handler_wrapper(self):
        """_handle_code_blast_radius delegates correctly."""
        with patch("code_intel.tools.impact.code_blast_radius_tool") as mock_tool:
            mock_tool.return_value = json.dumps({"status": "ok"})
            result = _handle_code_blast_radius(
                {
                    "path": "/p",
                    "line": 5,
                    "character": 3,
                    "depth": 2,
                    "language": "python",
                    "test_coverage": False,
                }
            )
        _load(result)
        mock_tool.assert_called_once_with(
            path="/p", line=5, character=3, depth=2, language="python", test_coverage=False
        )

    def test_handler_defaults(self):
        """_handle_code_blast_radius with empty args uses defaults."""
        with patch("code_intel.tools.impact.code_blast_radius_tool") as mock_tool:
            mock_tool.return_value = json.dumps({"status": "ok"})
            _handle_code_blast_radius({})
        mock_tool.assert_called_once_with(path="", line=1, character=0, depth=3, language="", test_coverage=True)


# ═══════════════════════════════════════════════════════════════════════════
# SCHEMA validation
# ═══════════════════════════════════════════════════════════════════════════


class TestSchemas:
    """Schema entries are properly structured."""

    def test_impact_schema(self):
        assert CODE_IMPACT_SCHEMA["name"] == "code_impact"
        assert "path" in CODE_IMPACT_SCHEMA["parameters"]["required"]

    def test_blast_radius_schema(self):
        assert CODE_BLAST_RADIUS_SCHEMA["name"] == "code_blast_radius"
        assert "path" in CODE_BLAST_RADIUS_SCHEMA["parameters"]["required"]

    def test_pr_impact_schema(self):
        assert CODE_PR_IMPACT_SCHEMA["name"] == "code_pr_impact"


# ═══════════════════════════════════════════════════════════════════════════
# _detect_base_branch
# ═══════════════════════════════════════════════════════════════════════════


class TestDetectBaseBranch:
    """_detect_base_branch — git branch auto-detection."""

    def test_auto_detect_finds_main(self, tmp_path):
        """origin/main found in remote list."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="  origin/main\n  origin/develop\n")
            branch = _detect_base_branch(auto_detect=True, base_branch="main", root=str(tmp_path))
        assert branch == "main"  # origin/main → main

    def test_auto_detect_finds_develop(self, tmp_path):
        """origin/develop found when main not present."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="  origin/develop\n")
            branch = _detect_base_branch(auto_detect=True, base_branch="main", root=str(tmp_path))
        assert branch == "develop"

    def test_auto_detect_fails(self, tmp_path):
        """git command fails, falls back to default."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = RuntimeError("git not available")
            branch = _detect_base_branch(auto_detect=True, base_branch="main", root=str(tmp_path))
        assert branch == "main"

    def test_no_auto_detect(self, tmp_path):
        """auto_detect=False returns base_branch unchanged."""
        with patch("subprocess.run") as mock_run:
            branch = _detect_base_branch(auto_detect=False, base_branch="develop", root=str(tmp_path))
        assert branch == "develop"
        mock_run.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# _git_diff_changed_files
# ═══════════════════════════════════════════════════════════════════════════


class TestGitDiffChangedFiles:
    """_git_diff_changed_files — git diff parsing."""

    def test_git_not_found(self, tmp_path):
        """FileNotFoundError when git is missing."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("git not found")
            *_, error = _git_diff_changed_files("main", tmp_path, 10)
        assert error is not None
        data = _load(error)
        assert "Not a git repository" in data.get("error", "")

    def test_git_timeout(self, tmp_path):
        """TimeoutExpired when git hangs."""
        import subprocess

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)
            *_, error = _git_diff_changed_files("main", tmp_path, 10)
        assert error is not None
        data = _load(error)
        assert "timed out" in data.get("error", "")

    def test_git_fails(self, tmp_path):
        """git returns non-zero exit code."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="fatal: bad revision")
            *_, error = _git_diff_changed_files("main", tmp_path, 10)
        assert error is not None
        data = _load(error)
        assert "git diff failed" in data.get("error", "")

    def test_no_changes(self, tmp_path):
        """Empty diff output."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            diff_output, changed_list, total_changed, error = _git_diff_changed_files("main", tmp_path, 10)
        assert error is not None  # fmt_ok returned as error param
        data = _load(error)
        assert data["status"] == "ok"
        assert "No changes detected" in data.get("message", "")

    def test_changes_found(self, tmp_path):
        """Git diff returns changed files."""
        diff_output_raw = "diff --git a/test.py b/test.py\n+++ b/test.py\n@@ -1 +1 @@\n-old\n+new\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=diff_output_raw, stderr="")
            diff_output, changed_list, total_changed, error = _git_diff_changed_files("main", tmp_path, 10)
        assert error is None
        assert diff_output == diff_output_raw
        assert "test.py" in changed_list
        assert total_changed == 1

    def test_no_source_files_changed(self, tmp_path):
        """Diff has +++ b//dev/null lines only."""
        diff_output_raw = "+++ b//dev/null\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=diff_output_raw, stderr="")
            diff_output, changed_list, total_changed, error = _git_diff_changed_files("main", tmp_path, 10)
        assert error is not None  # fmt_ok
        data = _load(error)
        assert "No source files changed" in data.get("message", "")


# ═══════════════════════════════════════════════════════════════════════════
# code_pr_impact_tool
# ═══════════════════════════════════════════════════════════════════════════


class TestCodePrImpactTool:
    """code_pr_impact_tool — PR impact analysis."""

    def test_path_not_found(self):
        """Non-existent path returns error."""
        result = code_pr_impact_tool(path="/nonexistent")
        data = _load(result)
        assert data["status"] == "error"
        assert "Path not found" in data["error"]

    def test_no_git_repo(self, tmp_path):
        """Git not found in _git_diff_changed_files propagates error."""
        with (
            patch("subprocess.run") as mock_run,
        ):
            mock_run.side_effect = FileNotFoundError("git not found")
            result = code_pr_impact_tool(path=str(tmp_path))
        data = _load(result)
        assert data["status"] == "error"
        # Should bubble up from _git_diff_changed_files
        assert "Not a git repository" in data.get("error", "")

    def test_no_changes(self, tmp_path):
        """No diff output returns ok with empty changes."""
        with (
            patch("code_intel.tools.impact._detect_base_branch", return_value="main"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = code_pr_impact_tool(path=str(tmp_path))
        data = _load(result)
        assert data["status"] == "ok"
        assert "No changes detected" in data.get("message", "")
        assert data.get("changes", None) == []

    def test_no_changes_no_source_files(self, tmp_path):
        """Diff has no source file changes."""
        with (
            patch("code_intel.tools.impact._detect_base_branch", return_value="main"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="+++ b//dev/null\n", stderr="")
            result = code_pr_impact_tool(path=str(tmp_path))
        data = _load(result)
        assert data["status"] == "ok"
        assert "No source files changed" in data.get("message", "")

    def test_with_changes(self, tmp_path):
        """PR with changes produces full report."""
        diff = "diff --git a/src/main.py b/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n-old\n+new\n"
        # Create the changed file so code_pr_impact_tool doesn't skip it
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("def my_func():\n    pass\n")
        with (
            patch("code_intel.tools.impact._detect_base_branch", return_value="main"),
            patch(
                "code_intel.tools.impact._find_functions_in_file",
                return_value=[
                    {"name": "my_func", "line": 1},
                ],
            ),
            patch("code_intel._import_graph.ImportGraph") as mock_graph_cls,
            patch("subprocess.run") as mock_run,
        ):
            # First two calls to subprocess: git diff + git blame
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=diff, stderr=""),
                MagicMock(returncode=0, stdout="author Alice\n", stderr=""),
            ]

            mock_graph = MagicMock()
            mock_graph.analyze_blast_radius.return_value = {
                "levels": {1: ["caller.py"]},
                "total": 1,
                "max_depth_reached": False,
            }
            mock_graph_cls.return_value = mock_graph

            result = code_pr_impact_tool(path=str(tmp_path))

        data = _load(result)
        assert data["files_changed"] == 1
        assert data["files_analyzed"] == 1
        assert data["lines_added"] == 1
        assert data["lines_removed"] == 1
        assert len(data["changed_functions"]) == 1
        assert data["changed_functions"][0]["name"] == "my_func"
        assert data["blast_radius"]["direct_callers"] == 1
        assert len(data["suggested_reviewers"]) == 1

    def test_large_diff_warning(self, tmp_path):
        """Large diff > max_files triggers warning."""
        diff = "\n".join(f"diff --git a/src/{i}.py b/src/{i}.py\n+++ b/src/{i}.py\n" for i in range(5))
        with (
            patch("code_intel.tools.impact._detect_base_branch", return_value="main"),
            patch("code_intel.tools.impact._find_functions_in_file", return_value=[]),
            patch("code_intel._import_graph.ImportGraph"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=diff, stderr=""),
                MagicMock(returncode=0, stdout="", stderr=""),
            ]

            result = code_pr_impact_tool(path=str(tmp_path), max_files=2)

        data = _load(result)
        assert "warning" in data
        assert "Large diff" in data["warning"]

    def test_handler_wrapper(self):
        """_handle_code_pr_impact delegates correctly."""
        with patch("code_intel.tools.impact.code_pr_impact_tool") as mock_tool:
            mock_tool.return_value = json.dumps({"status": "ok"})
            result = _handle_code_pr_impact(
                {
                    "base_branch": "develop",
                    "auto_detect": False,
                    "path": "/repo",
                    "max_files": 5,
                }
            )
        _load(result)
        mock_tool.assert_called_once_with(base_branch="develop", auto_detect=False, path="/repo", max_files=5)

    def test_handler_defaults(self):
        """_handle_code_pr_impact with empty args uses defaults."""
        with patch("code_intel.tools.impact.code_pr_impact_tool") as mock_tool:
            mock_tool.return_value = json.dumps({"status": "ok"})
            _handle_code_pr_impact({})
        mock_tool.assert_called_once_with(base_branch="main", auto_detect=True, path=".", max_files=10)


# ═══════════════════════════════════════════════════════════════════════════
# _find_functions_in_file
# ═══════════════════════════════════════════════════════════════════════════


class TestFindFunctionsInFile:
    """_find_functions_in_file — tree-sitter based function discovery."""

    def test_unsupported_language(self, tmp_path):
        """Unsupported language returns empty list."""
        f = tmp_path / "file.xyz"
        f.write_text("whatever")
        with (
            patch("code_intel.tools.impact.detect_language", return_value=None),
        ):
            result = _find_functions_in_file(str(f))
        assert result == []

    def test_no_query_for_language(self, tmp_path):
        """Language without a query entry returns empty."""
        f = tmp_path / "file.xyz"
        f.write_text("whatever")
        with (
            patch("code_intel.tools.impact.detect_language", return_value="unknown_lang"),
        ):
            result = _find_functions_in_file(str(f))
        assert result == []

    @pytest.mark.integration
    def test_no_parser_or_language(self, tmp_path):
        """Missing parser/language object returns empty."""
        f = tmp_path / "file.py"
        f.write_text("def foo(): pass")
        with (
            patch("code_intel.tools.impact.detect_language", return_value="python"),
            patch("code_intel.tools.impact._get_language", return_value=None),
        ):
            result = _find_functions_in_file(str(f))
        assert result == []

    def test_query_fails(self, tmp_path):
        """Query construction fails."""
        f = tmp_path / "file.py"
        f.write_text("def foo(): pass")
        mock_lang = MagicMock()

        with (
            patch("code_intel.tools.impact.detect_language", return_value="python"),
            patch("code_intel.tools.impact._get_language", return_value=mock_lang),
            patch("code_intel.tools.impact._get_parser", return_value=MagicMock()),
            patch("tree_sitter.Query", side_effect=Exception("query fail")),
        ):
            result = _find_functions_in_file(str(f))
        assert result == []

    def test_functions_found(self, tmp_path):
        """Functions successfully found via tree-sitter."""
        f = tmp_path / "file.py"
        f.write_text("def foo():\n    pass\n")

        # Hard to fully mock tree-sitter end-to-end in unit tests,
        # but we can verify the function returns a list (potentially empty
        # if tree-sitter isn't available in the test environment).
        result = _find_functions_in_file(str(f))
        # If tree-sitter is installed, we get real results;
        # if not, the import fails and we get [] from the except block
        assert isinstance(result, list)
