"""Namespace tool fusion (P3.3) — 7 dispatcher tools instead of 70 individual ones.

Why
---
Every request ships all tool schemas. 70 ``code_*`` schemas cost far more context
than they earn: in practice a handful of families are used (search/read, LSP
navigation, graph analysis, editing, review, git history, meta). Publishing one
dispatcher per family with an ``op`` enum keeps every capability but shrinks the
schema payload.

Invariants
----------
* Nothing is lost: each op maps 1:1 to a former tool (``code_<op>``); a test
  asserts that the union of all ops equals the real registration catalog.
* Parameters of a namespace are the *union of the real schemas* of its ops, so no
  argument disappears. Required-argument checks are done per op at dispatch time
  and report exactly what is missing.
* Reversible: ``CODE_INTEL_NAMESPACES=0`` restores the legacy 1:1 registration.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)

TOOLSET = "agentiker_code_intel"
ENV_FLAG = "CODE_INTEL_NAMESPACES"

# op value -> former tool name is always f"code_{op}".
GROUPS: dict[str, dict[str, Any]] = {
    "code_search": {
        "description": (
            "Search and understand code: AST-aware search, symbol indexes, overviews, "
            "capsules, workspace summaries. Start here for 'where is X / what does this "
            "file contain' questions. Prefer over terminal grep."
        ),
        "ops": ["search", "symbols", "overview", "query", "search_by_error", "todo_finder", "capsule", "explain"],
    },
    "code_lsp": {
        "description": (
            "Language-server navigation and refactoring primitives: definition, "
            "references, hover, diagnostics, document symbols, completion, type "
            "hierarchy, plus editing-support requests (prepare_rename, linked_editing)."
        ),
        "ops": [
            "definition",
            "references",
            "hover",
            "implementations",
            "type_definition",
            "type_hierarchy",
            "diagnostics",
            "document_symbols",
            "document_links",
            "completion",
            "signatures",
            "highlight",
            "inlay_hints",
            "inline_values",
            "selection_range",
            "folding_range",
            "semantic_tokens",
            "code_lens",
            "linked_editing",
            "prepare_rename",
        ],
    },
    "code_graph": {
        "description": (
            "Structure and impact analysis: callers/callees, call hierarchy, blast "
            "radius, impact, dependency graphs, import cycles, hot paths, complexity "
            "and metrics. Use before changing a symbol."
        ),
        "ops": [
            "callers",
            "callees",
            "call_hierarchy",
            "blast_radius",
            "impact",
            "dependency_graph",
            "graph_query",
            "cycle_detector",
            "hot_paths",
            "metrics",
            "complexity",
            "diagram_symbol",
        ],
    },
    "code_edit": {
        "description": (
            "AST-aware writes: structural search/replace, batch refactor across "
            "files, rename, replace body, insert before/after, move, safe delete, "
            "format, migrations. Always dry_run=True first unless told otherwise."
        ),
        "ops": [
            "refactor",
            "batch_refactor",
            "rename",
            "replace_body",
            "insert_before",
            "insert_after",
            "move",
            "safe_delete",
            "format",
            "action",
            "migration",
        ],
    },
    "code_review": {
        "description": (
            "Review and quality gates: code review between git refs, diff analysis, "
            "PR impact, security scan, unused code, duplicates, tests covering a "
            "symbol."
        ),
        "ops": [
            "review_assistant",
            "diff_analysis",
            "pr_impact",
            "security_scan",
            "unused_finder",
            "duplicates",
            "tests_for_symbol",
        ],
    },
    "code_git": {
        "description": (
            "Repository history: blame (line or file), uncommitted diffs, per-symbol "
            "history, merge-conflict markers, symbol timeline."
        ),
        "ops": ["git_blame", "git_diff_file", "git_log_symbol", "merge_conflict_finder", "timeline"],
    },
    "code_meta": {
        "description": (
            "Meta and generation: build/refresh the project index, export a project "
            "overview, dependency risk, workspace symbols/summary, docstring and test "
            "generation."
        ),
        "ops": [
            "index",
            "export",
            "dependency_risk",
            "workspace_symbols",
            "workspace_summary",
            "docstring_generate",
            "generate_tests",
        ],
    },
}

_CATALOG: dict[str, dict[str, Any]] | None = None


def namespaces_enabled() -> bool:
    """Namespaces are the default; ``CODE_INTEL_NAMESPACES=0`` restores legacy."""
    raw = os.environ.get(ENV_FLAG, "").strip().lower()
    return raw not in {"0", "false", "no", "off", "legacy"}


class _Catalog:
    """Minimal ctx stand-in that only records tool registrations."""

    def __init__(self) -> None:
        self.tools: dict[str, dict[str, Any]] = {}

    def register_tool(
        self,
        name: str | None = None,
        toolset: str | None = None,
        schema: dict | None = None,
        handler: Callable | None = None,
        description: str = "",
        **kwargs: Any,
    ) -> None:
        if name:
            self.tools[name] = {
                "schema": schema or {},
                "handler": handler,
                "description": description,
            }

    def __getattr__(self, item: str) -> Callable[..., None]:
        # Commands/hooks/steering calls are irrelevant while collecting schemas.
        def _noop(*args: Any, **kwargs: Any) -> None:
            return None

        return _noop


def build_catalog(
    register_ast: Callable[[Any], None], register_lsp: Callable[[Any], None]
) -> dict[str, dict[str, Any]]:
    """Replay the legacy registrations into a recorder and return name -> tool."""
    global _CATALOG
    if _CATALOG is not None:
        return _CATALOG

    collector = _Catalog()
    register_ast(collector)
    register_lsp(collector)
    _CATALOG = collector.tools
    logger.debug("code_intel namespaces: catalog built with %d tools", len(_CATALOG))
    return _CATALOG


def reset_catalog() -> None:
    """Drop the cached catalog (tests)."""
    global _CATALOG
    _CATALOG = None


def _param_meta(catalog: dict[str, dict[str, Any]], ops: list[str]) -> dict[str, dict]:
    """Union of the real parameter schemas of all ops (first non-empty wins)."""
    meta: dict[str, dict] = {}
    for op in ops:
        params = (catalog.get(f"code_{op}", {}).get("schema") or {}).get("parameters") or {}
        for key, spec in (params.get("properties") or {}).items():
            if key == "op":
                continue
            if key not in meta or len(str(spec.get("description", ""))) > len(str(meta[key].get("description", ""))):
                meta[key] = dict(spec)
    return meta


def _error(msg: str) -> str:
    """Error payload — JSON, so callers can parse it like every other tool."""
    return json.dumps({"status": "error", "error": msg, "message": msg}, ensure_ascii=False)


def _make_handler(group: str, ops: list[str], catalog: dict[str, dict[str, Any]]) -> Callable[..., str]:
    def _handler(args: dict | None = None, **kwargs: Any) -> str:
        payload = dict(args or {})
        payload.update(kwargs)
        op = str(payload.pop("op", "") or "").strip()

        if not op:
            return _error(f"{group}: 'op' is required. One of: {', '.join(ops)}")
        if op not in ops:
            return _error(f"{group}: unknown op '{op}'. One of: {', '.join(ops)}")

        tool_name = f"code_{op}"
        tool = catalog.get(tool_name)
        if tool is None or tool.get("handler") is None:
            return _error(f"{group}: op '{op}' has no handler ({tool_name} not registered)")

        required = ((tool.get("schema") or {}).get("parameters") or {}).get("required") or []
        missing = [r for r in required if payload.get(r) in (None, "")]
        if missing:
            return _error(
                f"{group}: op '{op}' requires {', '.join(missing)} (got: {', '.join(sorted(payload)) or 'nothing'})"
            )

        clean = {k: v for k, v in payload.items() if v is not None and k != "op"}
        try:
            return tool["handler"](clean)
        except Exception as exc:  # a broken op must not kill the whole tool
            logger.warning("code_intel namespaces: op %s failed: %s", op, exc)
            return _error(f"{group}: op '{op}' failed: {exc}")

    _handler.__name__ = f"_handle_{group}"
    return _handler


def build_tool_specs(catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Build (name, schema, description, handler) for all namespace tools."""
    specs: list[dict[str, Any]] = []
    for group, cfg in GROUPS.items():
        ops = [op for op in cfg["ops"] if f"code_{op}" in catalog]
        if not ops:
            logger.warning("code_intel namespaces: %s has no ops in catalog, skipped", group)
            continue

        properties: dict[str, dict] = {
            "op": {
                "type": "string",
                "enum": ops,
                "description": "Operation to run. Required parameters per op are "
                "validated at dispatch and reported on error.",
            }
        }
        properties.update(_param_meta(catalog, ops))

        specs.append(
            {
                "name": group,
                "description": cfg["description"] + f" Ops: {', '.join(ops)}.",
                "schema": {
                    "name": group,
                    "description": cfg["description"],
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": ["op"],
                    },
                },
                "handler": _make_handler(group, ops, catalog),
                "ops": ops,
            }
        )
    return specs


def register_namespaces(ctx: Any, catalog: dict[str, dict[str, Any]]) -> int:
    """Register the 7 namespace tools on a real PluginContext. Returns the count."""
    specs = build_tool_specs(catalog)
    for spec in specs:
        try:
            ctx.register_tool(
                name=spec["name"],
                toolset=TOOLSET,
                schema=spec["schema"],
                handler=spec["handler"],
                description=spec["description"],
            )
        except Exception as exc:  # one bad group must not block the rest
            logger.warning("code_intel namespaces: registering %s failed: %s", spec["name"], exc)
    logger.info("code_intel: %d namespace tools registered (70 legacy tools folded in)", len(specs))
    return len(specs)
