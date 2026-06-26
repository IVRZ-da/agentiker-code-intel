"""Tests for code_complexity_tool — cyclomatic complexity analysis."""

import json
import os
import tempfile
from pathlib import Path

from code_intel.code_tools import code_complexity_tool


def _make_py_file(content: str) -> str:
    tmp = tempfile.mkdtemp()
    f = Path(tmp) / "test.py"
    f.write_text(content)
    return str(f)


def _make_file(content: str, ext: str = ".py") -> str:
    """Create a temp file with given extension (not necessarily .py)."""
    tmp = tempfile.mkdtemp()
    f = Path(tmp) / f"test{ext}"
    f.write_text(content)
    return str(f)


class TestCodeComplexity:
    def test_simple_function_returns_rank_a(self):
        path = _make_py_file("def foo():\n    pass\n")
        result = code_complexity_tool(path=path)
        assert '"rank": "A"' in result
        assert '"total": 1' in result

    def test_if_else_increases_complexity(self):
        path = _make_py_file(
            "def foo(x):\n    if x > 0:\n        return 1\n    elif x < 0:\n        return -1\n    return 0\n"
        )
        result = code_complexity_tool(path=path)
        # 1 (base) + 2 (if/elif) = C=3
        data = json.loads(result)
        assert data["total"] >= 3
        assert data["function"] == "foo"

    def test_loop_increases_complexity(self):
        path = _make_py_file("def foo(items):\n    for item in items:\n        print(item)\n")
        result = code_complexity_tool(path=path)
        data = json.loads(result)
        assert data["breakdown"]["loops"] >= 1

    def test_exception_handling_counted(self):
        path = _make_py_file("def foo():\n    try:\n        bar()\n    except ValueError:\n        pass\n")
        result = code_complexity_tool(path=path)
        data = json.loads(result)
        assert data["breakdown"]["exceptions"] >= 1

    def test_error_for_nonexistent_path(self):
        result = code_complexity_tool(path="/nonexistent/file.py")
        assert "error" in result

    def test_function_by_name(self):
        path = _make_py_file("def bar():\n    pass\ndef baz():\n    pass\n")
        result = code_complexity_tool(path=path, function="baz")
        data = json.loads(result)
        assert data["function"] == "baz"

    def test_function_by_line(self):
        path = _make_py_file("def foo():\n    pass\ndef bar():\n    pass\n")
        result = code_complexity_tool(path=path, line=3)
        data = json.loads(result)
        assert data["function"] == "bar"

    def test_empty_file_returns_error(self):
        path = _make_py_file("")
        result = code_complexity_tool(path=path)
        assert "error" in result

    def test_high_complexity_rank(self):
        path = _make_py_file("def foo(x):\n" + "    if x == 1:\n        pass\n" * 20)
        result = code_complexity_tool(path=path)
        data = json.loads(result)
        assert data["total"] >= 20
        assert data["rank"] in ("C", "D", "E")


# ═══════════════════════════════════════════════════════════════════
# Extended coverage tests — bring coverage from ~65% to 90%+
# ═══════════════════════════════════════════════════════════════════

