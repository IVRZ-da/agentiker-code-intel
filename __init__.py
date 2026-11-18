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
    """Bring the code_intel tools into the ACP and API-server platform bundles.

    Only the platform-bundle append remains: the toolset itself is published by
    ``ctx.register_tool(..., toolset="agentiker_code_intel")`` and is therefore
    already known to Hermes through the tool registry (``get_all_toolsets()``,
    ``hermes tools list``). Two former rebinds were dropped (see
    ``references/core-override-analysis.md``):

    * the static ``TOOLSETS`` entry for the plugin toolset — redundant, because the
      registry already publishes it (only ``resolve_toolset(..., include_registry=False)``
      misses plugin toolsets).
    * appending to Hermes' core tool list — every bundle is built at module import,
      so a post-load append provably changed nothing.

    The append below is the one remaining core mutation: there is no plugin API
    that contributes tools to a platform bundle. It is why ``hermes plugins
    validate`` still reports ``no core override``; ``scripts/validate_gate.py``
    tracks it via ``references/core_override_allowlist.txt``.

    Filters tools based on the active profile (default: "core").
    Override via CODE_INTEL_TOOL_PROFILE env var.
    Only works inside Hermes runtime (toolsets must be available).
    """
    if toolsets is None:
        return

    active_profile = get_active_profile()
    profile_tools = list(_TOOL_PROFILES.get(active_profile, _TOOL_PROFILES["all"]))

    # Im Default-Modus existieren die Legacy-Einzel-Tools nicht — ein Preset, das sie
    # eintraegt, verweist auf tote Namen. Also die Dispatcher eintragen.
    try:
        from .namespaces import GROUPS, namespaces_enabled

        bundle_tools = sorted(GROUPS) if namespaces_enabled() else profile_tools
    except Exception:  # noqa: BLE001 — Namespace-Modul darf den Load nie brechen
        bundle_tools = profile_tools

    for preset in ["hermes-acp", "hermes-api-server"]:
        if preset in toolsets.TOOLSETS:
            tools = toolsets.TOOLSETS[preset]["tools"]
            for t in bundle_tools:
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
def _core_tool_registry():
    """Host-Registry (``tools.registry.registry``) liefern — oder ``None``.

    Bewusst kein blankes ``import tools.registry``: das Plugin bringt ein eigenes
    ``tools/``-Paket mit. Liegt das Plugin-Verzeichnis auf ``sys.path`` (Tests,
    Audit-/Gate-Skripte, ``register()`` ohne Host), importiert ``tools`` unser
    eigenes Paket als *Top-Level*-Modul — die relativen Importe darin
    (``from .._fmt``) scheitern dann mit "attempted relative import beyond
    top-level package" und ``register()`` platzt. Ein Teil-Import wird dabei
    zurueckgerollt, damit der Fehlversuch keinen kaputten Modul-Zustand hinterlaesst.
    """
    import sys
    from pathlib import Path

    plugin_dir = Path(__file__).resolve().parent
    module = sys.modules.get("tools.registry")
    if module is None:
        try:
            import tools.registry as module  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001 - Host fehlt / Kollision mit eigenem Paket
            partial = sys.modules.get("tools")
            if partial is not None and str(getattr(partial, "__file__", "")).startswith(str(plugin_dir)):
                for name in [m for m in sys.modules if m == "tools" or m.startswith("tools.")]:
                    del sys.modules[name]
            logging.getLogger("agentiker_code_intel").debug(
                "Steering-Hints uebersprungen (Host-Registry nicht importierbar): %s", exc)
            return None
    registry = getattr(module, "registry", None)
    if not hasattr(registry, "get_entry"):
        logging.getLogger("agentiker_code_intel").debug(
            "Steering-Hints uebersprungen: %r hat keine Registry", getattr(module, "__name__", module))
        return None
    return registry


def _inject_steering_hints() -> None:
    """Patch built-in tool descriptions to prefer code_intel tools.

    Namespace mode (default) must reference the dispatcher + op — the individual
    tool names (``code_symbols``, ``code_refactor``, …) do not exist there and a
    hint pointing at a missing tool is worse than no hint.
    """
    registry = _core_tool_registry()
    if registry is None:
        return

    from .namespaces import namespaces_enabled

    if namespaces_enabled():
        hints = [
            ("search_files",
             "\n\nFor structural code search, prefer code_search (op=search) — "
             "AST-aware, won't match comments or strings."),
            ("read_file",
             "\n\nTo understand what a file contains, prefer code_search (op=symbols) — "
             "lists functions/classes/methods more efficiently."),
            ("patch",
             "\n\nFor structural replacement, prefer code_edit (op=refactor) — "
             "AST-aware matching instead of raw text. Dry-run by default."),
        ]
        code_intel_hints = [
            ("code_lsp",
             "\n\nop=definition jumps to the symbol, op=references finds every call "
             "site across files; pass line (and character when ambiguous)."),
            ("code_search",
             "\n\nop=symbols lists a file's structure; op=capsule gives signature + "
             "doc + top references in one call instead of several."),
            ("code_edit",
             "\n\nAlways start with dry_run=true; op=rename and op=refactor are "
             "cross-file safe, op=format only reformats."),
        ]
    else:
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
        code_intel_hints = [
            ("code_definition",
             "\n\nFor all usages/references, also run code_references — "
             "finds every call site, import, and reference across files."),
            ("code_references",
             "\n\nTo group results by file for batch operations, "
             "use the group_by_file key in the returned dict."),
            ("code_symbols",
             "\n\nTo jump to a specific symbol's definition, "
             "also run code_definition with the symbol's line number."),
        ]

    for tool_name, hint_text in hints + code_intel_hints:
        entry = registry.get_entry(tool_name)
        if entry and "description" in entry.schema and hint_text not in entry.schema["description"]:
            entry.schema["description"] += hint_text
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
    _inject_toolsets()
    _register_lsp_and_cache(ctx)
    _inject_steering_hints()
