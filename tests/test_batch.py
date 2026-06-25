"""Tests for tools/batch.py — Bulk refactoring via ast-grep / fallback.

Mocks subprocess.run, shutil.which for ast-grep detection,
uses tmp_path for real temp directory and file I/O in fallback tests.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from code_intel.tools import batch as batch_tools

# ===========================================================================
# Helpers
# ===========================================================================


def _mock_completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


# ===========================================================================
# 1) _find_ast_grep
# ===========================================================================


class TestFindAstGrep:
    def test_sg_on_path(self):
        """shutil.which finds 'sg' → returns path."""
        with patch.object(batch_tools.shutil, "which", return_value="/usr/bin/sg"):
            result = batch_tools._find_ast_grep()
            assert result == "/usr/bin/sg"

    def test_sg_not_on_path_but_runs(self):
        """which returns None, but subprocess.run succeeds → returns 'sg'."""
        with patch.object(batch_tools.shutil, "which", return_value=None):
            with patch.object(batch_tools.subprocess, "run") as mock_run:
                mock_run.return_value = _mock_completed(stdout="sg 0.10", returncode=0)
                result = batch_tools._find_ast_grep()
                assert result == "sg"

    def test_sg_not_available(self):
        """which returns None and subprocess fails → returns None."""
        with patch.object(batch_tools.shutil, "which", return_value=None):
            with patch.object(batch_tools.subprocess, "run") as mock_run:
                mock_run.side_effect = FileNotFoundError("no sg")
                result = batch_tools._find_ast_grep()
                assert result is None

    def test_sg_timeout(self):
        """subprocess.run times out → returns None."""
        with patch.object(batch_tools.shutil, "which", return_value=None):
            with patch.object(batch_tools.subprocess, "run") as mock_run:
                mock_run.side_effect = subprocess.TimeoutExpired(cmd=["sg", "--version"], timeout=5)
                result = batch_tools._find_ast_grep()
                assert result is None


# ===========================================================================
# 2) _ast_grep_scan
# ===========================================================================


class TestAstGrepScan:
    def test_returns_list(self):
        """stdout is a list of results."""
        expected = [{"path": "a.ts", "matches": [{"line": 1}]}]
        with patch.object(batch_tools.subprocess, "run") as mock_run:
            mock_run.return_value = _mock_completed(stdout=json.dumps(expected), returncode=0)
            result = batch_tools._ast_grep_scan("sg", "/dir", "console.log($ARG)", "**/*.ts", "ts")
            assert result == expected

    def test_returns_dict_with_results(self):
        """stdout is a dict wrapping results."""
        data = {"results": [{"path": "b.ts", "matches": []}]}
        with patch.object(batch_tools.subprocess, "run") as mock_run:
            mock_run.return_value = _mock_completed(stdout=json.dumps(data), returncode=0)
            result = batch_tools._ast_grep_scan("sg", "/dir", "foo($A)", "**/*.ts", "ts")
            assert result == data["results"]

    def test_returns_dict_with_matches(self):
        """stdout is a dict with matches key."""
        data = {"matches": [{"path": "c.ts", "matches": []}]}
        with patch.object(batch_tools.subprocess, "run") as mock_run:
            mock_run.return_value = _mock_completed(stdout=json.dumps(data), returncode=0)
            result = batch_tools._ast_grep_scan("sg", "/dir", "foo($A)", "**/*.ts", "ts")
            assert result == data["matches"]

    def test_returns_empty_on_empty_stdout(self):
        """Empty stdout → []."""
        with patch.object(batch_tools.subprocess, "run") as mock_run:
            mock_run.return_value = _mock_completed(stdout="", returncode=0)
            result = batch_tools._ast_grep_scan("sg", "/dir", "foo($A)", "**/*.ts", "ts")
            assert result == []

    def test_returns_none_on_failure(self):
        """subprocess.TimeoutExpired → None."""
        with patch.object(batch_tools.subprocess, "run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(["sg"], timeout=120)
            result = batch_tools._ast_grep_scan("sg", "/dir", "foo($A)", "**/*.ts", "ts")
            assert result is None

    def test_json_decode_error(self):
        """Invalid JSON → None."""
        with patch.object(batch_tools.subprocess, "run") as mock_run:
            mock_run.return_value = _mock_completed(stdout="not json{{{", returncode=0)
            result = batch_tools._ast_grep_scan("sg", "/dir", "foo($A)", "**/*.ts", "ts")
            assert result is None


# ===========================================================================
# 3) _ast_grep_apply
# ===========================================================================


class TestAstGrepApply:
    def test_returns_list(self):
        expected = [{"path": "a.ts", "matches": [{"line": 1, "old": "foo()", "new": "bar()"}]}]
        with patch.object(batch_tools.subprocess, "run") as mock_run:
            mock_run.return_value = _mock_completed(stdout=json.dumps(expected), returncode=0)
            result = batch_tools._ast_grep_apply("sg", "/dir", "foo($A)", "bar($A)", "**/*.ts", "ts")
            assert result == expected

    def test_returns_dict_with_rewritten(self):
        data = {"rewritten": [{"path": "b.ts", "count": 1}]}
        with patch.object(batch_tools.subprocess, "run") as mock_run:
            mock_run.return_value = _mock_completed(stdout=json.dumps(data), returncode=0)
            result = batch_tools._ast_grep_apply("sg", "/dir", "foo($A)", "bar($A)", "**/*.ts", "ts")
            assert result == data["rewritten"]

    def test_returns_empty_on_empty_stdout(self):
        with patch.object(batch_tools.subprocess, "run") as mock_run:
            mock_run.return_value = _mock_completed(stdout="", returncode=0)
            result = batch_tools._ast_grep_apply("sg", "/dir", "foo($A)", "bar($A)", "**/*.ts", "ts")
            assert result == []

    def test_returns_none_on_oserror(self):
        with patch.object(batch_tools.subprocess, "run") as mock_run:
            mock_run.side_effect = OSError("pipe broken")
            result = batch_tools._ast_grep_apply("sg", "/dir", "foo($A)", "bar($A)", "**/*.ts", "ts")
            assert result is None


# ===========================================================================
# 4) _fallback_scan
# ===========================================================================


class TestFallbackScan:
    def test_finds_matches(self, tmp_path):
        """Scan a real temp dir with a file containing the pattern."""
        d = tmp_path / "src"
        d.mkdir()
        f = d / "test.ts"
        f.write_text("console.log('hello')\nconsole.log('world')\n")

        results = batch_tools._fallback_scan(str(d), r"console\.log\([^)]+\)", "**/*.ts")
        assert len(results) == 1
        assert results[0]["path"] == str(f)
        assert len(results[0]["matches"]) == 2

    def test_non_existent_directory(self, tmp_path):
        """Non-existent dir → empty list."""
        results = batch_tools._fallback_scan(str(tmp_path / "nonexistent"), "foo", "**/*.ts")
        assert results == []

    def test_no_matching_extension(self, tmp_path):
        """File with non-matching extension → empty results."""
        d = tmp_path / "src"
        d.mkdir()
        (d / "test.java").write_text("console.log('hi')")
        results = batch_tools._fallback_scan(str(d), "console.log", "**/*.ts")
        assert results == []

    def test_invalid_pattern_falls_back_to_escape(self, tmp_path):
        """Invalid regex triggers re.escape fallback."""
        d = tmp_path / "src"
        d.mkdir()
        f = d / "test.py"
        f.write_text("foo(bar)\n")
        results = batch_tools._fallback_scan(str(d), r"[invalid", "**/*.py")
        # After re.escape, it becomes a literal search for '[invalid'
        assert len(results) == 0

    def test_unicode_decode_error_skipped(self, tmp_path):
        """Binary file skipped gracefully."""
        d = tmp_path / "src"
        d.mkdir()
        f = d / "test.bin"
        f.write_bytes(b"\x80\x81\x82")
        results = batch_tools._fallback_scan(str(d), "foo", "**/*.bin")
        assert results == []

    def test_skip_subdirectories(self, tmp_path):
        """Subdirectories in the tree are skipped (not files)."""
        d = tmp_path / "src"
        d.mkdir()
        sub = d / "subdir"
        sub.mkdir()
        f = d / "test.ts"
        f.write_text("foo\n")
        results = batch_tools._fallback_scan(str(d), "foo", "**/*.ts")
        assert len(results) == 1

    def test_oserror_on_read_skipped(self, tmp_path):
        """File that raises OSError on read is skipped."""
        d = tmp_path / "src"
        d.mkdir()
        f = d / "test.ts"
        f.write_text("foo\n")

        with patch.object(Path, "read_text") as mock_read:
            mock_read.side_effect = OSError("permission denied")
            results = batch_tools._fallback_scan(str(d), "foo", "**/*.ts")
            assert results == []


# ===========================================================================
# 5) _fallback_apply
# ===========================================================================


class TestFallbackApply:
    def test_applies_changes(self, tmp_path):
        """Replace pattern in real files."""
        d = tmp_path / "src"
        d.mkdir()
        f = d / "test.ts"
        f.write_text("console.log('hello')\nconsole.log('world')\n")

        results = batch_tools._fallback_apply(str(d), r"console\.log", "console.info", "**/*.ts")
        assert len(results) == 1
        assert results[0]["path"] == str(f)
        assert results[0]["count"] == 2
        assert "console.info" in results[0]["new_text"]

    def test_non_existent_directory(self, tmp_path):
        results = batch_tools._fallback_apply(str(tmp_path / "nonexistent"), "foo", "bar", "**/*.ts")
        assert results == []

    def test_no_matches(self, tmp_path):
        d = tmp_path / "src"
        d.mkdir()
        (d / "test.ts").write_text("nothing to see\n")
        results = batch_tools._fallback_apply(str(d), "zzz_nonexistent", "bar", "**/*.ts")
        assert results == []

    def test_invalid_pattern_fallback(self, tmp_path):
        """Invalid regex triggers re.escape fallback — applies literal match."""
        d = tmp_path / "src"
        d.mkdir()
        f = d / "test.py"
        f.write_text("foo[bar]\n")
        results = batch_tools._fallback_apply(str(d), r"[invalid", "replacement", "**/*.py")
        # After re.escape, '[invalid' doesn't match 'foo[bar]'
        assert results == []

    def test_no_extension_match(self, tmp_path):
        d = tmp_path / "src"
        d.mkdir()
        (d / "test.java").write_text("foo")
        results = batch_tools._fallback_apply(str(d), "foo", "bar", "**/*.ts")
        assert results == []

    def test_oserror_reading_file(self, tmp_path):
        """File that raises OSError on read is skipped."""
        d = tmp_path / "src"
        d.mkdir()
        f = d / "test.ts"
        f.write_text("foo")

        with patch.object(Path, "read_text") as mock_read:
            mock_read.side_effect = OSError("permission denied")
            results = batch_tools._fallback_apply(str(d), "foo", "bar", "**/*.ts")
            assert results == []

    def test_skip_subdirectories_fallback_apply(self, tmp_path):
        """Subdirectories are skipped in fallback_apply."""
        d = tmp_path / "src"
        d.mkdir()
        sub = d / "subdir"
        sub.mkdir()
        f = d / "test.ts"
        f.write_text("foo\n")
        results = batch_tools._fallback_apply(str(d), "foo", "bar", "**/*.ts")
        assert len(results) == 1


# ===========================================================================
# 6) _glob_to_extensions
# ===========================================================================


class TestGlobToExtensions:
    def test_normal_glob(self):
        assert batch_tools._glob_to_extensions("**/*.ts") == {".ts"}

    def test_multiple_ext(self):
        assert batch_tools._glob_to_extensions("**/*.spec.ts") == {".ts"}

    def test_wildcard_ext(self):
        assert batch_tools._glob_to_extensions("**/*.*") == set()

    def test_no_dot(self):
        """Pattern without a dot → empty set."""
        assert batch_tools._glob_to_extensions("Makefile") == set()

    def test_py_glob(self):
        assert batch_tools._glob_to_extensions("**/*.py") == {".py"}


# ===========================================================================
# 7) code_batch_refactor_tool
# ===========================================================================


class TestCodeBatchRefactorTool:
    def test_path_not_found(self):
        """Non-existent path → fmt_err."""
        result = json.loads(batch_tools.code_batch_refactor_tool(path="/nonexistent/thing", pattern="a", rewrite="b"))
        assert result["status"] == "error"
        assert "Path not found" in result["error"]

    def test_dry_run_with_ast_grep(self, tmp_path):
        """ast-grep found, dry_run=True → uses _ast_grep_scan."""
        d = tmp_path / "src"
        d.mkdir()
        (d / "test.ts").write_text("console.log('hi')\n")

        mock_matches = [{"path": str(d / "test.ts"), "matches": [{"line": 1, "old": "console.log('hi')"}]}]

        with patch.object(batch_tools, "_find_ast_grep", return_value="/usr/bin/sg"):
            with patch.object(batch_tools, "_ast_grep_scan", return_value=mock_matches):
                result = json.loads(
                    batch_tools.code_batch_refactor_tool(
                        path=str(d), pattern="console.log($ARG)", rewrite="console.info($ARG)"
                    )
                )

        assert result["status"] == "ok"
        assert result["dry_run"] is True
        assert result["used_ast_grep"] is True
        assert result["files_scanned"] >= 1
        assert result["total_matches"] >= 1

    def test_dry_run_ast_grep_fails_fallback(self, tmp_path):
        """ast-grep found but scan returns None → falls back to _fallback_scan."""
        d = tmp_path / "src"
        d.mkdir()
        (d / "test.ts").write_text("console.log('hi')\n")

        with patch.object(batch_tools, "_find_ast_grep", return_value="sg"):
            with patch.object(batch_tools, "_ast_grep_scan", return_value=None):
                result = json.loads(
                    batch_tools.code_batch_refactor_tool(
                        path=str(d), pattern=r"console\.log\('[^']*'\)", rewrite="x", file_glob="**/*.ts"
                    )
                )

        assert result["status"] == "ok"
        assert result["used_ast_grep"] is False
        assert result["total_matches"] >= 1

    def test_dry_run_no_ast_grep_fallback(self, tmp_path):
        """ast-grep not found → uses fallback."""
        d = tmp_path / "src"
        d.mkdir()
        (d / "test.ts").write_text("console.log('hi')\n")

        with patch.object(batch_tools, "_find_ast_grep", return_value=None):
            result = json.loads(
                batch_tools.code_batch_refactor_tool(
                    path=str(d), pattern=r"console\.log\('[^']*'\)", rewrite="console.info($ARG)", file_glob="**/*.ts"
                )
            )

        assert result["status"] == "ok"
        assert result["used_ast_grep"] is False
        assert result["total_matches"] >= 1

    def test_non_dry_run_with_ast_grep(self, tmp_path):
        """ast-grep found, dry_run=False → uses _ast_grep_apply."""
        d = tmp_path / "src"
        d.mkdir()
        (d / "test.ts").write_text("console.log('hi')\n")

        mock_matches = [
            {
                "path": str(d / "test.ts"),
                "matches": [{"line": 1, "old": "console.log('hi')", "new": "console.info('hi')"}],
            }
        ]

        with patch.object(batch_tools, "_find_ast_grep", return_value="sg"):
            with patch.object(batch_tools, "_ast_grep_apply", return_value=mock_matches):
                result = json.loads(
                    batch_tools.code_batch_refactor_tool(
                        path=str(d), pattern="console.log($ARG)", rewrite="console.info($ARG)", dry_run=False
                    )
                )

        assert result["status"] == "ok"
        assert result["dry_run"] is False
        assert result["used_ast_grep"] is True
        assert result["files_actually_changed"] >= 1
        assert result["backups_created"] != "none"

    def test_non_dry_run_fallback_apply(self, tmp_path):
        """No ast-grep, dry_run=False → uses _fallback_apply with file writes."""
        d = tmp_path / "src"
        d.mkdir()
        f = d / "test.ts"
        f.write_text("console.log('hi')\n")

        with patch.object(batch_tools, "_find_ast_grep", return_value=None):
            result = json.loads(
                batch_tools.code_batch_refactor_tool(
                    path=str(d),
                    pattern=r"console\.log\('[^']*'\)",
                    rewrite="console.info('hi')",
                    file_glob="**/*.ts",
                    dry_run=False,
                )
            )

        assert result["status"] == "ok"
        assert result["dry_run"] is False
        assert result["used_ast_grep"] is False
        assert result["files_actually_changed"] >= 1
        # The file should have been modified
        assert ".bak" in result["backups_created"]
        # Verify .bak was actually created
        bak = f.with_suffix(f.suffix + ".bak")
        assert bak.exists()

    def test_non_dry_run_fallback_apply_write_error(self, tmp_path):
        """Fallback apply write error is caught gracefully."""
        d = tmp_path / "src"
        d.mkdir()
        f = d / "test.ts"
        f.write_text("foo\n")

        with patch.object(batch_tools, "_find_ast_grep", return_value=None):
            # Mock write_text to fail so it gets caught
            original_write = Path.write_text

            def _bad_write(self_self, *a, **kw):
                if "test.ts" in str(self_self):
                    raise OSError("disk full")
                return original_write(self_self, *a, **kw)

            with patch.object(Path, "write_text", _bad_write):
                result = json.loads(
                    batch_tools.code_batch_refactor_tool(
                        path=str(d), pattern="foo", rewrite="bar", file_glob="**/*.ts", dry_run=False
                    )
                )

        # Should still return ok with 0 actually changed
        assert result["status"] == "ok"

    def test_ast_grep_apply_no_new_in_result(self, tmp_path):
        """When ast-grep apply result has no 'new' field, rewrite is used as new_hint."""
        d = tmp_path / "src"
        d.mkdir()
        (d / "test.ts").write_text("foo()\n")

        mock_matches = [{"path": str(d / "test.ts"), "matches": [{"line": 1, "old": "foo()"}]}]

        with patch.object(batch_tools, "_find_ast_grep", return_value="sg"):
            with patch.object(batch_tools, "_ast_grep_apply", return_value=mock_matches):
                result = json.loads(
                    batch_tools.code_batch_refactor_tool(
                        path=str(d), pattern="foo($A)", rewrite="bar($A)", dry_run=False
                    )
                )

        assert result["status"] == "ok"
        # new_hint should fall back to the rewrite template
        assert result["changes"][0]["new"] == "bar($A)"

    def test_non_dry_run_no_fallback_no_sg(self, tmp_path):
        """Path is a file, ast-grep not found, dry_run=False.
        Uses fallback apply on the file's parent dir."""
        d = tmp_path / "src"
        d.mkdir()
        f = d / "test.ts"
        f.write_text("old_style()\n")

        with patch.object(batch_tools, "_find_ast_grep", return_value=None):
            result = json.loads(
                batch_tools.code_batch_refactor_tool(
                    path=str(f), pattern=r"old_style", rewrite="new_style", file_glob="**/*.ts", dry_run=False
                )
            )

        assert result["status"] == "ok"
        assert result["files_actually_changed"] >= 1

    def test_result_with_non_dict_items_skipped(self, tmp_path):
        """matches list containing non-dict items is handled."""
        d = tmp_path / "src"
        d.mkdir()
        (d / "test.ts").write_text("foo\n")

        mock_matches = [None, "string", 42, {"path": str(d / "test.ts"), "matches": [{"line": 1, "old": "foo"}]}]

        with patch.object(batch_tools, "_find_ast_grep", return_value="sg"):
            with patch.object(batch_tools, "_ast_grep_scan", return_value=mock_matches):
                result = json.loads(batch_tools.code_batch_refactor_tool(path=str(d), pattern="foo", rewrite="bar"))

        assert result["status"] == "ok"
        # The non-dict items should be skipped; the valid one counted
        assert result["total_matches"] >= 1

    def test_result_with_empty_path_skipped(self, tmp_path):
        """Result dict with empty 'path' is skipped."""
        d = tmp_path / "src"
        d.mkdir()
        (d / "test.ts").write_text("foo\n")

        mock_matches = [{"path": "", "matches": [{"line": 1, "old": "foo"}]}]

        with patch.object(batch_tools, "_find_ast_grep", return_value="sg"):
            with patch.object(batch_tools, "_ast_grep_scan", return_value=mock_matches):
                result = json.loads(batch_tools.code_batch_refactor_tool(path=str(d), pattern="foo", rewrite="bar"))
        assert result["status"] == "ok"

    def test_result_with_no_matches_skipped(self, tmp_path):
        """Result dict with empty matches list does not count as changed."""
        d = tmp_path / "src"
        d.mkdir()
        (d / "test.ts").write_text("foo\n")

        mock_matches = [{"path": str(d / "test.ts"), "matches": []}]

        with patch.object(batch_tools, "_find_ast_grep", return_value="sg"):
            with patch.object(batch_tools, "_ast_grep_scan", return_value=mock_matches):
                result = json.loads(batch_tools.code_batch_refactor_tool(path=str(d), pattern="foo", rewrite="bar"))
        assert result["status"] == "ok"
        assert result["files_changed"] == 0
        assert result["total_matches"] == 0

    def test_non_dry_run_fallback_no_text_skipped(self, tmp_path):
        """Fallback apply result without new_text is skipped (line 382)."""
        d = tmp_path / "src"
        d.mkdir()
        f = d / "test.ts"
        f.write_text("foo\n")

        mock_result = [{"path": str(f), "matches": [{"line": 1, "old": "foo", "new": "bar"}]}]
        # This result has no "new_text" key, so it is skipped in the apply loop

        with patch.object(batch_tools, "_find_ast_grep", return_value=None):
            with patch.object(batch_tools, "_fallback_apply", return_value=mock_result):
                result = json.loads(
                    batch_tools.code_batch_refactor_tool(
                        path=str(d), pattern="foo", rewrite="bar", file_glob="**/*.ts", dry_run=False
                    )
                )
        assert result["status"] == "ok"

    def test_non_dry_run_fallback_non_existent_file(self, tmp_path):
        """Fallback apply result with file not on disk is skipped (line 385)."""
        d = tmp_path / "src"
        d.mkdir()

        mock_result = [
            {
                "path": str(d / "nonexistent.ts"),
                "new_text": "bar\n",
                "count": 1,
                "matches": [],
            }
        ]

        with patch.object(batch_tools, "_find_ast_grep", return_value=None):
            with patch.object(batch_tools, "_fallback_apply", return_value=mock_result):
                result = json.loads(
                    batch_tools.code_batch_refactor_tool(
                        path=str(d), pattern="foo", rewrite="bar", file_glob="**/*.ts", dry_run=False
                    )
                )
        assert result["status"] == "ok"


# ===========================================================================
# 8) _handle_code_batch_refactor
# ===========================================================================


class TestHandleCodeBatchRefactor:
    def test_handler_dispatches_correctly(self, tmp_path):
        """Handler wrapper calls tool with correct args."""
        d = tmp_path / "src"
        d.mkdir()
        (d / "test.ts").write_text("foo\n")

        result = json.loads(
            batch_tools._handle_code_batch_refactor(
                {
                    "path": str(d),
                    "pattern": "foo",
                    "rewrite": "bar",
                    "file_glob": "**/*.ts",
                    "dry_run": True,
                    "language": "ts",
                }
            )
        )
        assert result["status"] == "ok"

    def test_handler_defaults(self, tmp_path):
        """Default args are applied correctly."""
        d = tmp_path / "src"
        d.mkdir()
        (d / "test.ts").write_text("foo\n")

        result = json.loads(
            batch_tools._handle_code_batch_refactor(
                {
                    "path": str(d),
                    "pattern": "foo",
                    "rewrite": "bar",
                }
            )
        )
        assert result["status"] == "ok"
        assert result["file_glob"] == "**/*.ts"
        assert result["dry_run"] is True
        assert result["language"] == "ts"