class TestCodeComplexityExtended:
    """Additional tests covering untested code paths."""

    # ── Language / detection edge cases ─────────────────────────

    def test_unsupported_language(self):
        """Passing unknown language override returns an error (line 405)."""
        path = _make_py_file("def foo():\n    pass\n")
        result = code_complexity_tool(path=path, language="unsupported")
        assert "error" in result

    def test_unrecognized_extension(self):
        """File with unknown extension triggers detection failure (line 403)."""
        path = _make_file("def foo():\n    pass\n", ext=".xyz")
        result = code_complexity_tool(path=path)
        assert "error" in result

    # ── OSError / JSON decode error paths ───────────────────────

    def test_oserror_reading_directory_as_file(self):
        """Opening a directory as a .py file triggers OSError in
        _analyze_file_complexity_single (lines 189-190) and then
        JSONDecodeError in code_complexity_tool (line 414)."""
        tmp = tempfile.mkdtemp()
        d = Path(tmp) / "test.py"
        d.mkdir()
        result = code_complexity_tool(path=str(d))
        assert "error" in result

    # ── Directory mode ──────────────────────────────────────────

    def test_directory_mode(self):
        """Directory scan returns hotspot summary (lines 373-398)."""
        tmp = tempfile.mkdtemp()
        (Path(tmp) / "a.py").write_text("def foo(x):\n    if x:\n        pass\n")
        (Path(tmp) / "b.py").write_text("def bar():\n    pass\n")
        result = code_complexity_tool(path=tmp, directory=True)
        data = json.loads(result)
        assert data["mode"] == "directory"
        assert data["total_functions"] == 2
        assert len(data["hotspots"]) == 2
        # foo has higher complexity, sorted first
        assert data["hotspots"][0]["function"] == "foo"

    def test_directory_mode_not_a_dir(self):
        """directory=True on a file returns error (lines 373-374)."""
        path = _make_py_file("def foo():\n    pass\n")
        result = code_complexity_tool(path=path, directory=True)
        assert "error" in result

    def test_directory_mode_no_results(self):
        """Empty directory returns 'No functions found' (lines 386-388)."""
        tmp = tempfile.mkdtemp()
        result = code_complexity_tool(path=tmp, directory=True)
        assert "error" in result

    # ── Loop types ──────────────────────────────────────────────

    def test_while_loop(self):
        """While loops are counted (while_statement branch)."""
        path = _make_py_file("def foo():\n    while True:\n        break\n")
        result = code_complexity_tool(path=path)
        data = json.loads(result)
        assert data["breakdown"]["loops"] >= 1

    # ── Exception handling variants ─────────────────────────────

    def test_multiple_except_clauses(self):
        """Multiple except clauses are each counted."""
        path = _make_py_file(
            "def foo():\n"
            "    try:\n"
            "        bar()\n"
            "    except ValueError:\n"
            "        pass\n"
            "    except TypeError:\n"
            "        pass\n"
        )
        result = code_complexity_tool(path=path)
        data = json.loads(result)
        assert data["breakdown"]["exceptions"] >= 2

    def test_finally_clause(self):
        """finally clause is counted as exception complexity."""
        path = _make_py_file(
            "def foo():\n"
            "    try:\n"
            "        bar()\n"
            "    finally:\n"
            "        pass\n"
        )
        result = code_complexity_tool(path=path)
        data = json.loads(result)
        assert data["breakdown"]["exceptions"] >= 1

    # ── Rank boundaries ─────────────────────────────────────────

    def test_rank_b(self):
        """Total 11 → rank B (line 167/218/269 B path)."""
        # 10 ifs = total 11
        path = _make_py_file("def foo(x):\n" + "    if x == 1:\n        pass\n" * 10)
        result = code_complexity_tool(path=path)
        data = json.loads(result)
        assert data["total"] == 11
        assert data["rank"] == "B"

    def test_rank_d(self):
        """Total 31 → rank D (line 167/218/269 D path)."""
        # 30 ifs = total 31
        path = _make_py_file("def foo(x):\n" + "    if x == 1:\n        pass\n" * 30)
        result = code_complexity_tool(path=path)
        data = json.loads(result)
        assert data["total"] == 31
        assert data["rank"] == "D"

    # ── Recommendations ─────────────────────────────────────────

    def test_recommendation_high(self):
        """Total 21 → recommendation to extract sub-functions (line 272)."""
        # 20 ifs = total 21
        path = _make_py_file("def foo(x):\n" + "    if x == 1:\n        pass\n" * 20)
        result = code_complexity_tool(path=path)
        data = json.loads(result)
        assert data["total"] == 21
        assert "Consider extracting" in data["recommendation"]

    def test_recommendation_critical(self):
        """Total 32 → 'High complexity' recommendation (line 274)."""
        # 31 ifs = total 32
        path = _make_py_file("def foo(x):\n" + "    if x == 1:\n        pass\n" * 31)
        result = code_complexity_tool(path=path)
        data = json.loads(result)
        assert data["total"] == 32
        assert "High complexity" in data["recommendation"]

    # ── Early returns ───────────────────────────────────────────

    def test_early_returns_in_breakdown(self):
        """Function with return statements shows early_returns in breakdown."""
        path = _make_py_file("def foo():\n    return 42\n")
        result = code_complexity_tool(path=path)
        data = json.loads(result)
        assert "early_returns" in data["breakdown"]


# ═══════════════════════════════════════════════════════════════════
# Internal helper function tests
# ═══════════════════════════════════════════════════════════════════

