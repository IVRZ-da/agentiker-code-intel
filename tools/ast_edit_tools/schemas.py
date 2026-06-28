"""ast-edit/ — AST editing tool schemas."""
from __future__ import annotations

from ..._logging import setup_logger

logger = setup_logger(__name__)

# ── Schemas ──────────────────────────────────────────────────────────────────

CODE_REPLACE_BODY_SCHEMA = {
    "name": "code_replace_body",
    "description": "Replace a symbol definition using AST boundaries. Supports name_path syntax.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file path to edit.",
            },
            "symbol": {
                "type": "string",
                "description": (
                    "Symbol name or name_path (e.g. 'my_function' or "
                    "'MyClass/my_method')."
                ),
            },
            "new_body": {
                "type": "string",
                "description": (
                    "The new source code replacing the entire symbol definition "
                    "(signature + body)."
                ),
            },
            "language": {
                "type": "string",
                "description": "Language override (auto-detected from extension).",
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "When True (default), returns a diff without modifying the file."
                ),
                "default": True,
            },
            "include_decorators": {
                "type": "boolean",
                "description": (
                    "When True (default), the replacement includes decorators. "
                    "When False, only the definition body (after decorators) is replaced."
                ),
                "default": True,
            },
        },
        "required": ["path", "symbol", "new_body"],
    },
}


CODE_SAFE_DELETE_SCHEMA = {
    "name": "code_safe_delete",
    "description": "Delete a symbol only if it has no external references. Use force=True to override.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file path containing the symbol.",
            },
            "symbol": {
                "type": "string",
                "description": (
                    "Symbol name or name_path (e.g. 'my_function' or "
                    "'MyClass/my_method')."
                ),
            },
            "language": {
                "type": "string",
                "description": "Language override (auto-detected from extension).",
            },
            "force": {
                "type": "boolean",
                "description": (
                    "When True, delete the symbol even if it has external "
                    "references. Default: False (refuse if referenced)."
                ),
                "default": False,
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "When True (default), shows what would be deleted without "
                    "modifying the file."
                ),
                "default": True,
            },
        },
        "required": ["path", "symbol"],
    },
}


CODE_INSERT_BEFORE_SCHEMA = {
    "name": "code_insert_before",
    "description": "Insert code before a symbol definition. Supports name_path syntax.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file path to edit.",
            },
            "symbol": {
                "type": "string",
                "description": (
                    "Symbol name or name_path (e.g. 'my_function' or "
                    "'MyClass/my_method')."
                ),
            },
            "code": {
                "type": "string",
                "description": (
                    "The source code to insert before the symbol definition."
                ),
            },
            "language": {
                "type": "string",
                "description": "Language override (auto-detected from extension).",
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "When True (default), returns a preview without modifying the file."
                ),
                "default": True,
            },
            "newline": {
                "type": "boolean",
                "description": (
                    "When True (default), adds a newline after the inserted code "
                    "to separate it from the symbol."
                ),
                "default": True,
            },
        },
        "required": ["path", "symbol", "code"],
    },
}


CODE_INSERT_AFTER_SCHEMA = {
    "name": "code_insert_after",
    "description": "Insert code after a symbol definition. Supports name_path syntax.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file path to edit.",
            },
            "symbol": {
                "type": "string",
                "description": (
                    "Symbol name or name_path (e.g. 'my_function' or "
                    "'MyClass/my_method')."
                ),
            },
            "code": {
                "type": "string",
                "description": (
                    "The source code to insert after the symbol definition."
                ),
            },
            "language": {
                "type": "string",
                "description": "Language override (auto-detected from extension).",
            },
            "dry_run": {
                "type": "boolean",
                "description": (
                    "When True (default), returns a preview without modifying the file."
                ),
                "default": True,
            },
            "newline": {
                "type": "boolean",
                "description": (
                    "When True (default), adds a newline before the inserted code "
                    "to separate it from the symbol."
                ),
                "default": True,
            },
        },
        "required": ["path", "symbol", "code"],
    },
}


CODE_MOVE_SCHEMA = {
    "name": "code_move",
    "description": "Move a symbol between files via AST extraction and insertion.",
    "parameters": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Source file path",
            },
            "symbol": {
                "type": "string",
                "description": "Symbol name to move",
            },
            "target": {
                "type": "string",
                "description": "Target file path",
            },
            "language": {
                "type": "string",
                "description": "Language override",
            },
            "dry_run": {
                "type": "boolean",
                "description": "Preview changes without writing (default: true)",
            },
        },
        "required": ["source", "symbol", "target"],
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
