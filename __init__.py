from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

try:
    import toolsets  # Hermes runtime — may not be available in standalone context
except ImportError:
    toolsets = None  # type: ignore[assignment]

from ._fmt import fmt_info
from ._logging import setup_logger as _setup_code_intel_logger

# ---------------------------------------------------------------------------
# Tool Profile System
# ---------------------------------------------------------------------------

_TOOL_PROFILES: dict = {
    "all": [
        "code_symbols", "code_search", "code_refactor",
        "code_definition", "code_references", "code_diagnostics",
        "code_callers", "code_callees", "code_capsule", "code_explain",
        "code_diagram_symbol",
        "code_workspace_summary", "code_impact", "code_tests_for_symbol",
        "code_query", "code_rename", "code_workspace_symbols",
        "code_hover", "code_type_definition",
        "code_signatures", "code_action",
        "code_format", "code_implementations",
        "code_call_hierarchy", "code_complexity",
        "code_type_hierarchy", "code_highlight",
        "code_inlay_hints", "code_document_symbols",
        "code_search_by_error", "code_hot_paths",
        "code_blast_radius", "code_pr_impact",
        "code_replace_body", "code_safe_delete",
        "code_insert_before", "code_insert_after",
        "code_overview", "code_cycle_detector",
        "code_dependency_graph", "code_unused_finder",
        # Previously missing from profile — registered but not listed
        "code_metrics", "code_duplicates", "code_move", "code_export",
        # New LSP 3.18 tools
        "code_completion", "code_code_lens",
        "code_folding_range", "code_selection_range",
        "code_linked_editing", "code_prepare_rename",
        # Additional LSP 3.18 tools
        "code_semantic_tokens",
        "code_document_links",
        "code_inline_values",
        # Git tools
        "code_todo_finder", "code_merge_conflict_finder",
        "code_git_log_symbol", "code_git_diff_file",
        # New AST tools
        "code_docstring_generate", "code_dependency_risk",
        # Batch refactoring
        "code_batch_refactor",
        # Security scanning
        "code_security_scan",
        # Git blame
        "code_git_blame",
        # Test generation
        "code_generate_tests",
        # Migration
        "code_migration",
        # Diff analysis
        "code_diff_analysis",
        # Timeline
        "code_timeline",
        # Knowledge graph
        "code_index",
        "code_graph_query",
        # Code review
        "code_review_assistant",
    ],
    # Core: daily drivers — navigation, search, understanding
    "core": [
        "code_symbols", "code_search", "code_definition",
        "code_references", "code_diagnostics",
        "code_callers", "code_callees", "code_capsule", "code_explain",
        "code_hover", "code_workspace_symbols",
        "code_query", "code_overview",
        # Git tools
        "code_todo_finder", "code_merge_conflict_finder",
        "code_git_diff_file",
        # Git blame
        "code_git_blame",
        # Batch refactoring
        "code_batch_refactor",
        # Diff & Timeline
        "code_diff_analysis",
        "code_timeline",
        # Knowledge graph
        "code_index",
        # Code review
        "code_review_assistant",
    ],
    # Search: AST-based search tools
    "search": [
        "code_search", "code_search_by_error",
        "code_symbols", "code_hot_paths",
        "code_workspace_symbols", "code_query",
        "code_callers", "code_callees",
        "code_git_log_symbol",
        "code_diagram_symbol",
        # Git blame
        "code_git_blame",
        # Security scanning
        "code_security_scan",
        # Diff & Timeline
        "code_diff_analysis",
        "code_timeline",
        # Graph queries
        "code_graph_query",
    ],
    # Edit: refactoring and code modification
    "edit": [
        "code_refactor", "code_replace_body", "code_safe_delete",
        "code_insert_before", "code_insert_after",
        "code_rename", "code_action",
        "code_format",
        "code_batch_refactor",
        "code_migration",
    ],
    # LSP: all LSP-powered tools
    "lsp": [
        "code_definition", "code_references", "code_diagnostics",
        "code_rename", "code_hover", "code_type_definition",
        "code_signatures", "code_action", "code_format",
        "code_implementations", "code_call_hierarchy",
        "code_type_hierarchy", "code_highlight",
        "code_inlay_hints", "code_document_symbols",
        "code_workspace_symbols",
        # New LSP 3.18 tools
        "code_completion", "code_code_lens",
        "code_folding_range", "code_selection_range",
        "code_linked_editing", "code_prepare_rename",
        # Additional LSP 3.18 tools
        "code_semantic_tokens",
        "code_document_links",
        "code_inline_values",
    ],
}
def get_active_profile() -> str:
    """Get the active tool profile from environment variable.

    Reads CODE_INTEL_TOOL_PROFILE env var (default: "core").
    Falls back to "all" if the profile is unknown.
    """
    profile = os.environ.get("CODE_INTEL_TOOL_PROFILE", "core").lower()
    if profile not in _TOOL_PROFILES:
        profile = "all"
    return profile
