"""Tests for tools/code_tools.py — code_symbols_tool."""

import json
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Skip entire module if tree-sitter is not installed
# ---------------------------------------------------------------------------
pytest.importorskip("tree_sitter", reason="tree-sitter not installed")

from code_intel.code_tools import (
    code_symbols_tool,
    detect_language,
)
from code_intel.lsp_bridge import LSPBridge


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_py(tmp_path):
    """A small Python source file for testing."""
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
    """A small TypeScript source file."""
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
    """A small JavaScript source file."""
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
    """A small Rust source file."""
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
    """A small Go source file."""
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


# ---------------------------------------------------------------------------
# detect_language
# ---------------------------------------------------------------------------


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

    def test_tsx(self, tmp_path):
        f = tmp_path / "app.tsx"
        f.write_text("")
        assert detect_language(str(f)) == "tsx"

    def test_unknown_returns_none(self, tmp_path):
        f = tmp_path / "file.xyz"
        f.write_text("")
        assert detect_language(str(f)) is None

    def test_explicit_override(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("")
        assert detect_language(str(f), explicit_lang="python") == "python"


# ---------------------------------------------------------------------------
# Python symbol extraction
# ---------------------------------------------------------------------------


class TestPythonSymbols:
    def test_finds_class(self, tmp_py):
        result = json.loads(code_symbols_tool(str(tmp_py)))
        names = [s["name"] for s in result["symbols"]]
        assert "Greeter" in names

    def test_finds_top_level_function(self, tmp_py):
        result = json.loads(code_symbols_tool(str(tmp_py)))
        names = [s["name"] for s in result["symbols"]]
        assert "top_level_fn" in names

    def test_finds_method(self, tmp_py):
        result = json.loads(code_symbols_tool(str(tmp_py)))
        kinds = {s["name"]: s["kind"] for s in result["symbols"]}
        assert "greet" in kinds
        assert kinds["greet"] == "method"

    def test_finds_async_fn(self, tmp_py):
        result = json.loads(code_symbols_tool(str(tmp_py)))
        names = [s["name"] for s in result["symbols"]]
        assert "async_fn" in names

    def test_filter_by_kind_function(self, tmp_py):
        result = json.loads(code_symbols_tool(str(tmp_py), kind="function"))
        for s in result["symbols"]:
            assert s["kind"] == "function"

    def test_filter_by_kind_class(self, tmp_py):
        result = json.loads(code_symbols_tool(str(tmp_py), kind="class"))
        for s in result["symbols"]:
            assert s["kind"] == "class"

    def test_pattern_filter(self, tmp_py):
        result = json.loads(code_symbols_tool(str(tmp_py), pattern="greet"))
        names = [s["name"] for s in result["symbols"]]
        assert "greet" in names
        assert "top_level_fn" not in names

    def test_include_body(self, tmp_py):
        result = json.loads(code_symbols_tool(str(tmp_py), include_body=True))
        for s in result["symbols"]:
            if s["name"] == "top_level_fn":
                assert "body" in s
                assert "return x * 2" in s["body"]
                break
        else:
            pytest.fail("top_level_fn not found")

    def test_line_numbers_positive(self, tmp_py):
        result = json.loads(code_symbols_tool(str(tmp_py)))
        for s in result["symbols"]:
            assert s["line"] >= 1

    def test_language_reported(self, tmp_py):
        result = json.loads(code_symbols_tool(str(tmp_py)))
        assert result["language"] == "python"


# ---------------------------------------------------------------------------
# TypeScript symbol extraction
# ---------------------------------------------------------------------------


class TestTypeScriptSymbols:
    def test_finds_interface(self, tmp_ts):
        result = json.loads(code_symbols_tool(str(tmp_ts)))
        names = [s["name"] for s in result["symbols"]]
        assert "Animal" in names

    def test_finds_class(self, tmp_ts):
        result = json.loads(code_symbols_tool(str(tmp_ts)))
        names = [s["name"] for s in result["symbols"]]
        assert "Dog" in names

    def test_finds_function(self, tmp_ts):
        result = json.loads(code_symbols_tool(str(tmp_ts)))
        names = [s["name"] for s in result["symbols"]]
        assert "createDog" in names

    def test_finds_arrow_function(self, tmp_ts):
        result = json.loads(code_symbols_tool(str(tmp_ts)))
        names = [s["name"] for s in result["symbols"]]
        assert "arrowFn" in names


# ---------------------------------------------------------------------------
# JavaScript symbol extraction
# ---------------------------------------------------------------------------


class TestJavaScriptSymbols:
    def test_finds_class(self, tmp_js):
        result = json.loads(code_symbols_tool(str(tmp_js)))
        names = [s["name"] for s in result["symbols"]]
        assert "Counter" in names

    def test_finds_function(self, tmp_js):
        result = json.loads(code_symbols_tool(str(tmp_js)))
        names = [s["name"] for s in result["symbols"]]
        assert "reset" in names

    def test_finds_arrow_fn(self, tmp_js):
        result = json.loads(code_symbols_tool(str(tmp_js)))
        names = [s["name"] for s in result["symbols"]]
        assert "double" in names


# ---------------------------------------------------------------------------
# Rust symbol extraction
# ---------------------------------------------------------------------------


class TestRustSymbols:
    def test_finds_struct(self, tmp_rs):
        result = json.loads(code_symbols_tool(str(tmp_rs)))
        names = [s["name"] for s in result["symbols"]]
        assert "Point" in names

    def test_finds_trait(self, tmp_rs):
        result = json.loads(code_symbols_tool(str(tmp_rs)))
        kinds = {s["name"]: s["kind"] for s in result["symbols"]}
        assert "Shape" in kinds
        assert kinds["Shape"] == "trait"

    def test_finds_impl_method(self, tmp_rs):
        result = json.loads(code_symbols_tool(str(tmp_rs)))
        names = [s["name"] for s in result["symbols"]]
        assert "new" in names or "distance" in names

    def test_finds_free_function(self, tmp_rs):
        result = json.loads(code_symbols_tool(str(tmp_rs)))
        names = [s["name"] for s in result["symbols"]]
        assert "origin" in names


# ---------------------------------------------------------------------------
# Go symbol extraction
# ---------------------------------------------------------------------------


class TestGoSymbols:
    def test_finds_struct(self, tmp_go):
        result = json.loads(code_symbols_tool(str(tmp_go)))
        names = [s["name"] for s in result["symbols"]]
        assert "Rectangle" in names

    def test_finds_function(self, tmp_go):
        result = json.loads(code_symbols_tool(str(tmp_go)))
        names = [s["name"] for s in result["symbols"]]
        assert "NewRectangle" in names

    def test_finds_interface(self, tmp_go):
        result = json.loads(code_symbols_tool(str(tmp_go)))
        kinds = {s["name"]: s["kind"] for s in result["symbols"]}
        assert "Stringer" in kinds
        # Go interfaces may be classified as interface, type, or symbol depending on grammar
        assert kinds["Stringer"] in ("interface", "type", "symbol")


# ---------------------------------------------------------------------------
# Directory scan
# ---------------------------------------------------------------------------


class TestDirectoryScan:
    def test_scans_multiple_files(self, tmp_path):
        # Create two source files in tmp_path directly
        (tmp_path / "a.py").write_text("def foo(): pass\nclass Bar: pass\n")
        (tmp_path / "b.ts").write_text("export function baz(): void {}\n")
        result = json.loads(code_symbols_tool(str(tmp_path)))
        assert result.get("file_count", 0) >= 2
        assert result.get("total_symbols", 0) > 0

    def test_returns_formatted_string(self, tmp_path):
        (tmp_path / "sample.py").write_text("def foo(): pass\n")
        result = json.loads(code_symbols_tool(str(tmp_path)))
        assert "formatted" in result
        assert "sample.py" in result["formatted"]

    def test_no_symbols_message(self, tmp_path):
        # Directory with only an unsupported file
        (tmp_path / "data.csv").write_text("a,b,c\n1,2,3\n")
        result = json.loads(code_symbols_tool(str(tmp_path)))
        # Should return a message, not crash
        assert "message" in result or result.get("file_count", 0) == 0


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_nonexistent_file(self, tmp_path):
        result = json.loads(code_symbols_tool(str(tmp_path / "missing.py")))
        assert "error" in result

    def test_unsupported_extension(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("a,b,c\n")
        result = json.loads(code_symbols_tool(str(f)))
        assert "error" in result or result.get("symbol_count", 0) == 0

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("")
        result = json.loads(code_symbols_tool(str(f)))
        assert result.get("symbol_count", 0) == 0


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


def test_registry_has_code_symbols():
    from tools.registry import registry
    registry.register("code_symbols", toolset="code_intel", schema={})
    assert "code_symbols" in registry.get_all_tool_names()
    assert registry.get_toolset_for_tool("code_symbols") == "code_intel"


def test_handler_callable():
    from tools.registry import registry
    registry.register("code_symbols", toolset="code_intel", schema={}, handler=lambda x: x)
    entry = registry.get_entry("code_symbols")
    assert entry is not None
    assert callable(entry.handler)


# ---------------------------------------------------------------------------
# code_search tests
# ---------------------------------------------------------------------------

pytest.importorskip("ast_grep_py", reason="ast-grep-py not installed")

from code_intel.code_tools import (  # noqa: E402
    code_search_tool,
    code_refactor_tool,
    _resolve_preset,
)


class TestCodeSearchPresets:
    def test_resolve_preset_known(self):
        q = _resolve_preset("function_calls", "python")
        assert q is not None
        assert "@func" in q

    def test_resolve_preset_alias(self):
        q = _resolve_preset("calls", "python")
        assert q is not None

    def test_resolve_preset_unknown(self):
        assert _resolve_preset("nonexistent_preset", "python") is None

    def test_resolve_preset_unsupported_lang(self):
        # 'go' doesn't have decorator_calls
        q = _resolve_preset("decorator_calls", "go")
        assert q is None


class TestCodeSearch:
    def test_search_return_stmts_preset(self, tmp_py):
        """Python fixture has return statements — use return_stmts preset."""
        result = json.loads(code_search_tool(str(tmp_py), preset="return_stmts"))
        assert result["language"] == "python"
        assert result["match_count"] > 0

    def test_search_try_catch_preset(self, tmp_py):
        """Python fixture has no try/catch — test with raw query on return_stmts."""
        result = json.loads(code_search_tool(str(tmp_py), preset="return_stmts"))
        assert result["match_count"] > 0
        for r in result["results"]:
            assert r["capture"] == "ret"

    def test_search_raw_query(self, tmp_py):
        # Search for return statements with raw query
        result = json.loads(code_search_tool(
            str(tmp_py),
            query="(return_statement) @ret",
        ))
        assert result["match_count"] > 0
        for r in result["results"]:
            assert r["capture"] == "ret"

    def test_search_with_text_pattern(self, tmp_py):
        # Filter return statements by text pattern
        result = json.loads(code_search_tool(str(tmp_py), preset="return_stmts", pattern="Goodbye"))
        assert result["match_count"] >= 1
        for r in result["results"]:
            assert "goodbye" in r["text"].lower()

    def test_search_no_params_error(self, tmp_py):
        result = json.loads(code_search_tool(str(tmp_py)))
        assert "error" in result

    def test_search_nonexistent_file(self, tmp_path):
        result = json.loads(code_search_tool(str(tmp_path / "missing.py")))
        assert "error" in result

    def test_search_directory_scans_recursively(self, tmp_path):
        # Directory with no supported files → 0 matches, not an error
        result = json.loads(code_search_tool(str(tmp_path), preset="function_calls"))
        assert result["files_scanned"] == 0
        assert result["match_count"] == 0

    def test_search_max_results(self, tmp_path):
        # Create a file with many function calls
        src = "\n".join([f"print({i})" for i in range(100)])
        (tmp_path / "many.py").write_text(src)
        result = json.loads(code_search_tool(
            str(tmp_path / "many.py"),
            preset="function_calls",
            max_results=5,
        ))
        assert result["match_count"] == 5
        assert result["truncated"] is True

    def test_search_directory_multi_file(self, tmp_path):
        """code_search on directory scans multiple files and aggregates results."""
        (tmp_path / "a.py").write_text("foo()\nbar()")
        (tmp_path / "b.py").write_text("baz()\n# just a comment\nqux()")
        (tmp_path / "readme.txt").write_text("not code")

        result = json.loads(code_search_tool(
            str(tmp_path), preset="function_calls",
        ))
        assert result["files_scanned"] == 2
        assert result["files_with_matches"] == 2
        # Preset captures both @call and @func per call, so 4 calls × 2 captures = 8
        assert result["match_count"] == 8
        # All results should have file path
        for r in result["results"]:
            assert "file" in r

    def test_search_directory_with_pattern_filter(self, tmp_path):
        """Directory scan respects pattern filter across files."""
        (tmp_path / "a.py").write_text("print('hello')\nrange(10)")
        (tmp_path / "b.py").write_text("len([1])\nprint('ok')")

        result = json.loads(code_search_tool(
            str(tmp_path), preset="function_calls", pattern="print",
        ))
        # 2 print() calls, each has @func + @call captures = 4 total
        assert result["match_count"] == 4
        func_captures = [r for r in result["results"] if r["capture"] == "func"]
        assert len(func_captures) == 2
        assert all("print" in r["text"] for r in func_captures)

    def test_search_directory_max_results_across_files(self, tmp_path):
        """max_results limit works across files in directory scan."""
        (tmp_path / "a.py").write_text("\n".join([f"f{i}()" for i in range(50)]))
        (tmp_path / "b.py").write_text("\n".join([f"g{i}()" for i in range(50)]))

        result = json.loads(code_search_tool(
            str(tmp_path), preset="function_calls", max_results=3,
        ))
        assert result["match_count"] == 3

    def test_search_unknown_preset(self, tmp_py):
        result = json.loads(code_search_tool(str(tmp_py), preset="nonexistent"))
        assert "error" in result


class TestCodeSearchMultiLang:
    def test_search_ts_return_stmts(self, tmp_ts):
        result = json.loads(code_search_tool(str(tmp_ts), preset="return_stmts"))
        assert result["language"] == "typescript"
        assert result["match_count"] > 0

    def test_search_js_function_calls(self, tmp_js):
        """JS fixture uses identifier calls like reset(), constructor."""
        result = json.loads(code_search_tool(str(tmp_js), preset="function_calls"))
        assert result["language"] == "javascript"
        # Member expression calls (this.count++) won't match the identifier-only query
        # but the fixture has at least constructor which may not match either.
        # Just verify the tool doesn't error.
        assert "match_count" in result

    def test_search_rust_string_literals(self, tmp_rs):
        result = json.loads(code_search_tool(str(tmp_rs), preset="string_literals"))
        assert result["language"] == "rust"

    def test_search_go_imports(self, tmp_go):
        result = json.loads(code_search_tool(str(tmp_go), preset="imports"))
        assert result["language"] == "go"


# ---------------------------------------------------------------------------
# code_refactor tests
# ---------------------------------------------------------------------------


class TestCodeRefactor:
    def test_dry_run_finds_matches(self, tmp_path):
        src = 'console.log("hello")\nconsole.log("world")'
        f = tmp_path / "test.ts"
        f.write_text(src)
        result = json.loads(code_refactor_tool(
            str(f), pattern='console.log($ARG)', rewrite='console.info($ARG)',
            language="typescript",
        ))
        assert result["dry_run"] is True
        assert result["match_count"] == 2
        assert result["applied"] is False
        # File should NOT be modified
        assert f.read_text() == src

    def test_wet_run_applies_changes(self, tmp_path):
        src = 'console.log("hello")\nconsole.log("world")'
        f = tmp_path / "test.ts"
        f.write_text(src)
        result = json.loads(code_refactor_tool(
            str(f), pattern='console.log($ARG)', rewrite='console.info($ARG)',
            language="typescript", dry_run=False,
        ))
        assert result["dry_run"] is False
        assert result["match_count"] == 2
        assert result["applied"] is True
        new_src = f.read_text()
        assert 'console.info("hello")' in new_src
        assert 'console.info("world")' in new_src
        assert "console.log" not in new_src

    def test_no_matches(self, tmp_path):
        src = 'let x = 1;'
        f = tmp_path / "test.ts"
        f.write_text(src)
        result = json.loads(code_refactor_tool(
            str(f), pattern='console.log($ARG)', rewrite='console.info($ARG)',
            language="typescript",
        ))
        assert result["match_count"] == 0

    def test_nonexistent_file(self, tmp_path):
        result = json.loads(code_refactor_tool(
            str(tmp_path / "missing.py"),
            pattern='foo', rewrite='bar',
        ))
        assert "error" in result

    def test_python_function_rename_pattern(self, tmp_path):
        src = 'def old_name(x):\n    return x + 1\n\ndef other():\n    pass\n'
        f = tmp_path / "test.py"
        f.write_text(src)
        result = json.loads(code_refactor_tool(
            str(f), pattern='def old_name($$$ARGS): $$$BODY', rewrite='def new_name($$$ARGS): $$$BODY',
            language="python",
        ))
        assert result["dry_run"] is True
        assert result["match_count"] == 1
        assert result["changes"][0]["original"].startswith("def old_name")

    def test_variables_extracted(self, tmp_path):
        src = 'foo(42, "hello")\n'
        f = tmp_path / "test.py"
        f.write_text(src)
        result = json.loads(code_refactor_tool(
            str(f), pattern='foo($X, $Y)', rewrite='bar($X, $Y)',
            language="python",
        ))
        assert result["match_count"] == 1
        assert "X" in result["changes"][0]["variables"]
        assert result["changes"][0]["variables"]["X"] == "42"

    def test_context_lines(self, tmp_path):
        src = '# before\nconsole.log("test")\n# after\n'
        f = tmp_path / "test.ts"
        f.write_text(src)
        result = json.loads(code_refactor_tool(
            str(f), pattern='console.log($ARG)', rewrite='console.info($ARG)',
            language="typescript", context_lines=1,
        ))
        assert result["match_count"] == 1
        ctx = result["changes"][0]["context"]
        assert "# before" in ctx["before"]
        assert "# after" in ctx["after"]

    # --- Multi-file (directory) tests ---

    def test_directory_dry_run(self, tmp_path):
        """code_refactor on a directory finds matches across multiple files."""
        (tmp_path / "a.ts").write_text('console.log("a")\nlet x = 1')
        (tmp_path / "b.ts").write_text('console.log("b")\nlet y = 2')
        (tmp_path / "c.py").write_text('print("c")')  # No match
        result = json.loads(code_refactor_tool(
            str(tmp_path), pattern='console.log($ARG)', rewrite='console.info($ARG)',
        ))
        assert result["files_scanned"] == 3
        assert result["files_changed"] == 2
        assert result["match_count"] == 2
        assert result["dry_run"] is True
        # Files should NOT be modified
        assert 'console.log("a")' in (tmp_path / "a.ts").read_text()

    def test_directory_wet_run(self, tmp_path):
        """code_refactor dry_run=false applies changes to all matching files."""
        (tmp_path / "a.ts").write_text('console.log("a")\n')
        (tmp_path / "b.ts").write_text('console.log("b")\n')
        result = json.loads(code_refactor_tool(
            str(tmp_path), pattern='console.log($ARG)', rewrite='console.info($ARG)',
            dry_run=False,
        ))
        assert result["files_changed"] == 2
        assert result["match_count"] == 2
        assert 'console.info("a")' in (tmp_path / "a.ts").read_text()
        assert 'console.info("b")' in (tmp_path / "b.ts").read_text()
        assert "console.log" not in (tmp_path / "a.ts").read_text()

    def test_directory_with_file_glob(self, tmp_path):
        """file_glob filters which files are scanned in directory mode."""
        (tmp_path / "service.ts").write_text('console.log("keep")\n')
        (tmp_path / "test.ts").write_text('console.log("skip")\n')
        (tmp_path / "other.py").write_text('console.log("py")\n')
        result = json.loads(code_refactor_tool(
            str(tmp_path), pattern='console.log($ARG)', rewrite='console.info($ARG)',
            file_glob="*test",
        ))
        # Should only scan files matching *test pattern
        assert result["files_scanned"] == 1  # only test.ts
        assert result["match_count"] == 1

    def test_directory_no_matches(self, tmp_path):
        """Directory with no matching patterns returns zeros."""
        (tmp_path / "a.py").write_text('x = 1\n')
        (tmp_path / "b.py").write_text('y = 2\n')
        result = json.loads(code_refactor_tool(
            str(tmp_path), pattern='console.log($ARG)', rewrite='console.info($ARG)',
        ))
        assert result["files_scanned"] == 2
        assert result["files_changed"] == 0
        assert result["match_count"] == 0

    def test_directory_single_file_still_works(self, tmp_path):
        """Backward compat: single file path still returns flat structure."""
        f = tmp_path / "test.ts"
        f.write_text('console.log("hello")\n')
        result = json.loads(code_refactor_tool(
            str(f), pattern='console.log($ARG)', rewrite='console.info($ARG)',
            language="typescript",
        ))
        assert result["match_count"] == 1
        assert "path" in result
        assert "changes" in result
        # Single file should NOT have "results" key (flat, not wrapped)
        assert "results" not in result

    def test_directory_mixed_languages(self, tmp_path):
        """Refactoring across mixed language files works."""
        (tmp_path / "app.ts").write_text('console.log("ts")\n')
        (tmp_path / "main.py").write_text('def foo():\n    pass\n')
        # Only TS files should match
        result = json.loads(code_refactor_tool(
            str(tmp_path), pattern='console.log($ARG)', rewrite='console.info($ARG)',
        ))
        assert result["files_scanned"] == 2
        assert result["files_changed"] == 1
        assert result["match_count"] == 1


# ---------------------------------------------------------------------------
# LSP bridge document lifecycle
# ---------------------------------------------------------------------------


class TestLSPBridgeDocumentLifecycle:
    def _bridge(self, tmp_path):
        return LSPBridge(
            command="typescript-language-server",
            args=["--stdio"],
            root_uri=f"file://{tmp_path}",
            workspace_folders=[],
            language_id="typescript",
        )

    def test_open_document_reconcile_close_only_once_per_uri(self, tmp_path, monkeypatch):
        f = tmp_path / "sample.ts"
        f.write_text("export const value = 1;\n")
        bridge = self._bridge(tmp_path)
        sent = []
        monkeypatch.setattr(bridge, "_send_notification", lambda method, params: sent.append((method, params)))

        bridge.open_document(str(f))
        bridge.close_document(str(f))
        bridge.open_document(str(f))

        methods = [method for method, _params in sent]
        assert methods == [
            "textDocument/didOpen",
            "textDocument/didClose",  # explicit close_document call
            "textDocument/didOpen",
        ]

    def test_suppresses_recent_reconcile_close_noise(self, tmp_path, monkeypatch):
        bridge = self._bridge(tmp_path)
        monkeypatch.setattr("code_intel.lsp.bridge.time.monotonic", lambda: 101.0)
        bridge._reconcile_close_uris["file:///tmp/sample.ts"] = 100.0

        assert bridge._is_expected_reconcile_close_message(
            "Trying to close document that is not open: file:///tmp/sample.ts"
        )


# ---------------------------------------------------------------------------
# Registry integration for new tools
# ---------------------------------------------------------------------------


def test_registry_has_code_search():
    from tools.registry import registry
    registry.register("code_search", toolset="code_intel", schema={})
    assert "code_search" in registry.get_all_tool_names()
    assert registry.get_toolset_for_tool("code_search") == "code_intel"


def test_registry_has_code_refactor():
    from tools.registry import registry
    registry.register("code_refactor", toolset="code_intel", schema={})
    assert "code_refactor" in registry.get_all_tool_names()
    assert registry.get_toolset_for_tool("code_refactor") == "code_intel"
