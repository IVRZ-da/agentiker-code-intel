"""
lsp/bridge/ — LSP Bridge Subpackage

Sub-modules:
  server.py — LSPBridge class + module-level helpers
  pool.py   — LSPManager class + bridge pool management
"""
from __future__ import annotations

from ..discovery import (  # noqa: F401
    _WORKSPACE_ROOT_CACHE,
    _WORKSPACE_ROOT_CACHE_MAX,
    _WORKSPACE_ROOT_CACHE_TTL,
    _find_tsconfig_root,
    _find_workspace_folders,
    _find_workspace_root,
)
from .pool import (  # noqa: F401
    LSPManager,
    _detect_language_for_lsp,
    _location_to_dict,
    _read_context_lines,
    get_lsp_manager,
)
from .server import (  # noqa: F401
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
    _group_by_file,
    _log_diagnostics,
    _parse_workspace_edit,
    _resolve_command,
    logger,
)

__all__ = [
    "LSPBridge",
    "LSPManager",
    "get_lsp_manager",
    "_LANGUAGE_SERVERS",
    "_apply_edits_by_file",
    "_ast_file_cache",
    "_AST_CACHE_MAX",
    "_AST_CACHE_TTL",
    "_build_rename_preview",
    "_cached_read_lines",
    "_detect_language_for_lsp",
    "_find_tsconfig_root",
    "_find_workspace_folders",
    "_find_workspace_root",
    "_WORKSPACE_ROOT_CACHE",
    "_WORKSPACE_ROOT_CACHE_MAX",
    "_WORKSPACE_ROOT_CACHE_TTL",
    "_group_by_file",
    "_LANGUAGE_SERVERS",
    "_LSP_FIRST_REQUEST_DELAY",
    "_LSP_GENERIC_DELAY",
    "_LSP_IDLE_TIMEOUT",
    "_LSP_INIT_TIMEOUT",
    "_LSP_PYTHON_FIRST_DELAY",
    "_LSP_REQUEST_TIMEOUT",
    "_LSP_SUBSEQUENT_DELAY",
    "_location_to_dict",
    "_log_diagnostics",
    "_parse_workspace_edit",
    "_read_context_lines",
    "_resolve_command",
    "logger",
]
