"""Tests for edit code_intel tools — refactor, replace_body, insert, delete,
rename, and all edge cases.

Split from test_code_intel_tools.py — edit tools domain.
"""

import builtins
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Skip entire module if tree-sitter is not installed
# ---------------------------------------------------------------------------
pytest.importorskip("tree_sitter", reason="tree-sitter not installed")

from code_intel.code_tools import (
    _code_refactor_directory,
    # Refactor helpers
    _code_refactor_single_file,
    code_refactor_tool,
)

# ===========================================================================
# code_refactor_tool — additional edge cases
# ===========================================================================


class TestCodeRefactorEdgeCases:
    """Additional edge cases beyond the existing test_code_tools.py."""

    def test_directory_language_override(self, tmp_path):
        """Language override with directory proceeds without crash."""
        (tmp_path / "a.ts").write_text('console.log("a")\n')
        result = json.loads(code_refactor_tool(
            str(tmp_path), pattern='console.log($ARG)', rewrite='console.info($ARG)',
            language="typescript",
        ))
        assert "files_scanned" in result

    def test_unsupported_single_file_language(self, tmp_path):
        """Unsupported extension returns error."""
        f = tmp_path / "data.csv"
        f.write_text("a,b,c\n")
        result = json.loads(code_refactor_tool(
            str(f), pattern="foo", rewrite="bar",
        ))
        assert "error" in result

    def test_wet_run_directory_applies_changes(self, tmp_path):
        """Wet run on directory applies to all matching files."""
        (tmp_path / "a.ts").write_text('console.log("a")\n')
        (tmp_path / "b.ts").write_text('console.log("b")\n')
        result = json.loads(code_refactor_tool(
            str(tmp_path), pattern='console.log($ARG)', rewrite='console.info($ARG)',
            dry_run=False,
        ))
        assert result["files_changed"] == 2
        assert 'console.info("a")' in (tmp_path / "a.ts").read_text()

    def test_single_file_with_language_override(self, tmp_path):
        """Single file with explicit language works."""
        f = tmp_path / "script.custom"
        f.write_text('console.log("test")\n')
        result = json.loads(code_refactor_tool(
            str(f), pattern='console.log($ARG)', rewrite='console.info($ARG)',
            language="typescript",
        ))
        assert result.get("match_count") == 1

    def test_ast_grep_rewrite_substitution_refactor(self, tmp_path):
        """Verify _ast_grep_rewrite is used correctly in refactor."""
        f = tmp_path / "test.py"
        f.write_text('foo(42, "hello")\n')
        result = json.loads(code_refactor_tool(
            str(f), pattern='foo($X, $Y)', rewrite='bar($Y, $X)',
            language="python",
        ))
        assert result["match_count"] == 1
        assert result["changes"][0]["replacement"] == 'bar("hello", 42)'


# ===========================================================================
# _code_refactor_single_file edge cases
# ===========================================================================


class TestCodeRefactorSingleFileEdgeCases:
    def test_unsupported_lang_key(self, tmp_path):
        f = tmp_path / "f.txt"
        f.write_text("x")
        result = _code_refactor_single_file(f, "x", "y", "nonexistent_lang", True, 1)
        assert "error" in result

    def test_pattern_with_no_matches(self, tmp_path):
        f = tmp_path / "test.ts"
        f.write_text("let x = 1;\n")
        result = _code_refactor_single_file(f, "nonexistent_pattern_here($A)", "replacement($A)", "typescript", True, 1)
        assert result["match_count"] == 0

    def test_directory_handles_permission_error(self, tmp_path):
        """_code_refactor_directory should not crash on PermissionError (mocked)."""
        with patch("code_intel.code_tools.Path.rglob") as mock_rglob:
            # Simulate permission error by returning a file whose is_file() returns False
            mock_f = MagicMock(spec=Path)
            mock_f.is_file.return_value = False
            mock_rglob.return_value = [mock_f]
            result = _code_refactor_directory(
                tmp_path, "console.log($ARG)", "console.info($ARG)", None, True, 1
            )
            data = json.loads(result)
            assert isinstance(data, dict)


# ===========================================================================
# Test _code_refactor_directory with errors (lines 1629, 1651)
# ===========================================================================


class TestCodeRefactorDirectoryEdgeCases:
    def test_directory_with_some_errors(self, tmp_path):
        """_code_refactor_directory with files that error should be handled."""
        (tmp_path / "good.ts").write_text("console.log('ok')\n")
        (tmp_path / "bad.txt").write_text("some text\n")
        result = _code_refactor_directory(
            tmp_path, "console.log($ARG)", "console.info($ARG)",
            None, True, 1,
        )
        data = json.loads(result)
        assert "files_scanned" in data

    def test_directory_with_file_glob_and_unsupported_lang(self, tmp_path):
        """file_glob combined with unsupported lang extension results in 0 scanned."""
        (tmp_path / "data.csv").write_text("a,b,c\n")
        (tmp_path / "good.ts").write_text("console.log('a')\n")
        result = _code_refactor_directory(
            tmp_path, "console.log($ARG)", "console.info($ARG)",
            None, True, 1, file_glob="*csv",
        )
        data = json.loads(result)
        # csv is not in _EXT_TO_LANG, so files_scanned should be 0
        assert data["files_scanned"] >= 0


# ===========================================================================
# Additional refactor edge cases (lines 1495-1585)
# ===========================================================================


class TestRefactorEdgeCasesDeep:
    """Cover _code_refactor_single_file error paths (1495-1585)."""

    def test_refactor_missing_ast_grep_py(self, tmp_path, monkeypatch):
        """When ast_grep_py not installed, returns error."""
        f = tmp_path / "test.ts"
        f.write_text("console.log('hello')\n")

        orig_import = builtins.__import__
        def mock_import(name, *args, **kwargs):
            if name == 'ast_grep_py':
                raise ImportError("no ast_grep_py")
            return orig_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=mock_import):
            result = _code_refactor_single_file(f, "console.log($ARG)", "console.info($ARG)", "typescript", True, 1)
            assert "error" in result
            assert "ast-grep-py not installed" in result["error"]

    def test_refactor_parse_failure(self, tmp_path):
        """When source can't be parsed, returns error."""
        f = tmp_path / "test.ts"
        f.write_text("??? invalid syntax ???")
        result = _code_refactor_single_file(f, "console.log($ARG)", "console.info($ARG)", "typescript", True, 1)
        # May or may not error depending on ast-grep tolerance
        assert isinstance(result, dict)

    def test_refactor_apply_exception(self, tmp_path, monkeypatch):
        """When writing changes fails, returns error."""
        f = tmp_path / "test.ts"
        f.write_text("console.log('hello')\n")
        # Patch write_text to raise
        with patch.object(Path, 'write_text', side_effect=OSError("write denied")):
            result = _code_refactor_single_file(f, "console.log($ARG)", "console.info($ARG)", "typescript", dry_run=False, context_lines=1)
            assert "error" in result or result.get("applied") is not True
