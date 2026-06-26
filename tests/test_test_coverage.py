"""tests/test_test_coverage.py — Comprehensive tests for tools/test_coverage.py.

Covers all functions and edge cases to reach 90%+ coverage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helper to reload the module under test fresh each time
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clean_modules():
    """Remove test_coverage module from sys.modules before each test."""
    import sys
    for key in list(sys.modules):
        if "test_coverage" in key and key != "code_intel.tools.test_coverage":
            pass
    sys.modules.pop("code_intel.tools.test_coverage", None)
    yield


def _import_mod():
    """Import the module under test fresh."""
    from code_intel.tools import test_coverage as m
    return m


# ===================================================================
# _tests_find_references
# ===================================================================

class TestFindReferences:
    """Tests for _tests_find_references()."""

    def test_success_returns_by_file(self, monkeypatch):
        """Happy path: code_references_tool returns JSON with by_file."""
        expected = {
            "/path/to/test_foo.py": [{"line": 10, "column": 5}],
            "/path/to/test_bar.py": [{"line": 20, "column": 3}],
        }
        mock_result = json.dumps({"by_file": expected, "total": 2})

        def mock_refs_tool(*args, **kwargs):
            return mock_result

        monkeypatch.setattr(
            "code_intel.lsp_bridge.code_references_tool", mock_refs_tool
        )

        mod = _import_mod()
        result = mod._tests_find_references("/path/to/file.py", 42, "python")
        assert result == expected

    def test_non_dict_response_returns_empty(self, monkeypatch):
        """If refs_data is not a dict, return {}."""
        mock_result = json.dumps([1, 2, 3])  # JSON array → not a dict

        monkeypatch.setattr(
            "code_intel.lsp_bridge.code_references_tool",
            lambda *a, **kw: mock_result,
        )

        mod = _import_mod()
        result = mod._tests_find_references("/x.py", 1, None)
        assert result == {}

    def test_exception_returns_empty_dict_and_logs(self, monkeypatch):
        """Exception during tool call → returns {}."""
        def _raise(*a, **kw):
            raise RuntimeError("LSP down")

        monkeypatch.setattr(
            "code_intel.lsp_bridge.code_references_tool", _raise
        )

        mod = _import_mod()
        result = mod._tests_find_references("/x.py", 1, None)
        assert result == {}

    def test_exception_on_json_parse(self, monkeypatch):
        """Invalid JSON from tool → returns {}."""
        monkeypatch.setattr(
            "code_intel.lsp_bridge.code_references_tool",
            lambda *a, **kw: "not json at all",
        )

        mod = _import_mod()
        result = mod._tests_find_references("/x.py", 1, None)
        assert result == {}

    def test_by_file_missing_from_data(self, monkeypatch):
        """JSON without by_file key → returns {}."""
        mock_result = json.dumps({"total": 0})

        monkeypatch.setattr(
            "code_intel.lsp_bridge.code_references_tool",
            lambda *a, **kw: mock_result,
        )

        mod = _import_mod()
        result = mod._tests_find_references("/x.py", 1, None)
        assert result == {}


# ===================================================================
# _tests_find_symbol_name
# ===================================================================

class TestFindSymbolName:
    """Tests for _tests_find_symbol_name()."""

    def test_finds_symbol_by_line_range(self, monkeypatch):
        """Happy path: symbol found within line range (first match)."""
        symbols_data = {
            "symbols": [
                # Non-overlapping ranges so first match is clear
                {"name": "my_func", "kind": "function", "start_line": 10, "end_line": 20},
                {"name": "other_func", "kind": "function", "start_line": 30, "end_line": 40},
            ]
        }

        monkeypatch.setattr(
            "code_intel.code_tools.code_symbols_tool",
            lambda *a, **kw: json.dumps(symbols_data),
        )

        mod = _import_mod()
        # line 15 falls inside my_func (10-20)
        result = mod._tests_find_symbol_name("/path/to/file.py", 15, "python")
        assert result == "my_func"

    def test_no_matching_symbol_returns_none(self, monkeypatch):
        """Line number not inside any symbol → None."""
        symbols_data = {
            "symbols": [
                {"name": "func_a", "kind": "function", "start_line": 1, "end_line": 10},
            ]
        }

        monkeypatch.setattr(
            "code_intel.code_tools.code_symbols_tool",
            lambda *a, **kw: json.dumps(symbols_data),
        )

        mod = _import_mod()
        result = mod._tests_find_symbol_name("/x.py", 99, None)
        assert result is None

    def test_symbols_not_a_dict(self, monkeypatch):
        """sym_data is not a dict → no crash, returns None."""
        monkeypatch.setattr(
            "code_intel.code_tools.code_symbols_tool",
            lambda *a, **kw: json.dumps([1, 2, 3]),
        )

        mod = _import_mod()
        result = mod._tests_find_symbol_name("/x.py", 1, None)
        assert result is None

    def test_symbols_missing_key(self, monkeypatch):
        """sym_data dict missing 'symbols' key → returns None."""
        monkeypatch.setattr(
            "code_intel.code_tools.code_symbols_tool",
            lambda *a, **kw: json.dumps({"other": "data"}),
        )

        mod = _import_mod()
        result = mod._tests_find_symbol_name("/x.py", 1, None)
        assert result is None

    def test_exception_returns_none(self, monkeypatch):
        """Exception during tool call → returns None."""
        def _raise(*a, **kw):
            raise ValueError("broken")

        monkeypatch.setattr(
            "code_intel.code_tools.code_symbols_tool", _raise
        )

        mod = _import_mod()
        result = mod._tests_find_symbol_name("/x.py", 1, None)
        assert result is None


# ===================================================================
# _calc_test_score
# ===================================================================

class TestCalcTestScore:
    """Tests for _calc_test_score()."""

    def test_base_score_is_ref_count(self):
        """Score starts at ref_count when no bonus applies."""
        mod = _import_mod()
        score = mod._calc_test_score(
            "/project/tests/test_foo.py",
            Path("/project/src/foo.py"),
            symbol_name=None,
            ref_count=3,
        )
        assert score == 3

    def test_same_parent_bonus(self):
        """+1 when fpath and target share the same parent dir."""
        mod = _import_mod()
        score = mod._calc_test_score(
            "/project/tests/test_foo.py",
            Path("/project/tests/helper.py"),  # same parent
            symbol_name=None,
            ref_count=0,
        )
        assert score == 1  # 0 + 1 (same parent)

    def test_symbol_name_in_stem_bonus(self, tmp_path, monkeypatch):
        """+2 when symbol_name appears in filename stem."""
        mod = _import_mod()
        score = mod._calc_test_score(
            "/project/tests/test_my_func.py",
            Path("/project/src/something.py"),
            symbol_name="my_func",
            ref_count=1,
        )
        # 1 (ref) + 0 (different parent) + 2 (symbol in stem) + 0 (no file read bonus)
        assert score == 3

    def test_symbol_name_in_fpath_bonus(self, monkeypatch):
        """+2 when symbol_name appears in full fpath."""
        mod = _import_mod()
        score = mod._calc_test_score(
            "/project/tests/test_myfunc_edge.py",
            Path("/project/src/other.py"),
            symbol_name="myfunc",
            ref_count=0,
        )
        assert score == 2

    def test_symbol_name_in_file_content_bonus(self, tmp_path):
        """+1 when symbol_name appears in file content."""
        test_file = tmp_path / "test_other.py"
        test_file.write_text("def test_my_func():\n    pass\n")

        mod = _import_mod()
        score = mod._calc_test_score(
            str(test_file),
            Path("/project/src/foo.py"),
            symbol_name="test_my_func",
            ref_count=0,
        )
        # stem is "test_other" — does NOT contain "test_my_func"
        # but content contains "test_my_func" → +1
        assert score == 1

    def test_file_read_exception_handled(self, monkeypatch):
        """Exception when reading file content for symbol check is caught."""
        def bad_read(*args, **kwargs):
            raise PermissionError("No access")

        monkeypatch.setattr(Path, "read_text", bad_read)

        mod = _import_mod()
        # symbol_name is not None so the read_text branch is entered
        score = mod._calc_test_score(
            "/protected/test_foo.py",
            Path("/src/foo.py"),
            symbol_name="foo",
            ref_count=2,
        )
        # 2 (ref) + 0 (different parent) + 2 (symbol in stem) = 4
        assert score == 4

    def test_all_bonuses_combined(self, tmp_path, monkeypatch):
        """All bonuses stack correctly."""
        test_file = tmp_path / "test_my_func.py"
        test_file.write_text("def test_my_func():\n    pass\n")

        mod = _import_mod()
        score = mod._calc_test_score(
            str(test_file),
            Path(tmp_path / "my_func.py"),  # same parent dir
            symbol_name="my_func",
            ref_count=3,
        )
        # 3 (ref) + 1 (same parent) + 2 (symbol in stem) + 1 (in content) = 7
        assert score == 7

    def test_no_symbol_name_skips_content_check(self, tmp_path):
        """When symbol_name is None, the symbol-related code is skipped."""
        mod = _import_mod()
        score = mod._calc_test_score(
            "/project/tests/test_foo.py",
            Path("/project/other/bar.py"),
            symbol_name=None,
            ref_count=2,
        )
        assert score == 2


# ===================================================================
# _tests_filter_and_score
# ===================================================================

class TestFilterAndScore:
    """Tests for _tests_filter_and_score()."""

    def test_filters_and_sorts_by_score(self, monkeypatch, tmp_path):
        """Test files are filtered by pattern and sorted by score descending."""
        mod = _import_mod()
        by_file = {
            "/project/tests/test_alpha.py": [{"line": 1}, {"line": 2}],
            "/project/tests/test_beta.py": [{"line": 5}],
            "/project/src/main.py": [{"line": 10}],  # not a test file
            "/project/spec/some_spec.py": [{"line": 15}],  # spec pattern
        }

        result = mod._tests_filter_and_score(by_file, Path("/project/src/main.py"), symbol_name=None)
        # main.py filtered out; spec/test remain
        assert len(result) == 3
        # alpha (ref_count=2) should be first, beta (ref_count=1) second, spec (ref_count=1) third
        assert result[0]["path"] == "/project/tests/test_alpha.py"
        assert result[0]["score"] >= result[1]["score"]

    def test_non_test_files_filtered_out(self):
        """Files not matching test/spec/__tests__ pattern are excluded."""
        mod = _import_mod()
        by_file = {
            "/project/utils/helper.py": [{"line": 1}],
            "/project/utils/config.py": [{"line": 2}],
        }
        result = mod._tests_filter_and_score(by_file, Path("/project/src/main.py"), symbol_name=None)
        assert result == []

    def test_describe_blocks_extracted(self, tmp_path, monkeypatch):
        """Describe/it/test/context blocks from first 30 lines are captured."""
        test_file = tmp_path / "test_something.py"
        test_file.write_text(
            "describe('MyClass', () => {\n"
            "    it('should work', () => {\n"
            "        test('nested', () => {})\n"
            "    })\n"
            "    context('when x', () => {})\n"
            "    def unrelated(): pass\n"
            "    # describe in comment\n"
            "    it('another', () => {})\n"
            "})\n"
        )

        mod = _import_mod()
        by_file = {str(test_file): [{"line": 1}]}
        result = mod._tests_filter_and_score(by_file, Path("/x.py"), symbol_name=None)
        assert len(result) == 1
        blocks = result[0]["describe_blocks"]
        assert any("describe('MyClass'" in b for b in blocks)
        assert any("it('should work'" in b for b in blocks)
        assert any("test('nested'" in b for b in blocks)
        assert any("context('when x'" in b for b in blocks)

    def test_file_read_exception_handled(self, monkeypatch):
        """Exception reading test file for describe_blocks is caught."""
        def bad_read(*args, **kwargs):
            raise OSError("Cannot read")

        monkeypatch.setattr(Path, "read_text", bad_read)

        mod = _import_mod()
        by_file = {
            "/project/tests/test_foo.py": [{"line": 1}],
        }
        result = mod._tests_filter_and_score(by_file, Path("/project/src/main.py"), symbol_name=None)
        assert len(result) == 1
        assert result[0]["describe_blocks"] == []

    def test_relevance_classification(self, tmp_path):
        """relevance field is set correctly based on score."""
        mod = _import_mod()
        test_file = tmp_path / "test_foo.py"
        test_file.write_text("def test(): pass\n")
        by_file = {str(test_file): [{"line": 1} for _ in range(6)]}  # ref_count=6
        result = mod._tests_filter_and_score(by_file, Path(tmp_path / "test_foo.py"), symbol_name="foo")
        assert len(result) == 1
        # score = 6 + 1 (same parent) + 2 (stem) + 1 (content) = 10 → "direct"
        assert result[0]["relevance"] == "direct"

    def test_classification_boundaries(self, tmp_path):
        """Test relevance: score=4 → high, score=2 → medium, score=1 → low."""
        mod = _import_mod()

        # Score 4 (high): ref_count=2 + same_parent + symbol_not_found = 3
        f1 = tmp_path / "test_a.py"
        f1.write_text("pass")
        by_file1 = {str(f1): [{"line": 1} for _ in range(2)]}
        r1 = mod._tests_filter_and_score(by_file1, Path(tmp_path / "x.py"), symbol_name=None)
        # score = 2 (ref) + 1 (same parent) = 3 → high
        assert r1[0]["relevance"] == "high"

        # Score 2 (medium): only same_parent and no ref
        f2 = tmp_path / "test_b.py"
        f2.write_text("pass")
        by_file2 = {str(f2): [{"line": 1}]}  # ref_count=1
        r2 = mod._tests_filter_and_score(by_file2, Path(tmp_path / "x.py"), symbol_name="unrelated")
        # score = 1 (ref) + 1 (same parent) + 0 + 0 = 2 → medium
        assert r2[0]["relevance"] == "medium"

        # Score 1 (low): only ref_count but no parent match, different directory
        f3 = tmp_path / "test_c.py"
        f3.write_text("pass")
        by_file3 = {str(f3): [{"line": 1}]}
        r3 = mod._tests_filter_and_score(by_file3, Path("/other/main.py"), symbol_name=None)
        # score = 1 → low
        assert r3[0]["relevance"] == "low"

    def test__test_dot_pattern_matches(self):
        """Files with .test. or .spec. in path are matched."""
        mod = _import_mod()
        by_file = {
            "/project/tests/main.test.js": [{"line": 1}],
            "/project/tests/app.spec.ts": [{"line": 2}],
        }
        result = mod._tests_filter_and_score(by_file, Path("/x.py"), symbol_name=None)
        assert len(result) == 2


# ===================================================================
# _tests_calc_coverage
# ===================================================================

class TestCalcCoverage:
    """Tests for _tests_calc_coverage()."""

    def test_empty_list_returns_none(self):
        mod = _import_mod()
        assert mod._tests_calc_coverage([]) == "none"

    def test_score_6_or_more_returns_high(self):
        mod = _import_mod()
        entries = [{"score": 6}, {"score": 3}]
        assert mod._tests_calc_coverage(entries) == "high"

    def test_score_6_or_more_high_boundary(self):
        """Score exactly 6."""
        mod = _import_mod()
        assert mod._tests_calc_coverage([{"score": 6}]) == "high"

    def test_score_3_to_5_returns_medium(self):
        mod = _import_mod()
        assert mod._tests_calc_coverage([{"score": 5}]) == "medium"
        assert mod._tests_calc_coverage([{"score": 3}]) == "medium"

    def test_score_3_to_5_medium_boundary(self):
        """Score exactly 3."""
        mod = _import_mod()
        assert mod._tests_calc_coverage([{"score": 3}]) == "medium"

    def test_score_less_than_3_returns_low(self):
        mod = _import_mod()
        assert mod._tests_calc_coverage([{"score": 2}]) == "low"
        assert mod._tests_calc_coverage([{"score": 1}]) == "low"
        assert mod._tests_calc_coverage([{"score": 0}]) == "low"

    def test_uses_highest_score(self):
        """Coverage is based on first entry score (which should be highest after sort)."""
        mod = _import_mod()
        # Already sorted desc — first entry = 7 → high
        entries = [{"score": 7}, {"score": 4}, {"score": 1}]
        assert mod._tests_calc_coverage(entries) == "high"


# ===================================================================
# code_tests_for_symbol_tool (main entry point)
# ===================================================================

class TestCodeTestsForSymbolTool:
    """Tests for code_tests_for_symbol_tool()."""

    def test_path_not_found_returns_error(self, monkeypatch):
        """Non-existent path returns fmt_err."""
        mod = _import_mod()
        result = mod.code_tests_for_symbol_tool("/nonexistent/path.py", 1)
        parsed = json.loads(result)
        assert parsed.get("status") == "error"

    def test_lsp_bridge_not_available(self, monkeypatch):
        """When lsp_bridge cannot be imported, return error."""
        import sys

        # Remove lsp_bridge from sys.modules so Python actually calls __import__
        for key in list(sys.modules):
            if key.startswith("code_intel.lsp_bridge"):
                del sys.modules[key]

        # Now make the import fail
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name.startswith("code_intel.lsp_bridge"):
                raise ImportError("lsp_bridge not installed")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        # Also ensure the module under test is fresh
        sys.modules.pop("code_intel.tools.test_coverage", None)

        # Make the path appear to exist so we get past the first check
        monkeypatch.setattr(Path, "exists", lambda self: True)

        mod = _import_mod()
        result = mod.code_tests_for_symbol_tool("/some/path.py", 1)
        parsed = json.loads(result)
        assert parsed.get("status") == "error"

    def test_happy_path(self, monkeypatch, tmp_path):
        """Full happy path: references found, symbol named, tests scored."""
        source_file = tmp_path / "my_func.py"
        source_file.write_text("def my_func():\n    pass\n")

        # Mock references — return one test file
        refs_result = json.dumps({
            "by_file": {
                str(tmp_path / "test_my_func.py"): [{"line": 5}],
            }
        })

        monkeypatch.setattr(
            "code_intel.lsp_bridge.code_references_tool",
            lambda *a, **kw: refs_result,
        )

        # Mock code_symbols_tool
        sym_result = json.dumps({
            "symbols": [
                {"name": "my_func", "start_line": 1, "end_line": 3},
            ]
        })

        monkeypatch.setattr(
            "code_intel.code_tools.code_symbols_tool",
            lambda *a, **kw: sym_result,
        )

        # Mock detect_language
        monkeypatch.setattr(
            "code_intel.code_tools.detect_language",
            lambda p: "python",
        )

        # Create the test file so describe_blocks can be read
        test_file = tmp_path / "test_my_func.py"
        test_file.write_text("def test_my_func():\n    pass\n")

        mod = _import_mod()
        result = mod.code_tests_for_symbol_tool(str(source_file), 2)
        parsed = json.loads(result)

        assert parsed.get("status") == "ok"
        assert parsed.get("symbol") == "my_func"
        assert len(parsed.get("test_files", [])) == 1
        assert parsed.get("total_tests_found") == 1
        assert "coverage_estimate" in parsed

    def test_no_references(self, monkeypatch, tmp_path):
        """When no references found, result has empty test_files and coverage none."""
        source_file = tmp_path / "orphan.py"
        source_file.write_text("def orphan():\n    pass\n")

        # No references
        monkeypatch.setattr(
            "code_intel.lsp_bridge.code_references_tool",
            lambda *a, **kw: json.dumps({"by_file": {}, "total": 0}),
        )

        monkeypatch.setattr(
            "code_intel.code_tools.detect_language",
            lambda p: "python",
        )

        mod = _import_mod()
        result = mod.code_tests_for_symbol_tool(str(source_file), 1)
        parsed = json.loads(result)

        assert parsed.get("status") == "ok"
        assert parsed.get("symbol") is None  # by_file is empty → no symbol lookup
        assert parsed.get("test_files") == []
        assert parsed.get("total_tests_found") == 0
        assert parsed.get("coverage_estimate") == "none"

    def test_language_override(self, monkeypatch, tmp_path):
        """Language parameter is passed through."""
        source_file = tmp_path / "func.ts"
        source_file.write_text("function func() {}\n")

        captured = {}

        def mock_refs(target, line, **kw):
            captured["lang"] = kw.get("language")
            return json.dumps({"by_file": {}})

        monkeypatch.setattr(
            "code_intel.lsp_bridge.code_references_tool", mock_refs,
        )

        mod = _import_mod()
        mod.code_tests_for_symbol_tool(str(source_file), 1, language="typescript")
        assert captured.get("lang") == "typescript"


# ===================================================================
# _handle_code_tests_for_symbol
# ===================================================================

class TestHandleCodeTestsForSymbol:
    """Tests for _handle_code_tests_for_symbol()."""

    def test_delegates_with_defaults(self, monkeypatch, tmp_path):
        """Handler calls code_tests_for_symbol_tool with args from dict."""
        source_file = tmp_path / "handler_test.py"
        source_file.write_text("pass")

        # Make exists() work, but references return empty
        monkeypatch.setattr(
            "code_intel.lsp_bridge.code_references_tool",
            lambda *a, **kw: json.dumps({"by_file": {}}),
        )
        monkeypatch.setattr(
            "code_intel.code_tools.detect_language",
            lambda p: "python",
        )

        mod = _import_mod()
        result = mod._handle_code_tests_for_symbol({
            "path": str(source_file),
            "line": 3,
            "language": None,
        })
        parsed = json.loads(result)
        assert parsed.get("status") == "ok"

    def test_missing_path_returns_error(self, monkeypatch):
        """Handler with non-existent path returns error."""
        mod = _import_mod()
        result = mod._handle_code_tests_for_symbol({
            "path": "/does/not/exist.py",
            "line": 1,
        })
        parsed = json.loads(result)
        assert parsed.get("status") == "error"

    def test_handler_with_empty_args(self, monkeypatch):
        """Handler with empty args calls tool with default values."""
        # Make a temp file so it's found
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
        tmp_path = tmp.name
        tmp.close()

        try:
            monkeypatch.setattr(
                "code_intel.lsp_bridge.code_references_tool",
                lambda *a, **kw: json.dumps({"by_file": {}}),
            )
            monkeypatch.setattr(
                "code_intel.code_tools.detect_language",
                lambda p: "python",
            )

            mod = _import_mod()
            result = mod._handle_code_tests_for_symbol({})
            parsed = json.loads(result)
            # empty path resolves to cwd which exists, so we get ok (no refs)
            assert parsed.get("status") == "ok"
        finally:
            import os
            os.unlink(tmp_path)


# ===================================================================
# CODE_TESTS_FOR_SYMBOL_SCHEMA validation
# ===================================================================

class TestSchema:
    """Tests for the CODE_TESTS_FOR_SYMBOL_SCHEMA constant."""

    def test_schema_structure(self):
        mod = _import_mod()
        schema = mod.CODE_TESTS_FOR_SYMBOL_SCHEMA

        assert schema["name"] == "code_tests_for_symbol"
        assert "description" in schema
        assert "parameters" in schema
        assert schema["parameters"]["type"] == "object"
        assert "path" in schema["parameters"]["properties"]
        assert "line" in schema["parameters"]["properties"]
        assert schema["parameters"]["required"] == ["path", "line"]

    def test_schema_properties(self):
        mod = _import_mod()
        props = mod.CODE_TESTS_FOR_SYMBOL_SCHEMA["parameters"]["properties"]

        assert props["path"]["type"] == "string"
        assert props["line"]["type"] == "integer"
        assert props["language"]["type"] == "string"


# ===================================================================
# Edge case: tmp_path-based tests with real file operations
# ===================================================================

class TestRealFileOperations:
    """Tests that exercise real filesystem reads for describe_blocks."""

    def test_describe_blocks_pagination(self, tmp_path):
        """Only first 30 lines and up to 5 blocks are captured."""
        mod = _import_mod()
        test_file = tmp_path / "long_test.py"
        test_file.write_text(
            "\n".join(
                [f"describe('block_{i}', () => {{}})" for i in range(20)]
            )
        )

        by_file = {str(test_file): [{"line": 1}]}
        result = mod._tests_filter_and_score(by_file, Path("/x.py"), symbol_name=None)
        assert len(result[0]["describe_blocks"]) <= 5
        assert all("describe" in b for b in result[0]["describe_blocks"])

    def test_symbol_context_from_real_file(self, tmp_path, monkeypatch):
        """End-to-end: a real test file with symbol references."""
        source_file = tmp_path / "calculator.py"
        source_file.write_text(
            "def add(a, b):\n    return a + b\n"
        )
        test_file = tmp_path / "test_calculator.py"
        test_file.write_text(
            "from calculator import add\n"
            "\n"
            "describe('Calculator', () => {\n"
            "    it('adds numbers', () => {\n"
            "        assert add(1, 2) == 3\n"
            "    })\n"
            "})\n"
        )

        monkeypatch.setattr(
            "code_intel.lsp_bridge.code_references_tool",
            lambda *a, **kw: json.dumps({
                "by_file": {str(test_file): [{"line": 5}]},
            }),
        )
        monkeypatch.setattr(
            "code_intel.code_tools.code_symbols_tool",
            lambda *a, **kw: json.dumps({
                "symbols": [{"name": "add", "start_line": 1, "end_line": 2}],
            }),
        )
        monkeypatch.setattr(
            "code_intel.code_tools.detect_language",
            lambda p: "python",
        )

        mod = _import_mod()
        result = mod.code_tests_for_symbol_tool(str(source_file), 1)
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert parsed["symbol"] == "add"
        assert len(parsed["test_files"]) == 1
        tf = parsed["test_files"][0]
        assert tf["path"] == str(test_file)
        assert tf["score"] >= 3
        assert any("describe" in b for b in tf.get("describe_blocks", []))


# ===================================================================
# Regression: ensure _tests_filter_and_score does not crash on empty by_file
# ===================================================================

class TestFilterEdgeCases:
    """Edge cases for _tests_filter_and_score."""

    def test_empty_by_file(self):
        mod = _import_mod()
        assert mod._tests_filter_and_score({}, Path("/x.py"), "foo") == []

    def test_only_non_test_files(self):
        mod = _import_mod()
        by_file = {
            "/src/main.py": [{"line": 1}],
            "/src/utils.py": [{"line": 5}],
        }
        result = mod._tests_filter_and_score(by_file, Path("/x.py"), None)
        assert result == []

    def test_case_insensitive_pattern(self):
        """Pattern matches Test, TEST, Spec, SPEC regardless of case."""
        mod = _import_mod()
        by_file = {
            "/project/TEST_main.py": [{"line": 1}],
            "/project/SPEC_main.py": [{"line": 2}],
            "/project/__tests__/main.py": [{"line": 3}],
        }
        result = mod._tests_filter_and_score(by_file, Path("/x.py"), None)
        assert len(result) == 3

    def test_describe_blocks_truncated(self, tmp_path):
        """Only first 5 matching lines are kept in describe_blocks."""
        mod = _import_mod()
        f = tmp_path / "test_trunc.py"
        f.write_text(
            "\n".join(["describe('block_' + str(i))" for i in range(10)])
        )
        by_file = {str(f): [{"line": 1}]}
        result = mod._tests_filter_and_score(by_file, Path("/x.py"), None)
        assert len(result[0]["describe_blocks"]) == 5
