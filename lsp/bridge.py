"""
lsp/bridge.py — Re-Export Facade

All code moved to lsp/bridge/ subpackage (server.py + pool.py).
This file re-exports everything for backward compatibility.
"""
from __future__ import annotations

from .bridge import (  # noqa: F401
    _WORKSPACE_ROOT_CACHE,
    _WORKSPACE_ROOT_CACHE_MAX,
    _WORKSPACE_ROOT_CACHE_TTL,
    _find_tsconfig_root,
    _find_workspace_folders,
)
from .bridge.pool import (  # noqa: F401
    LSPManager,
    _detect_language_for_lsp,
    _location_to_dict,
    _read_context_lines,
    get_lsp_manager,
)
from .bridge.server import (  # noqa: F401
    _AST_CACHE_MAX,
    _AST_CACHE_TTL,
    _LANGUAGE_SERVERS,
    _LSP_FIRST_REQUEST_DELAY,
    _LSP_GENERIC_DELAY,
    _LSP_IDLE_TIMEOUT,
    _LSP_INIT_TIMEOUT,
    _LSP_PYTHON_FIRST_DELAY,
    _LSP_REQUEST_TIMEOUT,
    _LSP_SUBSEQUENT_DELAY,
    LSPBridge,
    _apply_edits_by_file,
    _ast_file_cache,
    _build_rename_preview,
    _cached_read_lines,
    _find_workspace_root,
    _group_by_file,
    _log_diagnostics,
    _parse_workspace_edit,
    _resolve_command,
    logger,
)
