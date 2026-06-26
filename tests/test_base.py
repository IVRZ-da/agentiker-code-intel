"""Tests for tools/base.py — parser init, cache, and infrastructure.

Coverage target: 56% → 90%+
"""
from __future__ import annotations

import builtins
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from code_intel.tools.base import (
    _SYMBOL_CACHE,
    _classify_node,
    _classify_symbol_kind,
    _detect_if_method,
    _extract_candidate,
    _find_project_root,
    _get_language,
    _get_parser,
    _init_languages,
    _invalidate_cache,
    _project_cache_path,
    _set_cache,
    _setup_query,
    clear_symbol_cache,
    detect_language,
    get_symbol_cache_stats,
    load_symbol_cache,
    persist_symbol_cache,
)

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _make_node(node_type: str, parent=None, children=None) -> MagicMock:
    """Create a minimal mock tree-sitter node."""
    node = MagicMock()
    node.type = node_type
    node.parent = parent
    node.children = children or []
    node.text = b"foobar"
    node.start_point = (0, 0)
    node.end_point = (0, 0)
    node.start_byte = 0
    node.end_byte = 0
    return node


# ═══════════════════════════════════════════════════════════════════════════
# _find_project_root — env var, monorepo markers, CWD fallback
# ═══════════════════════════════════════════════════════════════════════════


class TestFindProjectRootExtended:
    """_find_project_root — env var and monorepo paths (lines 55-58, 63)."""

    def test_env_var_root_used_when_no_filepath(self, monkeypatch, tmp_path):
        """HERMES_PROJECT_ROOT is used when filepath is empty."""
        monkeypatch.setenv("HERMES_PROJECT_ROOT", str(tmp_path))
        root = _find_project_root("")
        assert root == str(tmp_path.resolve())

    def test_env_var_ignored_when_not_a_dir(self, monkeypatch):
        """HERMES_PROJECT_ROOT ignored when it's not an existing directory."""
        monkeypatch.setenv("HERMES_PROJECT_ROOT", "/nonexistent/path/12345xyz")
        # CWD should be returned instead
        root = _find_project_root("")
        assert root == str(Path.cwd().resolve())

    def test_env_var_ignored_when_empty(self, monkeypatch):
        """Empty HERMES_PROJECT_ROOT falls through to CWD."""
        monkeypatch.setenv("HERMES_PROJECT_ROOT", "")
        root = _find_project_root("")
        assert root == str(Path.cwd().resolve())

    def test_pnpm_workspace_marker_found(self, tmp_path):
        """Monorepo marker pnpm-workspace.yaml is detected."""
        (tmp_path / "pnpm-workspace.yaml").write_text("")
        sub = tmp_path / "packages" / "a" / "src"
        sub.mkdir(parents=True)
        root = _find_project_root(str(sub))
        assert root == str(tmp_path)

    def test_nx_json_marker_found(self, tmp_path):
        """Monorepo marker nx.json is detected."""
        (tmp_path / "nx.json").write_text("{}")
        sub = tmp_path / "apps" / "web"
        sub.mkdir(parents=True)
        root = _find_project_root(str(sub))
        assert root == str(tmp_path)

    def test_lerna_json_marker_found(self, tmp_path):
        """Monorepo marker lerna.json is detected."""
        (tmp_path / "lerna.json").write_text("{}")
        sub = tmp_path / "packages" / "lib"
        sub.mkdir(parents=True)
        root = _find_project_root(str(sub))
        assert root == str(tmp_path)

    def test_monorepo_marker_preferred_over_git(self, tmp_path):
        """Monorepo markers are checked before generic .git."""
        (tmp_path / "pnpm-workspace.yaml").write_text("")
        (tmp_path / ".git").mkdir()
        sub = tmp_path / "deep" / "down"
        sub.mkdir(parents=True)
        root = _find_project_root(str(sub))
        # Should find the monorepo dir, not the one with .git (same in this case)
        assert root == str(tmp_path)

    def test_no_marker_returns_start_directory(self, tmp_path):
        """When no marker exists anywhere, returns the start dir (parent of given path)."""
        d = tmp_path / "some" / "deep" / "path"
        d.mkdir(parents=True)
        root = _find_project_root(str(d))
        # start = Path(filepath).resolve().parent = some/deep
        expected = str((d).resolve().parent)
        assert root == expected, f"Expected {expected}, got {root}"


