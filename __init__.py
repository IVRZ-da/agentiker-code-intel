from typing import Any, Optional
from pathlib import Path
from hermes_cli.plugins import PluginContext
import toolsets
import os
import json
import logging
import time

from ._logging import setup_logger as _setup_code_intel_logger


def _setup_logger(name: str) -> logging.Logger:
    """Einheitliches Logging — delegiert an _logging.setup_logger."""
    return _setup_code_intel_logger(name)


def _status_show_summary(symbol_entries: int, file_cache_size: int) -> list:
    """Zeige Grund-Infos: Symbol-Cache + File-Read-Cache."""
    lines = ["[code_intel] Status:"]
    lines.append(f"  Symbol cache: {symbol_entries} parsed AST files in memory.")
    if file_cache_size:
        lines.append(f"  File-read cache: {file_cache_size} files cached")
    return lines



def _format_bridge_line(bridge_id, bridge):
    """Format a single LSP bridge status line."""
    import time
    info = bridge.get_server_info() if hasattr(bridge, 'get_server_info') else {}
    alive = "\u2713" if info.get("alive") else "\u2717"
    init = "init" if info.get("initialized") else "pending"
    diag = info.get("diagnostic_files", 0)
    cb = ""
    if bridge._circuit_open_until > 0:
        remaining = int(bridge._circuit_open_until - time.monotonic())
        cb = f" CB=open({remaining}s)" if remaining > 0 else " CB=closed"
    failures = bridge._failure_count
    idle = info.get("last_activity", None)
    idle_str = f" idle={idle:.0f}s" if idle is not None else ""
    return f"    {bridge_id}: {alive} {init} diag_files={diag}{cb} fail={failures}{idle_str}"


def _status_show_lsp_health(mgr) -> list:
    """Zeige LSP Bridge Details + Circuit Breaker Status."""
    from .lsp_bridge import _LANGUAGE_SERVERS
    lines = []
    active = []
    for lang_key, cfgs in _LANGUAGE_SERVERS.items():
        for cfg in cfgs:
            cmd = cfg.get("command")
            if cmd:
                active.append(f"{lang_key} ({cmd})")
    bridge_count = len(mgr._bridges)
    lines.append(f"  LSP bridges: {bridge_count} active")
    lines.append(f"  Registered servers: {', '.join(active) if active else 'none'}")

    for bridge_id, bridge in mgr._bridges.items():
        lines.append(_format_bridge_line(bridge_id, bridge))

    roots = set()
    for b in mgr._bridges.values():
        if getattr(b, "root_uri", None):
            roots.add(b.root_uri)
    if roots:
        lines.append(f"  Workspace roots: {', '.join(roots)}")

    total_diag = sum(
        len(b._diagnostics_cache) if hasattr(b, '_diagnostics_cache') else 0
        for b in mgr._bridges.values()
    )
    if total_diag:
        lines.append(f"  Cached diagnostics: {total_diag} files across bridges")
    return lines


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
        lines = _status_show_summary(
            stats['entries'],
            0,  # file_cache — via lsp_bridge import (not available at module level)
        )
        try:
            from .lsp_bridge import get_lsp_manager, _ast_file_cache
            if _ast_file_cache:
                lines[1] = f"  Symbol cache: {stats['entries']} parsed AST files in memory."
                lines.append(f"  File-read cache: {len(_ast_file_cache)} files cached")
            mgr = get_lsp_manager()
            lines.extend(_status_show_lsp_health(mgr))
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
    persist_symbol_cache()
    clear_symbol_cache()


def _register_skill(ctx: PluginContext) -> None:
    """Register the plugin-provided skill."""
    _plugin_dir = Path(__file__).parent
    _skill_md = _plugin_dir / "skills" / "native-code-intelligence.md"
    if _skill_md.exists():
        ctx.register_skill(
            name="native-code-intelligence",
            path=_skill_md,
            description="Native tree-sitter + ast-grep code intelligence tools for Hermes agent. Replaces deprecated LSP MCP with in-process AST parsing.",
        )


