"""Tests for tools/migration.py — YAML-based bulk pattern migration tool.

Coverage goal: 69% → 90%+
Tests cover:
  - _load_rules_from_yaml: happy, file not found, missing rules key, empty YAML
  - code_migration_tool: parameter validation, rule processing with mocked ast-grep,
    all error branches (missing pattern/rewrite, parse failure, rewrite failure,
    write failure, excluded dirs, import error)
  - _handle_code_migration handler (with mocked code_tools import)
  - CODE_MIGRATION_SCHEMA validation
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

# Module under test — imported AFTER conftest mocks (fmt, etc.)
from code_intel.tools.migration import (
    CODE_MIGRATION_SCHEMA,
    _handle_code_migration,
    _load_rules_from_yaml,
    code_migration_tool,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_TS = "console.log('hello')\n"


def _parse(result: str) -> dict:
    """Parse fmt_ok/fmt_err (JSON from conftest mock) back to dict."""
    if isinstance(result, dict):
        return result
    return json.loads(result)


def _assert_ok(result: dict) -> dict:
    assert result["status"] == "ok", f"Expected ok, got: {result}"
    return result


def _assert_err(result: dict) -> dict:
    assert result["status"] == "error", f"Expected error, got: {result}"
    return result


# ---------------------------------------------------------------------------
# Mock ast-grep factory
# ---------------------------------------------------------------------------


def _make_sg_module(
    find_all_return: list | None = None,
    sg_root_side_effect: list[Exception | Any] | None = None,
    replace_side_effect: Exception | None = None,
) -> Any:
    """Build a mock ast_grep_py module with controlled behavior.

    Args:
        find_all_return: List of mock matches to return (default: []).
        sg_root_side_effect: If set, iterable of return values/exceptions
            for SgRoot() calls (enables testing fallback logic).
        replace_side_effect: If set, match.replace() raises this exception.

    Returns:
        A mock module object that behaves like ast_grep_py.
    """
    mock_mod = MagicMock()

    # Setup match object
    mock_match = MagicMock()
    if replace_side_effect:
        mock_match.replace.side_effect = replace_side_effect
    else:
        mock_match.replace.return_value = None

    # Determine what find_all returns.
    # If find_all_return is given, use that list (which may include mock_match).
    # If replace_side_effect is set but find_all_return is not, wire mock_match into the result.
    if find_all_return is not None:
        result_list = find_all_return
    elif replace_side_effect:
        result_list = [mock_match]
    else:
        result_list = []

    # Setup find_all on the root node
    mock_root_node = MagicMock()
    mock_root_node.find_all.return_value = result_list

    # Setup SgRoot — the .root() method returns the root node
    mock_sg_root = MagicMock()
    mock_sg_root.root.return_value = mock_root_node

    if sg_root_side_effect:
        mock_mod.SgRoot.side_effect = sg_root_side_effect
    else:
        mock_mod.SgRoot.return_value = mock_sg_root

    return mock_mod


# ── Real __import__ captured once (before any mocking) ─────────────
_real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__


def _make_mock_import(ast_grep_mock: Any) -> Any:
    """Create a side_effect function for patching builtins.__import__.

    Returns the mock for ast_grep_py and uses real import for everything else.
    Uses the pre-captured _real_import to avoid recursion (since
    patching builtins.__import__ also affects __builtins__).
    """
    def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "ast_grep_py":
            return ast_grep_mock
        return _real_import(name, *args, **kwargs)

    return mock_import


# ---------------------------------------------------------------------------
# _load_rules_from_yaml
# ---------------------------------------------------------------------------


class TestLoadRulesFromYaml:
    """lines 74-83: YAML rules loader."""

    def test_happy_path(self, tmp_path: Path) -> None:
        """Valid YAML with rules list returns the rules."""
        yml = tmp_path / "rules.yml"
        rules_data = [{"pattern": "foo", "rewrite": "bar"}]
        yml.write_text(yaml.dump({"rules": rules_data}))
        assert _load_rules_from_yaml(str(yml)) == rules_data

    def test_file_not_found(self) -> None:
        """Line 78: non-existent path → FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            _load_rules_from_yaml("/nonexistent/path/to/rules.yml")

    def test_missing_rules_key(self, tmp_path: Path) -> None:
        """Line 81-82: YAML without 'rules' key → ValueError."""
        yml = tmp_path / "bad.yml"
        yml.write_text(yaml.dump({"foo": "bar"}))
        with pytest.raises(ValueError, match="must contain a 'rules' list"):
            _load_rules_from_yaml(str(yml))

    def test_empty_yaml(self, tmp_path: Path) -> None:
        """Line 81: empty YAML (not data) → ValueError."""
        yml = tmp_path / "empty.yml"
        yml.write_text("")
        with pytest.raises(ValueError, match="must contain a 'rules' list"):
            _load_rules_from_yaml(str(yml))


