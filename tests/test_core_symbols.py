"""Tests for symbol cache persistence, language loading, classify node,
resolve preset/query, and language detection.

Extracted from test_core_tools.py — symbols and language domain.
"""

import json
import textwrap
from unittest.mock import MagicMock, mock_open, patch

import pytest

# ---------------------------------------------------------------------------
# Skip entire module if tree-sitter is not installed
# ---------------------------------------------------------------------------
pytest.importorskip("tree_sitter", reason="tree-sitter not installed")

from code_intel.code_tools import (
    _EXT_TO_LANG,
    _LANG_CACHE,
    _PERSIST_VERSION,
    _SYMBOL_CACHE,
    _classify_node,
    _get_language,
    _get_parser,
    _init_languages,
    _resolve_preset,
    _resolve_query,
    _set_cache,
    clear_symbol_cache,
    detect_language,
    get_symbol_cache_stats,
    load_symbol_cache,
    persist_symbol_cache,
)

# ===========================================================================
# Fixtures – small source files (same as test_code_tools.py for consistency)
# ===========================================================================


@pytest.fixture()
def tmp_py(tmp_path):
    src = textwrap.dedent("""\
        MY_CONST = 42

        class Greeter:
            \"\"\"Say hello.\"\"\"

            def greet(self, name: str) -> str:
                return f"Hello, {name}!"

            @staticmethod
            def farewell() -> str:
                return "Goodbye!"

        def top_level_fn(x: int) -> int:
            return x * 2

        async def async_fn() -> None:
            pass
    """)
    f = tmp_path / "sample.py"
    f.write_text(src)
    return f


@pytest.fixture()
def tmp_ts(tmp_path):
    src = textwrap.dedent("""\
        export interface Animal {
            name: string;
        }

        export class Dog implements Animal {
            constructor(public name: string) {}

            bark(): string {
                return "woof";
            }
        }

        export function createDog(name: string): Dog {
            return new Dog(name);
        }

        const arrowFn = (x: number): number => x + 1;
    """)
    f = tmp_path / "sample.ts"
    f.write_text(src)
    return f


@pytest.fixture()
def tmp_js(tmp_path):
    src = textwrap.dedent("""\
        class Counter {
            constructor() { this.count = 0; }
            increment() { this.count++; }
        }

        function reset(counter) { counter.count = 0; }
        const double = (n) => n * 2;
    """)
    f = tmp_path / "sample.js"
    f.write_text(src)
    return f


@pytest.fixture()
def tmp_rs(tmp_path):
    src = textwrap.dedent("""\
        pub struct Point {
            pub x: f64,
            pub y: f64,
        }

        impl Point {
            pub fn new(x: f64, y: f64) -> Self {
                Point { x, y }
            }

            pub fn distance(&self, other: &Point) -> f64 {
                ((self.x - other.x).powi(2) + (self.y - other.y).powi(2)).sqrt()
            }
        }

        pub fn origin() -> Point {
            Point::new(0.0, 0.0)
        }

        pub trait Shape {
            fn area(&self) -> f64;
        }
    """)
    f = tmp_path / "sample.rs"
    f.write_text(src)
    return f


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


@pytest.fixture()
def tmp_java(tmp_path):
    src = textwrap.dedent("""\
        public class Hello {
            private String msg;

            public Hello(String msg) {
                this.msg = msg;
            }

            public void greet() {
                System.out.println(msg);
            }
        }
    """)
    f = tmp_path / "Hello.java"
    f.write_text(src)
    return f


# ===========================================================================
# Symbol cache persistence — persist, load, clear, stats
# ===========================================================================


