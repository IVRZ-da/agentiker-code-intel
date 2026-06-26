"""ast-edit/ — AST-based code editing tools."""

import logging
from pathlib import Path
from typing import Optional

from ..._fmt import fmt_err, fmt_ok
from .base import _find_symbol_in_ast

logger = logging.getLogger("agentiker_code_intel")

def code_replace_body_tool(
    path: str,
    symbol: str,
    new_body: str,
    language: Optional[str] = None,
    dry_run: bool = True,
    include_decorators: bool = True,
) -> str:
    """Replace the full definition of a symbol using AST-accurate boundaries.

    Args:
        path: Absolute file path.
        symbol: Symbol name or name_path (e.g. 'MyClass/my_method').
        new_body: Replacement source code.
        language: Language override.
        dry_run: When True, return diff without writing.
        include_decorators: When True, replace decorators too.

    Returns:
        JSON result with success/error message and optional diff.

    """
    from ...code_tools import (
        _get_language,
        _get_parser,
        _invalidate_cache,
        detect_language,
    )

    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Tree-sitter not available. Cannot perform AST editing.")

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"File not found: {path}")

    if not target.is_file():
        return fmt_err(f"Not a file: {path}")

    symbol_info = _find_symbol_in_ast(str(target), symbol, language)
    if symbol_info is None:
        return fmt_err(f"Symbol '{symbol}' not found in {path}")

    try:
        source_bytes = target.read_bytes()
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot read file: {e}")

    start_byte = symbol_info["start_byte"]
    end_byte = symbol_info["end_byte"]
    new_body_bytes = new_body.encode("utf-8")

    if not include_decorators:
        lang_key2 = detect_language(str(target), language)
        if lang_key2:
            # imports not needed — _get_parser/_get_language handles this
            _p2 = _get_parser(lang_key2)
            _l2 = _get_language(lang_key2)
            if _p2 and _l2:
                _tree2 = _p2.parse(source_bytes)
            # Walk from root to find the exact node at start_byte
            _node_at = _tree2.root_node.named_descendant_for_byte_range(
                start_byte, start_byte + 1
            )
            # If it's a decorated_definition, find the inner definition
            if _node_at and _node_at.type == "decorated_definition":
                for _child in _node_at.children:
                    if _child.type in (
                        "function_definition", "class_definition",
                        "function_declaration", "class_declaration",
                        "method_definition",
                    ):
                        start_byte = _child.start_byte
                        break

    old_text = source_bytes[start_byte:end_byte].decode("utf-8", errors="replace")

    if dry_run:
        import difflib as _dl
        _diff_lines = list(_dl.unified_diff(
            old_text.splitlines(keepends=True),
            new_body.splitlines(keepends=True),
            fromfile=f"a/{target.name}",
            tofile=f"b/{target.name}",
            n=3,
        ))
        diff_text = "".join(_diff_lines)
        return fmt_ok({
            "dry_run": True,
            "symbol": symbol_info["name"],
            "kind": symbol_info["kind"],
            "line": symbol_info["start_line"],
            "diff": diff_text,
            "message": "Dry-run mode. Set dry_run=False to apply.",
        })

    # --- Apply ---
    new_content = source_bytes[:start_byte] + new_body_bytes + source_bytes[end_byte:]

    # Create backup
    backup_path = target.with_suffix(target.suffix + ".bak")
    try:
        backup_path.write_bytes(source_bytes)
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot create backup: {e}")

    try:
        target.write_bytes(new_content)
    except (OSError, IOError) as e:
        # Restore backup
        backup_path.write_bytes(source_bytes)
        return fmt_err(f"Cannot write file: {e}")

    # Clean up backup on success
    try:
        backup_path.unlink()
    except OSError as e:
        logger.debug('cleanup backup unlink (replace_body): %s', e)
        pass

    # Invalidate symbol cache for this file
    _invalidate_cache(str(target))

    return fmt_ok({
        "success": True,
        "symbol": symbol_info["name"],
        "kind": symbol_info["kind"],
        "line": symbol_info["start_line"],
        "end_line": symbol_info["end_line"],
        "message": f"Replaced {symbol_info['kind']} '{symbol_info['name']}' "
                   f"(lines {symbol_info['start_line']}-{symbol_info['end_line']}).",
    })


def _handle_code_replace_body(args, **kw):
    return code_replace_body_tool(
        path=args.get("path", ""),
        symbol=args.get("symbol", ""),
        new_body=args.get("new_body", ""),
        language=args.get("language"),
        dry_run=args.get("dry_run", True),
        include_decorators=args.get("include_decorators", True),
    )


# ---------------------------------------------------------------------------
# code_safe_delete — Delete symbol if unreferenced
# ---------------------------------------------------------------------------