# ---------------------------------------------------------------------------
# code_migration_tool — parameter validation & early returns
# ---------------------------------------------------------------------------


class TestCodeMigrationToolValidation:
    """Lines 107-119: parameter validation."""

    def test_no_rules_no_rules_file(self) -> None:
        """Lines 114-115: neither rules nor rules_file → fmt_err."""
        result = _parse(code_migration_tool(path="/tmp", rules=None, rules_file=""))
        _assert_err(result)
        assert "Either 'rules' or 'rules_file' is required" in str(result)

    def test_path_not_found(self) -> None:
        """Lines 118-119: non-existent path → fmt_err."""
        result = _parse(code_migration_tool(
            path="/nonexistent_path_xyz_99999",
            rules=[{"pattern": "foo", "rewrite": "bar"}],
        ))
        _assert_err(result)
        assert "Path not found" in str(result)

    def test_rules_file_not_found(self, tmp_path: Path) -> None:
        """Lines 110-111: rules_file pointing to nonexistent file → fmt_err."""
        result = _parse(code_migration_tool(
            path=str(tmp_path),
            rules_file=str(tmp_path / "no_such_file.yml"),
        ))
        _assert_err(result)

    def test_rules_file_bad_yaml(self, tmp_path: Path) -> None:
        """Lines 110-111: rules_file with YAML missing 'rules' key → fmt_err."""
        yml = tmp_path / "bad.yml"
        yml.write_text("foo: bar")
        result = _parse(code_migration_tool(
            path=str(tmp_path),
            rules_file=str(yml),
        ))
        _assert_err(result)


# ---------------------------------------------------------------------------
# code_migration_tool — rule processing with mocked ast-grep
# ---------------------------------------------------------------------------


