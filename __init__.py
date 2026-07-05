from __future__ import annotations

import logging
import time
from typing import Any, Optional

try:
    import toolsets  # Hermes runtime — may not be available in standalone context
except ImportError:
    toolsets = None  # type: ignore[assignment]

from ._fmt import fmt_info
from ._logging import setup_logger as _setup_code_intel_logger
from ._profiles import _TOOL_PROFILES, get_active_profile, get_profile_tools

# ---------------------------------------------------------------------------
# Tool Profile System
# ---------------------------------------------------------------------------

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
    """Register all AST-based code_intel tools with ctx.register_tool().

    Called during plugin load. Schemas and handlers imported directly from
    tool sub-modules (statt ueber code_tools-Facade, die 28+ Module ladet).
    """

    from .tools.ast_edit_tools.insert import (
        _handle_code_insert_after,
        _handle_code_insert_before,
    )
    from .tools.ast_edit_tools.move import _handle_code_move
    from .tools.ast_edit_tools.replace_body import _handle_code_replace_body
    from .tools.ast_edit_tools.safe_delete import _handle_code_safe_delete

    # Schemas + Handler aus Tool-Sub-Modulen (direkt, ohne code_tools-Umweg)
    from .tools.ast_edit_tools.schemas import (
        CODE_INSERT_AFTER_SCHEMA,
        CODE_INSERT_BEFORE_SCHEMA,
        CODE_MOVE_SCHEMA,
        CODE_REPLACE_BODY_SCHEMA,
        CODE_SAFE_DELETE_SCHEMA,
    )
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
    from .tools.complexity import (
        CODE_COMPLEXITY_SCHEMA,
        _handle_code_complexity,
    )
    from .tools.diagram import (
        CODE_DIAGRAM_SYMBOL_SCHEMA,
        _handle_code_diagram_symbol,
    )
    from .tools.diff_analysis import (
        CODE_DIFF_ANALYSIS_SCHEMA,
        _handle_code_diff_analysis,
    )
    from .tools.duplicates_extractor import (
        CODE_DUPLICATES_SCHEMA,
        _handle_code_duplicates,
    )
    from .tools.explain_extractor import (
        CODE_EXPLAIN_SCHEMA,
        _handle_code_explain,
    )
    from .tools.export import (
        CODE_DEPENDENCY_RISK_SCHEMA,
        CODE_DOCSTRING_GENERATE_SCHEMA,
        CODE_EXPORT_SCHEMA,
        _handle_code_dependency_risk,
        _handle_code_docstring_generate,
        _handle_code_export,
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
    from .tools.graph_analysis import (
        CODE_CYCLE_DETECTOR_SCHEMA,
        CODE_DEPENDENCY_GRAPH_SCHEMA,
        CODE_HOT_PATHS_SCHEMA,
        _handle_code_cycle_detector,
        _handle_code_dependency_graph,
        _handle_code_hot_paths,
    )
    from .tools.impact import (
        CODE_BLAST_RADIUS_SCHEMA,
        CODE_IMPACT_SCHEMA,
        CODE_PR_IMPACT_SCHEMA,
        _handle_code_blast_radius,
        _handle_code_impact,
        _handle_code_pr_impact,
    )
    from .tools.knowledge_graph import (
        CODE_GRAPH_QUERY_SCHEMA,
        CODE_INDEX_SCHEMA,
        _handle_code_graph_query,
        _handle_code_index,
    )
    from .tools.metrics import (
        CODE_METRICS_SCHEMA,
        _handle_code_metrics,
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
    from .tools.refactor_extractor import (
        CODE_REFACTOR_SCHEMA,
        _handle_code_refactor,
    )
    from .tools.review_assistant import (
        CODE_REVIEW_ASSISTANT_SCHEMA,
        _handle_code_review_assistant,
    )
    from .tools.search_by_error import (
        CODE_SEARCH_BY_ERROR_SCHEMA,
        _handle_code_search_by_error,
    )
    from .tools.search_extractor import (
        CODE_SEARCH_SCHEMA,
        _handle_code_search,
    )
    from .tools.security import (
        CODE_SECURITY_SCHEMA,
        _handle_code_security,
    )
    from .tools.symbols_extractor import (
        CODE_SYMBOLS_SCHEMA,
        _check_code_intel_reqs,
        _handle_code_symbols,
    )
    from .tools.test_coverage import (
        CODE_TESTS_FOR_SYMBOL_SCHEMA,
        _handle_code_tests_for_symbol,
    )
    from .tools.testgen import (
        CODE_GENERATE_TESTS_SCHEMA,
        _handle_code_generate_tests,
    )
    from .tools.timeline import (
        CODE_TIMELINE_SCHEMA,
        _handle_code_timeline,
    )
    from .tools.unused import (
        CODE_UNUSED_FINDER_SCHEMA,
        _handle_code_unused_finder,
    )
    from .tools.workspace import (
        CODE_WORKSPACE_SUMMARY_SCHEMA,
        _handle_code_workspace_summary,
    )

    _AST_TOOL_REGISTRATIONS = [
        (CODE_SYMBOLS_SCHEMA, _handle_code_symbols),
        (CODE_SEARCH_SCHEMA, _handle_code_search),
        (CODE_REFACTOR_SCHEMA, _handle_code_refactor),
        (CODE_CAPSULE_SCHEMA, _handle_code_capsule),
        (CODE_EXPLAIN_SCHEMA, _handle_code_explain),
        (CODE_WORKSPACE_SUMMARY_SCHEMA, _handle_code_workspace_summary),
        (CODE_IMPACT_SCHEMA, _handle_code_impact),
        (CODE_COMPLEXITY_SCHEMA, _handle_code_complexity),
        (CODE_SEARCH_BY_ERROR_SCHEMA, _handle_code_search_by_error),
        (CODE_HOT_PATHS_SCHEMA, _handle_code_hot_paths),
        (CODE_CYCLE_DETECTOR_SCHEMA, _handle_code_cycle_detector),
        (CODE_DEPENDENCY_GRAPH_SCHEMA, _handle_code_dependency_graph),
        (CODE_BLAST_RADIUS_SCHEMA, _handle_code_blast_radius),
        (CODE_PR_IMPACT_SCHEMA, _handle_code_pr_impact),
        (CODE_TESTS_FOR_SYMBOL_SCHEMA, _handle_code_tests_for_symbol),
        (CODE_QUERY_SCHEMA, _handle_code_query),
        (CODE_REPLACE_BODY_SCHEMA, _handle_code_replace_body),
        (CODE_SAFE_DELETE_SCHEMA, _handle_code_safe_delete),
        (CODE_INSERT_BEFORE_SCHEMA, _handle_code_insert_before),
        (CODE_INSERT_AFTER_SCHEMA, _handle_code_insert_after),
        (CODE_OVERVIEW_SCHEMA, _handle_code_overview),
        (CODE_UNUSED_FINDER_SCHEMA, _handle_code_unused_finder),
        (CODE_METRICS_SCHEMA, _handle_code_metrics),
        (CODE_DUPLICATES_SCHEMA, _handle_code_duplicates),
        (CODE_MOVE_SCHEMA, _handle_code_move),
        (CODE_EXPORT_SCHEMA, _handle_code_export),
        (CODE_DIAGRAM_SYMBOL_SCHEMA, _handle_code_diagram_symbol),
        (CODE_DOCSTRING_GENERATE_SCHEMA, _handle_code_docstring_generate),
        (CODE_DEPENDENCY_RISK_SCHEMA, _handle_code_dependency_risk),
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
            ctx.register_tool(
                name=schema.get("name", ""),
                toolset="agentiker_code_intel",
                schema=schema,
                handler=handler,
                description=schema.get("description", ""),
                check_fn=_check_code_intel_reqs,
                emoji="🔍",
            )
        except Exception as e:
            import logging
            logging.getLogger("agentiker_code_intel").warning(
                "Failed to register AST tool '%s': %s", schema.get("name", "?"), e
            )
    import logging
    logging.getLogger("agentiker_code_intel").info(
        "code_intel: %d AST tools registered", len(_AST_TOOL_REGISTRATIONS),
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
         "\n\nFor structural code search, prefer code_search — "
         "AST-aware, won't match comments or strings."),
        ("read_file",
         "\n\nTo understand what a file contains, prefer code_symbols — "
         "lists functions/classes/methods more efficiently."),
        ("patch",
         "\n\nFor structural replacement, prefer code_refactor — "
         "AST-aware matching instead of raw text. Dry-run by default."),
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
            "AST + LSP tools. USE THEM FIRST for code tasks.\n\n"
            "**Discovery (instead of read_file):**\n"
            "- `code_symbols(path)` — list functions/classes/methods with line numbers.\n"
            "- `code_workspace_symbols(query)` — fuzzy find symbols across the workspace.\n\n"
            "**Navigation (instead of grep):**\n"
            "- `code_definition(path, line)` — go to definition.\n"
            "- `code_references(path, line)` — find all usages.\n"
            "- `code_capsule(path, line)` — one-shot: signature + doc + definition + refs.\n\n"
            "**Search (instead of search_files for code):**\n"
            "- `code_search(path, preset=...)` — AST-aware, no false positives.\n\n"
            "**Refactoring (instead of patch):**\n"
            "- `code_rename(path, line, new_name)` — semantic rename across files.\n"
            "- `code_refactor(path, pattern, rewrite)` — AST structural rewrite.\n\n"
            "**Quality:**\n"
            "- `code_diagnostics(path)` — LSP errors/warnings. RUN AFTER editing.\n"
            "- `code_impact(path, line)` — blast radius before refactoring.\n"
            "- `code_tests_for_symbol(path, line)` — find tests for a symbol.\n"
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
    except Exception as e:
        logger.warning("⚠️ Dependencies via pip nicht installierbar, versuche --user: %s", e)

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
