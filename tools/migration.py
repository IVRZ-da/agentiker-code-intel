"""tools/migration.py — YAML-based bulk pattern migration tool.

Applies multiple ast-grep patterns sequentially from a YAML config.
Each rule has: pattern + rewrite + optional file_glob + language.
Supports dry-run and per-rule status reporting.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .._fmt import fmt_err, fmt_ok
from .._logging import setup_logger as _setup_code_intel_logger
from .pattern import _AST_GREP_LANG_MAP

logger = _setup_code_intel_logger(__name__)

__all__ = ["code_migration_tool", "CODE_MIGRATION_SCHEMA"]

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
CODE_MIGRATION_SCHEMA = {
    "name": "code_migration",
    "description": (
        "Apply YAML-based bulk pattern migrations across a codebase. "
        "Each rule defines a pattern (ast-grep meta-variable syntax) and its replacement. "
        "Supports dry-run mode (default: true) and per-rule status reporting."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Project root or directory to apply migrations to.",
            },
            "rules": {
                "type": "string",
                "description": (
                    "JSON-encoded list of migration rule dicts. Each rule has: "
                    "pattern (required), rewrite (required), "
                    "file_glob (optional, default '**/*.ts'), "
                    "language (optional, auto-detected), "
                    "name (optional rule name for reporting). "
                    "Example: '[{\"pattern\":\"old\",\"rewrite\":\"new\"}]'"
                ),
            },
            "rules_file": {
                "type": "string",
                "description": "Path to YAML file with rules (alternative to inline 'rules').",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Preview changes without writing (default: true).",
                "default": True,
            },
        },
        "required": ["path", "rules"],
    },
}


# ---------------------------------------------------------------------------
# YAML file loader
# ---------------------------------------------------------------------------
def _load_rules_from_yaml(yaml_path: str) -> List[Dict]:
    """Load migration rules from a YAML file."""
    path = Path(yaml_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Rules file not found: {yaml_path}")
    with open(path) as f:
        data = yaml.safe_load(f)
    if not data or "rules" not in data:
        raise ValueError("YAML file must contain a 'rules' list")
    return data["rules"]


# ---------------------------------------------------------------------------
# Core tool function
# ---------------------------------------------------------------------------
def code_migration_tool(
    path: str,
    rules: Optional[List[Dict]] = None,
    rules_file: str = "",
    dry_run: bool = True,
) -> str:
    """Apply YAML-based bulk pattern migrations.

    Args:
        path: Project root or directory to apply migrations to.
        rules: Inline list of rule dicts (pattern + rewrite + optional params).
        rules_file: Path to YAML file with rules (alternative to inline 'rules').
        dry_run: When True (default), preview changes without writing.

    Returns:
        Formatted JSON with per-rule results (files_changed, total_matches, errors).
    """
    # Resolve rules
    if rules_file:
        try:
            resolved_rules = _load_rules_from_yaml(rules_file)
        except (FileNotFoundError, ValueError) as e:
            return fmt_err(str(e))
    elif rules:
        resolved_rules = rules
    else:
        return fmt_err("Either 'rules' or 'rules_file' is required")

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    # Apply each rule
    results = []
    total_files_changed = 0
    total_matches = 0
    errors = []

    for idx, rule in enumerate(resolved_rules):
        pattern = rule.get("pattern", "")
        rewrite = rule.get("rewrite", "")
        file_glob = rule.get("file_glob", "**/*.ts")
        language = rule.get("language", "")
        rule_name = rule.get("name", f"rule-{idx + 1}")

        if not pattern or not rewrite:
            errors.append(f"Rule '{rule_name}': missing pattern or rewrite")
            continue

        # Determine ast-grep language
        ag_lang = _AST_GREP_LANG_MAP.get(language) if language else None

        try:
            import ast_grep_py as sg
        except ImportError:
            errors.append("ast-grep-py not installed")
            continue

        file_matches = 0
        file_changes = 0
        rule_errors = []

        # Scan files
        for fpath in sorted(target.rglob(file_glob)):
            # Skip excluded dirs
            parts = fpath.parts
            if any(p in parts for p in ("node_modules", ".git", "__pycache__", "build", "dist", ".venv", ".next")):
                continue

            try:
                source = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.debug("code_migration: reading %s: %s", fpath, e)
                continue

            try:
                root = sg.SgRoot(source, ag_lang or _AST_GREP_LANG_MAP.get("typescript", "typescript"))
            except Exception:
                logger.debug("migration: SgRoot auto-detection failed, trying fallback")
                # Try auto-detection
                ext = fpath.suffix
                for lang, ag in _AST_GREP_LANG_MAP.items():
                    if ext in (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java"):
                        ag_lang = ag
                        break
                try:
                    root = sg.SgRoot(source, ag_lang or "typescript")
                except Exception as e:
                    rule_errors.append(f"  {fpath}: parse failed: {e}")
                    continue

            matches = root.root().find_all(pattern)
            if not matches:
                continue

            # Apply rewrite (reverse order to preserve offsets)
            for m in reversed(matches):
                try:
                    m.replace(rewrite)
                    file_matches += 1
                except Exception as e:
                    rule_errors.append(f"  {fpath}: rewrite failed: {e}")

            if not dry_run:
                try:
                    fpath.write_text(source)
                    file_changes += 1
                except Exception as e:
                    rule_errors.append(f"  {fpath}: write failed: {e}")

        total_matches += file_matches
        total_files_changed += file_changes

        results.append({
            "rule": rule_name,
            "pattern": pattern[:60],
            "matches": file_matches,
            "files_changed": file_changes,
            "errors": rule_errors[:5],  # Cap error reporting
        })

    report = {
        "mode": "dry_run" if dry_run else "applied",
        "path": str(target),
        "rules_processed": len(resolved_rules),
        "total_matches": total_matches,
        "total_files_changed": total_files_changed,
        "errors": len(errors),
        "results": results,
    }

    return fmt_ok(report)


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------
def _handle_code_migration(args: dict, **kw: Any) -> str:
    """Handler for code_migration tool."""
    import json as _json

    from ..code_tools import code_migration_tool as _real
    rules = args.get("rules", "")
    if isinstance(rules, str) and rules.strip():
        try:
            parsed = _json.loads(rules)
            if isinstance(parsed, list):
                args["rules"] = parsed
            else:
                return fmt_err("'rules' must be a JSON array of rule objects")
        except _json.JSONDecodeError as e:
            return fmt_err(f"Invalid JSON in 'rules': {e}")
    elif "rules" in args and not isinstance(args["rules"], list):
        args["rules"] = None
    return _real(
        path=args.get("path", "."),
        rules=args.get("rules") if isinstance(args.get("rules"), list) else None,
        rules_file=args.get("rules_file", ""),
        dry_run=args.get("dry_run", True),
    )
