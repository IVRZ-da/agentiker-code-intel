"""git-Refs werden geprüft, bevor sie in argv landen.

Hermes-Review PR #157345, Punkt 6: ``base``/``head`` gingen ungeprüft in
``git diff {base}..{head}``. Ein Wert mit führendem ``-`` wird von git als
Option geparst (Argument-Injection), obwohl die Aufrufe ohne Shell laufen.
"""

from __future__ import annotations

import subprocess

import pytest

from code_intel._refs import check_git_refs, validate_git_ref

VALID = (
    "main",
    "HEAD",
    "HEAD~3",
    "HEAD^",
    "origin/main",
    "feature/x-1.2",
    "v0.6.36",
    "475a950",
    "main..dev",
    "main...dev",
    "HEAD@{1}",
)

INVALID = (
    "--output=/tmp/pwned",
    "-p",
    "--upload-pack=touch /tmp/x",
    "",
    "   ",
    "main dev",
    "main;rm -rf /",
    "a`whoami`",
    "a$(id)",
    "main\nHEAD",
    "main\0",
    "main|x",
)


@pytest.mark.parametrize("ref", VALID)
def test_valid_refs(ref):
    assert validate_git_ref(ref) is None


@pytest.mark.parametrize("ref", INVALID)
def test_invalid_refs(ref):
    message = validate_git_ref(ref)
    assert message, f"{ref!r} haette abgelehnt werden muessen"


def test_non_string_rejected():
    assert validate_git_ref(None)
    assert validate_git_ref(42)
    assert validate_git_ref(["main"])


def test_check_git_refs_reports_first_error():
    assert check_git_refs(base="main", head="HEAD") is None
    message = check_git_refs(base="--output=/tmp/x", head="HEAD")
    assert message and "base" in message


@pytest.fixture()
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    return repo


def test_diff_analysis_rejects_injected_ref(git_repo, tmp_path):
    from code_intel.tools.diff_analysis import code_diff_analysis_tool

    marker = tmp_path / "pwned"
    out = code_diff_analysis_tool(str(git_repo), base=f"--output={marker}", head="HEAD")
    assert "base" in out and ("not allowed" in out or "unsupported" in out)
    assert not marker.exists(), "git hat die Option ausgefuehrt"


def test_review_assistant_rejects_injected_ref(git_repo, tmp_path):
    from code_intel.tools.review_assistant import code_review_assistant_tool

    out = code_review_assistant_tool(str(git_repo), base="-p", head="HEAD")
    assert "base" in out and ("not allowed" in out or "unsupported" in out)


def test_diff_calls_use_end_of_options():
    """Schutzschicht 2: kein git-diff-Aufruf ohne --end-of-options."""
    from pathlib import Path

    tools_dir = Path(__file__).resolve().parent.parent / "tools"
    for name in ("diff_analysis.py", "review_assistant.py"):
        source = (tools_dir / name).read_text(encoding="utf-8")
        for line in source.splitlines():
            if '"git", "diff"' in line and "{base}..{head}" in line:
                assert "--end-of-options" in line, f"{name}: {line.strip()}"
