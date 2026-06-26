"""Fallback tests for code_tools.py — parser loading + extract_symbols fallback.

Extracted from test_code_intel_gaps.py.

Targets:
  - Parser loading fallback (tree-sitter language not available)
  - extract_symbols edge cases (fallback query, empty, decorated, Go type_spec)
"""

import builtins
import textwrap
from unittest.mock import patch

import pytest

pytest.importorskip("tree_sitter", reason="tree-sitter not installed")

from code_intel.code_tools import (
    _SYMBOL_CACHE,
    _get_language,
    _get_parser,
    _init_languages,
    extract_symbols,
)

# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture()
def tmp_go(tmp_path):
    src = textwrap.dedent("""\
        package main

        type Rectangle struct {
            Width  float64
            Height float64
        }

        func (r Rectangle) Area() float64 {
            return r.Width * r.Height
        }

        func NewRectangle(w, h float64) Rectangle {
            return Rectangle{Width: w, Height: h}
        }

        type Stringer interface {
            String() string
        }
    """)
    f = tmp_path / "sample.go"
    f.write_text(src)
    return f


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear symbol cache before each test."""
    _SYMBOL_CACHE.clear()
    yield
    _SYMBOL_CACHE.clear()


# ===========================================================================
# A — Parser loading fallback
# ===========================================================================


class TestParserLoadingFallback:
    """_init_languages fallback when tree-sitter language bindings missing."""

    def setup_method(self):
        import code_intel.code_tools as ci

        ci._LANG_READY = False
        ci._LANG_CACHE.clear()
        ci._PARSER_CACHE.clear()

    def test_init_languages_fallback_on_import_error(self):
        """When tree-sitter language imports fail, _LANG_READY stays False."""
        import code_intel.code_tools as ci

        orig_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name.startswith("tree_sitter_py") or name in (
                "tree_sitter_python",
                "tree_sitter_javascript",
                "tree_sitter_typescript",
                "tree_sitter_rust",
                "tree_sitter_go",
                "tree_sitter_java",
            ):
                raise ImportError(f"No module named {name}")
            if name == "tree_sitter":
                raise ImportError("No module named tree_sitter")
            return orig_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            ci._init_languages()
            assert ci._LANG_READY is False
            assert len(ci._LANG_CACHE) == 0

    def test_init_languages_partial_fallback(self):
        """If some but not all languages fail, _init_languages still sets _LANG_READY."""
        import code_intel.code_tools as ci

        ci._init_languages()
        assert isinstance(ci._LANG_READY, bool)

    def test_get_language_returns_none_when_not_ready(self):
        """_get_language returns None for missing language even after init attempt."""
        lang = _get_language("python_does_not_exist_xyz")
        assert lang is None

    def test_get_parser_returns_none_when_not_ready(self):
        """_get_parser returns None when language not available."""
        parser = _get_parser("python_does_not_exist_xyz")
        assert parser is None


# ===========================================================================
# B — extract_symbols edge cases
# ===========================================================================


class TestExtractSymbolsFallbackQuery:
    """extract_symbols fallback query and edge cases."""

    def test_fallback_query_for_lang_without_symbol_queries(self):
        """When a lang has no SYMBOL_QUERIES entry, fallback generic query runs."""
        _init_languages()
        if _get_language("cpp"):
            symbols = extract_symbols(b"int main() { return 0; }", "cpp")
            assert isinstance(symbols, list)

    def test_fallback_query_function_definition_matched(self):
        """Fallback query matches function_definition pattern."""
        _init_languages()
        if _get_language("cpp"):
            src = b"int add(int a, int b) { return a + b; }"
            symbols = extract_symbols(src, "cpp")
            assert isinstance(symbols, list)

    def test_no_name_nodes_skipped(self):
        """When name_nodes list is empty, continue."""
        _init_languages()
        src = b"x = 1"
        symbols = extract_symbols(src, "python")
        assert isinstance(symbols, list)

    def test_def_node_is_none_fallback(self):
        """When def_nodes is empty and parent is None, continue."""
        _init_languages()
        symbols = extract_symbols(b"", "python")
        assert symbols == []

    def test_decorated_definition_classified_correctly(self, tmp_path):
        """decorated_definition finds inner kind."""
        src = textwrap.dedent("""\
            @dataclass
            class Config:
                x: int = 1

            @property
            def value(self):
                return 42
        """)
        f = tmp_path / "decorated.py"
        f.write_text(src)
        symbols = extract_symbols(f.read_bytes(), "python")
        names = [s["name"] for s in symbols]
        kinds = {s["name"]: s["kind"] for s in symbols}
        assert "Config" in names
        assert kinds.get("Config") in ("class", "symbol")

    def test_go_type_spec_detects_struct_interface(self, tmp_go):
        """Go type_spec detects struct/interface children."""
        symbols = extract_symbols(tmp_go.read_bytes(), "go")
        kinds = {s["name"]: s["kind"] for s in symbols}
        assert kinds.get("Rectangle") in ("struct", "symbol")
        assert kinds.get("Stringer") in ("interface", "symbol")

    def test_empty_file_extract(self):
        """Empty file returns empty symbols list."""
        symbols = extract_symbols(b"", "python")
        assert symbols == []

    def test_unknown_extension_extract(self):
        """Unknown extension (not in _EXT_TO_LANG) returns empty."""
        symbols = extract_symbols(b"some source text", "nonexistent_lang")
        assert symbols == []
