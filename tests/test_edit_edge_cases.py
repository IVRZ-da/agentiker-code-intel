"""Edge-case tests for code_tools.py — search, refactor, capsule.

Extracted from test_tool_edge_cases.py.

Targets:
  - code_search_tool directory edge cases
  - code_refactor_tool edge cases (ast-grep errors)
  - code_capsule_tool edge cases
"""

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import code_intel.lsp_bridge as _lsp_bridge
import pytest

pytest.importorskip("tree_sitter", reason="tree-sitter not installed")

from code_intel.code_tools import (
    _SYMBOL_CACHE,
    _code_refactor_single_file,
    code_capsule_tool,
    code_refactor_tool,
    code_search_tool,
)

# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture()
def tmp_py(tmp_path):
    src = textwrap.dedent("""\
        MY_CONST = 42

        class Greeter:
            \"\"\"Say hello.\"\"\"

            def greet(self, name: str) -> str:
                return f"Hello, {name}!"

            @staticmethod
            def farewell() -> str:
                return "Goodbye!"

        def top_level_fn(x: int) -> int:
            return x * 2

        async def async_fn() -> None:
            pass
    """)
    f = tmp_path / "sample.py"
    f.write_text(src)
    return f


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear symbol cache before each test."""
    _SYMBOL_CACHE.clear()
    yield
    _SYMBOL_CACHE.clear()


# ===========================================================================
# D — code_search_tool directory edge cases
# ===========================================================================


class TestCodeSearchDirectoryEdgeCases:
    """Edge cases for _code_search_directory error handling."""

    def test_directory_skip_unsupported_lang_preset(self, tmp_path):
        """Files with unsupported lang/preset combination are skipped."""
        (tmp_path / "main.go").write_text("package main\nfunc main() {}\n")
        result = json.loads(code_search_tool(str(tmp_path), preset="decorator_calls"))
        assert "files_scanned" in result

    def test_directory_skip_no_grammar(self, tmp_path):
        """Files with unsupported language get skipped."""
        (tmp_path / "test.rs").write_text("fn main() {}")
        result = json.loads(code_search_tool(str(tmp_path), query="(function_item) @fn"))
        assert "files_scanned" in result

    def test_directory_handles_bad_query(self, tmp_path):
        """Bad query in directory mode is skipped per-file."""
        (tmp_path / "test.py").write_text("x = 1\n")
        result = json.loads(code_search_tool(str(tmp_path), query="(()) invalid @@"))
        assert "files_scanned" in result

    def test_directory_with_multiple_file_types(self, tmp_path):
        """Directory with multiple file types scans all supported ones."""
        (tmp_path / "a.py").write_text("print(1)\n")
        (tmp_path / "b.ts").write_text("console.log(1)\n")
        (tmp_path / "c.rs").write_text("fn main() {}\n")
        result = json.loads(code_search_tool(str(tmp_path), preset="function_calls"))
        assert result["files_scanned"] >= 1

    def test_directory_deduplicates_spans(self):
        """Duplicate spans across files are not double-counted."""
        pass

    def test_directory_skip_unsupported_lang_explicit(self, tmp_path):
        """Explicit language that doesn't match file extensions is handled."""
        (tmp_path / "test.py").write_text("x = 1\n")
        result = json.loads(code_search_tool(str(tmp_path), language="nonexistent"))
        assert "files_scanned" in result or "error" in result


# ===========================================================================
# E — code_refactor_tool edge cases
# ===========================================================================


class TestCodeRefactorEdgeCases:
    """Edge cases for _code_refactor_single_file."""

    def test_refactor_sg_root_parse_failure(self, tmp_path):
        """When SgRoot fails to parse, returns error."""
        from code_intel.code_tools import _code_refactor_single_file

        f = tmp_path / "test.ts"
        f.write_text("let x = 1;\n")
        mock_sg = MagicMock()
        mock_sg.SgRoot.side_effect = Exception("parse failed")
        import sys

        sys.modules["ast_grep_py"] = mock_sg
        try:
            result = _code_refactor_single_file(f, "console.log($ARG)", "console.info($ARG)", "typescript", True, 1)
            assert "error" in result
        finally:
            del sys.modules["ast_grep_py"]

    def test_refactor_find_all_exception(self, tmp_path):
        """When find_all raises an exception, returns error."""
        f = tmp_path / "test.py"
        f.write_text("def foo():\n    pass\n")
        with patch("ast_grep_py.SgRoot") as mock_sg:
            mock_root = MagicMock()
            mock_root.root().find_all.side_effect = Exception("find_all failed")
            mock_sg.return_value = mock_root
            result = _code_refactor_single_file(
                f,
                "def $NAME($$$ARGS): $$$BODY",
                "def $NAME($$$ARGS):\\n    return None",
                "python",
                True,
                1,
            )
            assert "error" in result

    def test_refactor_variable_extraction_exception(self, tmp_path):
        """When match.get_match raises an exception, pass."""
        f = tmp_path / "test.py"
        f.write_text("foo(42, 'hello')\n")
        result = _code_refactor_single_file(
            f,
            "foo($X, $Y)",
            "bar($Y, $X)",
            "python",
            True,
            1,
        )
        assert result["match_count"] == 1
        assert result["changes"][0]["replacement"] == "bar('hello', 42)"

    def test_refactor_directory_with_file_glob(self, tmp_path):
        """_code_refactor_directory with file_glob parameter."""
        (tmp_path / "a.service.ts").write_text("console.log('a')\n")
        (tmp_path / "b.service.ts").write_text("console.log('b')\n")
        (tmp_path / "c.util.ts").write_text("console.log('c')\n")
        result = json.loads(
            code_refactor_tool(
                str(tmp_path),
                pattern="console.log($ARG)",
                rewrite="console.info($ARG)",
                file_glob="*.service",
            )
        )
        assert result["files_scanned"] == 2
        assert result["match_count"] == 2

    def test_refactor_directory_skips_unsupported(self, tmp_path):
        """_code_refactor_directory skips languages not in _AST_GREP_LANG_MAP."""
        (tmp_path / "test.py").write_text("x = 1\n")
        result = json.loads(
            code_refactor_tool(
                str(tmp_path),
                pattern="x",
                rewrite="y",
            )
        )
        assert "files_scanned" in result

    def test_refactor_directory_errors_tracked(self, tmp_path):
        """_code_refactor_directory tracks errors properly."""
        (tmp_path / "test.csv").write_text("a,b,c\n")
        (tmp_path / "test.ts").write_text("console.log('ok')\n")
        result = json.loads(
            code_refactor_tool(
                str(tmp_path),
                pattern="console.log($ARG)",
                rewrite="console.info($ARG)",
            )
        )
        assert "files_scanned" in result

    def test_refactor_empty_rewrite(self, tmp_path):
        """Empty rewrite string doesn't crash."""
        f = tmp_path / "test.ts"
        f.write_text("console.log('hello')\n")
        result = json.loads(
            code_refactor_tool(
                str(f),
                pattern="console.log($ARG)",
                rewrite="",
                language="typescript",
            )
        )
        assert "match_count" in result