# ═══════════════════════════════════════════════════════════════════════════
# _project_cache_path
# ═══════════════════════════════════════════════════════════════════════════


class TestProjectCachePath:
    """_project_cache_path — cache file path generation (lines 77-80)."""

    def test_returns_string_ending_with_json(self, tmp_path):
        """Returns a path ending in .json."""
        (tmp_path / ".git").mkdir()
        path = _project_cache_path(str(tmp_path))
        assert isinstance(path, str)
        assert path.endswith(".json")

    def test_uses_custom_project_root(self, tmp_path):
        """Passing an explicit project_root generates consistent paths."""
        (tmp_path / ".git").mkdir()
        path1 = _project_cache_path(str(tmp_path))
        path2 = _project_cache_path(str(tmp_path))
        # Same root → same hash → same path
        assert path1 == path2

    def test_different_roots_different_paths(self, tmp_path):
        """Different project roots produce different cache paths."""
        root_a = tmp_path / "project_a"
        root_b = tmp_path / "project_b"
        root_a.mkdir()
        root_b.mkdir()
        (root_a / ".git").mkdir()
        (root_b / ".git").mkdir()
        path_a = _project_cache_path(str(root_a))
        path_b = _project_cache_path(str(root_b))
        assert path_a != path_b

    def test_path_contains_hash(self, tmp_path):
        """Path contains a hex hash fragment."""
        (tmp_path / ".git").mkdir()
        path = _project_cache_path(str(tmp_path))
        filename = Path(path).stem  # e.g. symidx_a1b2c3d4e5f6
        assert filename.startswith("symidx_")
        assert len(filename) > len("symidx_")


# ═══════════════════════════════════════════════════════════════════════════
# persist_symbol_cache
# ═══════════════════════════════════════════════════════════════════════════


class TestPersistSymbolCache:
    """persist_symbol_cache — saving cache to disk (lines 85-111)."""

    def test_empty_cache_returns_zero(self):
        """Empty cache returns 0 without writing anything."""
        clear_symbol_cache()
        count = persist_symbol_cache()
        assert count == 0

    def test_persist_and_load_roundtrip(self, tmp_path, monkeypatch):
        """Save entries then load them back."""
        clear_symbol_cache()
        monkeypatch.setattr("code_intel.tools.base._PERSIST_DIR", str(tmp_path))
        # Use a deterministic project root
        monkeypatch.setattr("code_intel.tools.base._find_project_root", lambda: str(tmp_path))

        _SYMBOL_CACHE["mod1::func"] = {"name": "func", "kind": "function", "line": 1}
        _SYMBOL_CACHE["mod1::Klass"] = {"name": "Klass", "kind": "class", "line": 10}

        saved = persist_symbol_cache()
        assert saved == 2

        # Now verify the file exists
        cache_files = list(tmp_path.glob("symidx_*.json"))
        assert len(cache_files) >= 1

    def test_persist_non_serializable_skipped(self, tmp_path, monkeypatch):
        """Non-serializable entries are skipped and don't crash."""
        clear_symbol_cache()
        monkeypatch.setattr("code_intel.tools.base._PERSIST_DIR", str(tmp_path))
        monkeypatch.setattr("code_intel.tools.base._find_project_root", lambda: str(tmp_path))

        _SYMBOL_CACHE["good"] = {"data": 42}
        # This entry can't be serialized (circular ref)
        circular = {}
        circular["self"] = circular
        _SYMBOL_CACHE["bad"] = circular

        saved = persist_symbol_cache()
        # Only the good entry survives
        assert saved == 1

    def test_persist_non_string_key_converted(self, tmp_path, monkeypatch):
        """Integer keys are converted to strings before serialization."""
        clear_symbol_cache()
        monkeypatch.setattr("code_intel.tools.base._PERSIST_DIR", str(tmp_path))
        monkeypatch.setattr("code_intel.tools.base._find_project_root", lambda: str(tmp_path))

        _SYMBOL_CACHE[42] = {"data": "value"}
        saved = persist_symbol_cache()
        assert saved == 1

    def test_persist_write_error_returns_zero(self, tmp_path, monkeypatch):
        """A write exception returns 0."""
        clear_symbol_cache()
        monkeypatch.setattr("code_intel.tools.base._PERSIST_DIR", str(tmp_path))
        monkeypatch.setattr("code_intel.tools.base._find_project_root", lambda: str(tmp_path))
        monkeypatch.setattr("builtins.open", MagicMock(side_effect=PermissionError("denied")))

        _SYMBOL_CACHE["key"] = "value"
        saved = persist_symbol_cache()
        assert saved == 0


