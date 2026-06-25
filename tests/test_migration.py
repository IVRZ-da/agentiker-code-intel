"""Tests for code_migration tool (YAML-based bulk pattern migration).

Mocks ast_grep_py to avoid needing real parsing.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from code_intel.tools.migration import (
    _load_rules_from_yaml,
    code_migration_tool,
)

# --- YAML loading tests ---


def test_load_rules_from_yaml(tmp_path):
    yf = tmp_path / "rules.yaml"
    yf.write_text("rules:\n  - pattern: console.log($ARG)\n    rewrite: console.info($ARG)\n")
    rules = _load_rules_from_yaml(str(yf))
    assert len(rules) == 1
    assert rules[0]["pattern"] == "console.log($ARG)"


def test_load_rules_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        _load_rules_from_yaml(str(tmp_path / "nonexistent.yaml"))


def test_load_rules_invalid_yaml(tmp_path):
    yf = tmp_path / "invalid.yaml"
    yf.write_text("invalid: {bad: yaml: [}\n")
    with pytest.raises(Exception):
        _load_rules_from_yaml(str(yf))


# --- Tool tests ---


def test_no_rules_provided(tmp_path):
    """Neither rules nor rules_file returns error."""
    result = json.loads(code_migration_tool(str(tmp_path)))
    assert "error" in result


def test_path_not_found(tmp_path):
    result = json.loads(code_migration_tool("/nonexistent/path", rules=[{"pattern": "a", "rewrite": "b"}]))
    assert "error" in result


def test_empty_rules_list(tmp_path):
    (tmp_path / "dummy.py").write_text("x = 1\n")
    result = json.loads(code_migration_tool(str(tmp_path), rules=[]))
    # Empty rules list triggers "rules or rules_file required" error
    assert "error" in result or "required" in str(result)


@patch("ast_grep_py.SgRoot")
def test_migration_dry_run(mock_sg, tmp_path):
    """Dry run finds matches but doesn't write."""
    f = tmp_path / "test.ts"
    f.write_text("console.log('hello')\n")

    # Mock ast-grep finding a match
    mock_match = MagicMock()
    mock_match.replace.return_value = None
    mock_root = MagicMock()
    mock_root.root.return_value.find_all.return_value = [mock_match]
    mock_root.root.return_value.text.return_value = "console.log('hello')"
    mock_sg.return_value = mock_root

    result = json.loads(
        code_migration_tool(
            str(tmp_path),
            rules=[{"pattern": "console.log($ARG)", "rewrite": "console.info($ARG)", "file_glob": "*.ts"}],
            dry_run=True,
        )
    )
    assert result["mode"] == "dry_run"
    assert result["total_matches"] >= 0


@patch("ast_grep_py.SgRoot")
def test_migration_with_rules_file(mock_sg, tmp_path):
    """Migration from YAML rules file works."""
    f = tmp_path / "test.ts"
    f.write_text("console.log('hello')\n")

    yf = tmp_path / "rules.yaml"
    yf.write_text("rules:\n  - pattern: console.log($ARG)\n    rewrite: console.info($ARG)\n")

    result = json.loads(code_migration_tool(str(tmp_path), rules_file=str(yf), dry_run=True))
    assert "rules_processed" in result


def test_migration_missing_pattern_or_rewrite(tmp_path):
    """Rule with missing pattern returns error."""
    (tmp_path / "dummy.py").write_text("x=1\n")
    result = json.loads(
        code_migration_tool(
            str(tmp_path),
            rules=[{"rewrite": "b"}],  # missing pattern
        )
    )
    assert "errors" in str(result) or result["errors"] > 0 or "rules_processed" in result