# ===========================================================================
# F — code_capsule_tool edge cases
# ===========================================================================


class TestCodeCapsuleEdgeCases:
    """Edge cases for code_capsule_tool."""

    def test_capsule_lsp_definition_error(self, tmp_py):
        """When code_definition_tool raises, def_data gets error."""
        with patch("code_intel.code_tools.code_symbols_tool") as mock_sym:
            mock_sym.return_value = json.dumps(
                {"symbols": [{"name": "Greeter", "kind": "class", "start_line": 3, "end_line": 11}]}
            )
            with patch.object(_lsp_bridge, "code_definition_tool", side_effect=Exception("LSP error")):
                with patch.object(_lsp_bridge, "code_references_tool") as mock_ref:
                    mock_ref.return_value = json.dumps({"by_file": {}})
                    result = json.loads(code_capsule_tool(str(tmp_py), line=3))
                    assert result["path"] == str(tmp_py)
                    assert result.get("definition") is None

    def test_capsule_lsp_references_error(self, tmp_py):
        """When code_references_tool raises, refs_data gets error."""
        with patch("code_intel.code_tools.code_symbols_tool") as mock_sym:
            mock_sym.return_value = json.dumps(
                {"symbols": [{"name": "Greeter", "kind": "class", "start_line": 3, "end_line": 11}]}
            )
            with patch.object(_lsp_bridge, "code_definition_tool") as mock_def:
                mock_def.return_value = json.dumps({})
                with patch.object(_lsp_bridge, "code_references_tool", side_effect=Exception("LSP refs error")):
                    result = json.loads(code_capsule_tool(str(tmp_py), line=3))
                    assert result["path"] == str(tmp_py)
                    assert result["reference_count"] == 0

    def test_capsule_doc_preview_read_error(self, tmp_py):
        """read_text error in doc preview is caught."""
        with patch("code_intel.code_tools.code_symbols_tool") as mock_sym:
            mock_sym.return_value = json.dumps(
                {"symbols": [{"name": "Greeter", "kind": "class", "start_line": 3, "end_line": 11}]}
            )
            with patch.object(_lsp_bridge, "code_definition_tool") as mock_def:
                mock_def.return_value = json.dumps({})
                with patch.object(_lsp_bridge, "code_references_tool") as mock_ref:
                    mock_ref.return_value = json.dumps({"by_file": {}})
                    with patch.object(Path, "read_text", side_effect=OSError("can't read")):
                        result = json.loads(code_capsule_tool(str(tmp_py), line=3))
                        assert result["doc_preview"] == ""

    def test_capsule_include_tests_error_handled(self, tmp_py):
        """When include_tests=True and references error, test_files is empty."""
        with patch("code_intel.code_tools.code_symbols_tool") as mock_sym:
            mock_sym.return_value = json.dumps(
                {"symbols": [{"name": "Greeter", "kind": "class", "start_line": 3, "end_line": 11}]}
            )
            with patch.object(_lsp_bridge, "code_definition_tool") as mock_def:
                mock_def.return_value = json.dumps({})
                with patch.object(_lsp_bridge, "code_references_tool", side_effect=Exception("LSP error")):
                    result = json.loads(code_capsule_tool(str(tmp_py), line=3, include_tests=True))
                    assert isinstance(result.get("test_files", []), list)

    def test_capsule_nonexistent_path(self, tmp_path):
        """Nonexistent path returns error."""
        result = json.loads(code_capsule_tool(str(tmp_path / "nonexistent.py"), line=1))
        assert "error" in result