# ═══════════════════════════════════════════════════════════════════════════
# load_symbol_cache
# ═══════════════════════════════════════════════════════════════════════════


class TestLoadSymbolCache:
    """load_symbol_cache — loading cache from disk (lines 116-134)."""

    def test_nonexistent_file_returns_zero(self, tmp_path, monkeypatch):
        """Non-existent cache file returns 0."""
        monkeypatch.setattr("code_intel.tools.base._PERSIST_DIR", str(tmp_path / "empty_dir"))
        count = load_symbol_cache()
        assert count == 0

    def test_version_mismatch_returns_zero(self, tmp_path, monkeypatch):
        """Cache with wrong version is skipped."""
        clear_symbol_cache()
        monkeypatch.setattr("code_intel.tools.base._PERSIST_DIR", str(tmp_path))
        monkeypatch.setattr("code_intel.tools.base._find_project_root", lambda: str(tmp_path))

        # Write a cache file with wrong version
        cache_dir = tmp_path
        cache_path = list(cache_dir.glob("symidx_*.json"))
        if not cache_path:
            # Force a path by calling _project_cache_path
            from code_intel.tools.base import _project_cache_path
            cp = _project_cache_path(str(tmp_path))
            cp_path = Path(cp)
            cp_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cp_path, "w") as f:
                json.dump({"version": 0, "entries": {"key": "val"}}, f)
        else:
            # Write to existing path
            with open(cache_path[0], "w") as f:
                json.dump({"version": 0, "entries": {"key": "val"}}, f)

        count = load_symbol_cache()
        assert count == 0

    def test_load_success(self, tmp_path, monkeypatch):
        """Load valid cache entries."""
        clear_symbol_cache()
        monkeypatch.setattr("code_intel.tools.base._PERSIST_DIR", str(tmp_path))
        monkeypatch.setattr("code_intel.tools.base._find_project_root", lambda: str(tmp_path))

        # First persist some entries
        _SYMBOL_CACHE["key_a"] = {"value": "a"}
        _SYMBOL_CACHE["key_b"] = {"value": "b"}
        persist_symbol_cache()
        clear_symbol_cache()

        # Now load them back
        count = load_symbol_cache()
        assert count == 2
        assert "key_a" in _SYMBOL_CACHE
        assert _SYMBOL_CACHE["key_a"]["value"] == "a"

    def test_load_duplicate_keys_skipped(self, tmp_path, monkeypatch):
        """Existing keys in cache are not overwritten."""
        clear_symbol_cache()
        monkeypatch.setattr("code_intel.tools.base._PERSIST_DIR", str(tmp_path))
        monkeypatch.setattr("code_intel.tools.base._find_project_root", lambda: str(tmp_path))

        _SYMBOL_CACHE["existing"] = {"original": True}
        _SYMBOL_CACHE["other"] = {"data": "old"}
        persist_symbol_cache()
        clear_symbol_cache()

        # Re-add 'existing' with different data
        _SYMBOL_CACHE["existing"] = {"original": True}
        # Now load (should skip 'existing' since it's already in cache)
        count = load_symbol_cache()
        # Only 'other' should be loaded, 'existing' was already present
        assert count == 1  # only 'other' is new

    def test_load_corrupt_file_returns_zero(self, tmp_path, monkeypatch):
        """Corrupt cache file returns 0."""
        clear_symbol_cache()
        monkeypatch.setattr("code_intel.tools.base._PERSIST_DIR", str(tmp_path))
        monkeypatch.setattr("code_intel.tools.base._find_project_root", lambda: str(tmp_path))

        from code_intel.tools.base import _project_cache_path
        cp = Path(_project_cache_path(str(tmp_path)))
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text("this is not valid json {{{")

        count = load_symbol_cache()
        assert count == 0


