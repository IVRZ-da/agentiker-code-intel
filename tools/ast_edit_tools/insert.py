"""ast-edit/ — AST-based code editing tools."""

import logging
from pathlib import Path
from typing import Optional

from ..._fmt import fmt_err, fmt_ok
from .base import _find_symbol_in_ast

logger = logging.getLogger("agentiker_code_intel")

def code_insert_before_tool(
    path: str,
    symbol: str,
    code: str,
    language: Optional[str] = None,
    dry_run: bool = True,
    newline: bool = True,
) -> str:
    """Insert code before a symbol's definition using AST boundaries.

    Args:
        path: Absolute file path.
        symbol: Symbol name or name_path.
        code: Source code to insert.
        language: Language override.
        dry_run: Preview without writing.
        newline: Add newline after inserted code.

    Returns:
        JSON result.

    """
    from ...code_tools import _invalidate_cache

    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Tree-sitter not available.")

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

    insert_at = symbol_info["start_byte"]
    code_bytes = code.encode("utf-8")
    if newline:
        code_bytes += b"\n"

    if dry_run:
        preview = source_bytes[:insert_at].decode("utf-8", errors="replace")
        return fmt_ok({
            "dry_run": True,
            "symbol": symbol_info["name"],
            "kind": symbol_info["kind"],
            "insert_before_line": symbol_info["start_line"],
            "insertion": code,
            "preview_context": preview[-200:] if len(preview) > 200 else preview,
            "message": "Dry-run mode. Set dry_run=False to apply.",
        })

    # Backup
    backup_path = target.with_suffix(target.suffix + ".bak")
    try:
        backup_path.write_bytes(source_bytes)
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot create backup: {e}")

    new_content = source_bytes[:insert_at] + code_bytes + source_bytes[insert_at:]

    try:
        target.write_bytes(new_content)
    except (OSError, IOError) as e:
        backup_path.write_bytes(source_bytes)
        return fmt_err(f"Cannot write file: {e}")

    try:
        backup_path.unlink()
    except OSError as e:
        logger.debug('cleanup backup unlink (insert_before): %s', e)
        pass

    _invalidate_cache(str(target))

    return fmt_ok({
        "success": True,
        "symbol": symbol_info["name"],
        "kind": symbol_info["kind"],
        "insert_before_line": symbol_info["start_line"],
        "message": f"Inserted code before {symbol_info['kind']} "
                   f"'{symbol_info['name']}' (line {symbol_info['start_line']}).",
    })


def _handle_code_insert_before(args, **kw):
    return code_insert_before_tool(
        path=args.get("path", ""),
        symbol=args.get("symbol", ""),
        code=args.get("code", ""),
        language=args.get("language"),
        dry_run=args.get("dry_run", True),
        newline=args.get("newline", True),
    )


# ---------------------------------------------------------------------------
# code_insert_after — Insert code after a symbol
# ---------------------------------------------------------------------------


def code_insert_after_tool(
    path: str,
    symbol: str,
    code: str,
    language: Optional[str] = None,
    dry_run: bool = True,
    newline: bool = True,
) -> str:
    """Insert code after a symbol's definition using AST boundaries.

    Args:
        path: Absolute file path.
        symbol: Symbol name or name_path.
        code: Source code to insert.
        language: Language override.
        dry_run: Preview without writing.
        newline: Add newline before inserted code.

    Returns:
        JSON result.

    """
    from ...code_tools import _invalidate_cache

    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Tree-sitter not available.")

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

    insert_at = symbol_info["end_byte"]
    code_bytes = code.encode("utf-8")
    if newline:
        code_bytes = b"\n" + code_bytes

    if dry_run:
        preview = source_bytes[insert_at:].decode("utf-8", errors="replace")
        return fmt_ok({
            "dry_run": True,
            "symbol": symbol_info["name"],
            "kind": symbol_info["kind"],
            "insert_after_line": symbol_info["end_line"],
            "insertion": code,
            "preview_context": preview[:200] if len(preview) > 200 else preview,
            "message": "Dry-run mode. Set dry_run=False to apply.",
        })

    # Backup
    backup_path = target.with_suffix(target.suffix + ".bak")
    try:
        backup_path.write_bytes(source_bytes)
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot create backup: {e}")

    new_content = source_bytes[:insert_at] + code_bytes + source_bytes[insert_at:]

    try:
        target.write_bytes(new_content)
    except (OSError, IOError) as e:
        backup_path.write_bytes(source_bytes)
        return fmt_err(f"Cannot write file: {e}")

    try:
        backup_path.unlink()
    except OSError as e:
        logger.debug('cleanup backup unlink (insert_after): %s', e)
        pass

    _invalidate_cache(str(target))

    return fmt_ok({
        "success": True,
        "symbol": symbol_info["name"],
        "kind": symbol_info["kind"],
        "insert_after_line": symbol_info["end_line"],
        "message": f"Inserted code after {symbol_info['kind']} "
                   f"'{symbol_info['name']}' (line {symbol_info['end_line']}).",
    })


def _handle_code_insert_after(args, **kw):
    return code_insert_after_tool(
        path=args.get("path", ""),
        symbol=args.get("symbol", ""),
        code=args.get("code", ""),
        language=args.get("language"),
        dry_run=args.get("dry_run", True),
        newline=args.get("newline", True),
    )


# ---------------------------------------------------------------------------
# code_move_tool — Move a symbol between files via AST extraction
# ---------------------------------------------------------------------------