def get_profile_tools(profile: Optional[str] = None) -> list:
    """Get the list of tools for a given profile.

    If profile is None, uses the active profile.
    Returns all tools if profile is unknown.
    """
    if profile is None:
        profile = get_active_profile()
    return _TOOL_PROFILES.get(profile, _TOOL_PROFILES["all"])
def _setup_logger(name: str) -> logging.Logger:
    """Einheitliches Logging — delegiert an _logging.setup_logger."""
    return _setup_code_intel_logger(name)
def _status_show_summary(symbol_entries: int, file_cache_size: int) -> list:
    """Zeige Grund-Infos: Symbol-Cache + File-Read-Cache + Profile."""
    lines = ["[agentiker_code_intel] Status:"]
    profile = get_active_profile()
    tool_count = len(get_profile_tools(profile))
    lines.append(f"  Profile: {profile} ({tool_count}/{len(_TOOL_PROFILES['all'])} tools)")
    lines.append(f"  Symbol cache: {symbol_entries} parsed AST files in memory.")
    if file_cache_size:
        lines.append(f"  File-read cache: {file_cache_size} files cached")
    return lines

def _format_bridge_line(bridge_id, bridge):
    """Format a single LSP bridge status line."""
    info = bridge.get_server_info() if hasattr(bridge, 'get_server_info') else {}
    alive = "✓" if info.get("alive") else "✗"
    init = "init" if info.get("initialized") else "pending"
    diag = info.get("diagnostic_files", 0)
    cb = ""
    if bridge._circuit_open_until > 0:
        remaining = int(bridge._circuit_open_until - time.monotonic())
        cb = f" CB=open({remaining}s)" if remaining > 0 else " CB=closed"
    failures = bridge._failure_count
    idle = info.get("last_activity", None)
    idle_str = f" idle={idle:.0f}s" if idle is not None else ""
    text = f"    {bridge_id}: {alive} {init} diag_files={diag}{cb} fail={failures}{idle_str}"
    return fmt_info(text, title="LSP Bridge Status")
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
    from .code_tools import clear_symbol_cache, get_symbol_cache_stats

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
            from .lsp_bridge import _ast_file_cache, get_lsp_manager
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
        return "[agentiker_code_intel] AST symbol cache cleared successfully."

    if sub == "profile":
        if len(argv) > 1:
            new_profile = argv[1].lower()
            if new_profile in _TOOL_PROFILES:
                return (
                    f"Set CODE_INTEL_TOOL_PROFILE={new_profile} to enable.\n"
                    f"Run: export CODE_INTEL_TOOL_PROFILE={new_profile}\n"
                    f"Then restart Hermes or re-source your shell to apply."
                )
            else:
                return (
                    f"Unknown profile: {new_profile}\n"
                    f"Available: {', '.join(_TOOL_PROFILES.keys())}"
                )
        current = get_active_profile()
        count = len(get_profile_tools(current))
        total = len(_TOOL_PROFILES["all"])
        lines = [f"[agentiker_code_intel] Active profile: {current} ({count}/{total} tools)"]
        lines.append(f"Available profiles: {', '.join(_TOOL_PROFILES.keys())}")
        lines.append("Set via: CODE_INTEL_TOOL_PROFILE=<profile>")
        return "\n".join(lines)

    return f"Unknown subcommand: {sub}\nRun `/code-intel help` for usage."

# Hook handler
def _on_session_end(**kwargs: Any) -> None:
    """Persist AST caches to disk at session end, then clear memory."""
    from .code_tools import clear_symbol_cache, persist_symbol_cache
    persist_symbol_cache()
    clear_symbol_cache()