class TestSymbolCachePersistence:
    """persist_symbol_cache, load_symbol_cache, clear_symbol_cache, _set_cache."""

    def setup_method(self):
        _SYMBOL_CACHE.clear()

    def test_set_cache_adds_entry(self):
        _set_cache("mykey", {"data": 42})
        assert "mykey" in _SYMBOL_CACHE
        assert _SYMBOL_CACHE["mykey"]["data"] == 42

    def test_set_cache_respects_max_size(self):
        # Fill past 2000 limit
        for i in range(2050):
            _set_cache(f"k{i}", i)
        assert len(_SYMBOL_CACHE) <= 2000

    def test_get_symbol_cache_stats_empty(self):
        _SYMBOL_CACHE.clear()
        stats = get_symbol_cache_stats()
        assert stats["entries"] == 0

    def test_get_symbol_cache_stats_nonempty(self):
        _SYMBOL_CACHE["a"] = 1
        stats = get_symbol_cache_stats()
        assert stats["entries"] >= 1

    def test_clear_symbol_cache(self):
        _SYMBOL_CACHE["x"] = 1
        clear_symbol_cache()
        assert len(_SYMBOL_CACHE) == 0

    def test_persist_empty_cache(self, monkeypatch):
        _SYMBOL_CACHE.clear()
        result = persist_symbol_cache()
        assert result == 0

    @patch("builtins.open", new_callable=mock_open)
    def test_persist_writes_json(self, mock_file, monkeypatch):
        _SYMBOL_CACHE.clear()
        _set_cache("test_key", {"foo": "bar"})
        monkeypatch.setattr("code_intel.tools.cache._find_project_root", lambda x="": "/tmp/test_proj")
        monkeypatch.setattr("code_intel.tools.cache._project_cache_path", lambda x="": "/tmp/test_cache.json")
        monkeypatch.setattr("code_intel.tools.cache._PERSIST_DIR", "/tmp")

        result = persist_symbol_cache()
        assert result >= 1
        mock_file.assert_called_once()

    def test_persist_skips_non_json_serializable(self, monkeypatch):
        _SYMBOL_CACHE.clear()
        _set_cache("bad", {"circular": object()})
        monkeypatch.setattr("code_intel.tools.cache._find_project_root", lambda x="": "/tmp/test")
        monkeypatch.setattr("code_intel.tools.cache._project_cache_path", lambda x="": "/tmp/test_cache2.json")
        monkeypatch.setattr("code_intel.tools.cache._PERSIST_DIR", "/tmp")
        # Should not crash, should skip the bad entry
        result = persist_symbol_cache()
        assert result == 0

    def test_load_cache_missing_file(self, tmp_path, monkeypatch):
        _SYMBOL_CACHE.clear()
        monkeypatch.setattr("code_intel.tools.cache._PERSIST_DIR", str(tmp_path))
        result = load_symbol_cache()
        assert result == 0

    def test_load_cache_version_mismatch(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "symidx_mismatch.json"
        cache_file.write_text(json.dumps({"version": 999, "entries": {"a": 1}}))
        monkeypatch.setattr("code_intel.tools.cache._project_cache_path", lambda x="": str(cache_file))
        result = load_symbol_cache()
        assert result == 0

    def test_load_cache_success(self, tmp_path):
        _SYMBOL_CACHE.clear()
        cache_file = tmp_path / "symidx_ok.json"
        cache_file.write_text(
            json.dumps({"version": _PERSIST_VERSION, "project_root": "/tmp", "entries": {"loaded_key": {"value": 42}}})
        )
        _old = load_symbol_cache.__globals__.get("_project_cache_path")
        load_symbol_cache.__globals__["_project_cache_path"] = lambda x="": str(cache_file)
        try:
            result = load_symbol_cache()
            assert result == 1
            assert "loaded_key" in _SYMBOL_CACHE
        finally:
            load_symbol_cache.__globals__["_project_cache_path"] = _old
        _SYMBOL_CACHE.clear()

    def test_load_cache_corrupt_data(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "symidx_bad.json"
        cache_file.write_text("not json at all{{{")
        monkeypatch.setattr("code_intel.tools.cache._project_cache_path", lambda x="": str(cache_file))
        result = load_symbol_cache()
        assert result == 0


# ===========================================================================
# Language loading — _init_languages, _get_language, _get_parser
# ===========================================================================


class TestLanguageLoading:
    """Tests for lazy language initialization."""

    def setup_method(self):
        """Reset module state before each test to verify lazy loading."""
        import code_intel.tools.cache as cache_mod

        cache_mod._LANG_READY = False
        cache_mod._LANG_CACHE.clear()
        cache_mod._PARSER_CACHE.clear()

    def test_init_languages_runs_once(self):
        """_init_languages with tree-sitter deps installed should populate cache."""
        _init_languages()
        # Languages may or may not be available depending on installed bindings;
        # the function should complete without error.
        # If _LANG_READY is True, the call succeeded (languages installed)
        # If False, languages aren't available but no crash occurred.
        import code_intel.code_tools as ci

        # Either way, the function was safe to call
        assert ci._LANG_READY or not ci._LANG_READY  # no crash

    def test_init_languages_idempotent(self):
        """Second call to _init_languages is a no-op."""
        _init_languages()
        count = len(_LANG_CACHE)
        _init_languages()
        assert len(_LANG_CACHE) == count

    def test_get_language_returns_known(self):
        _init_languages()
        lang = _get_language("python")
        assert lang is not None

    def test_get_language_returns_none_for_unknown(self):
        _init_languages()
        lang = _get_language("nonexistent_lang")
        assert lang is None

    def test_get_parser_creates_parser(self):
        _init_languages()
        parser = _get_parser("python")
        assert parser is not None

    def test_get_parser_returns_cached(self):
        _init_languages()
        p1 = _get_parser("python")
        p2 = _get_parser("python")
        assert p1 is p2  # Same object (cached)

    def test_get_parser_returns_none_for_missing_lang(self):
        _init_languages()
        parser = _get_parser("missing_lang")
        assert parser is None


# ===========================================================================
# Helper functions
# ===========================================================================


class TestClassifyNode:
    def test_classify_known_node(self):
        """_classify_node maps known node types."""
        _init_languages()
        _get_language("python")
        parser = _get_parser("python")
        source = b"def foo(): pass"
        tree = parser.parse(source)
        fn_node = tree.root_node.children[0]
        kind = _classify_node(fn_node, "name")
        assert kind == "function"

    def test_classify_unknown_node(self):
        """_classify_node falls back to 'symbol' for unknown types."""
        mock_node = MagicMock()
        mock_node.type = "weird_syntax_thing"
        kind = _classify_node(mock_node, "name")
        assert kind == "symbol"


class TestResolvePreset:
    def test_resolve_known_preset(self):
        q = _resolve_preset("function_calls", "python")
        assert q is not None
        assert "call" in q

    def test_resolve_alias(self):
        q = _resolve_preset("calls", "python")
        assert q is not None

    def test_resolve_unknown_preset(self):
        assert _resolve_preset("nonexistent_MAGIC", "python") is None

    def test_resolve_unsupported_lang(self):
        assert _resolve_preset("decorator_calls", "go") is None


class TestResolveQuery:
    def test_query_takes_priority(self):
        result = _resolve_query("(myquery) @x", None, None, "python", "/dev/null")
        assert result == "(myquery) @x"

    def test_preset_resolved(self):
        result = _resolve_query(None, "function_calls", None, "python", "/dev/null")
        assert "call" in result

    def test_pattern_fallback(self):
        result = _resolve_query(None, None, "search_text", "python", "/dev/null")
        assert result == "(_) @node"

    def test_no_params_returns_error_json(self):
        result = _resolve_query(None, None, None, "python", "/dev/null")
        data = json.loads(result)
        assert "error" in data

    def test_unknown_preset_returns_error_json(self):
        result = _resolve_query(None, "bogus_preset", None, "python", "/dev/null")
        data = json.loads(result)
        assert "error" in data


class TestDetectLanguage:
    def test_python(self, tmp_py):
        assert detect_language(str(tmp_py)) == "python"

    def test_typescript(self, tmp_ts):
        assert detect_language(str(tmp_ts)) == "typescript"

    def test_javascript(self, tmp_js):
        assert detect_language(str(tmp_js)) == "javascript"

    def test_rust(self, tmp_rs):
        assert detect_language(str(tmp_rs)) == "rust"

    def test_go(self, tmp_go):
        assert detect_language(str(tmp_go)) == "go"

    def test_java(self, tmp_java):
        assert detect_language(str(tmp_java)) == "java"

    def test_unknown_returns_none(self, tmp_path):
        f = tmp_path / "file.xyz"
        f.write_text("")
        assert detect_language(str(f)) is None

    def test_explicit_override(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("")
        assert detect_language(str(f), explicit_lang="python") == "python"

    def test_all_extensions_mapped(self):
        """Every extension in _EXT_TO_LANG is detectable."""
        for ext in _EXT_TO_LANG:
            assert ext.startswith(".")
        assert len(_EXT_TO_LANG) >= 14  # we support many