class TestCodeMigrationToolRules:
    """Lines 122-217: rule processing, error handling, reporting."""

    def _run(self, path: str, rules: list | None = None,
             rules_file: str = "", dry_run: bool = True,
             ast_grep_mock: Any | None = None) -> dict:
        """Call code_migration_tool with ast_grep_py import mocked."""
        mock_mod = ast_grep_mock or _make_sg_module()
        mock_import = _make_mock_import(mock_mod)
        with patch("builtins.__import__", side_effect=mock_import):
            return _parse(code_migration_tool(
                path=path,
                rules=rules,
                rules_file=rules_file,
                dry_run=dry_run,
            ))

    # ── Happy paths ──────────────────────────────────────────────────

    def test_happy_path_dry_run(self, tmp_path: Path) -> None:
        """Lines 179-189: matches found, dry-run → no files written."""
        (tmp_path / "test.ts").write_text(SAMPLE_TS)

        mock_match = MagicMock()
        mock_mod = _make_sg_module(find_all_return=[mock_match])

        result = self._run(
            path=str(tmp_path),
            rules=[{
                "pattern": "console.log($ARG)",
                "rewrite": "console.info($ARG)",
                "file_glob": "*.ts",
                "language": "typescript",
                "name": "log-to-info",
            }],
            dry_run=True,
            ast_grep_mock=mock_mod,
        )

        _assert_ok(result)
        assert result["mode"] == "dry_run"
        assert result["rules_processed"] == 1
        assert result["total_matches"] == 1
        assert result["total_files_changed"] == 0

    def test_happy_path_applied(self, tmp_path: Path) -> None:
        """Lines 191-196: dry_run=False → files written."""
        (tmp_path / "test.ts").write_text(SAMPLE_TS)

        mock_mod = _make_sg_module(find_all_return=[MagicMock()])

        result = self._run(
            path=str(tmp_path),
            rules=[{
                "pattern": "console.log($ARG)",
                "rewrite": "console.info($ARG)",
                "file_glob": "*.ts",
                "language": "typescript",
            }],
            dry_run=False,
            ast_grep_mock=mock_mod,
        )

        _assert_ok(result)
        assert result["mode"] == "applied"
        assert result["total_files_changed"] == 1

    def test_no_matches(self, tmp_path: Path) -> None:
        """Line 181: no matching pattern → zero matches, continues."""
        (tmp_path / "test.ts").write_text(SAMPLE_TS)

        mock_mod = _make_sg_module(find_all_return=[])

        result = self._run(
            path=str(tmp_path),
            rules=[{
                "pattern": "this_never_matches_xyz($$$ARGS)",
                "rewrite": "nope($$$ARGS)",
                "name": "no-match-rule",
            }],
            ast_grep_mock=mock_mod,
        )

        _assert_ok(result)
        assert result["total_matches"] == 0
        assert result["results"][0]["matches"] == 0

    def test_multiple_rules(self, tmp_path: Path) -> None:
        """Lines 127-207: multiple rules → each processed."""
        (tmp_path / "a.ts").write_text(SAMPLE_TS)

        mock_mod = _make_sg_module(find_all_return=[MagicMock()])

        result = self._run(
            path=str(tmp_path),
            rules=[
                {"pattern": "foo", "rewrite": "bar", "name": "rule1"},
                {"pattern": "baz", "rewrite": "qux", "name": "rule2"},
            ],
            ast_grep_mock=mock_mod,
        )

        _assert_ok(result)
        assert result["rules_processed"] == 2
        assert len(result["results"]) == 2

    def test_excluded_dirs(self, tmp_path: Path) -> None:
        """Lines 154-156: files in excluded dirs are skipped."""
        for d in ("node_modules", ".git", "__pycache__", "build", "dist", ".venv"):
            p = tmp_path / d
            p.mkdir(parents=True, exist_ok=True)
            (p / "test.ts").write_text(SAMPLE_TS)

        (tmp_path / "real.ts").write_text(SAMPLE_TS)

        mock_mod = _make_sg_module(find_all_return=[MagicMock()])

        result = self._run(
            path=str(tmp_path),
            rules=[{"pattern": "foo", "rewrite": "bar"}],
            ast_grep_mock=mock_mod,
        )

        _assert_ok(result)
        # Only real.ts should match (one file → one call to find_all per rule)
        assert result["total_matches"] == 1

    def test_language_override(self, tmp_path: Path) -> None:
        """Line 139: explicit language sets ast-grep language."""
        (tmp_path / "test.ts").write_text(SAMPLE_TS)

        mock_mod = _make_sg_module(find_all_return=[MagicMock()])

        result = self._run(
            path=str(tmp_path),
            rules=[{
                "pattern": "foo",
                "rewrite": "bar",
                "language": "typescript",
            }],
            ast_grep_mock=mock_mod,
        )

        _assert_ok(result)
        assert result["total_matches"] == 1

    def test_default_rule_name(self, tmp_path: Path) -> None:
        """Line 132: rule without name gets rule-N."""
        (tmp_path / "a.ts").write_text(SAMPLE_TS)

        mock_mod = _make_sg_module(find_all_return=[MagicMock()])

        result = self._run(
            path=str(tmp_path),
            rules=[{"pattern": "foo", "rewrite": "bar"}],
            ast_grep_mock=mock_mod,
        )

        _assert_ok(result)
        assert result["results"][0]["rule"] == "rule-1"

    def test_explicit_rule_name(self, tmp_path: Path) -> None:
        """Line 132: rule with explicit name uses it."""
        (tmp_path / "a.ts").write_text(SAMPLE_TS)

        mock_mod = _make_sg_module(find_all_return=[MagicMock()])

        result = self._run(
            path=str(tmp_path),
            rules=[{
                "pattern": "foo",
                "rewrite": "bar",
                "name": "my-custom-rule",
            }],
            ast_grep_mock=mock_mod,
        )

        _assert_ok(result)
        assert result["results"][0]["rule"] == "my-custom-rule"

    # ── Error / recovery paths ───────────────────────────────────────

    def test_rule_missing_pattern(self, tmp_path: Path) -> None:
        """Lines 134-136: rule without pattern → error appended."""
        (tmp_path / "a.ts").write_text(SAMPLE_TS)
        mock_mod = _make_sg_module()

        result = self._run(
            path=str(tmp_path),
            rules=[
                {"pattern": "foo", "rewrite": "bar", "name": "good-rule"},
                {"rewrite": "baz", "name": "bad-rule"},
            ],
            ast_grep_mock=mock_mod,
        )

        _assert_ok(result)
        assert result["errors"] == 1

    def test_rule_missing_rewrite(self, tmp_path: Path) -> None:
        """Lines 134-136: rule without rewrite → error appended."""
        (tmp_path / "a.ts").write_text(SAMPLE_TS)
        mock_mod = _make_sg_module()

        result = self._run(
            path=str(tmp_path),
            rules=[{"pattern": "foo", "name": "no-rewrite"}],
            ast_grep_mock=mock_mod,
        )

        _assert_ok(result)
        assert result["errors"] == 1

    def test_file_read_error(self, tmp_path: Path) -> None:
        """Lines 160-162: unreadable file → logger.debug + continue."""
        d = tmp_path / "test.ts"
        d.mkdir()  # directory behaves like an unreadable "file"
        (tmp_path / "good.ts").write_text(SAMPLE_TS)

        mock_mod = _make_sg_module(find_all_return=[MagicMock()])

        result = self._run(
            path=str(tmp_path),
            rules=[{"pattern": "foo", "rewrite": "bar"}],
            ast_grep_mock=mock_mod,
        )

        _assert_ok(result)
        # Should only match good.ts
        assert result["total_matches"] == 1

    def test_parse_failure_auto_detection(self, tmp_path: Path) -> None:
        """Lines 164-177: first parse fails → auto-detection loop succeeds."""
        (tmp_path / "test.py").write_text("x = 1\n")

        # First SgRoot call raises, second returns a valid mock
        mock_root_node = MagicMock()
        mock_root_node.find_all.return_value = [MagicMock()]
        mock_sg_root = MagicMock()
        mock_sg_root.root.return_value = mock_root_node

        mock_mod = _make_sg_module(
            sg_root_side_effect=[Exception("parse fail"), mock_sg_root]
        )

        result = self._run(
            path=str(tmp_path),
            rules=[{
                "pattern": r"$A = $B",
                "rewrite": r"$A = $B",
                "file_glob": "*.py",
            }],
            ast_grep_mock=mock_mod,
        )

        _assert_ok(result)
        assert result["total_matches"] >= 1
        # SgRoot was called twice (first fail, then auto-detect)
        assert mock_mod.SgRoot.call_count == 2

    def test_parse_failure_both_attempts(self, tmp_path: Path) -> None:
        """Lines 174-176: both parse attempts fail → rule_errors reported."""
        (tmp_path / "test.py").write_text("x = 1\n")

        mock_mod = _make_sg_module(
            sg_root_side_effect=[Exception("parse fail"), Exception("parse fail 2")]
        )

        result = self._run(
            path=str(tmp_path),
            rules=[{
                "pattern": r"$A = $B",
                "rewrite": r"$A = $B",
                "file_glob": "*.py",
            }],
            ast_grep_mock=mock_mod,
        )

        _assert_ok(result)
        errors = result["results"][0].get("errors", [])
        assert len(errors) > 0
        assert any("parse failed" in e for e in errors)

    def test_rewrite_failure(self, tmp_path: Path) -> None:
        """Lines 188-189: match.replace() raises → rule_errors."""
        (tmp_path / "test.py").write_text("x = 1\n")

        mock_mod = _make_sg_module(replace_side_effect=Exception("rewrite boom"))

        result = self._run(
            path=str(tmp_path),
            rules=[{
                "pattern": r"$A = $B",
                "rewrite": r"$A = 42",
                "file_glob": "*.py",
                "language": "python",
            }],
            ast_grep_mock=mock_mod,
        )

        _assert_ok(result)
        errors = result["results"][0].get("errors", [])
        assert len(errors) > 0
        assert any("rewrite failed" in e for e in errors)

    def test_write_failure(self, tmp_path: Path) -> None:
        """Lines 193-195: dry_run=False, write fails → rule_errors."""
        (tmp_path / "test.py").write_text("x = 1\n")

        mock_mod = _make_sg_module(find_all_return=[MagicMock()])

        with patch.object(Path, "write_text", side_effect=PermissionError("no write")):
            result = self._run(
                path=str(tmp_path),
                rules=[{
                    "pattern": r"$A = $B",
                    "rewrite": r"$A = 42",
                    "file_glob": "*.py",
                    "language": "python",
                }],
                dry_run=False,
                ast_grep_mock=mock_mod,
            )

        _assert_ok(result)
        errors = result["results"][0].get("errors", [])
        assert len(errors) > 0

    def test_import_error(self, tmp_path: Path) -> None:
        """Lines 143-145: ast_grep_py import fails → error reported."""
        (tmp_path / "a.ts").write_text(SAMPLE_TS)

        def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "ast_grep_py":
                raise ImportError("Mocked import error")
            return _real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = _parse(code_migration_tool(
                path=str(tmp_path),
                rules=[{"pattern": "foo", "rewrite": "bar"}],
            ))

        _assert_ok(result)
        assert result["errors"] == 1


