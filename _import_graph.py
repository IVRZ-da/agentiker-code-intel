"""
AST-basierter Import-Graph für Python/TypeScript/Go/Rust.

Wiederverwendet von code_cycle_detector, code_dependency_graph,
code_unused_finder, code_hot_paths, code_blast_radius und code_pr_impact.

Nutzt die existierenden tree-sitter Parser aus code_intel:
  - detect_language() — extension → lang_key
  - _get_parser() / _get_language() — tree-sitter Setup
  - _SYMBOL_QUERIES (teilweise) — AST-Patterns
"""

from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, List, Optional, Set

from ._logging import setup_logger as _setup_logger

logger = _setup_logger(__name__)

# ---------------------------------------------------------------------------
# Language → Import-Query-Map
# Jede Query matcht Import-Statements und extrahiert das Ziel-Modul.
# ---------------------------------------------------------------------------

_IMPORT_QUERIES: Dict[str, str] = {
    "python": """
        ; import x
        (import_statement
            name: (dotted_name) @import_name
        )

        ; from x import y — capture module_name (e.g. "os" in "from os import path")
        (import_from_statement
            module_name: (dotted_name) @from_module
        )

        ; from . import x — capture the imported name x (e.g. "utils")
        (import_from_statement
            name: (dotted_name) @from_symbol
        )
    """,
    "typescript": """
        ; import ... from 'x'
        (import_statement
            source: (string) @import_name
        )

        ; import 'x' (side-effect import)
        (import_statement
            source: (string) @import_name
        )

        ; require('x')
        (call_expression
            function: (identifier) @require_func
            (#eq? @require_func "require")
            arguments: (arguments
                (string) @import_name
            )
        )
    """,
    "tsx": """
        (import_statement
            source: (string) @import_name
        )
        (call_expression
            function: (identifier) @require_func
            (#eq? @require_func "require")
            arguments: (arguments
                (string) @import_name
            )
        )
    """,
    "go": """
        ; import "x" (single and multi-line)
        (import_spec
            path: (interpreted_string_literal) @import_name
        )
    """,
    "rust": """
        ; use x::y;
        (use_declaration
            argument: (use_path) @import_name
        )

        ; extern crate x;
        (extern_crate_declaration
            (identifier) @import_name
        )
    """,
    "java": """
        ; import x.y.Z;
        (import_declaration
            name: (scoped_identifier) @import_name
        )
    """,
    "javascript": """
        (import_statement
            source: (string) @import_name
        )
        (call_expression
            function: (identifier) @require_func
            (#eq? @require_func "require")
            arguments: (arguments
                (string) @import_name
            )
        )
    """,
}

_SUPPORTED_LANGUAGES = set(_IMPORT_QUERIES.keys())

# Standard-Verzeichnisse die immer übersprungen werden
_DEFAULT_EXCLUDE = {
    "node_modules", ".venv", "venv", "__pycache__", ".git",
    ".next", "dist", "build", "target", "out",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".hypothesis",
}