class TestInternalHelpers:
    """Direct tests for internal helper functions (imported directly)."""

    # Import internal functions
    @staticmethod
    def _get_internals():
        from code_intel.tools.complexity import (
            _COMPLEXITY_NODE_TYPES,
            _count_early_returns,
            _count_nodes,
            _handle_code_complexity,
            _select_and_format_complexity,
            _select_complexity_target,
        )
        return (
            _count_nodes,
            _count_early_returns,
            _handle_code_complexity,
            _select_complexity_target,
            _select_and_format_complexity,
            _COMPLEXITY_NODE_TYPES,
        )

    # ── _handle_code_complexity (line 450) ──────────────────────

    def test_handle_code_complexity(self):
        """Handler delegates correctly to code_complexity_tool."""
        (_, _, _handle_code_complexity, _, _, _) = self._get_internals()
        path = _make_py_file("def foo():\n    pass\n")
        result = _handle_code_complexity({"path": path})
        data = json.loads(result)
        assert data["function"] == "foo"

    def test_handle_code_complexity_all_params(self):
        """Handler passes all parameters through."""
        (_, _, _handle_code_complexity, _, _, _) = self._get_internals()
        path = _make_py_file("def foo():\n    pass\ndef bar():\n    pass\n")
        result = _handle_code_complexity({
            "path": path,
            "function": "bar",
            "line": 0,
            "language": "python",
            "directory": False,
        })
        data = json.loads(result)
        assert data["function"] == "bar"

    def test_handle_code_complexity_error(self):
        """Handler returns error for invalid path."""
        (_, _, _handle_code_complexity, _, _, _) = self._get_internals()
        result = _handle_code_complexity({"path": "/nonexistent/file.py"})
        assert "error" in result

    # ── _select_complexity_target ───────────────────────────────

    def test_select_complexity_target_empty(self):
        """Empty list returns None (line 241-242)."""
        (_, _, _, _select_complexity_target, _, _) = self._get_internals()
        assert _select_complexity_target([]) is None

    def test_select_complexity_target_by_name(self):
        """Select by 'name' key (lines 243-246)."""
        (_, _, _, _select_complexity_target, _, _) = self._get_internals()
        funcs = [
            {"name": "foo", "line": 1},
            {"name": "bar", "line": 5},
        ]
        result = _select_complexity_target(funcs, function_name="bar")
        assert result["name"] == "bar"

    def test_select_complexity_target_by_function_key(self):
        """Select by 'function' key (lines 243-246)."""
        (_, _, _, _select_complexity_target, _, _) = self._get_internals()
        funcs = [{"function": "baz", "line": 1}]
        result = _select_complexity_target(funcs, function_name="baz")
        assert result["function"] == "baz"

    def test_select_complexity_target_no_match_returns_first(self):
        """No match falls through to return functions[0] (line 253)."""
        (_, _, _, _select_complexity_target, _, _) = self._get_internals()
        funcs = [
            {"name": "alpha", "line": 1},
            {"name": "beta", "line": 5},
        ]
        result = _select_complexity_target(funcs, function_name="nonexistent")
        assert result["name"] == "alpha"

    def test_select_complexity_target_by_line(self):
        """Select by target line (lines 247-252)."""
        (_, _, _, _select_complexity_target, _, _) = self._get_internals()
        funcs = [
            {"name": "foo", "line": 1, "end_line": 3},
            {"name": "bar", "line": 5, "end_line": 10},
        ]
        result = _select_complexity_target(funcs, target_line=7)
        assert result["name"] == "bar"

    def test_select_complexity_target_line_on_boundary(self):
        """Select by target line — exact line match and end_line match."""
        (_, _, _, _select_complexity_target, _, _) = self._get_internals()
        funcs = [
            {"name": "foo", "line": 1, "end_line": 3},
            {"name": "bar", "line": 5, "end_line": 10},
        ]
        # Exact line match
        assert _select_complexity_target(funcs, target_line=1)["name"] == "foo"
        # End line match
        assert _select_complexity_target(funcs, target_line=3)["name"] == "foo"
        # Out of range — returns first
        assert _select_complexity_target(funcs, target_line=4)["name"] == "foo"

    # ── _select_and_format_complexity ───────────────────────────

    def test_select_and_format_complexity_no_functions(self):
        """File with no functions returns None (line 341-342)."""
        (_, _, _, _, _select_and_format_complexity, _) = self._get_internals()
        tmp = tempfile.mkdtemp()
        f = Path(tmp) / "test.py"
        f.write_text("x = 1\n")
        result = _select_and_format_complexity(f, "python", "", 0)
        assert result is None

    def test_select_and_format_complexity_oserror(self):
        """Nonexistent file returns None via OSError (line 307-308)."""
        (_, _, _, _, _select_and_format_complexity, _) = self._get_internals()
        result = _select_and_format_complexity(Path("/nonexistent/test.py"), "python", "", 0)
        assert result is None

    # ── _count_nodes ────────────────────────────────────────────

    def test_count_nodes_empty_types(self):
        """_count_nodes handles empty type list."""
        (_count_nodes, _, _, _, _, _) = self._get_internals()
        # Parse a simple Python function and count with empty types
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser

        py_lang = Language(tspython.language())
        parser = Parser(py_lang)
        tree = parser.parse(b"def foo():\n    pass\n")
        fn_node = tree.root_node.named_children[0]
        count = _count_nodes(fn_node, [])
        assert count == 0


# ═══════════════════════════════════════════════════════════════════
# Integration: Directory scan with .py only (covers _scan_directory)
# ═══════════════════════════════════════════════════════════════════