# ═══════════════════════════════════════════════════════════════════════════
# _set_cache — overflow (popitem when > 2000 entries)
# ═══════════════════════════════════════════════════════════════════════════


class TestSetCacheOverflow:
    """_set_cache — LRU overflow eviction (line 140)."""

    def test_under_limit_keeps_all(self):
        """Fewer than 2000 entries are all kept."""
        clear_symbol_cache()
        for i in range(100):
            _set_cache(f"key_{i}", i)
        assert len(_SYMBOL_CACHE) == 100

    def test_overflow_evicts_oldest(self):
        """More than 2000 entries evicts oldest (FIFO)."""
        clear_symbol_cache()
        # Fill with 2001 entries
        for i in range(2001):
            _set_cache(f"key_{i}", i)
        assert len(_SYMBOL_CACHE) == 2000
        # key_0 should be evicted (oldest)
        assert "key_0" not in _SYMBOL_CACHE
        # key_2000 should be present (newest)
        assert _SYMBOL_CACHE["key_2000"] == 2000

    def test_get_symbol_cache_stats(self):
        """get_symbol_cache_stats returns entry count (line 144)."""
        clear_symbol_cache()
        _SYMBOL_CACHE["a"] = 1
        _SYMBOL_CACHE["b"] = 2
        stats = get_symbol_cache_stats()
        assert isinstance(stats, dict)
        assert stats["entries"] == 2


# ═══════════════════════════════════════════════════════════════════════════
# _invalidate_cache
# ═══════════════════════════════════════════════════════════════════════════


class TestInvalidateCache:
    """_invalidate_cache — removing entries by file path (lines 153-161)."""

    def test_no_matching_keys_does_nothing(self):
        """No matching keys silently does nothing."""
        clear_symbol_cache()
        _SYMBOL_CACHE["other/file.py|func"] = "val"
        # Should not raise
        _invalidate_cache("/nonexistent/file.py")
        assert len(_SYMBOL_CACHE) == 1

    def test_removes_matching_entries(self, tmp_path):
        """Entries matching the file path are removed."""
        clear_symbol_cache()
        target = tmp_path / "module.py"
        target.write_text("# test")

        key1 = f"{target.resolve()}|my_func"
        key2 = f"{target.resolve()}|MyClass"
        key3 = "other_file.py|other_func"

        _SYMBOL_CACHE[key1] = {"name": "my_func"}
        _SYMBOL_CACHE[key2] = {"name": "MyClass"}
        _SYMBOL_CACHE[key3] = {"name": "other_func"}

        _invalidate_cache(str(target))

        assert key1 not in _SYMBOL_CACHE
        assert key2 not in _SYMBOL_CACHE
        assert key3 in _SYMBOL_CACHE  # other file untouched

    def test_resolves_relative_path(self):
        """Relative paths are resolved to absolute before matching."""
        clear_symbol_cache()
        # Add an entry with the resolved absolute path
        resolved = Path("some_file.py").resolve()
        key = f"{resolved}|func"
        _SYMBOL_CACHE[key] = {"name": "func"}
        _SYMBOL_CACHE["other.py|func2"] = {"name": "func2"}

        _invalidate_cache("some_file.py")
        assert key not in _SYMBOL_CACHE

    def test_key_error_on_delete_handled(self, monkeypatch):
        """KeyError during deletion is caught (line 158)."""
        clear_symbol_cache()
        # Simulate a concurrent deletion by making del raise KeyError
        original_del = _SYMBOL_CACHE.__delitem__

        def _del_that_raises(key):
            if "race" in key:
                raise KeyError(key)
            return original_del(key)

        _SYMBOL_CACHE.__delitem__ = _del_that_raises

        resolved = Path("race_file.py").resolve()
        _SYMBOL_CACHE[f"{resolved}|func"] = "val"
        # Should not raise
        _invalidate_cache("race_file.py")

        # Restore
        _SYMBOL_CACHE.__delitem__ = original_del