class ImportGraph:
    """AST-basierter Import-Graph für mehrsprachige Projekte.

    Baut einen gerichteten Graphen aus Datei-Import-Beziehungen.
    Alle Pfade sind absolut (str), nicht Path — für einfache Serialisierung.
    """

    def __init__(self, project_root: str):
        self.project_root = Path(project_root).resolve()
        self._files: List[Path] = []
        self._graph: Dict[str, Set[str]] = {}
        self._reverse_graph: Dict[str, Set[str]] = {}
        self._exclude_dirs = _DEFAULT_EXCLUDE.copy()

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def scan(self, depth: int = 5, exclude: Optional[List[str]] = None) -> None:
        """Scanne das Projekt nach Quelldateien (rekursiv bis depth).

        depth = 0: nur root-Verzeichnis (keine Subdirs)
        depth >= 1: Subdirs bis zur angegebenen Tiefe (default 5)
        """
        exclude_set = set(exclude or [])
        root = self.project_root

        for ext, lang_key in _EXT_TO_LANG.items():
            if lang_key not in _SUPPORTED_LANGUAGES:
                continue
            pattern = f"**/*{ext}"
            # depth=0: nur root/**/*.ext (Subdirs eingeschlossen)
            # Wir nutzen Path.rglob() mit manuellem Tiefen-Filter
            for f in sorted(root.glob(pattern)):
                try:
                    rel = f.relative_to(root)
                except ValueError:
                    continue
                parts = rel.parts
                # Exclude-Prüfung
                if any(p in self._exclude_dirs or p in exclude_set for p in parts):
                    continue
                # Tiefen-Filter: depth=0 erlaubt nur root-level
                # depth=N erlaubt bis N Ebenen
                if depth >= 0 and len(parts) > depth + 1:
                    continue
                self._files.append(f)

        logger.debug(
            "ImportGraph.scan: %d files in %s (depth=%d, exclude=%d dirs)",
            len(self._files), root, depth, len(exclude_set) + len(self._exclude_dirs),
        )

    def add_exclude_dir(self, *dirs: str) -> None:
        """Zusätzliche Exclude-Verzeichnisse hinzufügen."""
        for d in dirs:
            self._exclude_dirs.add(d)

    @property
    def files(self) -> List[Path]:
        return list(self._files)

    @property
    def graph(self) -> Dict[str, Set[str]]:
        return dict(self._graph)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def parse_imports(self, file_path: str) -> List[str]:
        """Parse Imports aus einer Datei via tree-sitter AST.

        Returns: Liste der importierten Modul-Pfade (originals, unverändert).
        """
        from .code_intel import _get_language, _get_parser, detect_language

        lang_key = detect_language(file_path)
        if not lang_key or lang_key not in _SUPPORTED_LANGUAGES:
            return []

        parser = _get_parser(lang_key)
        if parser is None:
            return []

        lang_obj = _get_language(lang_key)
        if lang_obj is None:
            return []

        # Query kompilieren
        query_source = _IMPORT_QUERIES.get(lang_key)
        if not query_source:
            return []

        try:
            from tree_sitter import Query, QueryCursor

            query = Query(lang_obj, query_source)
        except Exception as exc:
            logger.debug("ImportGraph: Query compile error for %s: %s", lang_key, exc)
            return []

        # Datei parsen
        try:
            with open(file_path, "rb") as f:
                source_bytes = f.read()
        except (OSError, IOError) as exc:
            logger.debug("ImportGraph: Cannot read %s: %s", file_path, exc)
            return []

        tree = parser.parse(source_bytes)
        if tree is None:
            return []

        # Captures durchgehen — nutzt QueryCursor (tree-sitter API v0.23+)
        imports = []
        from_modules = []  # @from_module captures
        from_symbols = []  # @from_symbol captures
        seen = set()
        qc = QueryCursor(query)
        for _pattern_idx, captures_dict in qc.matches(tree.root_node):
            # 1. Standard-Import: import x, import x.y
            for node in captures_dict.get("import_name", []):
                raw = _extract_node_text(source_bytes, node)
                if raw and raw not in seen:
                    seen.add(raw)
                    imports.append(raw)

            # 2. from-Import: from x import y → module_name (x)
            for node in captures_dict.get("from_module", []):
                raw = _extract_node_text(source_bytes, node)
                if raw and raw not in seen:
                    seen.add(raw)
                    from_modules.append(raw)

            # 3. from . import y → symbol name (y)
            #    NOT added to seen — processed separately in post-processing
            for node in captures_dict.get("from_symbol", []):
                raw = _extract_node_text(source_bytes, node)
                if raw:
                    from_symbols.append(raw)

        # From-Import Verarbeitung:
        # - from_module startet mit "." → relative import (z.B. ".utils" → "./utils")
        # - from_module ist einfach "." → symbol from from_symbol wird genutzt
        # - from_module ohne "." → extern (nichts tun)
        has_relative_from = False
        for mod in from_modules:
            if mod.startswith("."):
                has_relative_from = True
                if mod != ".":
                    # ".utils" → "./utils",  "..utils" → "../utils"
                    prefix = "."
                    if mod.startswith(".."):
                        prefix = ".."
                        mod = mod[2:]
                    elif mod.startswith("."):
                        prefix = "."
                        mod = mod[1:]
                    imports.append(f"{prefix}{mod}")
            else:
                # from os import path → "os" is external, not added
                # from pathlib import Path → "pathlib" captured, will be resolved later
                imports.append(mod)

        # from . import x → füge "./x" hinzu (nur wenn relative from_module existiert)
        if has_relative_from or not from_modules:
            for sym in from_symbols:
                if sym not in seen:
                    seen.add(sym)
                    imports.append(f"./{sym}")

        return imports

    def parse_all(self) -> None:
        """Parse ALLE gescannten Dateien → baue den gerichteten Graphen."""
        if not self._files:
            logger.warning("ImportGraph.parse_all: No files scanned. Call scan() first.")
            return

        self._graph = {}
        self._reverse_graph = defaultdict(set)

        for f in self._files:
            abs_path = str(f.resolve())
            imports = self.parse_imports(abs_path)
            # Nur Projekt-interne Imports behalten (relative Pfade)
            project_imports = set()
            for imp in imports:
                resolved = self._resolve_import(abs_path, imp)
                if resolved:
                    project_imports.add(resolved)
            self._graph[abs_path] = project_imports
            for target in project_imports:
                self._reverse_graph[target].add(abs_path)

        logger.debug(
            "ImportGraph.parse_all: %d nodes, %d edges",
            len(self._graph),
            sum(len(v) for v in self._graph.values()),
        )

    def _resolve_import(self, source_file: str, import_path: str) -> Optional[str]:
        """Löse einen Import-Pfad zu einer existierenden Datei auf.

        Reihenfolge:
        1. Relative Imports (./, ../) → direkte Auflösung
        2. Python: Kurznamen (utils, mymodule) → als relativen Import probieren
        3. Alles andere → extern (None)
        """
        root = self.project_root
        source_dir = Path(source_file).resolve().parent

        # 1. Relative Imports (./, ../)
        if import_path.startswith((".", "../", "./")):
            for resolved in _try_resolve_import(source_dir, import_path, root):
                if resolved.exists():
                    return str(resolved)
            return None

        # 2. Python: Kurznamen ohne Punkte
        #    Versuche als relativen Import (e.g. "utils" → "./utils.py")
        #    Nur für einfache Namen, nicht für "os", "sys" oder "package.module"
        if "." not in import_path and import_path.isidentifier():
            for resolved in _try_resolve_import(source_dir, f"./{import_path}", root):
                if resolved.exists():
                    return str(resolved)

        # 3. Extern (PyPI, npm, stdlib) — nicht auflösbar
        return None

    # ------------------------------------------------------------------
    # Analyse: Zyklen
    # ------------------------------------------------------------------

    def find_cycles(self) -> List[List[str]]:
        """Tarjan SCC Algorithmus → finde Zyklen > 1 (starke Zyklen).

        Returns: Liste von Zyklen, jeder Cycle ist [file1, file2, ...]
        """
        if not self._graph:
            return []

        index = 0
        stack: List[str] = []
        indices: Dict[str, int] = {}
        lowlinks: Dict[str, int] = {}
        on_stack: Set[str] = set()
        cycles: List[List[str]] = []

        def strongconnect(node: str) -> None:
            nonlocal index
            indices[node] = index
            lowlinks[node] = index
            index += 1
            stack.append(node)
            on_stack.add(node)

            for neighbor in self._graph.get(node, set()):
                if neighbor not in indices:
                    strongconnect(neighbor)
                    lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
                elif neighbor in on_stack:
                    lowlinks[node] = min(lowlinks[node], indices[neighbor])

            if lowlinks[node] == indices[node]:
                scc: List[str] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    scc.append(w)
                    if w == node:
                        break
                if len(scc) > 1:
                    cycles.append(scc)

        for node in list(self._graph.keys()):
            if node not in indices:
                strongconnect(node)

        return cycles

    # ------------------------------------------------------------------
    # Analyse: Hot Paths
    # ------------------------------------------------------------------

    def find_hot_paths(self, top_n: int = 10) -> List[Dict]:
        """Ranke Dateien nach Anzahl transitiver Caller (Importeure).

        Returns: [{"file": "...", "caller_count": N, "callers": [...]}, ...]
        """
        if not self._reverse_graph:
            self.parse_all()

        scores = []
        for symbol in self._reverse_graph:
            visited: Set[str] = set()
            queue: deque = deque([symbol])
            while queue:
                node = queue.popleft()
                if node in visited:
                    continue
                visited.add(node)
                for caller in self._reverse_graph.get(node, set()):
                    if caller not in visited:
                        queue.append(caller)

            caller_count = len(visited) - 1  # subtract self
            scores.append({
                "file": symbol,
                "caller_count": caller_count,
                "callers": list(visited - {symbol}),
            })

        scores.sort(key=lambda x: x["caller_count"], reverse=True)
        return scores[:top_n]

    # ------------------------------------------------------------------
    # Analyse: Blast Radius
    # ------------------------------------------------------------------

    def analyze_blast_radius(
        self, file_path: str, depth: int = 3
    ) -> Dict:
        """Finde transitive Importeure (Caller) einer Datei.

        Args:
            file_path: Absolute Pfad zur Datei
            depth: Maximale Tiefe (default 3)

        Returns:
            {"levels": {1: [...], 2: [...], ...},
             "total": N,
             "max_depth_reached": True/False}
        """
        if not self._reverse_graph:
            self.parse_all()

        abs_path = str(Path(file_path).resolve())
        if abs_path not in self._reverse_graph:
            return {"levels": {}, "total": 0, "max_depth_reached": False}

        levels: Dict[int, List[str]] = {}
        visited: Set[str] = {abs_path}
        current: Set[str] = set(self._reverse_graph.get(abs_path, set())) - visited
        total = 0

        for d in range(1, depth + 1):
            if not current:
                break
            levels[d] = sorted(current)
            total += len(current)
            visited.update(current)

            # Nächste Ebene: Caller der aktuellen Caller
            next_level: Set[str] = set()
            for c in current:
                callers = self._reverse_graph.get(c, set())
                next_level.update(callers - visited)
            current = next_level

        return {
            "levels": levels,
            "total": total,
            "max_depth_reached": bool(current),
        }

    # ------------------------------------------------------------------
    # Visualisierung
    # ------------------------------------------------------------------

    def to_mermaid(self, direction: str = "LR", module_level: bool = False) -> str:
        """Generiere Mermaid-Code-Block."""
        if not self._graph:
            return f"graph {direction}\n    %% No imports found\n"

        lines = [f"graph {direction}"]
        edges = set()

        for caller, callees in self._graph.items():
            caller_label = _short_label(caller, module_level)
            for callee in callees:
                if callee not in self._graph:
                    continue  # nur interne Nodes
                callee_label = _short_label(callee, module_level)
                edge = (caller_label, callee_label)
                if edge not in edges:
                    edges.add(edge)
                    lines.append(f"    {caller_label} --> {callee_label}")

        return "\n".join(lines)

    def to_tree(self, root: Optional[str] = None) -> str:
        """Generiere Text-Tree der Import-Struktur.

        Args:
            root: Start-Datei (absoluter Pfad). None = alle Dateien.
        """
        if not self._graph:
            return "(empty)"

        if root:
            root_abs = str(Path(root).resolve())
            if root_abs not in self._graph:
                return f"(not found: {root})"
            return _build_tree(root_abs, self._graph, visited=set(), prefix="")

        # Alle Roots (Dateien ohne Importeure) zeigen
        all_files = set(self._graph.keys())
        imported: Set[str] = set()
        for callees in self._graph.values():
            imported.update(callees)
        roots = sorted(all_files - imported)

        if not roots:
            # Zyklisch — irgendeinen nehmen
            roots = [sorted(all_files)[0]]

        parts = []
        for r in roots:
            tree = _build_tree(r, self._graph, visited=set(), prefix="")
            parts.append(f"{r}\n{tree}")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

