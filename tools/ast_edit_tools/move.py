"""ast-edit/ — AST-based code editing tools."""

import logging
from pathlib import Path
from typing import Optional

from ..._fmt import fmt_err, fmt_ok
from .base import _find_symbol_in_ast

logger = logging.getLogger("agentiker_code_intel")

def _validate_move_inputs(
    source: str, target: str, symbol: str, lang_key: str
) -> tuple:
    """Validate inputs for code_move_tool. Returns (source_path, target_path, lang_key, symbol_info, error)."""
    try:
        import tree_sitter  # noqa: F401
    except ImportError:
        return (None, None, None, None, fmt_err("Tree-sitter not available."))

    source_path = Path(source).expanduser().resolve()
    if not source_path.exists():
        return (None, None, None, None, fmt_err(f"Source file not found: {source}"))
    if not source_path.is_file():
        return (None, None, None, None, fmt_err(f"Not a file: {source}"))

    target_path = Path(target).expanduser().resolve()
    if not target_path.exists():
        return (None, None, None, None, fmt_err(f"Target file not found: {target}"))
    if not target_path.is_file():
        return (None, None, None, None, fmt_err(f"Not a file: {target}"))

    if lang_key is None:
        return (None, None, None, None, fmt_err(
            f"Cannot detect language for source file: {source}. Set 'language' explicitly."
        ))

    symbol_info = _find_symbol_in_ast(str(source_path), symbol, lang_key)
    if symbol_info is None:
        return (None, None, None, None, fmt_err(f"Symbol '{symbol}' not found in {source}"))

    return (source_path, target_path, lang_key, symbol_info, None)


def _read_source_bytes(source_path: Path) -> tuple:
    """Read source file bytes. Returns (bytes, error_str)."""
    try:
        return (source_path.read_bytes(), None)
    except (OSError, IOError) as e:
        return (None, fmt_err(f"Cannot read source file: {e}"))


def _read_target_bytes(target_path: Path) -> tuple:
    """Read target file bytes. Returns (bytes, error_str)."""
    try:
        return (target_path.read_bytes(), None)
    except (OSError, IOError) as e:
        return (None, fmt_err(f"Cannot read target file: {e}"))


def _compute_new_file_contents(
    source_bytes: bytes, symbol_info: dict, target_bytes: bytes
) -> tuple:
    """Compute new source and target content after the move. Returns (new_source, new_target)."""
    start_byte = symbol_info["start_byte"]
    end_byte = symbol_info["end_byte"]
    symbol_code = source_bytes[start_byte:end_byte].decode("utf-8", errors="replace")
    leaf_name = symbol_info.get("name", symbol_info.get("_name", "")).strip().split("/")[-1]

    target_text = target_bytes.decode("utf-8", errors="replace")
    target_stripped = target_text.rstrip()
    insert_pos = len(target_stripped.encode("utf-8"))
    insertion_code = "\n\n" + symbol_code + "\n"

    new_target = (
        target_bytes[:insert_pos]
        + insertion_code.encode("utf-8")
        + target_bytes[insert_pos:].lstrip(b"\n")
    )
    new_source = source_bytes[:start_byte] + source_bytes[end_byte:]
    leaf_name_final = symbol_info.get("_name", leaf_name) or leaf_name
    return (new_source, new_target, leaf_name_final)


def _build_move_diff(
    source_path: Path, target_path: Path,
    source_bytes: bytes, new_source_bytes: bytes,
    target_bytes: bytes, new_target_bytes: bytes,
) -> str:
    """Build a unified diff string for both files."""
    import difflib as _dl

    source_old = source_bytes.decode("utf-8", errors="replace")
    source_new = new_source_bytes.decode("utf-8", errors="replace")
    source_diff = list(_dl.unified_diff(
        source_old.splitlines(keepends=True),
        source_new.splitlines(keepends=True),
        fromfile=f"a/{source_path.name}",
        tofile=f"b/{source_path.name}",
        n=3,
    ))
    target_old = target_bytes.decode("utf-8", errors="replace")
    target_new = new_target_bytes.decode("utf-8", errors="replace")
    target_diff = list(_dl.unified_diff(
        target_old.splitlines(keepends=True),
        target_new.splitlines(keepends=True),
        fromfile=f"a/{target_path.name}",
        tofile=f"b/{target_path.name}",
        n=3,
    ))
    return "".join(source_diff) + "\n" + "".join(target_diff)


def _safe_write_with_backup(file_path: Path, original_bytes: bytes, new_bytes: bytes) -> Optional[str]:
    """Write a file with .bak backup. Returns error string or None on success."""
    backup_path = file_path.with_suffix(file_path.suffix + ".bak")
    try:
        backup_path.write_bytes(original_bytes)
    except (OSError, IOError) as e:
        return f"Cannot create backup: {e}"
    try:
        file_path.write_bytes(new_bytes)
    except (OSError, IOError) as e:
        backup_path.write_bytes(original_bytes)
        return f"Cannot write file: {e}"
    try:
        backup_path.unlink()
    except OSError as e:
        logger.debug('cleanup backup unlink: %s', e)
    return None


def code_move_tool(
    source: str,
    symbol: str,
    target: str,
    language: str = "",
    dry_run: bool = True,
) -> str:
    """Move a symbol between files. AST-based extraction + insertion."""
    from ...code_tools import _invalidate_cache, detect_language

    lang_key = language if language else detect_language(source)
    source_path, target_path, _, symbol_info, err = _validate_move_inputs(
        source, target, symbol, lang_key
    )
    if err:
        return err

    source_bytes, src_err = _read_source_bytes(source_path)
    if src_err:
        return src_err

    target_bytes, tgt_err = _read_target_bytes(target_path)
    if tgt_err:
        return tgt_err

    new_source, new_target, leaf_name = _compute_new_file_contents(
        source_bytes, symbol_info, target_bytes
    )

    if dry_run:
        diff = _build_move_diff(source_path, target_path,
                                source_bytes, new_source, target_bytes, new_target)
        return fmt_ok({
            "dry_run": True,
            "symbol": leaf_name,
            "kind": symbol_info["kind"],
            "source_line": symbol_info["start_line"],
            "source": str(source_path),
            "target": str(target_path),
            "diff": diff,
            "message": (
                f"Dry-run mode. Would move {symbol_info['kind']} '{leaf_name}' "
                f"from {source_path.name}:{symbol_info['start_line']} "
                f"to {target_path.name}. Set dry_run=False to apply."
            ),
        })

    # Apply: write both files
    src_write_err = _safe_write_with_backup(source_path, source_bytes, new_source)
    if src_write_err:
        return fmt_err(f"Source write error: {src_write_err}")

    tgt_write_err = _safe_write_with_backup(target_path, target_bytes, new_target)
    if tgt_write_err:
        return fmt_err(f"Target write error: {tgt_write_err}")

    _invalidate_cache(str(source_path))
    _invalidate_cache(str(target_path))

    return fmt_ok({
        "success": True,
        "symbol": leaf_name,
        "kind": symbol_info["kind"],
        "source": str(source_path),
        "source_line": symbol_info["start_line"],
        "target": str(target_path),
        "message": (
            f"Moved {symbol_info['kind']} '{leaf_name}' "
            f"from {source_path.name}:{symbol_info['start_line']} "
            f"to {target_path.name}."
        ),
    })


def _handle_code_move(args, **kw):
    return code_move_tool(
        source=args.get("source", ""),
        symbol=args.get("symbol", ""),
        target=args.get("target", ""),
        language=args.get("language", ""),
        dry_run=args.get("dry_run", True),
    )