def _register_command_and_hooks(ctx: PluginContext) -> None:  # noqa: F821
    """Register slash command, session hooks, and pre_llm_call context injection."""
    ctx.register_command(
        "code-intel",
        handler=_handle_code_intel_slash,
        description="Manage AST-aware code intelligence and symbol caching."
    )
    ctx.register_hook("on_session_end", _on_session_end)

    from .hooks import on_pre_llm_call
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
def _inject_toolsets() -> None:
    """Register the code_intel toolset and inject into core platforms.

    Filters tools based on the active profile (default: "core").
    Override via CODE_INTEL_TOOL_PROFILE env var.
    Only works inside Hermes runtime (toolsets must be available).
    """
    if toolsets is None:
        return

    active_profile = get_active_profile()
    profile_tools = _TOOL_PROFILES.get(active_profile, _TOOL_PROFILES["all"])

    if "agentiker_code_intel" not in toolsets.TOOLSETS:
        toolsets.TOOLSETS["agentiker_code_intel"] = {
            "description": (
                f"AST-aware code intelligence [{active_profile} profile]: symbol extraction, "
                "structural search, safe refactoring, LSP go-to-definition and "
                "find-all-references (tree-sitter + ast-grep + LSP)"
            ),
            "tools": list(profile_tools),
        }

    new_tools = list(profile_tools)

    for t in new_tools:
        toolsets._HERMES_CORE_TOOLS.append(t)
    for preset in ["hermes-acp", "hermes-api-server"]:
        if preset in toolsets.TOOLSETS:
            tools = toolsets.TOOLSETS[preset]["tools"]
            for t in new_tools:
                if t not in tools:
                    tools.append(t)
