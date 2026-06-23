"""tools/knowledge_graph.py — Pre-indexed code knowledge graph with SQLite cache.

Provides:
- code_index — Build/persist import graph for a project
- code_graph_query — Query cached graph (callers, callees, hot paths, cycles)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .._fmt import fmt_err, fmt_ok
from .._import_graph import ImportGraph
from .._logging import setup_logger as _setup_code_intel_logger

logger = _setup_code_intel_logger(__name__)

__all__ = ["code_index_tool", "code_graph_query_tool",
           "CODE_INDEX_SCHEMA", "CODE_GRAPH_QUERY_SCHEMA"]

# ---------------------------------------------------------------------------
# Schema: code_index
# ---------------------------------------------------------------------------
CODE_INDEX_SCHEMA = {
    "name": "code_index",
    "description": (
        "Build and persist a code knowledge graph for a project. "
        "Scans all source files, builds import relationships, call graphs, "
        "and caches them in a SQLite database (~/.hermes/code_graph/<project>.db). "
        "Subsequent calls are instant (mtime check). Use force_rescan=True to rebuild."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Project root path.",
            },
            "force_rescan": {
                "type": "boolean",
                "description": "Force full rescan even if cache is fresh (default: false).",
                "default": False,
            },
            "depth": {
                "type": "integer",
                "description": "Directory scan depth (default: 5).",
                "default": 5,
            },
        },
        "required": ["path"],
    },
}

# ---------------------------------------------------------------------------
# Schema: code_graph_query
# ---------------------------------------------------------------------------
CODE_GRAPH_QUERY_SCHEMA = {
    "name": "code_graph_query",
    "description": (
        "Query a cached code knowledge graph. Returns callers, callees, "
        "hot paths, cycles, or dependency health for a project. "
        "Run code_index first to build the cache."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Project root path (must have been indexed).",
            },
            "query": {
                "type": "string",
                "enum": ["callers", "callees", "hot_paths", "cycles", "health", "summary"],
                "description": "Query type.",
            },
            "symbol": {
                "type": "string",
                "description": "File path or symbol name for callers/callees queries.",
            },
            "top_n": {
                "type": "integer",
                "description": "Max results (default: 10).",
                "default": 10,
            },
        },
        "required": ["path", "query"],
    },
}


# ---------------------------------------------------------------------------
# Default cache path
# ---------------------------------------------------------------------------
def _default_cache_path(project_root: str) -> str:
    """Default cache path: ~/.hermes/code_graph/<project_name>.db"""
    name = Path(project_root).resolve().name
    return str(Path.home() / ".hermes" / "code_graph" / f"{name}.db")


# ---------------------------------------------------------------------------
# Tool: code_index
# ---------------------------------------------------------------------------
def code_index_tool(path: str, force_rescan: bool = False, depth: int = 5) -> str:
    """Build and persist a code knowledge graph for a project."""
    project = Path(path).expanduser().resolve()
    if not project.exists():
        return fmt_err(f"Path not found: {path}")

    db_path = _default_cache_path(str(project))

    graph = ImportGraph.for_project(str(project), db_path, depth, force_rescan)

    stats = {
        "project": str(project),
        "cache": db_path,
        "files_indexed": len(graph._graph),
        "edges": sum(len(v) for v in graph._graph.values()),
        "rescan": force_rescan or not Path(db_path).exists(),
    }
    return fmt_ok(stats)


# ---------------------------------------------------------------------------
# Tool: code_graph_query
# ---------------------------------------------------------------------------
def code_graph_query_tool(path: str, query: str,
                          symbol: str = "", top_n: int = 10) -> str:
    """Query a cached code knowledge graph."""
    project = Path(path).expanduser().resolve()
    if not project.exists():
        return fmt_err(f"Path not found: {path}")

    db_path = _default_cache_path(str(project))
    graph = ImportGraph.load(db_path, str(project))
    if graph is None:
        return fmt_err(
            f"No cached graph for {project}. Run code_index first."
        )

    if query == "summary":
        return fmt_ok({
            "project": str(project),
            "files": len(graph._graph),
            "edges": sum(len(v) for v in graph._graph.values()),
            "hot_paths": graph.find_hot_paths(top_n),
            "cycles": len(graph.find_cycles()),
        })

    elif query == "hot_paths":
        hot = graph.find_hot_paths(top_n)
        return fmt_ok({"hot_paths": hot})

    elif query == "cycles":
        cycles = graph.find_cycles()
        return fmt_ok({
            "cycles_found": len(cycles),
            "cycles": cycles[:top_n],
        })

    elif query == "health":
        cycles = graph.find_cycles()
        hot = graph.find_hot_paths(5)
        return fmt_ok({
            "total_files": len(graph._graph),
            "total_edges": sum(len(v) for v in graph._graph.values()),
            "cycle_count": len(cycles),
            "has_cycles": len(cycles) > 0,
            "top_imported": hot,
            "health_score": max(0, 10 - len(cycles) * 2) if cycles else 10,
        })

    elif query in ("callers", "callees"):
        if not symbol:
            return fmt_err("'symbol' parameter required for callers/callees query")

        # Resolve symbol to file path
        target = Path(symbol)
        if not target.is_absolute():
            # Try to find the file in the graph
            target_abs = None
            for node in graph._graph:
                if symbol in node:
                    target_abs = node
                    break
            if target_abs is None:
                return fmt_err(f"Symbol '{symbol}' not found in graph")
            target_path = target_abs
        else:
            target_path = str(target.resolve())

        if target_path not in graph._graph:
            return fmt_err(f"File not indexed: {target_path}")

        if query == "callers":
            # Files that import this file
            importers = []
            for node, imports in graph._graph.items():
                if target_path in imports:
                    importers.append(node)
            return fmt_ok({
                "file": target_path,
                "callers": importers[:top_n],
                "caller_count": len(importers),
            })
        else:  # callees
            imports = list(graph._graph.get(target_path, set()))
            return fmt_ok({
                "file": target_path,
                "callees": imports[:top_n],
                "callee_count": len(imports),
            })

    else:
        return fmt_err(f"Unknown query: {query}. Use: callers, callees, hot_paths, cycles, health, summary")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
def _handle_code_index(args: dict, **kw: Any) -> str:
    return code_index_tool(
        path=args.get("path", "."),
        force_rescan=args.get("force_rescan", False),
        depth=args.get("depth", 5),
    )


def _handle_code_graph_query(args: dict, **kw: Any) -> str:
    return code_graph_query_tool(
        path=args.get("path", "."),
        query=args.get("query", "summary"),
        symbol=args.get("symbol", ""),
        top_n=args.get("top_n", 10),
    )
