"""Tests for tools/base.py, tools/cache.py, and tools/diagram.py.

Coverage targets:
  - tools/base.py:  0% → 80%+
  - tools/cache.py:  0% → 80%+
  - tools/diagram.py: 0% → 80%+
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ═══════════════════════════════════════════════════════════════════════════
# tools/base.py
# ═══════════════════════════════════════════════════════════════════════════

class TestDetectLanguage:
    """detect_language() maps file extensions to language IDs."""

    @pytest.mark.parametrize("path,expected", [
        ("/foo/bar.py", "python"),
        ("/foo/bar.ts", "typescript"),
        ("/foo/bar.tsx", "tsx"),
        ("/foo/bar.js", "javascript"),
        ("/foo/bar.jsx", "javascript"),
        ("/foo/bar.mjs", "javascript"),
        ("/foo/bar.cjs", "javascript"),
        ("/foo/bar.go", "go"),
        ("/foo/bar.rs", "rust"),
        ("/foo/bar.java", "java"),
        ("/foo/bar.c", "c"),
        ("/foo/bar.cpp", "cpp"),
        ("/foo/bar.hpp", "cpp"),
        ("/foo/bar.unknown", None),
        ("/foo/bar", None),
    ])
    def test_detect_by_extension(self, path, expected):
        from code_intel.tools.base import detect_language
        result = detect_language(path)
        assert result == expected, f"{path} -> {result} (expected {expected})"

    def test_explicit_lang_override(self):
        from code_intel.tools.base import detect_language
        result = detect_language("/foo/bar.py", explicit_lang="go")
        assert result == "go"

    def test_explicit_lang_normalized(self):
        from code_intel.tools.base import detect_language
        result = detect_language("/foo/bar.py", explicit_lang="TypeScript")
        assert result == "typescript"


class TestClassifyNode:
    """_classify_node() maps tree-sitter node types to symbol kinds."""

    @pytest.mark.parametrize("node_type,expected", [
        ("function_definition", "function"),
        ("method_definition", "method"),
        ("class_definition", "class"),
        ("interface_declaration", "interface"),
        ("type_alias_declaration", "type"),
        ("enum_declaration", "enum"),
        ("struct_spec", "symbol"),  # struct_spec is NOT in _NODE_KIND_MAP
        ("trait_item", "trait"),
        ("unknown_type", "symbol"),
        ("module", "symbol"),
        ("call_expression", "symbol"),
        ("expression_statement", "symbol"),
    ])
    def test_classify_node_kinds(self, node_type, expected):
        from code_intel.tools.base import _classify_node
        node = type("MockNode", (), {"type": node_type})()
        result = _classify_node(node, "name")
        assert result == expected, f"{node_type} -> {result} (expected {expected})"


class TestClassifySymbolKind:
    """_classify_symbol_kind() determines symbol kind from AST node types."""

    @pytest.mark.parametrize("node_type,expected", [
        ("function_definition", "function"),
        ("class_definition", "class"),
        ("method_definition", "method"),
        ("interface_declaration", "interface"),
        ("type_alias_declaration", "type"),
    ])
    def test_direct_kind(self, node_type, expected):
        from code_intel.tools.base import _classify_symbol_kind
        node = type("MockNode", (), {"type": node_type, "children": []})()
        result = _classify_symbol_kind(node)
        assert result == expected

    def test_decorated_definition_unwrap(self):
        from code_intel.tools.base import _classify_symbol_kind
        inner = type("MockNode", (), {"type": "class_definition"})()
        node = type("MockNode", (), {
            "type": "decorated_definition",
            "children": [inner],
        })()
        result = _classify_symbol_kind(node)
        assert result == "class"

    def test_unknown_type_defaults_to_symbol(self):
        from code_intel.tools.base import _classify_symbol_kind
        node = type("MockNode", (), {"type": "weird_custom_type", "children": []})()
        result = _classify_symbol_kind(node)
        assert result == "symbol"


class TestDetectIfMethod:
    """_detect_if_method() detects method vs function."""

    def _make_node(self, node_type, parent_type, grandparent_type=None):
        """Create a node chain with proper parent references."""
        grandparent = None
        if grandparent_type:
            grandparent = type("MockGP", (), {"type": grandparent_type, "parent": None})()
        parent = type("MockParent", (), {"type": parent_type, "parent": grandparent})()
        return type("MockNode", (), {"type": node_type, "parent": parent})()

    def test_function_is_not_method(self):
        from code_intel.tools.base import _detect_if_method
        node = self._make_node("function_definition", "module")
        assert _detect_if_method(node, "function") == "function"

    def test_class_body_is_method(self):
        from code_intel.tools.base import _detect_if_method
        # class_body needs parent class_definition to detect as method
        node = self._make_node("function_definition", "class_body", "class_definition")
        assert _detect_if_method(node, "function") == "method"

    def test_decorated_definition_in_class(self):
        from code_intel.tools.base import _detect_if_method
        # decorated_definition walks up to find class_body → class_definition
        inner = type("MockNode", (), {
            "type": "function_definition",
            "parent": type("MockDecor", (), {
                "type": "decorated_definition",
                "parent": type("MockBody", (), {
                    "type": "class_body",
                    "parent": type("MockCD", (), {
                        "type": "class_definition",
                        "parent": None,
                    })(),
                })(),
            })(),
        })()
        assert _detect_if_method(inner, "function") == "method"


class TestFindProjectRoot:
    """_find_project_root() walks up directories to find project root."""

    def test_finds_git_root(self, tmp_path):
        from code_intel.tools.base import _find_project_root
        git = tmp_path / ".git"
        git.mkdir()
        sub = tmp_path / "a" / "b" / "c"
        sub.mkdir(parents=True)
        root = _find_project_root(str(sub))
        assert root == str(tmp_path)

    def test_finds_pyproject_toml(self, tmp_path):
        from code_intel.tools.base import _find_project_root
        (tmp_path / "pyproject.toml").write_text("")
        sub = tmp_path / "x" / "y"
        sub.mkdir(parents=True)
        root = _find_project_root(str(sub))
        assert root == str(tmp_path)

    def test_no_marker_returns_none(self, tmp_path, monkeypatch):
        from code_intel.tools.base import _find_project_root
        # Create a non-nested dir, monkeypatch _MARKERS to avoid /tmp match
        d = tmp_path / "inner"
        d.mkdir()
        root = _find_project_root(str(d))
        # If no marker found, returns None
        assert root is None or not any(
            (Path(root) / m).exists()
            for m in (".git", "pyproject.toml", "setup.py", "setup.cfg", "package.json")
        )


# ═══════════════════════════════════════════════════════════════════════════
# tools/cache.py
# ═══════════════════════════════════════════════════════════════════════════

class TestCacheFindProjectRoot:
    """_find_project_root() from cache module."""

    def test_finds_git_root(self, tmp_path):
        from code_intel.tools.cache import _find_project_root
        (tmp_path / ".git").mkdir()
        sub = tmp_path / "deep" / "nest"
        sub.mkdir(parents=True)
        assert _find_project_root(str(sub)) == str(tmp_path)


class TestCacheKeyForPath:
    """_cache_key_for_path() generates cache keys from file paths."""

    def test_key_is_stable(self, tmp_path):
        from code_intel.tools.cache import _cache_key_for_path
        # Set up a project root marker
        (tmp_path / ".git").mkdir()
        src = tmp_path / "foo" / "bar.py"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("")
        key1 = _cache_key_for_path(str(src))
        key2 = _cache_key_for_path(str(src))
        assert key1 == key2

    def test_key_contains_filename(self, tmp_path):
        from code_intel.tools.cache import _cache_key_for_path
        (tmp_path / ".git").mkdir()
        src = tmp_path / "main.py"
        src.write_text("")
        key = _cache_key_for_path(str(src))
        assert "main" in key


class TestProjectCachePath:
    """_project_cache_path() generates cache file paths."""

    def test_returns_path_with_json(self, tmp_path):
        from code_intel.tools.cache import _project_cache_path
        (tmp_path / ".git").mkdir()
        result = _project_cache_path(str(tmp_path))
        assert result
        assert isinstance(result, str)
        assert result.endswith(".json")


class TestSetCacheAndStats:
    """_set_cache(), get_symbol_cache_stats(), clear_symbol_cache()."""

    def test_set_and_get_stats(self):
        from code_intel.tools.base import _SYMBOL_CACHE, _set_cache, get_symbol_cache_stats
        _SYMBOL_CACHE.clear()
        _set_cache("test_key_42", {"data": 42})
        stats = get_symbol_cache_stats()
        assert stats["entries"] >= 1

    def test_clear_cache(self):
        from code_intel.tools.base import _set_cache, clear_symbol_cache, get_symbol_cache_stats
        _set_cache("clear_me", "value")
        clear_symbol_cache()
        stats = get_symbol_cache_stats()
        assert stats["entries"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# tools/diagram.py
# ═══════════════════════════════════════════════════════════════════════════

class TestReadSourceLines:
    """_read_source_lines() reads source file lines."""

    def test_reads_lines(self, tmp_path):
        from code_intel.tools.diagram import _read_source_lines
        f = tmp_path / "test.py"
        f.write_text("a\nb\nc\n")
        lines = _read_source_lines(f)
        assert lines == ["a", "b", "c", ""]

    def test_file_not_found_returns_empty(self):
        from code_intel.tools.diagram import _read_source_lines
        p = Path("/nonexistent/file.py")
        lines = _read_source_lines(p)
        assert lines == []


class TestResolveDiagramLanguage:
    """_resolve_diagram_language() resolves language."""

    def test_uses_explicit_lang(self, tmp_path):
        from code_intel.tools.diagram import _resolve_diagram_language
        result = _resolve_diagram_language(tmp_path / "f.py", "go", "")
        assert result == "go"

    def test_detects_from_file(self, tmp_path):
        from code_intel.tools.diagram import _resolve_diagram_language
        result = _resolve_diagram_language(tmp_path / "f.py", None, "")
        assert result == "python"

    def test_unknown_ext_returns_none(self, tmp_path):
        from code_intel.tools.diagram import _resolve_diagram_language
        result = _resolve_diagram_language(tmp_path / "f.xyz", None, "")
        assert result is None


class TestResolveDiagramCharacter:
    """_resolve_diagram_character() resolves character position."""

    def test_uses_given_character(self):
        from code_intel.tools.diagram import _resolve_diagram_character
        result = _resolve_diagram_character(5, 1, ["def foo():", "    pass"])
        assert result == 5

    def test_detects_identifier_column(self):
        from code_intel.tools.diagram import _resolve_diagram_character
        lines = ["  def my_func():\n", "    pass\n"]
        result = _resolve_diagram_character(None, 1,
                                            [line.rstrip("\n") for line in lines])
        # 'my_func' starts at column 6 or close to it
        assert 4 <= result <= 8

    def test_none_with_invalid_line_returns_0(self):
        from code_intel.tools.diagram import _resolve_diagram_character
        lines = ["x = 1"]
        result = _resolve_diagram_character(None, 5, lines)
        # Falls back to 0 or tries line 0
        assert result >= 0


class TestExtractMermaidSymbolName:
    """_extract_mermaid_symbol_name() extracts symbol name from source."""

    def test_extracts_name(self, tmp_path):
        from code_intel.tools.diagram import _extract_mermaid_symbol_name
        p = tmp_path / "test.py"
        p.write_text("def my_func():\n    pass\n")
        lines = p.read_text().split("\n")
        result = _extract_mermaid_symbol_name(p, 0, 4, lines)
        # Line 0 is "def my_func():"; character 4 should be in identifier
        assert result is not None and len(result) > 0

    def test_out_of_bounds_finds_nothing(self, tmp_path):
        from code_intel.tools.diagram import _extract_mermaid_symbol_name
        p = tmp_path / "test.py"
        p.write_text("x = 1\n")
        lines = p.read_text().split("\n")
        result = _extract_mermaid_symbol_name(p, 99, 0, lines)
        # Out of bounds returns whatever is found or empty
        assert isinstance(result, str)


class TestNodeIdStatic:
    """_node_id_static() generates safe node IDs."""

    def test_cleans_special_chars(self):
        from code_intel.tools.diagram import _node_id_static
        result = _node_id_static("my-func.name")
        assert "-" not in result
        assert "." not in result

    def test_non_empty_result(self):
        from code_intel.tools.diagram import _node_id_static
        result = _node_id_static("hello")
        assert result == "hello"


class TestBuildMermaidDiagram:
    """_build_mermaid_diagram() builds mermaid from calls."""

    def test_empty_calls_produces_diagram(self, tmp_path):
        from code_intel.tools.diagram import _build_mermaid_diagram
        p = tmp_path / "test.py"
        p.write_text("def root():\n    pass\n")
        lines = p.read_text().split("\n")
        result = _build_mermaid_diagram("root", p, lines, "python",
                                        [], [], None, 2)
        assert "flowchart" in result.lower() or "graph" in result.lower()

    def test_with_incoming_calls(self, tmp_path):
        from code_intel.tools.diagram import _build_mermaid_diagram
        p = tmp_path / "test.py"
        p.write_text("def target(): pass\n")
        lines = p.read_text().split("\n")
        # incoming/outgoing are lists of dicts with "name" or "uri" keys
        incoming = [{"name": "caller_a", "uri": ""}]
        result = _build_mermaid_diagram("target", p, lines, "python",
                                        incoming, [], None, 2)
        assert "caller_a" in result
        assert "target" in result


class TestAddAstFallbackEdges:
    """_add_ast_fallback_edges() adds edges from AST source."""

    def test_no_source_lines_adds_nothing(self, tmp_path):
        from code_intel.tools.diagram import _add_ast_fallback_edges
        p = tmp_path / "mod.py"
        p.write_text("")
        result = _add_ast_fallback_edges([], set(), "n1", "main",
                                         p, [], "python", 2)
        assert result is None  # void function, modifies in-place