def _register_command_and_hooks(ctx: PluginContext) -> None:
    """Register slash command, session hooks, and pre_llm_call context injection."""
    ctx.register_command(
        "code-intel",
        handler=_handle_code_intel_slash,
        description="Manage AST-aware code intelligence and symbol caching."
    )
    ctx.register_hook("on_session_end", _on_session_end)

    # pre_llm_call hook — inject symbol context + diagnostics for mentioned files
    _pre_llm_call_cache: dict = {}  # abs_path -> (text, timestamp)
    _PRE_LLM_CALL_CACHE_MAX = 20
    _PRE_LLM_CALL_CACHE_TTL = 30  # seconds

    def _pre_llm_call_inject_context(**kwargs: Any) -> Optional[str]:
        """Before the LLM prompt, inject compact context for files in the message."""
        try:
            messages = kwargs.get("messages", [])
            if not messages:
                return None
            last_msg = ""
            for m in reversed(messages):
                if isinstance(m, dict) and m.get("role") == "user":
                    content = m.get("content", "")
                    if isinstance(content, str):
                        last_msg = content
                    break
            if not last_msg:
                return None

            # Quick-Check: nur bei Code-Keywords weitermachen
            _code_keywords = ('def ', 'class ', 'function ', 'import ', 'const ',
                             '.py', '.ts', '.tsx', '.js', '.rs', '.go', '.java',
                             ' file', ' code', ' fix', ' refactor', ' test')
            if not any(kw in last_msg.lower() for kw in _code_keywords):
                return None

            import re
            file_refs = re.findall(
                r'(?:^|[\s"\'`({])((?:@/|\./|/)?[\w/_.-]+\.(?:py|ts|tsx|js|jsx|rs|go|java|css|scss))',
                last_msg
            )
            if not file_refs:
                file_refs = re.findall(
                    r'(?:^|[\s"\'`({])((?:@/|\./)?[\w/_.-]+/(?:[\w/_.-]+\.(?:py|ts|tsx|js|jsx|rs|go|java)))',
                    last_msg
                )
            if not file_refs:
                return None
            file_refs = file_refs[:3]
            from .code_intel import code_symbols_tool, detect_language
            from .lsp_bridge import code_diagnostics_tool as _diag_tool
            context_parts = []
            for fref in file_refs:
                path = fref
                if path.startswith("@/"):
                    cwd = os.getcwd()
                    for prefix in ("src", "app", "lib", "components"):
                        candidate = os.path.join(cwd, prefix, path[2:])
                        if os.path.exists(candidate):
                            path = candidate
                            break
                if not os.path.isabs(path):
                    path = os.path.join(os.getcwd(), path)
                if not os.path.exists(path):
                    continue
                abs_path = os.path.abspath(path)
                cached = _pre_llm_call_cache.get(abs_path)
                if cached:
                    cached_text, cached_ts = cached
                    if time.monotonic() - cached_ts < _PRE_LLM_CALL_CACHE_TTL:
                        context_parts.append(cached_text)
                        continue
                    # TTL expired — discard and re-fetch
                    del _pre_llm_call_cache[abs_path]
                lang = detect_language(path)
                if not lang:
                    continue
                try:
                    symbols_json = code_symbols_tool(path=path, pattern="", include_body=False)
                    symbols = json.loads(symbols_json) if isinstance(symbols_json, str) else symbols_json
                    sym_list = symbols if isinstance(symbols, list) else symbols.get("symbols", [])
                    parts = []
                    if sym_list:
                        summary = f"[auto-context] {fref}: {len(sym_list)} symbols"
                        for s in sym_list[:8]:
                            name = s.get("name", "?")
                            kind = s.get("kind", "")
                            line = s.get("line", "")
                            summary += f"\n  L{line} {kind} {name}"
                        parts.append(summary)
                    try:
                        diag_json = _diag_tool(path=path)
                        diag = json.loads(diag_json) if isinstance(diag_json, str) else diag_json
                        if isinstance(diag, dict):
                            errs = diag.get("errors", 0) or diag.get("diagnostic_count", 0)
                            if errs:
                                parts.append(f"  ⚠ {errs} diagnostics")
                    except Exception:
                        pass
                    cached = "\n".join(parts) if parts else None
                    if cached:
                        context_parts.append(cached)
                        _pre_llm_call_cache[abs_path] = (cached, time.monotonic())
                        if len(_pre_llm_call_cache) > _PRE_LLM_CALL_CACHE_MAX:
                            _pre_llm_call_cache.pop(next(iter(_pre_llm_call_cache)), None)
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


