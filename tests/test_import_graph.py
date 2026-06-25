"""Tests for ImportGraph — AST-basierter Import-Graph."""

import tempfile
from pathlib import Path

from code_intel._import_graph import (
    ImportGraph,
    _short_label,
    _try_resolve_import,
)


def _make_project(files: dict) -> Path:
    """Erstelle ein temporäres Projekt mit Dateien.

    files: {"src/main.py": "content", "src/utils.py": "content", ...}
    """
    tmp = Path(tempfile.mkdtemp())
    for rel_path, content in files.items():
        full = tmp / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    return tmp


# ---------------------------------------------------------------------------
# scan()
# ---------------------------------------------------------------------------


class TestScan:
    def test_scan_basic(self):
        project = _make_project(
            {
                "main.py": "",
                "utils.py": "",
                "README.md": "# Docs",
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        # Nur .py Dateien
        assert len(g.files) == 2

    def test_scan_excludes_node_modules(self):
        project = _make_project(
            {
                "main.py": "",
                "node_modules/foo/index.js": "",
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        paths = [str(f) for f in g.files]
        assert all("node_modules" not in p for p in paths)

    def test_scan_depth_limit(self):
        project = _make_project(
            {
                "a.py": "",
                "sub/b.py": "",
                "sub/deep/c.py": "",
            }
        )
        g = ImportGraph(str(project))
        g.scan(depth=1)
        paths = [f.name for f in g.files]
        assert "a.py" in paths
        assert "b.py" in paths
        assert "c.py" not in paths  # depth=1 → nur 1 subdir

    def test_scan_depth_zero(self):
        project = _make_project(
            {
                "a.py": "",
                "sub/b.py": "",
            }
        )
        g = ImportGraph(str(project))
        g.scan(depth=0)
        assert len(g.files) == 1  # nur root-level

    def test_scan_custom_exclude(self):
        project = _make_project(
            {
                "main.py": "",
                "generated/code.py": "",
            }
        )
        g = ImportGraph(str(project))
        g.scan(exclude=["generated"])
        assert len(g.files) == 1

    def test_scan_empty_project(self):
        project = _make_project({})
        g = ImportGraph(str(project))
        g.scan()
        assert g.files == []


# ---------------------------------------------------------------------------
# parse_imports() — Python
# ---------------------------------------------------------------------------


class TestParseImportsPython:
    def test_import_standard_lib(self):
        project = _make_project({"main.py": "import os\nimport sys\n"})
        g = ImportGraph(str(project))
        g.scan()
        imports = g.parse_imports(str(project / "main.py"))
        assert "os" in imports
        assert "sys" in imports

    def test_from_import(self):
        project = _make_project({"main.py": "from pathlib import Path\n"})
        g = ImportGraph(str(project))
        g.scan()
        imports = g.parse_imports(str(project / "main.py"))
        # from-imports liefern das Modul, nicht das Symbol
        assert "pathlib" in imports

    def test_relative_import(self):
        project = _make_project(
            {
                "pkg/__init__.py": "",
                "pkg/main.py": "from . import utils\n",
                "pkg/utils.py": "",
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        imports = g.parse_imports(str(project / "pkg/main.py"))
        assert any("utils" in i for i in imports)

    def test_no_imports(self):
        project = _make_project({"main.py": "x = 1\n"})
        g = ImportGraph(str(project))
        g.scan()
        imports = g.parse_imports(str(project / "main.py"))
        assert imports == []


# ---------------------------------------------------------------------------
# parse_imports() — TypeScript
# ---------------------------------------------------------------------------


class TestParseImportsTypeScript:
    def test_import_named(self):
        project = _make_project(
            {
                "main.ts": 'import { foo } from "./bar";\n',
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        imports = g.parse_imports(str(project / "main.ts"))
        assert "./bar" in imports

    def test_require(self):
        project = _make_project(
            {
                "main.ts": 'const fs = require("fs");\n',
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        imports = g.parse_imports(str(project / "main.ts"))
        assert "fs" in imports


# ---------------------------------------------------------------------------
# parse_imports() — Go
# ---------------------------------------------------------------------------


class TestParseImportsGo:
    def test_import_std(self):
        project = _make_project(
            {
                "main.go": """package main\nimport "fmt"\n""",
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        imports = g.parse_imports(str(project / "main.go"))
        assert "fmt" in imports

    def test_import_multi(self):
        project = _make_project(
            {
                "main.go": """package main\nimport (\n\t"fmt"\n\t"os"\n)\n""",
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        imports = g.parse_imports(str(project / "main.go"))
        assert "fmt" in imports
        assert "os" in imports


# ---------------------------------------------------------------------------
# parse_all()
# ---------------------------------------------------------------------------


class TestParseAll:
    def test_basic_graph(self):
        project = _make_project(
            {
                "main.py": "from . import utils\n",
                "utils.py": "import os\n",
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        g.parse_all()
        assert len(g.graph) == 2
        # main.py → utils.py (interner Import)
        main_path = str(project / "main.py")
        assert any("utils.py" in c for c in g.graph.get(main_path, set()))

    def test_empty_graph_after_scan(self):
        project = _make_project({})
        g = ImportGraph(str(project))
        g.scan()
        g.parse_all()
        assert g.graph == {}

    def test_cross_language_graph(self):
        project = _make_project(
            {
                "main.py": "import json\n",
                "util.ts": 'import {x} from "./helper";\n',
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        g.parse_all()
        assert len(g.graph) == 2


# ---------------------------------------------------------------------------
# find_cycles()
# ---------------------------------------------------------------------------


class TestFindCycles:
    def test_no_cycles(self):
        project = _make_project(
            {
                "a.py": "from . import b\n",
                "b.py": "import os\n",
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        g.parse_all()
        cycles = g.find_cycles()
        assert cycles == []

    def test_direct_cycle(self):
        project = _make_project(
            {
                "a.py": "from . import b\n",
                "b.py": "from . import a\n",
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        g.parse_all()
        cycles = g.find_cycles()
        assert len(cycles) >= 1
        # Prüfe dass a.py und b.py im Cycle sind
        cycle_files = [Path(f).name for f in cycles[0]]
        assert "a.py" in cycle_files
        assert "b.py" in cycle_files

    def test_triple_cycle(self):
        project = _make_project(
            {
                "a.py": "from . import b\n",
                "b.py": "from . import c\n",
                "c.py": "from . import a\n",
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        g.parse_all()
        cycles = g.find_cycles()
        assert len(cycles) >= 1
        cycle_files = [Path(f).name for f in cycles[0]]
        assert "a.py" in cycle_files
        assert "b.py" in cycle_files
        assert "c.py" in cycle_files

    def test_cycle_and_normal(self):
        """Zyklische + normale Imports sollten getrennt erkannt werden."""
        project = _make_project(
            {
                "a.py": "from . import b\n",
                "b.py": "from . import a\n",
                "c.py": "from . import a\n",
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        g.parse_all()
        cycles = g.find_cycles()
        assert len(cycles) >= 1
        # c.py sollte NICHT im Cycle sein
        cycle_files = [Path(f).name for f in cycles[0]]
        assert "c.py" not in cycle_files

    def test_empty_graph_cycles(self):
        g = ImportGraph("/tmp/nonexistent")
        cycles = g.find_cycles()
        assert cycles == []


# ---------------------------------------------------------------------------
# find_hot_paths()
# ---------------------------------------------------------------------------


class TestFindHotPaths:
    def test_basic_ranking(self):
        project = _make_project(
            {
                "a.py": "from . import b\n",
                "b.py": "import os\n",
                "c.py": "from . import b\n",
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        g.parse_all()
        hot = g.find_hot_paths(top_n=5)
        # b.py sollte die meisten Caller haben (von a.py und c.py)
        assert len(hot) >= 1
        top = hot[0]
        assert "b.py" in top["file"]
        assert top["caller_count"] >= 1

    def test_top_n_limit(self):
        project = _make_project(
            {
                "a.py": "",
                "b.py": "",
                "c.py": "",
                "d.py": "",
                "e.py": "",
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        g.parse_all()
        hot = g.find_hot_paths(top_n=3)
        assert len(hot) <= 3

    def test_no_callers(self):
        project = _make_project({"a.py": ""})
        g = ImportGraph(str(project))
        g.scan()
        g.parse_all()
        hot = g.find_hot_paths(top_n=5)
        assert len(hot) >= 0  # keine Caller, aber trotzdem kein Crash


# ---------------------------------------------------------------------------
# analyze_blast_radius()
# ---------------------------------------------------------------------------


class TestBlastRadius:
    def test_basic_blast_radius(self):
        project = _make_project(
            {
                "a.py": "from . import b\n",
                "b.py": "",
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        g.parse_all()
        result = g.analyze_blast_radius(str(project / "b.py"))
        assert result["total"] >= 1
        assert 1 in result["levels"]

    def test_deep_transitive(self):
        project = _make_project(
            {
                "a.py": "from . import b\n",
                "b.py": "from . import c\n",
                "c.py": "",
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        g.parse_all()
        result = g.analyze_blast_radius(str(project / "c.py"), depth=3)
        assert result["total"] >= 2  # a + b

    def test_no_callers(self):
        project = _make_project({"a.py": ""})
        g = ImportGraph(str(project))
        g.scan()
        g.parse_all()
        result = g.analyze_blast_radius(str(project / "a.py"))
        assert result["total"] == 0

    def test_file_not_in_graph(self):
        g = ImportGraph("/tmp")
        result = g.analyze_blast_radius("/tmp/nonexistent.py")
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# to_mermaid() / to_tree()
# ---------------------------------------------------------------------------


class TestVisualization:
    def test_mermaid_basic(self):
        """Files with no imports or no inter-file edges."""
        project = _make_project(
            {
                "a.py": "",
                "b.py": "",
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        g.parse_all()
        mermaid = g.to_mermaid()
        assert mermaid.startswith("graph")
        # 2 nodes, 0 edges: should either show no-imports placeholder or empty graph
        has_placeholder = "%% No imports found" in mermaid
        is_empty = "graph LR" in mermaid and "%%" not in mermaid and "a.py" not in mermaid
        assert has_placeholder or is_empty

    def test_mermaid_empty(self, tmp_path):
        g = ImportGraph(str(tmp_path))
        g.scan()
        mermaid = g.to_mermaid()
        assert "No imports" in mermaid

    def test_tree_basic(self):
        project = _make_project(
            {
                "a.py": "from . import b\n",
                "b.py": "",
            }
        )
        g = ImportGraph(str(project))
        g.scan()
        g.parse_all()
        tree = g.to_tree()
        assert "a.py" in tree or "b.py" in tree

    def test_tree_empty(self, tmp_path):
        g = ImportGraph(str(tmp_path))
        g.scan()
        tree = g.to_tree()
        assert "(empty)" in tree


# ---------------------------------------------------------------------------
# _try_resolve_import()
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_short_label(self):
        assert _short_label("/tmp/project/main.py") == "main.py"
        assert _short_label("/tmp/project/main.py", module_level=True) == "main"

    def test_try_resolve_exact(self):
        """Exakte Datei sollte als erster Kandidat kommen."""
        candidates = _try_resolve_import(Path("/src"), "./bar.py", Path("/"))
        assert candidates[0] == Path("/src/./bar.py")
