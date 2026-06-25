"""Tests for tools/blame.py — Per-line git blame tool.

Mocks subprocess.run and Path operations to avoid needing
a real git repository. Uses tmp_path for path existence checks.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from code_intel.tools import blame as blame_tools

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
# Fixtures
# ===========================================================================


@pytest.fixture
def mock_git_root():
    """_find_git_root returns a fake git root."""
    with patch.object(blame_tools, "_find_git_root", return_value="/fake/repo") as m:
        yield m


@pytest.fixture
def mock_git_root_none():
    """_find_git_root returns None → no git repo."""
    with patch.object(blame_tools, "_find_git_root", return_value=None) as m:
        yield m


@pytest.fixture
def mock_subprocess():
    """Mock subprocess.run used by _run_git."""
    with patch.object(blame_tools.subprocess, "run") as m:
        yield m


# ===========================================================================
# 1) _run_git
# ===========================================================================


class TestRunGit:
    def test_normal(self, mock_subprocess):
        mock_subprocess.return_value = _mock_completed(stdout="abc", returncode=0)
        result = blame_tools._run_git(["rev-parse", "HEAD"], "/repo")
        assert result.stdout == "abc"
        assert result.returncode == 0

    def test_timeout(self, mock_subprocess):
        mock_subprocess.side_effect = subprocess.TimeoutExpired(["git"], timeout=30)
        with pytest.raises(RuntimeError, match="timed out"):
            blame_tools._run_git(["blame"], "/repo")

    def test_file_not_found(self, mock_subprocess):
        mock_subprocess.side_effect = FileNotFoundError()
        with pytest.raises(RuntimeError, match="Git not found"):
            blame_tools._run_git(["blame"], "/repo")


# ===========================================================================
# 2) _find_git_root
# ===========================================================================


class TestFindGitRoot:
    def test_finds_root_for_directory(self, mock_subprocess):
        """Target is a directory → rev-parse on that dir."""
        mock_subprocess.return_value = _mock_completed(stdout="/fake/repo\n", returncode=0)
        root = blame_tools._find_git_root("/some/dir")
        assert root == "/fake/repo"

    def test_finds_root_for_file(self, mock_subprocess):
        """Target is a file → rev-parse on its parent."""
        with patch.object(Path, "is_file", return_value=True):
            with patch.object(Path, "parent", Path("/some/dir")):
                mock_subprocess.return_value = _mock_completed(stdout="/fake/repo\n", returncode=0)
                root = blame_tools._find_git_root("/some/dir/file.py")
                assert root == "/fake/repo"

    def test_not_a_repo(self, mock_subprocess):
        """rev-parse fails → None."""
        mock_subprocess.return_value = _mock_completed(stdout="", returncode=128, stderr="not a git repo")
        root = blame_tools._find_git_root("/some/dir")
        assert root is None

    def test_runtime_error_returns_none(self, mock_subprocess):
        """_run_git raises RuntimeError → None."""
        mock_subprocess.side_effect = RuntimeError("git not found")
        # We need to patch _run_git since _find_git_root calls it
        with patch.object(blame_tools, "_run_git") as mock_run:
            mock_run.side_effect = RuntimeError("git not found")
            root = blame_tools._find_git_root("/some/dir")
            assert root is None


# ===========================================================================
# 3) _parse_blame_porcelain
# ===========================================================================


SAMPLE_PORCELAIN = (
    "abc123def4567890123456789012345678901234 1 1 3\n"
    "author Alice\n"
    "author-mail <alice@example.com>\n"
    "author-time 1700000000\n"
    "\tline one\n"
    "\tline two\n"
    "\tline three\n"
    "def456abc7890123456789012345678901234567890 2 4 1\n"
    "author Bob\n"
    "author-mail <bob@test.com>\n"
    "author-time 1700100000\n"
    "\tline four\n"
)

SAMPLE_PORCELAIN_UNCOMMITTED = (
    "0000000000000000000000000000000000000000 1 1 1\n"
    "author Not Committed Yet\n"
    "author-mail <>\n"
    "author-time 0\n"
    "\tnew line\n"
)


class TestParseBlamePorcelain:
    def test_full_parse(self):
        parsed = blame_tools._parse_blame_porcelain(SAMPLE_PORCELAIN)
        assert len(parsed) == 4

        # First line
        assert parsed[0]["commit_hash"] == "abc123def4567890123456789012345678901234"
        assert parsed[0]["author"] == "Alice"
        assert parsed[0]["author_email"] == "alice@example.com"
        assert parsed[0]["line_number"] == 1
        assert parsed[0]["content"] == "line one"

        # Fourth line (Bob)
        assert parsed[3]["author"] == "Bob"
        assert parsed[3]["line_number"] == 4
        assert parsed[3]["content"] == "line four"

    def test_empty_input(self):
        assert blame_tools._parse_blame_porcelain("") == []

    def test_only_newlines(self):
        assert blame_tools._parse_blame_porcelain("\n\n\n") == []

    def test_uncommitted_changes(self):
        parsed = blame_tools._parse_blame_porcelain(SAMPLE_PORCELAIN_UNCOMMITTED)
        assert len(parsed) == 1
        assert parsed[0]["commit_hash"] == "0000000000000000000000000000000000000000"
        assert parsed[0]["content"] == "new line"
        # author-time 0 → fromtimestamp(0) → epoch
        assert parsed[0]["timestamp"] == "1970-01-01T00:00:00+00:00"

    def test_missing_author_fields_defaults(self):
        """Minimal porcelain with only hash and content."""
        porcelain = "abc123def4567890123456789012345678901234 1 1 1\n\tcontent line\n"
        parsed = blame_tools._parse_blame_porcelain(porcelain)
        assert len(parsed) == 1
        # 'author' key is never set if not in porcelain
        assert "author" not in parsed[0]
        assert parsed[0]["content"] == "content line"

    def test_bad_timestamp(self):
        """Invalid timestamp string should result in empty timestamp."""
        porcelain = "abc123def4567890123456789012345678901234 1 1 1\nauthor-time not_a_number\n\tcontent\n"
        parsed = blame_tools._parse_blame_porcelain(porcelain)
        assert len(parsed) == 1
        assert parsed[0]["timestamp"] == ""


# ===========================================================================
# 4) code_git_blame_tool
# ===========================================================================


# --- Path errors ---


class TestCodeGitBlameToolPathErrors:
    def test_path_not_found(self):
        result = json.loads(blame_tools.code_git_blame_tool(path="/nonexistent/file.py"))
        assert result["status"] == "error"
        assert "Path not found" in result["error"]

    def test_not_a_file(self, tmp_path):
        d = tmp_path / "adir"
        d.mkdir()
        result = json.loads(blame_tools.code_git_blame_tool(path=str(d)))
        assert result["status"] == "error"
        assert "Not a file" in result["error"]


# --- Git repo errors ---


class TestCodeGitBlameToolGitErrors:
    def test_no_git_repo(self, tmp_path, mock_git_root_none):
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")
        result = json.loads(blame_tools.code_git_blame_tool(path=str(f)))
        assert result["status"] == "error"
        assert "Not inside a git repository" in result["error"]

    def test_file_not_tracked(self, tmp_path, mock_git_root, mock_subprocess):
        """git ls-files --error-unmatch returns non-zero → untracked file."""
        f = tmp_path / "untracked.py"
        f.write_text("x = 1\n")
        mock_subprocess.return_value = _mock_completed(returncode=128, stderr="")

        result = json.loads(blame_tools.code_git_blame_tool(path=str(f)))
        assert result["status"] == "ok"
        assert result["blamed_lines"] == 0
        assert "not tracked" in result["message"]

    def test_tracked_check_runtime_error(self, tmp_path, mock_git_root, mock_subprocess):
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")
        mock_subprocess.side_effect = RuntimeError("failed")
        result = json.loads(blame_tools.code_git_blame_tool(path=str(f)))
        assert result["status"] == "error"
        assert "Failed to check git tracking" in result["error"]


# --- Happy paths ---


class TestCodeGitBlameToolHappy:
    def test_specific_line(self, tmp_path, mock_git_root, mock_subprocess):
        """line > 0: blame with -L start,end."""
        f = tmp_path / "test.py"
        f.write_text("line1\nline2\nline3\n")

        porcelain = (
            "abc123def4567890123456789012345678901234 1 1 1\n"
            "author Alice\n"
            "author-mail <alice@example.com>\n"
            "author-time 1700000000\n"
            "\tline2\n"
        )
        # First call: ls-files (returncode 0 = tracked)
        # Second call: blame
        mock_subprocess.side_effect = [
            _mock_completed(returncode=0),  # ls-files succeeds
            _mock_completed(stdout=porcelain, returncode=0),  # blame
        ]

        result = json.loads(blame_tools.code_git_blame_tool(path=str(f), line=2, limit=10))
        assert result["status"] == "ok"
        assert result["blamed_lines"] == 1
        assert result["line_requested"] == 2
        assert result["lines"][0]["author"] == "Alice"
        assert result["lines"][0]["line_number"] == 1

    def test_whole_file(self, tmp_path, mock_git_root, mock_subprocess):
        """line=0: blame entire file, paginated."""
        f = tmp_path / "test.py"
        f.write_text("a\nb\nc\n")

        h1 = "a" + "0" * 39
        h2 = "b" + "0" * 39
        h3 = "c" + "0" * 39
        porcelain = f"{h1} 1 1 1\n\ta\n{h2} 2 2 1\n\tb\n{h3} 3 3 1\n\tc\n"
        mock_subprocess.side_effect = [
            _mock_completed(returncode=0),
            _mock_completed(stdout=porcelain, returncode=0),
        ]

        result = json.loads(blame_tools.code_git_blame_tool(path=str(f), line=0, limit=200))
        assert result["status"] == "ok"
        assert result["blamed_lines"] == 3
        assert len(result["lines"]) == 3

    def test_paginates_whole_file(self, tmp_path, mock_git_root, mock_subprocess):
        """line=0 and parsed > limit → paginated to limit."""
        f = tmp_path / "test.py"
        f.write_text("\n".join(f"line{i}" for i in range(50)))

        # Build porcelain for 20 lines with valid hex hashes (40 chars, no 'h' prefix)
        lines_out = ""
        for i in range(20):
            sha = f"a{i:039d}"  # 40-char hex-valid hash (a + 39 decimal digits = all valid hex)
            lines_out += f"{sha} {i + 1} {i + 1} 1\n\theader line {i}\n"

        mock_subprocess.side_effect = [
            _mock_completed(returncode=0),
            _mock_completed(stdout=lines_out, returncode=0),
        ]

        result = json.loads(blame_tools.code_git_blame_tool(path=str(f), line=0, limit=5))
        assert result["status"] == "ok"
        # Should be paginated to 5
        assert result["blamed_lines"] == 5

    def test_empty_blame_output(self, tmp_path, mock_git_root, mock_subprocess):
        """Empty blame output (empty file) → ok with blamed_lines=0."""
        f = tmp_path / "empty.py"
        f.write_text("")

        mock_subprocess.side_effect = [
            _mock_completed(returncode=0),
            _mock_completed(stdout="", returncode=0),
        ]

        result = json.loads(blame_tools.code_git_blame_tool(path=str(f)))
        assert result["status"] == "ok"
        assert result["blamed_lines"] == 0
        assert "No blame information" in result["message"]


# --- Error paths ---


class TestCodeGitBlameToolErrors:
    def test_blame_nonzero_returncode(self, tmp_path, mock_git_root, mock_subprocess):
        """git blame returns non-zero → fmt_err with stderr."""
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")

        mock_subprocess.side_effect = [
            _mock_completed(returncode=0),
            _mock_completed(returncode=128, stderr="fatal: bad revision"),
        ]

        result = json.loads(blame_tools.code_git_blame_tool(path=str(f)))
        assert result["status"] == "error"
        assert "Git blame error" in result["error"]
        assert "fatal: bad revision" in result["error"]

    def test_blame_runtime_error(self, tmp_path, mock_git_root, mock_subprocess):
        """_run_git raises RuntimeError during blame."""
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")

        mock_subprocess.side_effect = [
            _mock_completed(returncode=0),
            RuntimeError("git blame failed"),
        ]

        result = json.loads(blame_tools.code_git_blame_tool(path=str(f)))
        assert result["status"] == "error"
        assert "Git blame failed" in result["error"]

    def test_limit_capped_at_200(self, tmp_path, mock_git_root, mock_subprocess):
        """limit > 200 is capped to 200."""
        f = tmp_path / "test.py"
        f.write_text("x\n")

        mock_subprocess.side_effect = [
            _mock_completed(returncode=0),
            _mock_completed(stdout="h" * 40 + " 1 1 1\n\tx\n", returncode=0),
        ]

        result = json.loads(blame_tools.code_git_blame_tool(path=str(f), limit=500))
        assert result["status"] == "ok"
        # The cap happens before calling blame, so limit reflects 200
        # but blame returns what it returns
        assert result["limit"] == 200


# ===========================================================================
# 5) _handle_code_git_blame
# ===========================================================================


class TestHandleCodeGitBlame:
    def test_handler_dispatches(self, tmp_path, mock_git_root, mock_subprocess):
        f = tmp_path / "test.py"
        f.write_text("x\n")

        sha = "a1234567890123456789012345678901234567890"
        mock_subprocess.side_effect = [
            _mock_completed(returncode=0),
            _mock_completed(stdout=f"{sha} 1 1 1\n\tx\n", returncode=0),
        ]

        result = json.loads(
            blame_tools._handle_code_git_blame(
                {
                    "path": str(f),
                    "line": "0",
                    "limit": "50",
                }
            )
        )
        assert result["status"] == "ok"
        assert result["blamed_lines"] >= 1
        assert result["path"] == str(f)

    def test_handler_defaults(self, tmp_path):
        """Handler with only path uses defaults."""
        f = tmp_path / "test.py"
        f.write_text("x\n")
        with patch.object(blame_tools, "_find_git_root", return_value="/fake/repo"):
            with patch.object(blame_tools.subprocess, "run") as mock_run:
                mock_run.side_effect = [
                    _mock_completed(returncode=0),
                    _mock_completed(stdout="h" * 40 + " 1 1 1\n\tx\n", returncode=0),
                ]
                result = json.loads(
                    blame_tools._handle_code_git_blame(
                        {
                            "path": str(f),
                        }
                    )
                )
        assert result["status"] == "ok"