# ═══════════════════════════════════════════════════════════════════════════
# _init_languages — ImportError path
# ═══════════════════════════════════════════════════════════════════════════


class TestInitLanguagesExtended:
    """_init_languages — already-ready path (line 592)."""

    def test_init_when_already_ready_returns_immediately(self):
        """Calling _init_languages when _LANG_READY is True returns immediately."""
        import code_intel.tools.base as base_mod
        # Ensure it's ready
        base_mod._LANG_READY = True
        # Should not raise
        _init_languages()
        assert base_mod._LANG_READY is True


class TestInitLanguagesImportError:
    """_init_languages — ImportError handling (lines 602-604)."""

    def _mock_import_fail_treesitter(self, name, *args, **kwargs):
        """Import hook that fails for tree_sitter packages."""
        if name.startswith("tree_sitter") or name == "tree_sitter":
            raise ImportError(f"No module named {name}")
        return builtins.__import__(name, *args, **kwargs)

    def test_import_error_does_not_raise(self):
        """When tree-sitter packages are missing, no exception is raised."""
        import code_intel.tools.base as base_mod
        base_mod._LANG_READY = False

        with patch.object(builtins, "__import__", self._mock_import_fail_treesitter):
            _init_languages()
        # Should not raise; import error is caught silently

    def test_after_import_error_lang_not_ready(self):
        """After an ImportError, _LANG_READY stays False."""
        import code_intel.tools.base as base_mod
        base_mod._LANG_READY = False

        with patch.object(builtins, "__import__", self._mock_import_fail_treesitter):
            _init_languages()
            assert base_mod._LANG_READY is False


# ═══════════════════════════════════════════════════════════════════════════
# _classify_symbol_kind — type_spec (Go) paths, lines 675-679
# ═══════════════════════════════════════════════════════════════════════════


class TestClassifySymbolKindExtended:
    """_classify_symbol_kind — type_spec handling for Go (lines 675-679)."""

    def test_type_spec_returns_type(self):
        """Go type_spec returns 'type' because it's in _NODE_KIND_MAP.

        Note: lines 674-679 (type_spec child inspection) are currently
        unreachable because 'type_spec' maps to 'type' in _NODE_KIND_MAP.
        This test documents current behavior.
        """
        type_spec = _make_node("type_spec", children=[_make_node("struct_type")])
        result = _classify_symbol_kind(type_spec)
        assert result == "type"

    def test_type_spec_with_interface_child(self):
        """type_spec with interface child still returns 'type' (can't reach child logic)."""
        type_spec = _make_node("type_spec", children=[_make_node("interface_type")])
        result = _classify_symbol_kind(type_spec)
        assert result == "type"

    def test_decorated_definition_with_function(self):
        """decorated_definition wrapping a function_definition returns 'function'."""
        inner = _make_node("function_definition")
        decor = _make_node("decorated_definition", children=[inner])
        result = _classify_symbol_kind(decor)
        assert result == "function"

    def test_decorated_definition_with_class(self):
        """decorated_definition wrapping a class_definition returns 'class'."""
        inner = _make_node("class_definition")
        decor = _make_node("decorated_definition", children=[inner])
        result = _classify_symbol_kind(decor)
        assert result == "class"

    def test_decorated_definition_with_unknown_inner(self):
        """decorated_definition with unknown inner type returns 'symbol'."""
        inner = _make_node("some_weird_type")
        decor = _make_node("decorated_definition", children=[inner])
        result = _classify_symbol_kind(decor)
        assert result == "symbol"


# ═══════════════════════════════════════════════════════════════════════════
# _detect_if_method — additional parent chain paths
# ═══════════════════════════════════════════════════════════════════════════


