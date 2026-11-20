from __future__ import annotations

import logging
import time
from typing import Any, Optional

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

    # Nutzungs-Messung (0.6.34): zählt pro Dispatcher + op, damit "wird kaum
    # genutzt" künftig eine Zahl hat statt eines Gefühls.
    from . import usage as usage_mod
    ctx.register_hook("post_tool_call", usage_mod.on_post_tool_call)
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
    """Register LSP-backed tools, AST tools, and restore the persisted symbol cache.

    Two modes (see namespaces.py):
    * default — 7 namespace dispatchers (``code_search``, ``code_lsp``, …) replace
      the 70 individual ``code_*`` schemas in the prompt; every op still runs the
      original handler.
    * ``CODE_INTEL_NAMESPACES=0`` — legacy 1:1 registration, unchanged behaviour.
    """
    from . import code_tools
    from .namespaces import build_catalog, namespaces_enabled, register_namespaces

    def _register_ast(target) -> None:
        _register_ast_tools(target)

    def _register_lsp(target) -> None:
        try:
            from .lsp_bridge import register_lsp_tools
            register_lsp_tools(target)
        except Exception as e:
            import logging
            logging.getLogger("agentiker_code_intel").warning(
                "LSP tool registration failed: %s", e)

    if namespaces_enabled():
        catalog = build_catalog(_register_ast, _register_lsp)
        register_namespaces(ctx, catalog)
    else:
        _register_ast(ctx)
        _register_lsp(ctx)

    loaded = code_tools.load_symbol_cache()
    if loaded:
        import logging
        logging.getLogger("agentiker_code_intel").info("Restored %d symbol cache entries from disk", loaded)


_STEERING_SECTION_ID = "code-intel"


def _register_steering_section(ctx) -> None:  # noqa: F821
    """Registriere den Code-Intel-Steering-Text als System-Prompt-Sektion.

    Fruehere Versionen patchten die ``description`` der Core-Tools
    (``search_files``/``read_file``/``patch``) im Host-Registry. Das ist eine
    Mutation fremder Schemas; Hermes-Review (PR #157345, Punkt 2) hat sie
    abgelehnt. Der oeffentliche Weg ist ``ctx.register_system_prompt_section``:
    der Text landet im System-Prompt jeder neuen Session und nennt die
    Dispatcher, ohne fremde Tools anzufassen.
    """
    text = (
        "## Code intelligence (agentiker-code-intel)\n"
        "For questions about the code in this workspace, prefer the code_* "
        "dispatchers over raw file reads: `code_search` (op=symbols|search|overview) "
        "to understand a file or find a symbol, `code_lsp` "
        "(op=definition|references|diagnostics) to navigate and to verify an edit, "
        "`code_graph` (op=impact|callers) before changing a symbol, `code_edit` "
        "(op=refactor|rename, dry_run=true first) for structural rewrites. Each "
        "dispatcher takes an `op` argument; use it instead of read_file/"
        "search_files/patch for structural work."
    )
    try:
        ctx.register_system_prompt_section(_STEERING_SECTION_ID, text)
    except Exception as exc:  # noqa: BLE001 — aeltere Hermes-Version ohne diese API
        logging.getLogger("agentiker_code_intel").debug(
            "Steering-Sektion nicht registriert (Hermes-API fehlt): %s", exc)
# Subagent-Steering: kein Core-Patch mehr. Zwei frueher hier installierte Rebinds sind
# entfallen (siehe references/core-override-analysis.md):
#   * delegate_tool_toolsets.DEFAULT_TOOLSETS.append(...) — Child-Toolsets werden seit
#     v0.23.1 vom Parent geerbt; der Default greift nur im Fallback ohne Parent-Toolsets.
#   * Patch von tools.delegate_tool._build_child_system_prompt — ersetzt durch den
#     oeffentlichen Hook pre_llm_call (hooks.on_pre_llm_call), der fuer jeden Turn eines
#     Subagenten feuert, wenn ``parent_session_id`` gesetzt ist.
_DEP_IMPORTS: list[tuple[str, str]] = [
    # (Paketname fuer pip, Importname)
    ("tree-sitter", "tree_sitter"),
    ("ast-grep-py", "ast_grep_py"),
    ("tree-sitter-python", "tree_sitter_python"),
    ("tree-sitter-typescript", "tree_sitter_typescript"),
    ("tree-sitter-javascript", "tree_sitter_javascript"),
    ("tree-sitter-go", "tree_sitter_go"),
    ("tree-sitter-rust", "tree_sitter_rust"),
    ("tree-sitter-java", "tree_sitter_java"),
    ("PyYAML", "yaml"),
    ("rich", "rich"),
]


def _missing_deps() -> list[str]:
    """Gibt die pip-Namen aller nicht importierbaren Code-Intel-Abhaengigkeiten zurueck."""
    import importlib

    missing: list[str] = []
    for pkg_name, import_name in _DEP_IMPORTS:
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(pkg_name)
    return missing


def _ensure_deps() -> None:
    """Pruefe die Code-Intel-Abhaengigkeiten und melde fehlende.

    Wichtig: Es wird NICHT ungefragt installiert. Ein Plugin-Load darf das
    Runtime-venv nicht veraendern (Hermes-Review: offline/parallele Starts/
    Package-Konflikte koennen das Environment beschaedigen). Installation nur mit
    ``CODE_INTEL_AUTO_INSTALL=1`` und ausschliesslich in ``sys.executable``.

    Hintergrund: Nach einem Hermes-Update laeuft eine NEUE Runtime-venv
    (z.B. Python 3.14 unter ``~/.hermes/installs/<id>/environments/<id>/venv``);
    dort fehlen die Grammatiken und ALLE AST-Tools fallen still aus.
    """
    import logging
    import os
    import subprocess
    import sys

    logger = logging.getLogger(__name__)

    missing = _missing_deps()
    if not missing:
        return

    logger.warning(
        "code_intel: %d Python-Abhaengigkeit(en) fehlen: %s — AST-Tools liefern ohne sie "
        "keine Ergebnisse. Aktives venv: %s. Reparatur: 'hermes pm repair' oder "
        "'uv pip install --python %s %s'.",
        len(missing), ", ".join(missing), sys.prefix,
        sys.executable, " ".join(f"'{m}'" for m in missing),
    )

    if os.environ.get("CODE_INTEL_AUTO_INSTALL") != "1":
        return

    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", *missing],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300,
        )
        logger.info("✅ Dependencies installiert (CODE_INTEL_AUTO_INSTALL=1): %s", missing)
    except Exception as e:  # noqa: BLE001 — Plugin-Load darf nie crashen
        logger.error(
            "❌ Auto-Install fehlgeschlagen: %s. Manuell: %s -m pip install %s",
            e, sys.executable, " ".join(missing),
        )


def register(ctx: PluginContext) -> None:  # noqa: F821
    """Plugin entry point: register skills, commands, toolsets, hooks, and steering."""
    _ensure_deps()
    from hermes_cli.plugins import (
        PluginContext,  # noqa: F811, F401 — lazy import, nur in Hermes-Runtime
    )

    _register_command_and_hooks(ctx)
    _register_steering_section(ctx)
    _register_lsp_and_cache(ctx)
