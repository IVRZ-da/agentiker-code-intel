from typing import Any, Optional
from hermes_cli.plugins import PluginContext
import toolsets
import os
import json

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
        from .code_intel import get_symbol_cache_stats
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
            bridge_count = len(mgr._bridges)
            lines.append(f"  LSP bridges: {bridge_count} active")
            lines.append(f"  Registered servers: {', '.join(active) if active else 'none'}")

            # Per-bridge details
            for bridge_id, bridge in mgr._bridges.items():
                info = bridge.get_server_info() if hasattr(bridge, 'get_server_info') else {}
                alive = "✓" if info.get("alive") else "✗"
                init = "init" if info.get("initialized") else "pending"
                diag = info.get("diagnostic_files", 0)
                lines.append(f"    {bridge_id}: {alive} {init} diag_files={diag}")

            # Workspace roots
            roots = set()
            for b in mgr._bridges.values():
                if getattr(b, "root_uri", None):
                    roots.add(b.root_uri)
            if roots:
                lines.append(f"  Workspace roots: {', '.join(roots)}")

            # Cache stats per bridge
            total_diag = sum(
                len(b._diagnostics_cache) if hasattr(b, '_diagnostics_cache') else 0
                for b in mgr._bridges.values()
            )
            if total_diag:
                lines.append(f"  Cached diagnostics: {total_diag} files across bridges")
        except Exception as exc:
            lines.append(f"  LSP info unavailable: {exc}")

        return "\n".join(lines)

    if sub == "clear":
        clear_symbol_cache()
        return "[code_intel] AST symbol cache cleared successfully."

    return f"Unknown subcommand: {sub}\nRun `/code-intel help` for usage."

# Hook handler
def _on_session_end(**kwargs: Any) -> None:
    """Persist AST caches to disk at session end, then clear memory."""
    from .code_intel import persist_symbol_cache, clear_symbol_cache
    saved = persist_symbol_cache()
    clear_symbol_cache()


def register(ctx: PluginContext) -> None:
    # 1. Register command & hooks
    ctx.register_command(
        "code-intel",
        handler=_handle_code_intel_slash,
        description="Manage AST-aware code intelligence and symbol caching."
    )
    ctx.register_hook("on_session_end", _on_session_end)

    # C2: pre_llm_call hook — inject compact code context for coding queries
    def _pre_llm_call_inject_context(**kwargs: Any) -> Optional[str]:
        """
        Before the LLM processes a prompt, detect if it's a coding query and
        inject compact context (symbol list, imports, diagnostics) for files
        mentioned in the conversation. This saves multiple manual navigation steps.
        """
        try:
            # Extract recent file paths from conversation context
            messages = kwargs.get("messages", [])
            if not messages:
                return None
            
            # Only inject for the last user message
            last_msg = ""
            for m in reversed(messages):
                if isinstance(m, dict) and m.get("role") == "user":
                    content = m.get("content", "")
                    if isinstance(content, str):
                        last_msg = content
                    break
            
            if not last_msg:
                return None
            
            # Detect file paths in the message (simple heuristic)
            import re
            file_refs = re.findall(
                r'(?:^|[\s"\'])([\w/_.-]+\.(?:py|ts|tsx|js|jsx|rs|go|java))',
                last_msg
            )
            if not file_refs:
                return None
            
            # Limit to 3 files to keep context compact
            file_refs = file_refs[:3]
            
            from .code_intel import code_symbols_tool, detect_language
            context_parts = []
            for fref in file_refs:
                path = fref
                if not os.path.isabs(path):
                    path = os.path.join(os.getcwd(), path)
                if not os.path.exists(path):
                    continue
                lang = detect_language(path)
                if lang:
                    try:
                        symbols_json = code_symbols_tool(path=path, pattern="", include_body=False)
                        symbols = json.loads(symbols_json) if isinstance(symbols_json, str) else symbols_json
                        sym_list = symbols if isinstance(symbols, list) else symbols.get("symbols", [])
                        if sym_list:
                            summary = f"[auto-context] {fref}: {len(sym_list)} symbols"
                            # Top 8 symbols only
                            for s in sym_list[:8]:
                                name = s.get("name", "?")
                                kind = s.get("kind", "")
                                line = s.get("line", "")
                                summary += f"\n  L{line} {kind} {name}"
                            context_parts.append(summary)
                    except Exception:
                        pass
            
            if context_parts:
                return "\n".join(context_parts)
            return None
        except Exception as e:
            import logging
            logging.getLogger("code_intel").debug(f"pre_llm_call hook error: {e}")
            return None

    ctx.register_hook("pre_llm_call", _pre_llm_call_inject_context)

    # 2. Inject the code_intel toolset definition
    if "code_intel" not in toolsets.TOOLSETS:
        toolsets.TOOLSETS["code_intel"] = {
            "description": "AST-aware code intelligence: symbol extraction, structural search, safe refactoring, LSP go-to-definition and find-all-references (tree-sitter + ast-grep + LSP)",
            "tools": [
                "code_symbols", "code_search", "code_refactor",
                "code_definition", "code_references", "code_diagnostics",
                "code_callers", "code_callees", "code_capsule",
                "code_workspace_summary", "code_impact", "code_tests_for_symbol",
                "code_query", "code_rename", "code_workspace_symbols",
                "code_hover", "code_type_definition",
                "code_signatures", "code_action",
            ],
            "includes": []
        }

    # Inject into core platforms so it's globally available
    new_tools = [
        "code_symbols", "code_search", "code_refactor",
        "code_definition", "code_references", "code_diagnostics",
        "code_callers", "code_callees", "code_capsule",
        "code_workspace_summary", "code_impact", "code_tests_for_symbol",
        "code_query", "code_rename", "code_workspace_symbols",
        "code_hover", "code_type_definition",
        "code_signatures", "code_action",
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

    # Register LSP-backed tools (definition, references, diagnostics, callers, callees).
    # These are NOT auto-registered at import time — must be invoked explicitly.
    try:
        from .lsp_bridge import register_lsp_tools
        register_lsp_tools()
    except Exception as e:
        import logging
        logging.getLogger("code_intel").warning(f"LSP tool registration failed: {e}")

    # Restore persisted symbol cache from disk (B5)
    loaded = code_intel.load_symbol_cache()
    if loaded:
        import logging
        logging.getLogger("code_intel").info(f"Restored {loaded} symbol cache entries from disk")

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
