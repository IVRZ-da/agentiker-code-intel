"""ast-edit/ — AST editing tool schemas."""
from __future__ import annotations

from ..._logging import setup_logger

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

CODE_REPLACE_BODY_SCHEMA = {
    "name": "code_replace_body",
    "description": (
        "Replace the full definition of a symbol (function, method, class) in a "
        "source file using AST-accurate boundaries. Supports name_path syntax "
        "(e.g. 'MyClass/my_method'). dry_run=True (default) shows a diff without "
        "writing. include_decorators=True replaces decorators with the definition."
    ),
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
    "description": (
        "Delete a symbol (function, method, class) ONLY if it has no external "
        "references. Uses AST-based reference search across the project. "
        "Set force=True to delete even if referenced. "
        "dry_run=True (default) shows what would be deleted."
    ),
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
    "description": (
        "Insert code before a symbol's definition in a source file. Uses "
        "AST-accurate boundaries to find the insertion point. Supports "
        "name_path syntax (e.g. 'MyClass/my_method'). "
        "dry_run=True (default) shows a preview without writing."
    ),
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
    "description": (
        "Insert code after a symbol's definition in a source file. Uses "
        "AST-accurate boundaries to find the insertion point. Supports "
        "name_path syntax (e.g. 'MyClass/my_method'). "
        "dry_run=True (default) shows a preview without writing."
    ),
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