def _inject_toolsets() -> None:
    """Register the code_intel toolset and inject into core platforms."""
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
                "code_format", "code_implementations",
                "code_call_hierarchy",
                "code_complexity",
                "code_type_hierarchy",
                "code_highlight",
                "code_inlay_hints",
                "code_document_symbols",
                "code_search_by_error",
                "code_hot_paths",
                "code_blast_radius",
                "code_pr_impact",
                "code_replace_body",
                "code_safe_delete",
                "code_insert_before",
                "code_insert_after",
                "code_overview",
            ],
        }

    new_tools = [
        "code_symbols", "code_search", "code_refactor",
        "code_definition", "code_references", "code_diagnostics",
        "code_callers", "code_callees", "code_capsule",
        "code_workspace_summary", "code_impact", "code_tests_for_symbol",
        "code_query", "code_rename", "code_workspace_symbols",
        "code_hover", "code_type_definition",
        "code_signatures", "code_action",
        "code_format", "code_implementations",
        "code_highlight",
        "code_inlay_hints",
        "code_document_symbols",
        "code_call_hierarchy",
        "code_complexity",
        "code_type_hierarchy",
        "code_search_by_error",
        "code_hot_paths",
        "code_blast_radius",
        "code_pr_impact",
        "code_replace_body",
        "code_safe_delete",
        "code_insert_before",
        "code_insert_after",
        "code_overview",
    ]
    for t in new_tools:
        toolsets._HERMES_CORE_TOOLS.append(t)
    for preset in ["hermes-acp", "hermes-api-server"]:
        if preset in toolsets.TOOLSETS:
            tools = toolsets.TOOLSETS[preset]["tools"]
            for t in new_tools:
                if t not in tools:
                    tools.append(t)


def _register_lsp_and_cache() -> None:
    """Register LSP-backed tools and restore the persisted symbol cache."""
    from . import code_intel
    try:
        from .lsp_bridge import register_lsp_tools
        register_lsp_tools()
    except Exception as e:
        import logging
        logging.getLogger("code_intel").warning(f"LSP tool registration failed: {e}")
    loaded = code_intel.load_symbol_cache()
    if loaded:
        import logging
        logging.getLogger("code_intel").info(f"Restored {loaded} symbol cache entries from disk")


def _inject_steering_hints() -> None:
    """Patch built-in tool descriptions to prefer code_intel tools."""
    import tools.registry

    hints = [
        ("search_files",
         "\n\nFor AST-aware structural search inside source files "
         "(find function calls, imports, decorators, etc.), prefer code_search — "
         "it understands syntax and won't match comments or strings."),
        ("read_file",
         "\n\nFor understanding what a file contains (list of functions, classes, "
         "methods with line numbers and signatures), prefer code_symbols — "
         "much more token-efficient than reading the entire file."),
        ("patch",
         "\n\nFor AST-aware structural replacement (rename patterns, wrap "
         "functions, add parameters across a file), prefer code_refactor — "
         "matches by syntax tree, not raw text. Dry-run by default."),
        ("code_definition",
         "\n\nWhen you need to understand HOW a symbol is used across the project, "
         "call code_references AFTER code_definition. For a quick one-shot overview, use code_capsule instead."),
        ("code_references",
         "\n\nBefore renaming or refactoring a symbol, always run code_references first "
         "to see all impacted files. Use group_by_file=True to save tokens on large codebases. "
         "For a compact summary, use code_capsule."),
        ("code_symbols",
         "\n\nFor cross-file navigation, first use code_symbols on the current file to confirm "
         "the symbol exists, then use code_definition or code_references for deeper analysis."),
    ]
    for tool_name, hint_text in hints:
        entry = tools.registry.registry.get_entry(tool_name)
        if entry and "description" in entry.schema and hint_text not in entry.schema["description"]:
            entry.schema["description"] += hint_text