# ---------------------------------------------------------------------------
# code_migration_tool — rules_file path
# ---------------------------------------------------------------------------


class TestCodeMigrationToolRulesFile:
    """Lines 107-111: loading rules from YAML file."""

    def test_rules_file_happy(self, tmp_path: Path) -> None:
        """Load rules from YAML file and apply them."""
        (tmp_path / "test.py").write_text("x = 1\n")

        yml = tmp_path / "rules.yml"
        yml.write_text(yaml.dump({"rules": [{
            "pattern": r"$A = $B",
            "rewrite": r"$A = 42",
            "file_glob": "*.py",
            "language": "python",
            "name": "from-yaml",
        }]}))

        mock_mod = _make_sg_module(find_all_return=[MagicMock()])
        mock_import_fn = _make_mock_import(mock_mod)

        with patch("builtins.__import__", side_effect=mock_import_fn):
            result = _parse(code_migration_tool(
                path=str(tmp_path),
                rules_file=str(yml),
            ))

        _assert_ok(result)
        assert result["results"][0]["rule"] == "from-yaml"
        assert result["total_matches"] >= 1

    def test_rules_file_precedence(self, tmp_path: Path) -> None:
        """rules_file takes precedence over rules when both provided."""
        (tmp_path / "test.py").write_text("x = 1\n")

        yml = tmp_path / "rules.yml"
        yml.write_text(yaml.dump({"rules": [{
            "pattern": r"$A = $B",
            "rewrite": r"$A = 42",
            "file_glob": "*.py",
            "language": "python",
            "name": "from-yaml-rule",
        }]}))

        mock_mod = _make_sg_module(find_all_return=[MagicMock()])
        mock_import_fn = _make_mock_import(mock_mod)

        with patch("builtins.__import__", side_effect=mock_import_fn):
            result = _parse(code_migration_tool(
                path=str(tmp_path),
                rules=[{"pattern": "NONEXISTENT", "rewrite": "X", "name": "inline"}],
                rules_file=str(yml),
            ))

        _assert_ok(result)
        # Should use the YAML rule (rules_file wins)
        assert result["results"][0]["rule"] == "from-yaml-rule"
        assert result["total_matches"] >= 1