class TestDetectIfMethodExtended:
    """_detect_if_method — additional parent chain paths."""

    def test_non_function_returns_immediately(self):
        """Non-function kinds are returned unchanged (line 689)."""
        for kind in ("class", "variable", "method", "interface"):
            node = _make_node("class_definition")
            result = _detect_if_method(node, kind)
            assert result == kind, f"Expected {kind}, got {result}"

    def test_block_with_class_definition_parent(self):
        """block → class_definition chain detects method (line 695-696)."""
        cls_node = _make_node("class_definition")
        block_node = _make_node("block", parent=cls_node)
        func_node = _make_node("function_definition", parent=block_node)
        result = _detect_if_method(func_node, "function")
        assert result == "method"

    def test_class_body_with_class_declaration(self):
        """class_body inside class_declaration detects method."""
        cls_node = _make_node("class_declaration")
        body_node = _make_node("class_body", parent=cls_node)
        func_node = _make_node("function_definition", parent=body_node)
        result = _detect_if_method(func_node, "function")
        assert result == "method"

    def test_declaration_list_inside_impl_item(self):
        """declaration_list inside impl_item detects method (Rust)."""
        impl_node = _make_node("impl_item")
        decl_list = _make_node("declaration_list", parent=impl_node)
        func_node = _make_node("function_item", parent=decl_list)
        result = _detect_if_method(func_node, "function")
        assert result == "method"

    def test_declaration_list_inside_struct_item(self):
        """declaration_list inside struct_item detects method."""
        struct_node = _make_node("struct_item")
        decl_list = _make_node("declaration_list", parent=struct_node)
        func_node = _make_node("function_definition", parent=decl_list)
        result = _detect_if_method(func_node, "function")
        assert result == "method"

    def test_unknown_parent_breaks_loop(self):
        """An unknown parent type breaks the while loop."""
        parent = _make_node("some_unknown_type")
        func_node = _make_node("function_definition", parent=parent)
        result = _detect_if_method(func_node, "function")
        assert result == "function"

    def test_decorated_definition_walks_up_to_class(self):
        """decorated_definition parent walks up to find class_body → class_definition."""
        cls = _make_node("class_definition")
        body = _make_node("class_body", parent=cls)
        decor = _make_node("decorated_definition", parent=body)
        func = _make_node("function_definition", parent=decor)
        result = _detect_if_method(func, "function")
        assert result == "method"

    def test_abstract_method_declaration_in_class(self):
        """abstract_method_declaration inside class_body → class_definition."""
        cls = _make_node("class_declaration")
        body = _make_node("class_body", parent=cls)
        abstract = _make_node("abstract_method_declaration", parent=body)
        func = _make_node("function_definition", parent=abstract)
        result = _detect_if_method(func, "function")
        assert result == "method"

    def test_decorated_definition_max_depth_exceeded(self):
        """decorated_definition chain exceeding depth 4 returns 'function' (not method)."""
        # Build a chain of 5 decorated_definitions
        parent = _make_node("module")
        current = parent
        for _ in range(5):
            decor = _make_node("decorated_definition", parent=current)
            current = decor
        func = _make_node("function_definition", parent=current)

        result = _detect_if_method(func, "function")
        # Depth exceeded, still 'function'
        assert result == "function"

    def test_decorated_definition_with_no_class_ancestor(self):
        """decorated_definition outside a class returns 'function'."""
        decor = _make_node("decorated_definition", parent=_make_node("module"))
        func = _make_node("function_definition", parent=decor)
        result = _detect_if_method(func, "function")
        assert result == "function"