def _register_ast_tools(ctx) -> None:
    """Register all 21 AST-based code_intel tools with ctx.register_tool().

    Called during plugin load. Handlers and schemas live in code_tools.py.
    """
    from tools.registry import registry

    from . import code_tools as ct
    from .tools.batch import (
        CODE_BATCH_REFACTOR_SCHEMA,
        _handle_code_batch_refactor,
    )
    from .tools.blame import (
        CODE_GIT_BLAME_SCHEMA,
        _handle_code_git_blame,
    )
    from .tools.capsule import (
        CODE_CAPSULE_SCHEMA,
        _handle_code_capsule,
    )
    from .tools.diff_analysis import (
        CODE_DIFF_ANALYSIS_SCHEMA,
        _handle_code_diff_analysis,
    )
    from .tools.git import (
        CODE_GIT_DIFF_FILE_SCHEMA,
        CODE_GIT_LOG_SYMBOL_SCHEMA,
        CODE_MERGE_CONFLICT_FINDER_SCHEMA,
        CODE_TODO_FINDER_SCHEMA,
        _handle_code_git_diff_file,
        _handle_code_git_log_symbol,
        _handle_code_merge_conflict_finder,
        _handle_code_todo_finder,
    )
    from .tools.knowledge_graph import (
        CODE_GRAPH_QUERY_SCHEMA,
        CODE_INDEX_SCHEMA,
        _handle_code_graph_query,
        _handle_code_index,
    )
    from .tools.migration import (
        CODE_MIGRATION_SCHEMA,
        _handle_code_migration,
    )
    from .tools.overview import (
        CODE_OVERVIEW_SCHEMA,
        _handle_code_overview,
    )
    from .tools.query import (
        CODE_QUERY_SCHEMA,
        _handle_code_query,
    )
    from .tools.review_assistant import (
        CODE_REVIEW_ASSISTANT_SCHEMA,
        _handle_code_review_assistant,
    )
    from .tools.security import (
        CODE_SECURITY_SCHEMA,
        _handle_code_security,
    )
    from .tools.testgen import (
        CODE_GENERATE_TESTS_SCHEMA,
        _handle_code_generate_tests,
    )
    from .tools.timeline import (
        CODE_TIMELINE_SCHEMA,
        _handle_code_timeline,
    )
    from .tools.workspace import (
        CODE_WORKSPACE_SUMMARY_SCHEMA,
        _handle_code_workspace_summary,
    )

    _AST_TOOL_REGISTRATIONS = [
        (ct.CODE_SYMBOLS_SCHEMA, ct._handle_code_symbols),
        (ct.CODE_SEARCH_SCHEMA, ct._handle_code_search),
        (ct.CODE_REFACTOR_SCHEMA, ct._handle_code_refactor),
        (CODE_CAPSULE_SCHEMA, _handle_code_capsule),
        (ct.CODE_EXPLAIN_SCHEMA, ct._handle_code_explain),
        (CODE_WORKSPACE_SUMMARY_SCHEMA, _handle_code_workspace_summary),
        (ct.CODE_IMPACT_SCHEMA, ct._handle_code_impact),
        (ct.CODE_COMPLEXITY_SCHEMA, ct._handle_code_complexity),
        (ct.CODE_SEARCH_BY_ERROR_SCHEMA, ct._handle_code_search_by_error),
        (ct.CODE_HOT_PATHS_SCHEMA, ct._handle_code_hot_paths),
        (ct.CODE_CYCLE_DETECTOR_SCHEMA, ct._handle_code_cycle_detector),
        (ct.CODE_DEPENDENCY_GRAPH_SCHEMA, ct._handle_code_dependency_graph),
        (ct.CODE_BLAST_RADIUS_SCHEMA, ct._handle_code_blast_radius),
        (ct.CODE_PR_IMPACT_SCHEMA, ct._handle_code_pr_impact),
        (ct.CODE_TESTS_FOR_SYMBOL_SCHEMA, ct._handle_code_tests_for_symbol),
        (CODE_QUERY_SCHEMA, _handle_code_query),
        (ct.CODE_REPLACE_BODY_SCHEMA, ct._handle_code_replace_body),
        (ct.CODE_SAFE_DELETE_SCHEMA, ct._handle_code_safe_delete),
        (ct.CODE_INSERT_BEFORE_SCHEMA, ct._handle_code_insert_before),
        (ct.CODE_INSERT_AFTER_SCHEMA, ct._handle_code_insert_after),
        (CODE_OVERVIEW_SCHEMA, _handle_code_overview),
        (ct.CODE_UNUSED_FINDER_SCHEMA, ct._handle_code_unused_finder),
        (ct.CODE_METRICS_SCHEMA, ct._handle_code_metrics),
        (ct.CODE_DUPLICATES_SCHEMA, ct._handle_code_duplicates),
        (ct.CODE_MOVE_SCHEMA, ct._handle_code_move),
        (ct.CODE_EXPORT_SCHEMA, ct._handle_code_export),
        (ct.CODE_DIAGRAM_SYMBOL_SCHEMA, ct._handle_code_diagram_symbol),
        (ct.CODE_DOCSTRING_GENERATE_SCHEMA, ct._handle_code_docstring_generate),
        (ct.CODE_DEPENDENCY_RISK_SCHEMA, ct._handle_code_dependency_risk),
        # Git tools
        (CODE_TODO_FINDER_SCHEMA, _handle_code_todo_finder),
        (CODE_MERGE_CONFLICT_FINDER_SCHEMA, _handle_code_merge_conflict_finder),
        (CODE_GIT_LOG_SYMBOL_SCHEMA, _handle_code_git_log_symbol),
        (CODE_GIT_DIFF_FILE_SCHEMA, _handle_code_git_diff_file),
        # Batch refactoring
        (CODE_BATCH_REFACTOR_SCHEMA, _handle_code_batch_refactor),
        # Security scanning
        (CODE_SECURITY_SCHEMA, _handle_code_security),
        # Git blame
        (CODE_GIT_BLAME_SCHEMA, _handle_code_git_blame),
        # Test generation
        (CODE_GENERATE_TESTS_SCHEMA, _handle_code_generate_tests),
        # Migration
        (CODE_MIGRATION_SCHEMA, _handle_code_migration),
        # Diff analysis
        (CODE_DIFF_ANALYSIS_SCHEMA, _handle_code_diff_analysis),
        # Timeline
        (CODE_TIMELINE_SCHEMA, _handle_code_timeline),
        # Knowledge graph
        (CODE_INDEX_SCHEMA, _handle_code_index),
        (CODE_GRAPH_QUERY_SCHEMA, _handle_code_graph_query),
        # Code review
        (CODE_REVIEW_ASSISTANT_SCHEMA, _handle_code_review_assistant),
    ]
    for schema, handler in _AST_TOOL_REGISTRATIONS:
        try:
            # Agent-facing registration
            ctx.register_tool(
                name=schema["name"],
                toolset="agentiker_code_intel",
                schema=schema["parameters"],
                handler=handler,
                description=schema["description"],
            )
            # Internal registry (for steering hints, subagent toolset, legacy compat)
            registry.register(
                name=schema["name"],
                toolset="agentiker_code_intel",
                schema=schema,
                handler=handler,
                check_fn=ct._check_code_intel_reqs,
                emoji="🔍",
            )
        except Exception as e:
            import logging
            logging.getLogger("agentiker_code_intel").warning(
                "Failed to register AST tool '%s': %s", schema.get("name", "?"), e
            )
    import logging
    logging.getLogger("agentiker_code_intel").info(
        "code_intel: %d AST tools registered via ctx.register_tool()",
        len(_AST_TOOL_REGISTRATIONS),
    )
