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
            "  status   Show symbol cache, LSP health, workspace roots\n"
            "  clear    Clear the AST symbol cache to free memory\n"
        )

    sub = argv[0]
    if sub == "status":
        stats = get_symbol_cache_stats()
        lines = ["[code_intel] Status:"]
        lines.append(f"  Symbol cache: {stats['entries']} parsed AST files in memory.")

        # LSP health
        try:
            from .lsp_bridge import get_lsp_manager, _LANGUAGE_SERVERS, _find_workspace_root
            mgr = get_lsp_manager()
            active = []
            for lang_key, cfgs in _LANGUAGE_SERVERS.items():
                for cfg in cfgs:
                    cmd = cfg.get("command")
                    if cmd:
                        active.append(f"{lang_key} ({cmd})")
            lines.append(f"  LSP bridges: {len(mgr.bridges)} active")
            lines.append(f"  Registered servers: {', '.join(active) if active else 'none'}")

            # Workspace roots
            roots = set()
            for b in mgr.bridges.values():
                if getattr(b, "root_uri", None):
                    roots.add(b.root_uri)
            if roots:
                lines.append(f"  Workspace roots: {', '.join(roots)}")
        except Exception as exc:
            lines.append(f"  LSP info unavailable: {exc}")

        return "\n".join(lines)

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
            "tools": [
                "code_symbols", "code_search", "code_refactor",
                "code_definition", "code_references", "code_diagnostics",
                "code_callers", "code_callees", "code_capsule",
            ],
            "includes": []
        }

    # Inject into core platforms so it's globally available
    new_tools = [
        "code_symbols", "code_search", "code_refactor",
        "code_definition", "code_references", "code_diagnostics",
        "code_callers", "code_callees", "code_capsule",
    ]
    for t in new_tools:
        if t not in toolsets._HERMES_CORE_TOOLS:
            toolsets._HERMES_CORE_TOOLS.append(t)

    for preset in ["hermes-acp", "hermes-api-server"]:
        if preset in toolsets.TOOLSETS:
            tools = toolsets.TOOLSETS[preset]["tools"]
            for t in new_tools:
                if t not in tools:
                    tools.append(t)

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

    # Additional steering for new tools
    cd_entry = tools.registry.registry.get_entry("code_definition")
    if cd_entry and "description" in cd_entry.schema:
        hint = (
            "\n\nWhen you need to understand HOW a symbol is used across the project, "
            "call code_references AFTER code_definition. For a quick one-shot overview, use code_capsule instead."
        )
        if hint not in cd_entry.schema["description"]:
            cd_entry.schema["description"] += hint

    cr_entry = tools.registry.registry.get_entry("code_references")
    if cr_entry and "description" in cr_entry.schema:
        hint = (
            "\n\nBefore renaming or refactoring a symbol, always run code_references first "
            "to see all impacted files. Use group_by_file=True to save tokens on large codebases. "
            "For a compact summary, use code_capsule."
        )
        if hint not in cr_entry.schema["description"]:
            cr_entry.schema["description"] += hint

    cs_entry = tools.registry.registry.get_entry("code_symbols")
    if cs_entry and "description" in cs_entry.schema:
        hint = (
            "\n\nFor cross-file navigation, first use code_symbols on the current file to confirm "
            "the symbol exists, then use code_definition or code_references for deeper analysis."
        )
        if hint not in cs_entry.schema["description"]:
            cs_entry.schema["description"] += hint
