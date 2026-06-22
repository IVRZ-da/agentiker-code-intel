"""tools/graph_analysis.py — Graph analysis tools (hot paths, cycles, dependency graph).

Extracted from code_tools.py (L2809-3051) into its own module.
Uses ImportGraph directly — no lazy imports needed.
"""

from __future__ import annotations

from pathlib import Path

from .._fmt import fmt_err, fmt_json
from .._import_graph import ImportGraph
from .._logging import setup_logger as _setup_code_intel_logger

logger = _setup_code_intel_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# code_hot_paths
# ═══════════════════════════════════════════════════════════════════════

def code_hot_paths_tool(
    path: str,
    top_n: int = 10,
    depth: int = 5,
) -> str:
    """Find the most-imported files (hot paths) in a project.

    Uses ImportGraph to scan the project and rank files by
    transitive caller count.

    Args:
        path: Project root directory to scan.
        top_n: Number of top results (default: 10).
        depth: Scan depth for subdirectories (default: 5).

    Returns:
        JSON with ranked hot paths.

    """

    root = Path(path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return fmt_err(f"Directory not found: {path}")

    g = ImportGraph(str(root))
    g.scan(depth=depth)
    if not g.files:
        return fmt_err("No source files found")

    g.parse_all()
    hot = g.find_hot_paths(top_n=top_n)

    result = {
        "project": str(root),
        "total_files": len(g.files),
        "total_edges": sum(len(v) for v in g.graph.values()),
        "top_n": top_n,
        "hot_paths": hot,
    }
    return fmt_json(result)


CODE_HOT_PATHS_SCHEMA = {
    "name": "code_hot_paths",
    "description": "Find the most-imported files (hot paths) in a project. "
                   "Uses ImportGraph to rank files by transitive caller count.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project root directory"},
            "top_n": {"type": "integer", "description": "Number of top results (default: 10)"},
            "depth": {"type": "integer", "description": "Scan depth (default: 5)"},
        },
        "required": ["path"],
    },
}


def _handle_code_hot_paths(args, **kw):
    return code_hot_paths_tool(
        path=args.get("path", ""),
        top_n=args.get("top_n", 10),
        depth=args.get("depth", 5),
    )


# ═══════════════════════════════════════════════════════════════════════
# code_cycle_detector
# ═══════════════════════════════════════════════════════════════════════

def code_cycle_detector_tool(
    path: str,
    max_cycles: int = 20,
    depth: int = 5,
) -> str:
    """Find circular import chains in a project using ImportGraph.

    Uses Tarjan's strongly-connected-components algorithm on the
    project's import graph to detect cycles. A cycle of length >1
    means file A imports B and B imports A (directly or transitively).

    Args:
        path: Project root directory to scan.
        max_cycles: Max cycles to report (default: 20, 0 = unlimited).
        depth: Scan depth for subdirectories (default: 5).

    Returns:
        JSON with list of cycles, each showing the files in the cycle.

    """

    root = Path(path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return fmt_err(f"Directory not found: {path}")

    g = ImportGraph(str(root))
    g.scan(depth=depth)
    if not g.files:
        return fmt_err("No source files found")

    g.parse_all()
    cycles = g.find_cycles()

    # Filter: cycles of length > 1 (trivial self-imports are not interesting)
    real_cycles = [c for c in cycles if len(c) > 1]

    if max_cycles and max_cycles > 0:
        real_cycles = real_cycles[:max_cycles]

    # Build detailed output: for each cycle, trace the import edges
    detailed = []
    for cycle in real_cycles:
        edges = []
        n = len(cycle)
        for i in range(n):
            a = cycle[i]
            b = cycle[(i + 1) % n]
            callees = g.graph.get(a, set())
            if b in callees:
                edges.append(f"{a} \u2192 {b}")
        detailed.append({
            "cycle": cycle,
            "length": n,
            "edges": edges,
        })

    result = {
        "project": str(root),
        "total_files": len(g.files),
        "total_edges": sum(len(v) for v in g.graph.values()),
        "cycles_found": len(real_cycles),
        "cycles": detailed,
    }
    return fmt_json(result)


CODE_CYCLE_DETECTOR_SCHEMA = {
    "name": "code_cycle_detector",
    "description": "Find circular import chains in a project. "
                   "Uses ImportGraph with Tarjan's SCC algorithm.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project root directory"},
            "max_cycles": {"type": "integer", "description": "Max cycles to report (default: 20, 0 = unlimited)"},
            "depth": {"type": "integer", "description": "Scan depth (default: 5)"},
        },
        "required": ["path"],
    },
}


def _handle_code_cycle_detector(args, **kw):
    return code_cycle_detector_tool(
        path=args.get("path", ""),
        max_cycles=args.get("max_cycles", 20),
        depth=args.get("depth", 5),
    )


# ═══════════════════════════════════════════════════════════════════════
# code_dependency_graph
# ═══════════════════════════════════════════════════════════════════════

def code_dependency_graph_tool(
    path: str,
    format: str = "mermaid",
    direction: str = "LR",
    module_level: bool = False,
    depth: int = 5,
) -> str:
    """Generate a visual dependency graph for a project using ImportGraph.

    Supports Mermaid flowchart format and ASCII tree view.

    Args:
        path: Project root directory to scan.
        format: Output format — "mermaid" (default) or "tree".
        direction: Mermaid graph direction — "LR" (left-right, default) or "TD" (top-down).
        module_level: When True, show module-level paths instead of full file paths.
        depth: Scan depth for subdirectories (default: 5).

    Returns:
        Mermaid code block or ASCII tree string.

    """

    root = Path(path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return fmt_err(f"Directory not found: {path}")

    g = ImportGraph(str(root))
    g.scan(depth=depth)
    if not g.files:
        return fmt_err("No source files found")

    g.parse_all()

    fmt = format.lower()
    if fmt == "mermaid":
        return g.to_mermaid(direction=direction, module_level=module_level)
    elif fmt == "tree":
        return g.to_tree()
    else:
        return fmt_err(f"Unknown format: {format}. Use 'mermaid' or 'tree'.")


CODE_DEPENDENCY_GRAPH_SCHEMA = {
    "name": "code_dependency_graph",
    "description": "Generate a visual dependency graph for a project. "
                   "Supports Mermaid flowchart and ASCII tree view.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project root directory"},
            "format": {"type": "string", "description": "Output format: 'mermaid' (default) or 'tree'", "enum": ["mermaid", "tree"]},
            "direction": {"type": "string", "description": "Mermaid graph direction: 'LR' (left-right) or 'TD' (top-down)", "enum": ["LR", "TD"]},
            "module_level": {"type": "boolean", "description": "Show module-level paths instead of full file paths (default: false)"},
            "depth": {"type": "integer", "description": "Scan depth (default: 5)"},
        },
        "required": ["path"],
    },
}


def _handle_code_dependency_graph(args, **kw):
    return code_dependency_graph_tool(
        path=args.get("path", ""),
        format=args.get("format", "mermaid"),
        direction=args.get("direction", "LR"),
        module_level=args.get("module_level", False),
        depth=args.get("depth", 5),
    )


__all__ = [
    "code_hot_paths_tool",
    "code_cycle_detector_tool",
    "code_dependency_graph_tool",
    "CODE_HOT_PATHS_SCHEMA",
    "CODE_CYCLE_DETECTOR_SCHEMA",
    "CODE_DEPENDENCY_GRAPH_SCHEMA",
    "_handle_code_hot_paths",
    "_handle_code_cycle_detector",
    "_handle_code_dependency_graph",
]