def _register_lsp_and_cache(ctx) -> None:
    """Register LSP-backed tools, AST tools, and restore the persisted symbol cache."""
    from . import code_tools

    # Register all AST-based tools via ctx.register_tool()
    _register_ast_tools(ctx)

    try:
        from .lsp_bridge import register_lsp_tools
        register_lsp_tools(ctx)
    except Exception as e:
        import logging
        logging.getLogger("agentiker_code_intel").warning("LSP tool registration failed: %s", e)
    loaded = code_tools.load_symbol_cache()
    if loaded:
        import logging
        logging.getLogger("agentiker_code_intel").info("Restored %d symbol cache entries from disk", loaded)
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
    if toolsets is None:
        return
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
            task_ts = dt.DELEGATE_TASK_SCHEMA["parameters"]["properties"]["tasks"]\
                ["items"]["properties"].get("toolsets")
            if task_ts:
                task_ts["description"] = (
                    f"Toolsets for this specific task. Available: {dt._TOOLSET_LIST_STR}. "
                    "Use 'web' for network access, 'terminal' for shell, 'browser' for web interaction."
                )

        import logging
        logging.getLogger("agentiker_code_intel").info(
            "Refreshed delegate_task toolsets: %s", dt._TOOLSET_LIST_STR
        )

        if "agentiker_code_intel" not in dt.DEFAULT_TOOLSETS:
            dt.DEFAULT_TOOLSETS.append("agentiker_code_intel")

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
            "**Anti-pattern:** read_file for large files — code_symbols is more efficient."
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
            if ts is not None and "agentiker_code_intel" not in ts:
                kwargs["toolsets"] = list(ts) + ["agentiker_code_intel"]
            return _orig_build_agent(*args, **kwargs)
        dt._build_child_agent = _patched_build_agent

        logging.getLogger("agentiker_code_intel").info(
            "agentiker_code_intel: forced into DEFAULT_TOOLSETS + steering injected into child prompts"
        )
    except Exception as e:
        import logging
        logging.getLogger("agentiker_code_intel").warning(
            "Failed to refresh delegate_task toolsets: %s", e
        )
def _ensure_deps() -> None:
    """Auto-install fehlender Dependencies beim ersten Plugin-Start."""
    import importlib
    import logging
    import subprocess
    import sys
    logger = logging.getLogger(__name__)

    missing: list[str] = []
    for pkg_name, import_name in [
        ("PyYAML", "yaml"),
    ]:
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(pkg_name)

    if not missing:
        return

    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install"] + missing,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        logger.info("✅ Dependencies auto-installiert: %s", missing)
        return
    except Exception:
        pass

    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--user"] + missing,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        logger.info("✅ Dependencies via --user installiert: %s", missing)
        return
    except Exception as e:
        logger.error("❌ Auto-Install fehlgeschlagen: %s. Manuell: %s -m pip install %s",
                     e, sys.executable, " ".join(missing))


def register(ctx: PluginContext) -> None:  # noqa: F821
    """Plugin entry point: register skills, commands, toolsets, hooks, and steering."""
    _ensure_deps()
    from hermes_cli.plugins import (
        PluginContext,  # noqa: F811, F401 — lazy import, nur in Hermes-Runtime
    )

    _register_command_and_hooks(ctx)
    _inject_toolsets()
    _register_lsp_and_cache(ctx)
    _inject_steering_hints()
    _patch_delegate_task()
