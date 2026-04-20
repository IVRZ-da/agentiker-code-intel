from typing import Any, Optional
from hermes_cli.plugins import PluginContext
import toolsets

# Slash command handler
def _handle_code_intel_slash(raw_args: str) -> Optional[str]:
    from .code_intel import get_symbol_cache_stats, clear_symbol_cache

    argv = raw_args.strip().split()
    if not argv or argv[0] in ("help", "-h", "--help"):
        return (
            "/code-intel — AST code intelligence management\n\n"
            "Subcommands:\n"
            "  status   Show current AST symbol cache size\n"
            "  clear    Clear the AST symbol cache to free memory\n"
        )
    
    sub = argv[0]
    if sub == "status":
        stats = get_symbol_cache_stats()
        return f"[code_intel] Cache status: {stats['entries']} parsed AST files in memory."
    
    if sub == "clear":
        clear_symbol_cache()
        return "[code_intel] AST symbol cache cleared successfully."

    return f"Unknown subcommand: {sub}\nRun `/code-intel help` for usage."

# Hook handler
def _on_session_end(**kwargs: Any) -> None:
    """Automatically clear AST caches at session end to free memory."""
    from .code_intel import clear_symbol_cache
    clear_symbol_cache()


def register(ctx: PluginContext) -> None:
    # 1. Register command & hooks
    ctx.register_command(
        "code-intel",
        handler=_handle_code_intel_slash,
        description="Manage AST-aware code intelligence and symbol caching."
    )
    ctx.register_hook("on_session_end", _on_session_end)

    # 2. Inject the code_intel toolset definition
    if "code_intel" not in toolsets.TOOLSETS:
        toolsets.TOOLSETS["code_intel"] = {
            "description": "AST-aware code intelligence: symbol extraction, structural search, safe refactoring, LSP go-to-definition and find-all-references (tree-sitter + ast-grep + LSP)",
            "tools": ["code_symbols", "code_search", "code_refactor", "code_definition", "code_references"],
            "includes": []
        }

    # Inject into core platforms so it's globally available
    if "code_symbols" not in toolsets._HERMES_CORE_TOOLS:
        toolsets._HERMES_CORE_TOOLS.extend(["code_symbols", "code_search", "code_refactor", "code_definition", "code_references"])

    for preset in ["hermes-acp", "hermes-api-server"]:
        if preset in toolsets.TOOLSETS:
            tools = toolsets.TOOLSETS[preset]["tools"]
            if "code_symbols" not in tools:
                tools.extend(["code_symbols", "code_search", "code_refactor", "code_definition", "code_references"])

    # Load our tools
    from . import code_intel

    # Inject steering hints directly into the registry schemas of the builtin tools!
    import tools.registry
    
    sf_entry = tools.registry.registry.get_entry("search_files")
    if sf_entry and "description" in sf_entry.schema:
        hint = (
            "\n\nFor AST-aware structural search inside source files "
            "(find function calls, imports, decorators, etc.), prefer code_search — "
            "it understands syntax and won't match comments or strings."
        )
        if hint not in sf_entry.schema["description"]:
            sf_entry.schema["description"] += hint

    rf_entry = tools.registry.registry.get_entry("read_file")
    if rf_entry and "description" in rf_entry.schema:
        hint = (
            "\n\nFor understanding what a file contains (list of functions, classes, "
            "methods with line numbers and signatures), prefer code_symbols — "
            "much more token-efficient than reading the entire file."
        )
        if hint not in rf_entry.schema["description"]:
            rf_entry.schema["description"] += hint

    p_entry = tools.registry.registry.get_entry("patch")
    if p_entry and "description" in p_entry.schema:
        hint = (
            "\n\nFor AST-aware structural replacement (rename patterns, wrap "
            "functions, add parameters across a file), prefer code_refactor — "
            "matches by syntax tree, not raw text. Dry-run by default."
        )
        if hint not in p_entry.schema["description"]:
            p_entry.schema["description"] += hint