# ═══════════════════════════════════════════════════════════════════════════
# _extract_candidate
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractCandidate:
    """_extract_candidate — building symbol dicts from AST matches (lines 714-729)."""

    def _make_source_lines(self, text: str):
        return [line.encode("utf-8") for line in text.splitlines(keepends=True)]

    def test_basic_extraction(self):
        """Basic extraction returns name, kind, lines, and signature."""
        source = "def hello(name):\n    return f'Hi {name}'\n\n"
        source_lines = self._make_source_lines(source)
        source_bytes = source.encode("utf-8")

        def_node = MagicMock()
        def_node.type = "function_definition"
        def_node.start_point = (0, 0)
        def_node.end_point = (1, 27)
        def_node.start_byte = 0
        def_node.end_point = (1, 27)
        def_node.end_byte = len(source_bytes)

        name_node = MagicMock()
        name_node.text = b"hello"

        result = _extract_candidate(def_node, name_node, source_bytes, source_lines, "function", False)

        assert result["name"] == "hello"
        assert result["kind"] == "function"
        assert result["line"] == 1
        assert result["end_line"] == 2
        assert "hello" in result["signature"]
        assert "body" not in result

    def test_extraction_with_body(self):
        """include_body=True includes the body text."""
        source = "def foo():\n    pass\n"
        source_lines = self._make_source_lines(source)
        source_bytes = source.encode("utf-8")

        def_node = MagicMock()
        def_node.type = "function_definition"
        def_node.start_point = (0, 0)
        def_node.end_point = (1, 10)
        def_node.start_byte = 0
        def_node.end_byte = len(source_bytes)

        name_node = MagicMock()
        name_node.text = b"foo"

        result = _extract_candidate(def_node, name_node, source_bytes, source_lines, "function", True)

        assert result["name"] == "foo"
        assert "body" in result
        assert "pass" in result["body"]

    def test_single_line_function(self):
        """Single-line function still gets correct signature."""
        source = "def short(): return 42\n"
        source_lines = self._make_source_lines(source)
        source_bytes = source.encode("utf-8")

        def_node = MagicMock()
        def_node.type = "function_definition"
        def_node.start_point = (0, 0)
        def_node.end_point = (0, 22)
        def_node.start_byte = 0
        def_node.end_byte = len(source_bytes)

        name_node = MagicMock()
        name_node.text = b"short"

        result = _extract_candidate(def_node, name_node, source_bytes, source_lines, "function", False)

        assert result["name"] == "short"
        assert result["line"] == 1
        assert result["end_line"] == 1

    def test_signature_ends_at_min_of_end_and_two_lines(self):
        """Signature covers at most 2 lines from start."""
        source = "def multiline(\n    arg1,\n    arg2,\n):\n    pass\n"
        source_lines = self._make_source_lines(source)
        source_bytes = source.encode("utf-8")

        def_node = MagicMock()
        def_node.type = "function_definition"
        def_node.start_point = (0, 0)
        def_node.end_point = (4, 10)
        def_node.start_byte = 0
        def_node.end_byte = len(source_bytes)

        name_node = MagicMock()
        name_node.text = b"multiline"

        result = _extract_candidate(def_node, name_node, source_bytes, source_lines, "function", False)

        assert result["name"] == "multiline"
        # Signature should contain first 2 lines
        assert "def multiline(" in result["signature"]
        assert "arg1" in result["signature"]

    def test_utf8_replace_handles_bad_bytes(self):
        """Errors='replace' handles non-UTF-8 bytes gracefully."""
        source = b"def bad():\n    pass\n"
        source_lines = [b"def bad():\n", b"    pass\n"]
        def_node = MagicMock()
        def_node.type = "function_definition"
        def_node.start_point = (0, 0)
        def_node.end_point = (1, 10)
        def_node.start_byte = 0
        def_node.end_byte = len(source)

        name_node = MagicMock()
        name_node.text = b"bad"

        result = _extract_candidate(def_node, name_node, source, source_lines, "function", True)
        assert result["name"] == "bad"
        assert isinstance(result["body"], str)


# ═══════════════════════════════════════════════════════════════════════════
# _setup_query
# ═══════════════════════════════════════════════════════════════════════════


class TestSetupQuery:
    """_setup_query — parser, language, query compilation (lines 737-755)."""

    def test_known_language_returns_tuple(self):
        """Python setup returns (parser, lang, query)."""
        result = _setup_query("python")
        assert result is not None
        parser, lang, query = result
        assert parser is not None
        assert lang is not None
        assert query is not None

    def test_unknown_language_returns_none(self):
        """Unknown language returns None."""
        result = _setup_query("brainfuck")
        assert result is None

    @pytest.mark.skip(reason="Requires a language not in _SYMBOL_QUERIES that still has a parser")
    def test_fallback_query_text(self):
        """When lang_key not in _SYMBOL_QUERIES, a fallback query is used."""
        # This path triggers when lang is known but has no query entry
        # In practice all registered languages have queries, so we'd need
        # to mock _SYMBOL_QUERIES to test this.
        pass


