"""ast-edit/ — AST-based code editing tools."""

import logging
from pathlib import Path
from typing import Optional

from ..._fmt import fmt_err, fmt_ok
from .base import _ast_search_references, _find_symbol_in_ast

logger = logging.getLogger("agentiker_code_intel")

def code_safe_delete_tool(
    path: str,
    symbol: str,
    language: Optional[str] = None,
    force: bool = False,
    dry_run: bool = True,
) -> str:
    """Delete a symbol ONLY if it has no external references.

    Uses AST-based reference search. Set force=True to bypass the check.

    Args:
        path: File containing the symbol.
        symbol: Symbol name or name_path.
        language: Language override.
        force: Delete even if referenced.
        dry_run: Preview without writing.

    Returns:
        JSON with result message and reference info.

    """
    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return fmt_err("Tree-sitter not available. Cannot perform AST editing.")

    from ...code_tools import _invalidate_cache

    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"File not found: {path}")
    if not target.is_file():
        return fmt_err(f"Not a file: {path}")

    symbol_info = _find_symbol_in_ast(str(target), symbol, language)
    if symbol_info is None:
        return fmt_err(f"Symbol '{symbol}' not found in {path}")

    start_byte = symbol_info["start_byte"]
    end_byte = symbol_info["end_byte"]
    leaf_name = symbol.strip().split("/")[-1]

    # --- Reference check ---
    ext_refs = []
    if not force:
        refs = _ast_search_references(str(target.parent), leaf_name, language)
        definition_path = str(target)
        for ref in refs:
            # Skip self-references (the definition itself)
            if ref["file"] == definition_path and ref["line"] == symbol_info["start_line"]:
                continue
            ext_refs.append(ref)

    if ext_refs and not force:
        ref_summary = "\n".join(
            f"  {r['file']}:{r['line']}  {r['context'][:80]}"
            for r in ext_refs[:20]
        )
        if len(ext_refs) > 20:
            ref_summary += f"\n  ... and {len(ext_refs) - 20} more"
        return fmt_ok({
            "safe": False,
            "symbol": leaf_name,
            "kind": symbol_info["kind"],
            "references_found": len(ext_refs),
            "message": (
                f"Cannot delete '{leaf_name}': {len(ext_refs)} external "
                f"reference(s) found. Use force=True to override."
            ),
            "references": ref_summary,
        })

    # --- Dry-run ---
    if dry_run:
        return fmt_ok({
            "dry_run": True,
            "symbol": leaf_name,
            "kind": symbol_info["kind"],
            "line": symbol_info["start_line"],
            "end_line": symbol_info["end_line"],
            "body_preview": symbol_info["body"][:200],
            "external_references": len(ext_refs),
            "references_found": len(ext_refs) > 0,
            "message": f"Would delete {symbol_info['kind']} '{leaf_name}' "
                       f"(lines {symbol_info['start_line']}-{symbol_info['end_line']})."
                       f" Set dry_run=False to apply.",
        })

    # --- Apply: delete symbol range ---
    try:
        source_bytes = target.read_bytes()
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot read file: {e}")

    new_content = source_bytes[:start_byte] + source_bytes[end_byte:]

    backup_path = target.with_suffix(target.suffix + ".bak")
    try:
        backup_path.write_bytes(source_bytes)
    except (OSError, IOError) as e:
        return fmt_err(f"Cannot create backup: {e}")

    try:
        target.write_bytes(new_content)
    except (OSError, IOError) as e:
        backup_path.write_bytes(source_bytes)
        return fmt_err(f"Cannot write file: {e}")

    try:
        backup_path.unlink()
    except OSError as e:
        logger.debug('cleanup backup unlink (safe_delete): %s', e)
        pass

    _invalidate_cache(str(target))

    return fmt_ok({
        "success": True,
        "symbol": leaf_name,
        "kind": symbol_info["kind"],
        "line": symbol_info["start_line"],
        "end_line": symbol_info["end_line"],
        "external_references": len(ext_refs),
        "message": f"Deleted {symbol_info['kind']} '{leaf_name}' "
                   f"(lines {symbol_info['start_line']}-{symbol_info['end_line']}).",
    })


def _handle_code_safe_delete(args, **kw):
    return code_safe_delete_tool(
        path=args.get("path", ""),
        symbol=args.get("symbol", ""),
        language=args.get("language"),
        force=args.get("force", False),
        dry_run=args.get("dry_run", True),
    )


# ---------------------------------------------------------------------------
# code_insert_before — Insert code before a symbol
# ---------------------------------------------------------------------------