# ---------------------------------------------------------------------------
# _handle_code_migration — handler wrapper (lines 225-233)
# ---------------------------------------------------------------------------


class TestHandleCodeMigration:
    """Handler that delegates to code_tools.code_migration_tool."""

    def _run(self, args: dict) -> dict:
        """Call handler with code_tools import patched."""
        with patch(
            "code_intel.code_tools.code_migration_tool",
            create=True,
            wraps=code_migration_tool,
        ):
            return _parse(_handle_code_migration(args))

    def test_with_rules(self) -> None:
        """Handler forwards rules with default dry_run=True."""
        # Pass a path that exists; use mock for ast_grep
        mock_mod = _make_sg_module(find_all_return=[])
        mock_import_fn = _make_mock_import(mock_mod)

        with patch("builtins.__import__", side_effect=mock_import_fn):
            with patch(
                "code_intel.code_tools.code_migration_tool",
                create=True,
                wraps=code_migration_tool,
            ):
                result = _parse(_handle_code_migration({
                    "path": "/tmp",
                    "rules": [{"pattern": "foo", "rewrite": "bar"}],
                }))

        assert "status" in result

    def test_with_rules_file(self, tmp_path: Path) -> None:
        """Handler forwards rules_file."""
        yml = tmp_path / "r.yml"
        yml.write_text(yaml.dump({"rules": [{"pattern": "foo", "rewrite": "bar"}]}))

        mock_mod = _make_sg_module(find_all_return=[])
        mock_import_fn = _make_mock_import(mock_mod)

        with patch("builtins.__import__", side_effect=mock_import_fn):
            with patch(
                "code_intel.code_tools.code_migration_tool",
                create=True,
                wraps=code_migration_tool,
            ):
                result = _parse(_handle_code_migration({
                    "path": str(tmp_path),
                    "rules_file": str(yml),
                }))

        _assert_ok(result)
        assert result["rules_processed"] == 1

    def test_minimal_args(self) -> None:
        """Handler with only path → error (no rules)."""
        mock_mod = _make_sg_module(find_all_return=[])
        mock_import_fn = _make_mock_import(mock_mod)

        with patch("builtins.__import__", side_effect=mock_import_fn):
            with patch(
                "code_intel.code_tools.code_migration_tool",
                create=True,
                wraps=code_migration_tool,
            ):
                result = _parse(_handle_code_migration({"path": "/tmp"}))

        _assert_err(result)

    def test_default_path(self) -> None:
        """Default path '.' when path not provided."""
        mock_mod = _make_sg_module(find_all_return=[])
        mock_import_fn = _make_mock_import(mock_mod)

        with patch("builtins.__import__", side_effect=mock_import_fn):
            with patch(
                "code_intel.code_tools.code_migration_tool",
                create=True,
                wraps=code_migration_tool,
            ):
                result = _parse(_handle_code_migration({
                    "rules": [{"pattern": "foo", "rewrite": "bar"}],
                }))

        assert "status" in result

    def test_default_dry_run(self) -> None:
        """Default dry_run=True."""
        mock_mod = _make_sg_module(find_all_return=[])
        mock_import_fn = _make_mock_import(mock_mod)

        with patch("builtins.__import__", side_effect=mock_import_fn):
            with patch(
                "code_intel.code_tools.code_migration_tool",
                create=True,
                wraps=code_migration_tool,
            ):
                result = _parse(_handle_code_migration({
                    "path": "/tmp",
                    "rules": [{"pattern": "foo", "rewrite": "bar"}],
                }))

        assert "status" in result

    def test_handler_triggers_real_function(self) -> None:
        """Handler actually calls through to code_migration_tool."""
        mock_mod = _make_sg_module(find_all_return=[MagicMock()])
        mock_import_fn = _make_mock_import(mock_mod)
        (tmp_path := Path("/tmp")).mkdir(parents=True, exist_ok=True)
        tf = tmp_path / "_migration_test.ts"
        tf.write_text(SAMPLE_TS)

        try:
            with patch("builtins.__import__", side_effect=mock_import_fn):
                with patch(
                    "code_intel.code_tools.code_migration_tool",
                    create=True,
                    wraps=code_migration_tool,
                ):
                    result = _parse(_handle_code_migration({
                        "path": "/tmp",
                        "rules": [{"pattern": "foo", "rewrite": "bar"}],
                    }))
            assert "status" in result
        finally:
            tf.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# CODE_MIGRATION_SCHEMA
# ---------------------------------------------------------------------------


class TestMigrationSchema:
    """Schema structure validation."""

    def test_schema_name(self) -> None:
        assert CODE_MIGRATION_SCHEMA["name"] == "code_migration"

    def test_schema_required_params(self) -> None:
        props = CODE_MIGRATION_SCHEMA["parameters"]["properties"]
        assert "path" in props
        assert "rules" in props
        assert "dry_run" in props

    def test_schema_required_list(self) -> None:
        assert CODE_MIGRATION_SCHEMA["parameters"]["required"] == ["path", "rules"]

    def test_schema_rule_items(self) -> None:
        rules = CODE_MIGRATION_SCHEMA["parameters"]["properties"]["rules"]
        assert rules["type"] == "string"
        assert isinstance(rules.get("description", ""), str)
        assert len(rules["description"]) > 20

    def test_schema_rule_required(self) -> None:
        required = CODE_MIGRATION_SCHEMA["parameters"]["required"]
        assert "rules" in required

    def test_dry_run_default(self) -> None:
        dry_run = CODE_MIGRATION_SCHEMA["parameters"]["properties"]["dry_run"]
        assert dry_run.get("default") is True