def _patch_delegate_task() -> None:
    """Force code_intel into subagent toolsets and inject steering into child prompts."""
    try:
        import tools.delegate_tool as dt
        dt._SUBAGENT_TOOLSETS = sorted(
            name for name, defn in toolsets.TOOLSETS.items()
            if name not in dt._EXCLUDED_TOOLSET_NAMES
            and not name.startswith("hermes-")
            and not all(t in dt.DELEGATE_BLOCKED_TOOLS for t in defn.get("tools", []))
        )
        dt._TOOLSET_LIST_STR = ", ".join(f"'{n}'" for n in dt._SUBAGENT_TOOLSETS)

        if "toolsets" in dt.DELEGATE_TASK_SCHEMA["parameters"]["properties"]:
            ts_prop = dt.DELEGATE_TASK_SCHEMA["parameters"]["properties"]["toolsets"]
            ts_prop["description"] = (
                "Toolsets to enable for this subagent. "
                "Default: inherits your enabled toolsets. "
                f"Available toolsets: {dt._TOOLSET_LIST_STR}. "
                "Common patterns: ['terminal', 'file'] for code work, "
                "['web'] for research, ['browser'] for web interaction, "
                "['terminal', 'file', 'web'] for full-stack tasks."
            )
        if "tasks" in dt.DELEGATE_TASK_SCHEMA["parameters"]["properties"]:
            task_ts = dt.DELEGATE_TASK_SCHEMA["parameters"]["properties"]["tasks"]["items"]["properties"].get("toolsets")
            if task_ts:
                task_ts["description"] = (
                    f"Toolsets for this specific task. Available: {dt._TOOLSET_LIST_STR}. "
                    "Use 'web' for network access, 'terminal' for shell, 'browser' for web interaction."
                )

        import logging
        logging.getLogger("code_intel").info(f"Refreshed delegate_task toolsets: {dt._TOOLSET_LIST_STR}")

        if "code_intel" not in dt.DEFAULT_TOOLSETS:
            dt.DEFAULT_TOOLSETS.append("code_intel")

        _CODE_INTEL_STEERING = (
            "\n\n## 🧠 Code Intelligence Tools (PREFER over read_file/grep/patch)\n"
            "You have native AST + LSP code-intel tools. USE THEM FIRST for any code task.\n\n"
            "**Discovery (instead of read_file on whole files):**\n"
            "- `code_workspace_summary(path)` — monorepo overview: apps, packages, entry points.\n"
            "- `code_symbols(path)` — list functions/classes/methods in a file with line numbers.\n"
            "- `code_workspace_symbols(query)` — fuzzy find a symbol across the entire workspace.\n\n"
            "**Navigation (instead of grep):**\n"
            "- `code_definition(path, line)` — jump to where a symbol is defined.\n"
            "- `code_references(path, line, group_by_file=True)` — find ALL usages of a symbol.\n"
            "- `code_callers(path, line)` / `code_callees(path, line)` — call graph.\n"
            "- `code_capsule(path, line)` — one-shot: signature + doc + definition + top refs.\n"
            "- `code_hover(path, line)` — type signature + docstring without reading source.\n"
            "- `code_signatures(path, line)` — parameter hints inside a call site.\n"
            "- `code_type_definition(path, line)` — jump to the TYPE shape (interface/class).\n\n"
            "**Search (instead of search_files for code):**\n"
            "- `code_search(path, preset='function_calls'|'imports'|'decorator_calls'|...)` — "
            "AST-aware, won't match comments/strings.\n\n"
            "**Refactoring (instead of patch + sed):**\n"
            "- `code_rename(path, line, new_name, dry_run=True)` — semantic rename across files.\n"
            "- `code_refactor(path, pattern, rewrite, dry_run=True)` — AST structural rewrite.\n"
            "- `code_action(path, line)` — quick-fixes / organize imports / source.fixAll.\n\n"
            "**Quality:**\n"
            "- `code_diagnostics(path)` — LSP errors/warnings. RUN AFTER editing code.\n"
            "- `code_impact(path, line)` — blast radius before refactor.\n"
            "- `code_tests_for_symbol(path, line)` — find tests covering a symbol.\n\n"
            "**Workflow:** capsule → references → impact → rename/refactor (dry_run) → apply → diagnostics.\n"
            "**Anti-pattern:** read_file on a 1000-line file when code_symbols would give you what you need in 50 tokens."
        )

        _orig_build_prompt = dt._build_child_system_prompt
        def _patched_build_prompt(*args, **kwargs):
            base = _orig_build_prompt(*args, **kwargs)
            if _CODE_INTEL_STEERING not in base:
                base = base + _CODE_INTEL_STEERING
            return base
        dt._build_child_system_prompt = _patched_build_prompt

        _orig_build_agent = dt._build_child_agent
        def _patched_build_agent(*args, **kwargs):
            ts = kwargs.get("toolsets")
            if ts is not None and "code_intel" not in ts:
                kwargs["toolsets"] = list(ts) + ["code_intel"]
            return _orig_build_agent(*args, **kwargs)
        dt._build_child_agent = _patched_build_agent

        logging.getLogger("code_intel").info(
            "code_intel: forced into DEFAULT_TOOLSETS + steering injected into child prompts"
        )
    except Exception as e:
        import logging
        logging.getLogger("code_intel").warning(f"Failed to refresh delegate_task toolsets: {e}")


def register(ctx: PluginContext) -> None:
    """Plugin entry point: register skills, commands, toolsets, hooks, and steering."""
    _register_skill(ctx)
    _register_command_and_hooks(ctx)
    _inject_toolsets()
    _register_lsp_and_cache()
    _inject_steering_hints()
    _patch_delegate_task()