# Kopie der Extension→Language-Map aus code_intel
_EXT_TO_LANG = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".mts": "typescript",
    ".cts": "typescript",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
}


def _extract_node_text(source_bytes: bytes, node) -> Optional[str]:
    """Extrahiere bereinigten Text aus einem tree-sitter Node."""
    try:
        raw = source_bytes[node.start_byte:node.end_byte].decode("utf-8")
    except (UnicodeDecodeError, IndexError):
        return None
    cleaned = raw.strip().strip("\"'`").strip()
    return cleaned or None


def _short_label(abs_path: str, module_level: bool = False) -> str:
    """Kurzes Label für einen Dateipfad (z.B. für Mermaid)."""
    p = Path(abs_path)
    if module_level:
        # Nur der Dateiname ohne Extension
        return p.stem
    # Relativer Pfad vom Projekt (oder nur filename)
    return p.name


def _try_resolve_import(
    source_dir: Path, import_path: str, root: Path
) -> List[Path]:
    """Versuche verschiedene Extension-Varianten für einen Import.

    Reihenfolge: exakte Datei → +.py → +.ts → +.tsx → +.js → +.jsx → +.go → +.rs
    → index/index.ts → index/index.js
    """
    candidates: List[Path] = []

    # 1. Exakter Pfad (schon mit Extension)
    candidates.append(source_dir / import_path)

    # 2. Mit Extension
    for ext in [".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java"]:
        candidates.append(source_dir / f"{import_path}{ext}")

    # 3. index-Dateien (TypeScript/JavaScript Konvention)
    import_dir = source_dir / import_path
    if import_dir.exists() and import_dir.is_dir():
        for idx in ["index.ts", "index.tsx", "index.js", "index.jsx", "__init__.py"]:
            candidates.append(import_dir / idx)

    return candidates


def _build_tree(
    node: str, graph: Dict[str, Set[str]],
    visited: Set[str], prefix: str,
) -> str:
    """Rekursiver Tree-Builder (für to_tree)."""
    if node in visited:
        return f"{prefix}└── {Path(node).name} (*cycle*)\n"
    visited.add(node)

    children = sorted(graph.get(node, set()), key=lambda x: Path(x).name)
    if not children:
        return ""

    lines = []
    for i, child in enumerate(children):
        is_last = i == len(children) - 1
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{Path(child).name}\n")
        next_prefix = prefix + ("    " if is_last else "│   ")
        subtree = _build_tree(child, graph, visited.copy(), next_prefix)
        if subtree:
            lines.append(subtree)

    return "".join(lines)