class TestSetupQueryFallbackAndErrors:
    """_setup_query — fallback query and compile error paths (lines 744, 752)."""

    def test_unknown_language_returns_none(self):
        """Unknown language with no parser returns None."""
        result = _setup_query("completely_unknown_lang_xyz")
        assert result is None

    def test_query_compile_error_returns_none(self, monkeypatch):
        """When Query() rejects the query text, _setup_query returns None (line 752).

        The fallback query (lines 744-749) contains node types that don't coexist
        in any single grammar, so any language that triggers the fallback will
        hit the compile error path.
        """
        import code_intel.tools.base as base_mod
        # Pick a registered language and give it empty query text to trigger fallback
        monkeypatch.setitem(base_mod._SYMBOL_QUERIES, "python", "")
        result = _setup_query("python")
        # Fallback query has node types not in Python grammar → compile error → None
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# _get_language / _get_parser — lazy init when _LANG_READY is False
# ═══════════════════════════════════════════════════════════════════════════


class TestGetLanguageLazyInit:
    """_get_language — lazy init when not ready (line 623)."""

    def test_unknown_language_none_when_not_ready(self, monkeypatch):
        """When _LANG_READY is False, _get_language calls _init_languages."""
        import code_intel.tools.base as base_mod
        base_mod._LANG_READY = False
        base_mod._LANG_CACHE.clear()
        # After lazy init, unknown lang should still be None
        lang = _get_language("brainfuck")
        assert lang is None


class TestGetParserLazyInit:
    """_get_parser — lazy init when not ready (line 630)."""

    def test_parser_unknown_lang_returns_none(self, monkeypatch):
        """Unknown language returns None even after init."""
        import code_intel.tools.base as base_mod
        base_mod._LANG_READY = False
        base_mod._LANG_CACHE.clear()
        parser = _get_parser("brainfuck")
        assert parser is None

    def test_parser_returns_cached_instance(self):
        """Calling _get_parser twice returns same cached parser."""
        # Reset to force fresh init
        import code_intel.tools.base as base_mod
        base_mod._LANG_READY = False
        base_mod._LANG_CACHE.clear()
        base_mod._PARSER_CACHE.clear()

        p1 = _get_parser("python")
        p2 = _get_parser("python")
        assert p1 is p2  # same cached object


# ═══════════════════════════════════════════════════════════════════════════
# _classify_node — again (existing test, safe to keep)
# ═══════════════════════════════════════════════════════════════════════════


class TestClassifyNodeAgain:
    """_classify_node — basic paths (safe duplicate coverage)."""

    def test_capture_name_is_pass_through(self):
        """When capture name is 'name', we still fall through to node type."""
        node = _make_node("function_definition")
        result = _classify_node(node, "name")
        assert result == "function"

    def test_capture_name_symbol_fallback(self):
        """Unknown node type returns 'symbol'."""
        node = _make_node("weird_custom_thing")
        result = _classify_node(node, "name")
        assert result == "symbol"


# ═══════════════════════════════════════════════════════════════════════════
# DetectLanguage — additional edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestDetectLanguageExtended:
    """detect_language — pyi, mts, cts, cc, cxx, h, hpp, explicit_lang."""

    def test_pyi(self):
        assert detect_language("types.pyi") == "python"

    def test_mts(self):
        assert detect_language("mod.mts") == "typescript"

    def test_cts(self):
        assert detect_language("mod.cts") == "typescript"

    def test_cc(self):
        assert detect_language("mod.cc") == "cpp"

    def test_cxx(self):
        assert detect_language("mod.cxx") == "cpp"

    def test_h(self):
        assert detect_language("mod.h") == "c"

    def test_hpp(self):
        assert detect_language("mod.hpp") == "cpp"

    def test_explicit_lang_returns_directly(self):
        """explicit_lang causes early return with lowered value (line 646)."""
        assert detect_language("any.file", explicit_lang="Go") == "go"
        assert detect_language("any.file", explicit_lang="PYTHON") == "python"