class TestDirectoryScan:
    """Integration tests for the directory scanning helper."""

    def test_directory_scan_skips_node_modules(self):
        """_scan_directory_for_complexity skips node_modules (line 134)."""
        tmp = tempfile.mkdtemp()
        # Create a file in node_modules — should be skipped
        nm = Path(tmp) / "node_modules"
        nm.mkdir()
        (nm / "ignored.py").write_text("def foo():\n    pass\n")
        # And a real source file
        (Path(tmp) / "real.py").write_text("def bar():\n    pass\n")
        result = code_complexity_tool(path=tmp, directory=True)
        data = json.loads(result)
        assert data["total_functions"] == 1
        assert data["hotspots"][0]["function"] == "bar"

    def test_directory_scan_skips_git(self):
        """_scan_directory_for_complexity skips .git (line 134)."""
        tmp = tempfile.mkdtemp()
        git = Path(tmp) / ".git"
        git.mkdir()
        (git / "ignored.py").write_text("def foo():\n    pass\n")
        (Path(tmp) / "real.py").write_text("def bar():\n    pass\n")
        result = code_complexity_tool(path=tmp, directory=True)
        data = json.loads(result)
        assert data["total_functions"] == 1
        assert data["hotspots"][0]["function"] == "bar"

    def test_directory_scan_skips_pycache(self):
        """_scan_directory_for_complexity skips __pycache__ (line 134)."""
        tmp = tempfile.mkdtemp()
        pc = Path(tmp) / "__pycache__"
        pc.mkdir()
        (pc / "ignored.py").write_text("def foo():\n    pass\n")
        (Path(tmp) / "real.py").write_text("def bar():\n    pass\n")
        result = code_complexity_tool(path=tmp, directory=True)
        data = json.loads(result)
        assert data["total_functions"] == 1
        assert data["hotspots"][0]["function"] == "bar"

    def test_directory_scan_skips_build_dist_venv(self):
        """_scan_directory_for_complexity skips build/dist/.venv (line 134)."""
        tmp = tempfile.mkdtemp()
        for dirname in ("build", "dist", ".venv"):
            d = Path(tmp) / dirname
            d.mkdir()
            (d / "ignored.py").write_text("def foo():\n    pass\n")
        (Path(tmp) / "real.py").write_text("def bar():\n    pass\n")
        result = code_complexity_tool(path=tmp, directory=True)
        data = json.loads(result)
        assert data["total_functions"] == 1
        assert data["hotspots"][0]["function"] == "bar"

    def test_directory_scan_oserror_skips_file(self):
        """OSError reading a file in directory scan is caught (lines 138-140)."""
        tmp = tempfile.mkdtemp()
        (Path(tmp) / "good.py").write_text("def foo():\n    pass\n")
        bad = Path(tmp) / "bad.py"
        bad.write_text("def bar():\n    pass\n")
        os.chmod(bad, 0o000)  # Remove read permission → OSError on read
        try:
            result = code_complexity_tool(path=tmp, directory=True)
            data = json.loads(result)
            assert data["total_functions"] == 1
            assert data["hotspots"][0]["function"] == "foo"
        finally:
            os.chmod(bad, 0o644)  # Restore for cleanup


class TestEdgeCases:
    """Additional edge-case coverage tests."""

    def test_fmt_err_returns_json_during_pytest(self):
        """fmt_err returns JSON (not box-drawing) when rich detects
        a non-TTY output (as happens during pytest capture)."""
        path = _make_py_file("")
        result = code_complexity_tool(path=path)
        # During pytest, rich outputs JSON, so this is parseable
        import json
        data = json.loads(result)
        assert data["status"] == "error"
        assert "error" in data

    def test_oserror_in_analyze_returns_json_error(self):
        """OSError in _analyze_file_complexity_single returns JSON error
        during pytest (due to rich Console non-TTY behavior)."""
        tmp = tempfile.mkdtemp()
        d = Path(tmp) / "test.py"
        d.mkdir()
        result = code_complexity_tool(path=str(d))
        import json
        data = json.loads(result)
        assert data["status"] == "error"
        assert "Cannot read" in data.get("error", "")

    def test_rank_e(self):
        """Total >40 → rank E."""
        # 40 ifs = total 41
        path = _make_py_file("def foo(x):\n" + "    if x == 1:\n        pass\n" * 40)
        result = code_complexity_tool(path=path)
        data = json.loads(result)
        assert data["total"] == 41
        assert data["rank"] == "E"

    def test_file_with_only_comments_and_strings(self):
        """File with no functions triggers error."""
        path = _make_py_file("# just a comment\nx = 42\n")
        result = code_complexity_tool(path=path)
        assert "error" in result
