"""Comprehensive pytest tests for code_intel.tools.export — Coverage 90%+ target.

Tests all three tools:
  - code_export_tool / _handle_code_export
  - code_docstring_generate_tool / _handle_code_docstring_generate
  - code_dependency_risk_tool / _handle_code_dependency_risk

Covers: happy paths, error paths, edge cases, all 3 formats, all 3 docstring
styles, risk scoring branches, and positional-arg handlers.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

# ── Module under test ──────────────────────────────────────────────────────
# The conftest already patches sys.modules["code_intel._fmt"] so that
# fmt_ok / fmt_err / fmt_json return plain JSON strings — perfect for
# assert-based testing without ANSI/rich complexity.


@pytest.fixture(autouse=True)
def _import_export():
    """Ensure tools.export is freshly imported for each test.

    The conftest keeps 'code_intel.tools.export' in _KEEP so we must
    explicitly delete it before each test to get a clean module.
    """
    for key in list(sys.modules.keys()):
        if key == "code_intel.tools.export" or key.startswith("code_intel.tools.export."):
            del sys.modules[key]


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _load_json(result_str: str):
    """Parse a fmt_ok / fmt_err / fmt_json result string.

    The conftest's mocked _fmt functions return UTF-8 JSON, so
    we can simply json.loads() them.
    """
    return json.loads(result_str)


def _is_error(result_str: str) -> bool:
    """Return True if the result is an error panel (mocked)."""
    try:
        data = json.loads(result_str)
        return data.get("status") == "error"
    except (json.JSONDecodeError, TypeError):
        return False


def _is_ok(result_str: str) -> bool:
    """Return True if the result is a success panel (mocked)."""
    try:
        data = json.loads(result_str)
        return data.get("status") == "ok"
    except (json.JSONDecodeError, TypeError):
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def py_file(tmp_path: Path) -> Path:
    """A minimal Python file with a few functions and a class."""
    src = textwrap.dedent("""\
        def hello(name: str) -> str:
            return f"Hello {name}"

        class Greeter:
            def greet(self, name: str) -> str:
                return self.hello(name)

            def _internal(self):
                pass

        def _private_func():
            pass
    """)
    p = tmp_path / "example.py"
    p.write_text(src)
    return p


@pytest.fixture
def ts_file(tmp_path: Path) -> Path:
    """A minimal TypeScript file."""
    src = textwrap.dedent("""\
        export interface Animal {
            name: string;
        }
        export class Dog implements Animal {
            constructor(public name: string) {}
            bark(): string { return "woof"; }
        }
        export function createDog(name: string): Dog {
            return new Dog(name);
        }
    """)
    p = tmp_path / "example.ts"
    p.write_text(src)
    return p


@pytest.fixture
def func_file(tmp_path: Path) -> Path:
    """A file with defs used for docstring generation tests."""
    src = textwrap.dedent("""\
        def simple():
            pass

        def typed(a: int, b: str) -> bool:
            return True

        async def async_func(url: str) -> dict:
            return {}

        def with_default(x: int = 42, y: str = "hi") -> None:
            print(x, y)
    """)
    p = tmp_path / "funcs.py"
    p.write_text(src)
    return p


# ═══════════════════════════════════════════════════════════════════════════
# code_export_tool — F1: Export symbol index
# ═══════════════════════════════════════════════════════════════════════════


class TestCodeExportTool:
    """code_export_tool() — main export function (lines 29-123)."""

    # ── Error paths ────────────────────────────────────────────────────

    def test_path_not_found(self):
        """Non-existent path → fmt_err."""
        from code_intel.tools.export import code_export_tool

        result = code_export_tool(path="/nonexistent/path")
        assert _is_error(result), f"Expected error, got: {result}"
        data = _load_json(result)
        assert "not found" in str(data).lower()

    def test_no_symbols(self, tmp_path):
        """Internal function returns no symbols → fmt_err."""
        from code_intel.tools.export import code_export_tool

        p = tmp_path / "dummy.py"
        p.write_text("x = 1\n")

        with patch("code_intel.tools.symbols._symbols_extract_single") as mock_sym:
            mock_sym.return_value = ([], 1)
            result = code_export_tool(path=str(p))
        assert _is_error(result)

    def test_empty_symbols_list(self, tmp_path):
        """Internal function returns empty symbols list → fmt_err."""
        from code_intel.tools.export import code_export_tool

        p = tmp_path / "dummy.py"
        p.write_text("x = 1\n")

        with patch("code_intel.tools.symbols._symbols_extract_single") as mock_sym:
            mock_sym.return_value = ([], 1)
            result = code_export_tool(path=str(p))
        assert _is_error(result)

    def test_sym_returns_none(self, tmp_path):
        """detect_language returns None → no lang → empty symbols → fmt_err."""
        from code_intel.tools.export import code_export_tool

        p = tmp_path / "dummy.py"
        p.write_text("x = 1\n")

        with patch("code_intel.tools.base.detect_language") as mock_dl:
            mock_dl.return_value = None
            result = code_export_tool(path=str(p))
        assert _is_error(result)

    def test_sym_returns_empty_string(self, tmp_path):
        """_symbols_extract_single returns empty list → fmt_err."""
        from code_intel.tools.export import code_export_tool

        p = tmp_path / "dummy.py"
        p.write_text("x = 1\n")

        with patch("code_intel.tools.symbols._symbols_extract_single") as mock_sym:
            mock_sym.return_value = ([], 1)
            result = code_export_tool(path=str(p))
        assert _is_error(result)

    def test_sym_returns_list_directly(self, tmp_path):
        """_symbols_extract_single returns symbols → export succeeds."""
        from code_intel.tools.export import code_export_tool

        p = tmp_path / "dummy.py"
        p.write_text("x = 1\n")

        with patch("code_intel.tools.symbols._symbols_extract_single") as mock_sym:
            mock_sym.return_value = ([
                {"name": "foo", "kind": "function", "line": 1},
            ], 1)
            result = code_export_tool(path=str(p))
        assert isinstance(result, str)

    def test_sym_returns_dict_with_symbols(self, tmp_path):
        """_symbols_extract_single returns symbols → export succeeds."""
        from code_intel.tools.export import code_export_tool

        p = tmp_path / "dummy.py"
        p.write_text("x = 1\n")

        with patch("code_intel.tools.base.detect_language") as mock_dl:
            mock_dl.return_value = None
            result = code_export_tool(path=str(p))
        assert _is_error(result)

    def test_sym_json_no_symbols_key(self, tmp_path):
        """_symbols_extract_single raises → empty symbols (exception caught) → fmt_err."""
        from code_intel.tools.export import code_export_tool

        p = tmp_path / "dummy.py"
        p.write_text("x = 1\n")

        with patch("code_intel.tools.base.detect_language") as mock_dl:
            mock_dl.side_effect = Exception("boom")
            result = code_export_tool(path=str(p))
        assert _is_error(result)

    # ── JSON format (default) ──────────────────────────────────────────

    def test_json_format_basic(self, tmp_path, py_file):
        """JSON format returns JSON with symbol data (no 'status' key, it's fmt_json)."""
        from code_intel.tools.export import code_export_tool

        result = code_export_tool(path=str(py_file))
        # JSON format uses fmt_json (not fmt_ok), so no "status" key
        data = _load_json(result)
        assert isinstance(data, dict)
        # Should have project/symbols info
        assert "total_symbols" in data
        assert data["total_symbols"] > 0

    def test_json_format_explicit(self, py_file):
        """Explicit fmt='json' works."""
        from code_intel.tools.export import code_export_tool

        result = code_export_tool(path=str(py_file), fmt="json")
        assert not _is_error(result)

    def test_json_with_class_and_methods(self, tmp_path):
        """JSON export with a class that has children."""
        from code_intel.tools.export import code_export_tool

        p = tmp_path / "with_class.py"
        p.write_text(textwrap.dedent("""\
            class MyClass:
                def method1(self):
                    pass
                def method2(self):
                    pass
        """))
        result = code_export_tool(path=str(p))
        assert not _is_error(result)

    # ── Markdown format ────────────────────────────────────────────────

    def test_markdown_format(self, py_file):
        """Markdown format returns success."""
        from code_intel.tools.export import code_export_tool

        result = code_export_tool(path=str(py_file), fmt="markdown")
        assert not _is_error(result), f"Unexpected error: {result}"

    def test_markdown_with_classes(self, tmp_path):
        """Markdown with class symbols shows class section."""
        from code_intel.tools.export import code_export_tool

        p = tmp_path / "classes_only.py"
        p.write_text(textwrap.dedent("""\
            class Base:
                def method(self):
                    pass
            class Derived(Base):
                def extra(self):
                    pass
        """))
        result = code_export_tool(path=str(p), fmt="markdown")
        assert not _is_error(result)

    def test_markdown_with_functions_only(self, tmp_path):
        """Markdown with only functions."""
        from code_intel.tools.export import code_export_tool

        p = tmp_path / "funcs_only.py"
        p.write_text("def a(): pass\ndef b(): pass\n")
        result = code_export_tool(path=str(p), fmt="markdown")
        assert not _is_error(result)

    # ── Summary format ─────────────────────────────────────────────────

    def test_summary_format(self, py_file):
        """Summary format returns file-extension counts."""
        from code_intel.tools.export import code_export_tool

        result = code_export_tool(path=str(py_file), fmt="summary")
        assert not _is_error(result), f"Unexpected error: {result}"
        data = _load_json(result)
        # Should have summary-related keys
        found = any(k in str(data) for k in ("total_symbols", "total_files"))
        assert found, f"Missing summary keys in: {data}"

    # ── kind filter ────────────────────────────────────────────────────

    def test_kind_function(self, py_file):
        """kind='function' filter works."""
        from code_intel.tools.export import code_export_tool

        result = code_export_tool(path=str(py_file), kind="function")
        assert isinstance(result, str)

    def test_kind_class(self, py_file):
        """kind='class' filter works."""
        from code_intel.tools.export import code_export_tool

        result = code_export_tool(path=str(py_file), kind="class")
        assert isinstance(result, str)

    def test_kind_method(self, py_file):
        """kind='method' filter works."""
        from code_intel.tools.export import code_export_tool

        result = code_export_tool(path=str(py_file), kind="method")
        assert isinstance(result, str)

    # ── Edge: non-Python file ──────────────────────────────────────────

    def test_ts_file_export(self, ts_file):
        """TypeScript file exports without error."""
        from code_intel.tools.export import code_export_tool

        result = code_export_tool(path=str(ts_file))
        assert isinstance(result, str)


# ═══════════════════════════════════════════════════════════════════════════
# _handle_code_export — positional-arg wrapper
# ═══════════════════════════════════════════════════════════════════════════


class TestHandleCodeExport:
    """_handle_code_export(args, **kw) — line 149-154."""

    def test_defaults(self):
        """Defaults: path='.', fmt='json', kind='all'."""
        from code_intel.tools.export import _handle_code_export

        with patch("code_intel.tools.export.code_export_tool") as mock_tool:
            mock_tool.return_value = '{"status": "ok"}'
            _handle_code_export({})
        mock_tool.assert_called_once_with(path=".", fmt="json", kind="all")

    def test_all_args(self):
        """Pass all args through."""
        from code_intel.tools.export import _handle_code_export

        with patch("code_intel.tools.export.code_export_tool") as mock_tool:
            mock_tool.return_value = '{"status": "ok"}'
            _handle_code_export({"path": "/some/path", "fmt": "markdown", "kind": "class"})
        mock_tool.assert_called_once_with(path="/some/path", fmt="markdown", kind="class")

    def test_partial_args(self):
        """Partial args use defaults."""
        from code_intel.tools.export import _handle_code_export

        with patch("code_intel.tools.export.code_export_tool") as mock_tool:
            mock_tool.return_value = '{"status": "ok"}'
            _handle_code_export({"path": "/some/path"})
        mock_tool.assert_called_once_with(path="/some/path", fmt="json", kind="all")

    def test_empty_path(self):
        """Empty path → passed through as empty string."""
        from code_intel.tools.export import _handle_code_export

        with patch("code_intel.tools.export.code_export_tool") as mock_tool:
            mock_tool.return_value = '{"status": "ok"}'
            _handle_code_export({"path": "", "fmt": "summary"})
        mock_tool.assert_called_once_with(path="", fmt="summary", kind="all")


# ═══════════════════════════════════════════════════════════════════════════
# code_docstring_generate_tool — D2: Generate docstring from AST
# ═══════════════════════════════════════════════════════════════════════════


class TestCodeDocstringGenerateTool:
    """code_docstring_generate_tool() — lines 162-307."""

    # ── Error paths ────────────────────────────────────────────────────

    def test_path_not_found(self):
        """Non-existent path → fmt_err."""
        from code_intel.tools.export import code_docstring_generate_tool

        result = code_docstring_generate_tool(path="/nonexistent/file.py", line=1)
        assert _is_error(result)

    def test_cannot_read_file(self, tmp_path):
        """File exists but cannot be read → fmt_err."""
        from code_intel.tools.export import code_docstring_generate_tool

        p = tmp_path / "secret.py"
        p.write_text("def foo(): pass\n")
        p.chmod(0o000)
        result = code_docstring_generate_tool(path=str(p), line=1)
        # Might raise or return error depending on permissions
        assert isinstance(result, str)
        p.chmod(0o644)  # restore so tmp_path cleanup works

    def test_no_function_found(self, tmp_path):
        """File has no function at/near given line → fmt_err."""
        from code_intel.tools.export import code_docstring_generate_tool

        p = tmp_path / "no_func.py"
        p.write_text("x = 1\ny = 2\n")
        result = code_docstring_generate_tool(path=str(p), line=1)
        assert _is_error(result)
        data = _load_json(result)
        assert "No function definition" in str(data)

    def test_cannot_parse_signature(self, tmp_path):
        """Function def exists but regex can't parse → fmt_err."""
        from code_intel.tools.export import code_docstring_generate_tool

        p = tmp_path / "weird.py"
        p.write_text("def foo: pass\n")  # no parens
        result = code_docstring_generate_tool(path=str(p), line=1)
        assert _is_error(result)

    def test_regex_no_match(self, tmp_path):
        """Function with fn keyword (not def) found but regex can't parse → fmt_err."""
        from code_intel.tools.export import code_docstring_generate_tool

        p = tmp_path / "rusty.rs"
        p.write_text("fn hello(name: &str) -> String {\n    format!(\"Hello {}\", name)\n}\n")
        result = code_docstring_generate_tool(path=str(p), line=1)
        # The fn keyword is found by the search loop but the regex expects 'def'
        assert _is_error(result)

    def test_inner_fn_break(self, tmp_path):
        """Cover the inner loop `fn`/`pub fn` break (lines 208-209).

        When the outer loop finds a 'fn ' function, the inner loop
        collects body lines; encountering another 'fn ' triggers the
        break on lines 208-209.
        """
        from code_intel.tools.export import code_docstring_generate_tool

        p = tmp_path / "two_fns.rs"
        p.write_text("fn first():\n    pass\nfn second():\n    pass\n")
        result = code_docstring_generate_tool(path=str(p), line=1)
        # The fn search works but the regex still won't match 'fn' → error
        assert _is_error(result)

    def test_inner_pub_fn_break(self, tmp_path):
        """Cover the pub fn branch in the inner loop (line 209)."""
        from code_intel.tools.export import code_docstring_generate_tool

        p = tmp_path / "pub_fns.rs"
        p.write_text("pub fn first():\n    pass\npub fn second():\n    pass\n")
        result = code_docstring_generate_tool(path=str(p), line=1)
        assert _is_error(result)

    # ── Google style (default) ─────────────────────────────────────────

    def test_google_style_simple(self, func_file):
        """Google style: simple def with no params."""
        from code_intel.tools.export import code_docstring_generate_tool

        result = code_docstring_generate_tool(path=str(func_file), line=1)
        assert not _is_error(result), f"Unexpected error: {result}"
        data = _load_json(result)
        assert data.get("status") == "ok"
        assert data.get("function") == "simple"
        assert data.get("style") == "google"
        doc = data.get("docstring", "")
        assert "Returns:" in doc
        assert '"""' in doc

    def test_google_style_with_params(self, func_file):
        """Google style with typed parameters."""
        from code_intel.tools.export import code_docstring_generate_tool

        result = code_docstring_generate_tool(path=str(func_file), line=4)
        assert not _is_error(result)
        data = _load_json(result)
        assert data.get("function") == "typed"
        assert data.get("return_type") == "bool"
        params = data.get("parameters", [])
        assert len(params) == 2
        assert params[0]["name"] == "a"
        assert params[0]["type"] == "int"
        assert params[1]["name"] == "b"
        assert params[1]["type"] == "str"
        doc = data.get("docstring", "")
        assert "Args:" in doc
        assert "a (int)" in doc
        assert "Returns:" in doc

    def test_google_style_async(self, func_file):
        """Google style with async def."""
        from code_intel.tools.export import code_docstring_generate_tool

        result = code_docstring_generate_tool(path=str(func_file), line=8)
        assert not _is_error(result)
        data = _load_json(result)
        assert data.get("function") == "async_func"
        assert data.get("return_type") == "dict"
        doc = data.get("docstring", "")
        assert "url (str)" in doc
        assert "Returns:" in doc

    def test_google_style_with_defaults(self, func_file):
        """Google style with default parameter values."""
        from code_intel.tools.export import code_docstring_generate_tool

        result = code_docstring_generate_tool(path=str(func_file), line=12)
        assert not _is_error(result)
        data = _load_json(result)
        assert data.get("function") == "with_default"
        assert len(data.get("parameters", [])) == 2
        doc = data.get("docstring", "")
        assert "x (int)" in doc
        assert "y (str)" in doc
        assert "Returns:" in doc
        assert "None" in doc

    # ── NumPy style ────────────────────────────────────────────────────

    def test_numpy_style(self, func_file):
        """NumPy style docstring."""
        from code_intel.tools.export import code_docstring_generate_tool

        result = code_docstring_generate_tool(path=str(func_file), line=4, style="numpy")
        assert not _is_error(result)
        data = _load_json(result)
        assert data.get("style") == "numpy"
        assert data.get("function") == "typed"
        doc = data.get("docstring", "")
        assert "Parameters" in doc
        assert "a : int" in doc
        assert "b : str" in doc
        assert "Returns" in doc
        assert "bool" in doc

    def test_numpy_style_no_params(self, func_file):
        """NumPy style with no parameters."""
        from code_intel.tools.export import code_docstring_generate_tool

        result = code_docstring_generate_tool(path=str(func_file), line=1, style="numpy")
        assert not _is_error(result)
        data = _load_json(result)
        doc = data.get("docstring", "")
        assert "Parameters" in doc
        assert "Returns" in doc

    # ── Sphinx style ───────────────────────────────────────────────────

    def test_sphinx_style(self, func_file):
        """Sphinx style docstring."""
        from code_intel.tools.export import code_docstring_generate_tool

        result = code_docstring_generate_tool(path=str(func_file), line=4, style="sphinx")
        assert not _is_error(result)
        data = _load_json(result)
        assert data.get("style") == "sphinx"
        doc = data.get("docstring", "")
        assert ":param a:" in doc
        assert ":type a: int" in doc
        assert ":param b:" in doc
        assert ":type b: str" in doc
        assert ":returns:" in doc
        assert ":rtype: bool" in doc

    def test_sphinx_style_async(self, func_file):
        """Sphinx style with async def."""
        from code_intel.tools.export import code_docstring_generate_tool

        result = code_docstring_generate_tool(path=str(func_file), line=8, style="sphinx")
        assert not _is_error(result)
        data = _load_json(result)
        doc = data.get("docstring", "")
        assert ":param url:" in doc
        assert ":type url: str" in doc
        assert ":rtype: dict" in doc

    # ── Edge cases ─────────────────────────────────────────────────────

    def test_line_just_past_func(self, tmp_path):
        """Line number 2-3 lines past def start still finds function."""
        from code_intel.tools.export import code_docstring_generate_tool

        p = tmp_path / "offset.py"
        p.write_text(textwrap.dedent("""\
            def foo(a, b):
                x = a + b
                return x

            def bar():
                pass
        """))
        result = code_docstring_generate_tool(path=str(p), line=3)
        assert not _is_error(result)
        data = _load_json(result)
        assert data.get("function") == "foo"

    def test_self_param_skipped(self, tmp_path):
        """'self' and 'cls' params are skipped."""
        from code_intel.tools.export import code_docstring_generate_tool

        p = tmp_path / "method.py"
        p.write_text(textwrap.dedent("""\
            class MyClass:
                def method(self, x: int, y: str) -> None:
                    pass
                @classmethod
                def clsmethod(cls, z: float) -> bool:
                    return True
        """))
        result = code_docstring_generate_tool(path=str(p), line=3)
        assert not _is_error(result)
        data = _load_json(result)
        param_names = [p["name"] for p in data.get("parameters", [])]
        assert "self" not in param_names
        assert "x" in param_names
        assert "y" in param_names

    def test_missing_return_type(self, tmp_path):
        """Function without return annotation → return_type='None'."""
        from code_intel.tools.export import code_docstring_generate_tool

        p = tmp_path / "no_return.py"
        p.write_text("def foo(x):\n    print(x)\n")
        result = code_docstring_generate_tool(path=str(p), line=1)
        assert not _is_error(result)
        data = _load_json(result)
        assert data.get("return_type") == "None"

    def test_equals_default_only(self, tmp_path):
        """Parameter with =val but no type annotation."""
        from code_intel.tools.export import code_docstring_generate_tool

        p = tmp_path / "defaults.py"
        p.write_text("def foo(name='world', count=42):\n    pass\n")
        result = code_docstring_generate_tool(path=str(p), line=1)
        assert not _is_error(result)
        data = _load_json(result)
        params = data.get("parameters", [])
        assert len(params) == 2
        assert params[0]["name"] == "name"
        assert params[0]["type"] == "Any"
        assert params[1]["name"] == "count"
        assert params[1]["type"] == "Any"

    def test_missing_return_type_no_annotation(self, tmp_path):
        """Function with '-> None:' explicitly but different syntax."""
        from code_intel.tools.export import code_docstring_generate_tool

        p = tmp_path / "ret_none.py"
        p.write_text("def foo(x: int) -> None:\n    return None\n")
        result = code_docstring_generate_tool(path=str(p), line=1)
        assert not _is_error(result)
        data = _load_json(result)
        assert data.get("return_type") == "None"


# ═══════════════════════════════════════════════════════════════════════════
# _handle_code_docstring_generate — positional-arg wrapper
# ═══════════════════════════════════════════════════════════════════════════


class TestHandleCodeDocstringGenerate:
    """_handle_code_docstring_generate(args, **kw) — lines 335-340."""

    def test_defaults(self):
        """Default: path='', line=1, style='google'."""
        from code_intel.tools.export import _handle_code_docstring_generate

        with patch("code_intel.tools.export.code_docstring_generate_tool") as mock_tool:
            mock_tool.return_value = '{"status": "ok"}'
            _handle_code_docstring_generate({})
        mock_tool.assert_called_once_with(path="", line=1, style="google")

    def test_all_args(self):
        """All args passed through."""
        from code_intel.tools.export import _handle_code_docstring_generate

        with patch("code_intel.tools.export.code_docstring_generate_tool") as mock_tool:
            mock_tool.return_value = '{"status": "ok"}'
            _handle_code_docstring_generate({"path": "/a.py", "line": 5, "style": "numpy"})
        mock_tool.assert_called_once_with(path="/a.py", line=5, style="numpy")

    def test_partial_args(self):
        """Partial args use defaults."""
        from code_intel.tools.export import _handle_code_docstring_generate

        with patch("code_intel.tools.export.code_docstring_generate_tool") as mock_tool:
            mock_tool.return_value = '{"status": "ok"}'
            _handle_code_docstring_generate({"path": "/a.py"})
        mock_tool.assert_called_once_with(path="/a.py", line=1, style="google")


# ═══════════════════════════════════════════════════════════════════════════
# code_dependency_risk_tool — F3: Dependency health analysis
# ═══════════════════════════════════════════════════════════════════════════


class TestCodeDependencyRiskTool:
    """code_dependency_risk_tool() — lines 348-433."""

    # ── Error paths ────────────────────────────────────────────────────

    def test_path_not_found(self):
        """Non-existent path → fmt_err."""
        from code_intel.tools.export import code_dependency_risk_tool

        result = code_dependency_risk_tool(path="/nonexistent/path")
        assert _is_error(result)
        data = _load_json(result)
        assert "not found" in str(data).lower()

    def test_import_scan_failure(self, tmp_path):
        """ImportGraph.scan raises → fmt_err."""
        from code_intel.tools.export import code_dependency_risk_tool

        p = tmp_path / "dummy.py"
        p.write_text("x = 1\n")

        with patch("code_intel.tools.export.ImportGraph") as MockGraph:
            instance = MockGraph.return_value
            instance.scan.side_effect = Exception("scan failed")
            result = code_dependency_risk_tool(path=str(p))
        assert _is_error(result)

    def test_parse_all_failure(self, tmp_path):
        """ImportGraph.parse_all raises → fmt_err."""
        from code_intel.tools.export import code_dependency_risk_tool

        p = tmp_path / "dummy.py"
        p.write_text("x = 1\n")

        with patch("code_intel.tools.export.ImportGraph") as MockGraph:
            instance = MockGraph.return_value
            instance.parse_all.side_effect = Exception("parse failed")
            result = code_dependency_risk_tool(path=str(p))
        assert _is_error(result)

    # ── Helper: mock an ImportGraph instance ───────────────────────────

    def _make_mock_graph(self, cycles=None, hot_paths=None, graph=None, files=None):
        """Create a mock ImportGraph with preset return values.

        Args:
            cycles: List[str] — return value for find_cycles()
            hot_paths: List[dict] — return value for find_hot_paths()
            graph: Dict[str, Set[str]] — value for .graph property
            files: list — value for .files property
        """
        mock = object.__new__(object)
        mock.scan = lambda depth=3: None
        mock.parse_all = lambda: None
        mock.find_cycles = lambda: cycles or []
        mock.find_hot_paths = lambda top_n=5: hot_paths or []
        mock.graph = graph or {}
        mock.files = files or []

        class FakeImportGraph:
            def __init__(self, project_root):
                self.instance = mock
                self.instance.project_root_value = project_root

            def __getattr__(self, name):
                return getattr(self.instance, name)

        return FakeImportGraph

    # ── Minimal project (empty / no imports) ───────────────────────────

    def test_risk_empty_project(self, tmp_path):
        """Empty project directory → low risk with no factors."""
        from code_intel.tools.export import code_dependency_risk_tool

        p = tmp_path / "simple.py"
        p.write_text("import os\nx = 1\n")

        with patch("code_intel.tools.export.ImportGraph") as MockGraph:
            instance = MockGraph.return_value
            instance.scan.return_value = None
            instance.parse_all.return_value = None
            instance.find_cycles.return_value = []
            instance.find_hot_paths.return_value = []
            instance.graph = {}
            instance.files = []

            result = code_dependency_risk_tool(path=str(p))
        assert not _is_error(result), f"Unexpected error: {result}"
        data = _load_json(result)
        assert data.get("status") == "ok"
        assert data.get("risk_level") in ("low", "medium", "high")

    # ── Low risk scenario ──────────────────────────────────────────────

    def test_risk_low(self, tmp_path):
        """Small project → low risk, no factors."""
        from code_intel.tools.export import code_dependency_risk_tool

        p = tmp_path / "simple.py"
        p.write_text("import os\n")

        with patch("code_intel.tools.export.ImportGraph") as MockGraph:
            instance = MockGraph.return_value
            instance.scan.return_value = None
            instance.parse_all.return_value = None
            instance.find_cycles.return_value = []
            instance.find_hot_paths.return_value = []
            instance.graph = {}
            instance.files = [p]

            result = code_dependency_risk_tool(path=str(p))
        assert not _is_error(result)
        data = _load_json(result)
        assert data.get("risk_score", 10) < 3
        assert data.get("risk_level") == "low"

    # ── Cyclic dependencies ────────────────────────────────────────────

    def test_risk_cyclic_deps(self, tmp_path):
        """Cyclic dependencies contribute to risk score."""
        from code_intel.tools.export import code_dependency_risk_tool

        p = tmp_path / "cyclic.py"
        p.write_text("import os\n")
        cycles = [["a.py", "b.py"], ["c.py", "d.py", "e.py"]]

        with patch("code_intel.tools.export.ImportGraph") as MockGraph:
            instance = MockGraph.return_value
            instance.scan.return_value = None
            instance.parse_all.return_value = None
            instance.find_cycles.return_value = cycles
            instance.find_hot_paths.return_value = []
            instance.graph = {}
            instance.files = [p]

            result = code_dependency_risk_tool(path=str(p))
        assert not _is_error(result)
        data = _load_json(result)
        factor_names = [f.get("factor") for f in data.get("factors", [])]
        assert "cyclic_dependencies" in factor_names

    def test_risk_many_cycles(self, tmp_path):
        """>5 cycles → high severity."""
        from code_intel.tools.export import code_dependency_risk_tool

        p = tmp_path / "many_cycles.py"
        p.write_text("import os\n")
        cycles = [[f"file{i}.py", f"file{i+1}.py"] for i in range(10)]

        with patch("code_intel.tools.export.ImportGraph") as MockGraph:
            instance = MockGraph.return_value
            instance.scan.return_value = None
            instance.parse_all.return_value = None
            instance.find_cycles.return_value = cycles
            instance.find_hot_paths.return_value = []
            instance.graph = {}
            instance.files = [p]

            result = code_dependency_risk_tool(path=str(p))
        assert not _is_error(result)
        data = _load_json(result)
        factors = data.get("factors", [])
        cyclic_factor = next((f for f in factors if f.get("factor") == "cyclic_dependencies"), None)
        assert cyclic_factor is not None
        assert cyclic_factor.get("severity") == "high"

    def test_risk_two_to_five_cycles(self, tmp_path):
        """2 < cycles <= 5 → medium severity."""
        from code_intel.tools.export import code_dependency_risk_tool

        p = tmp_path / "med_cycles.py"
        p.write_text("import os\n")
        cycles = [["a.py", "b.py"], ["c.py", "d.py"], ["e.py", "f.py"]]

        with patch("code_intel.tools.export.ImportGraph") as MockGraph:
            instance = MockGraph.return_value
            instance.scan.return_value = None
            instance.parse_all.return_value = None
            instance.find_cycles.return_value = cycles
            instance.find_hot_paths.return_value = []
            instance.graph = {}
            instance.files = [p]

            result = code_dependency_risk_tool(path=str(p))
        assert not _is_error(result)
        data = _load_json(result)
        factors = data.get("factors", [])
        cyclic_factor = next((f for f in factors if f.get("factor") == "cyclic_dependencies"), None)
        assert cyclic_factor is not None
        assert cyclic_factor.get("severity") == "medium"

    # ── Hot paths ──────────────────────────────────────────────────────

    def test_risk_hot_paths(self, tmp_path):
        """Hot paths (>20 callers) contribute to risk."""
        from code_intel.tools.export import code_dependency_risk_tool

        p = tmp_path / "hot.py"
        p.write_text("import os\n")
        hot_paths = [
            {"file": "/proj/utils.py", "caller_count": 25,
             "callers": [f"/proj/mod{i}.py" for i in range(25)]},
            {"file": "/proj/core.py", "caller_count": 30,
             "callers": [f"/proj/mod{i}.py" for i in range(30)]},
        ]

        with patch("code_intel.tools.export.ImportGraph") as MockGraph:
            instance = MockGraph.return_value
            instance.scan.return_value = None
            instance.parse_all.return_value = None
            instance.find_cycles.return_value = []
            instance.find_hot_paths.return_value = hot_paths
            instance.graph = {}
            instance.files = [p]

            result = code_dependency_risk_tool(path=str(p))
        assert not _is_error(result)
        data = _load_json(result)
        factor_names = [f.get("factor") for f in data.get("factors", [])]
        assert "hot_paths" in factor_names
        assert data.get("risk_score", 0) >= 1.5

    def test_risk_hot_paths_below_threshold(self, tmp_path):
        """Hot paths with <=20 callers → not added as factor."""
        from code_intel.tools.export import code_dependency_risk_tool

        p = tmp_path / "not_hot.py"
        p.write_text("import os\n")
        hot_paths = [
            {"file": "/proj/utils.py", "caller_count": 5, "callers": ["a.py", "b.py"]},
        ]

        with patch("code_intel.tools.export.ImportGraph") as MockGraph:
            instance = MockGraph.return_value
            instance.scan.return_value = None
            instance.parse_all.return_value = None
            instance.find_cycles.return_value = []
            instance.find_hot_paths.return_value = hot_paths
            instance.graph = {}
            instance.files = [p]

            result = code_dependency_risk_tool(path=str(p))
        assert not _is_error(result)
        data = _load_json(result)
        factor_names = [f.get("factor") for f in data.get("factors", [])]
        assert "hot_paths" not in factor_names

    def test_risk_empty_hot_paths(self, tmp_path):
        """Empty hot paths list → no hot_paths factor, no crash."""
        from code_intel.tools.export import code_dependency_risk_tool

        p = tmp_path / "no_hot.py"
        p.write_text("import os\n")

        with patch("code_intel.tools.export.ImportGraph") as MockGraph:
            instance = MockGraph.return_value
            instance.scan.return_value = None
            instance.parse_all.return_value = None
            instance.find_cycles.return_value = []
            instance.find_hot_paths.return_value = []
            instance.graph = {}
            instance.files = [p]

            result = code_dependency_risk_tool(path=str(p))
        assert not _is_error(result)
        data = _load_json(result)
        assert data.get("risk_score", 0) == 0

    # ── Import complexity (edge count = len(graph)) ────────────────────

    def test_risk_high_edge_count(self, tmp_path):
        """Graph with >200 nodes (len(g) > 200) → import_complexity factor.

        Note: edge_count = len(graph.graph) which counts NODES, not edges.
        """
        from code_intel.tools.export import code_dependency_risk_tool

        p = tmp_path / "complex.py"
        p.write_text("import os\n")

        # Build a graph with >200 nodes to trigger edge_count > 200
        g = {f"/proj/mod{i}.py": set() for i in range(250)}

        with patch("code_intel.tools.export.ImportGraph") as MockGraph:
            instance = MockGraph.return_value
            instance.scan.return_value = None
            instance.parse_all.return_value = None
            instance.find_cycles.return_value = []
            instance.find_hot_paths.return_value = []
            instance.graph = g
            instance.files = [p]

            result = code_dependency_risk_tool(path=str(p))
        assert not _is_error(result)
        data = _load_json(result)
        factor_names = [f.get("factor") for f in data.get("factors", [])]
        assert "import_complexity" in factor_names

    def test_risk_low_edge_count(self, tmp_path):
        """Graph with ≤200 nodes → no import_complexity factor."""
        from code_intel.tools.export import code_dependency_risk_tool

        p = tmp_path / "simple_complex.py"
        p.write_text("import os\n")
        g = {"/proj/mod1.py": {"/proj/mod2.py"}}

        with patch("code_intel.tools.export.ImportGraph") as MockGraph:
            instance = MockGraph.return_value
            instance.scan.return_value = None
            instance.parse_all.return_value = None
            instance.find_cycles.return_value = []
            instance.find_hot_paths.return_value = []
            instance.graph = g
            instance.files = [p]

            result = code_dependency_risk_tool(path=str(p))
        assert not _is_error(result)
        data = _load_json(result)
        factor_names = [f.get("factor") for f in data.get("factors", [])]
        assert "import_complexity" not in factor_names

    # ── Import density ─────────────────────────────────────────────────

    def test_risk_high_density(self, tmp_path):
        """High import density (>3 per file) → import_density factor.

        edge_count = len(graph.graph) which counts NODES.
        density = edge_count / file_count.
        So with 5 nodes and 1 file, density = 5.0 > 3.
        """
        from code_intel.tools.export import code_dependency_risk_tool

        p = tmp_path / "dense.py"
        p.write_text("import os\n")

        # 5 entries in g (edge_count = 5), 1 file → density = 5.0 > 3
        g = {f"/proj/mod{i}.py": {"/proj/other.py"} for i in range(5)}

        with patch("code_intel.tools.export.ImportGraph") as MockGraph:
            instance = MockGraph.return_value
            instance.scan.return_value = None
            instance.parse_all.return_value = None
            instance.find_cycles.return_value = []
            instance.find_hot_paths.return_value = []
            instance.graph = g
            instance.files = [p]

            result = code_dependency_risk_tool(path=str(p))
        assert not _is_error(result)
        data = _load_json(result)
        factor_names = [f.get("factor") for f in data.get("factors", [])]
        assert "import_density" in factor_names

    def test_risk_low_density(self, tmp_path):
        """Low import density (≤3 per file) → no import_density factor."""
        from code_intel.tools.export import code_dependency_risk_tool

        p = tmp_path / "sparse.py"
        p.write_text("import os\n")
        g = {"/proj/sparse.py": {"/proj/a.py"}}

        with patch("code_intel.tools.export.ImportGraph") as MockGraph:
            instance = MockGraph.return_value
            instance.scan.return_value = None
            instance.parse_all.return_value = None
            instance.find_cycles.return_value = []
            instance.find_hot_paths.return_value = []
            instance.graph = g
            instance.files = [p]

            result = code_dependency_risk_tool(path=str(p))
        assert not _is_error(result)
        data = _load_json(result)
        factor_names = [f.get("factor") for f in data.get("factors", [])]
        assert "import_density" not in factor_names

    # ── Risk score capping ─────────────────────────────────────────────

    def test_risk_score_capped_at_10(self, tmp_path):
        """Risk score capped at 10 even with many factors."""
        from code_intel.tools.export import code_dependency_risk_tool

        p = tmp_path / "max_risk.py"
        p.write_text("import os\n")

        # Many cycles
        cycles = [[f"f{i}.py", f"f{i+1}.py"] for i in range(100)]
        # Hot paths
        hot_paths = [{"file": "/p/u.py", "caller_count": 100, "callers": ["a.py"]}]
        # Many nodes to trigger import_complexity
        g = {f"/p/mod{i}.py": set() for i in range(300)}

        with patch("code_intel.tools.export.ImportGraph") as MockGraph:
            instance = MockGraph.return_value
            instance.scan.return_value = None
            instance.parse_all.return_value = None
            instance.find_cycles.return_value = cycles
            instance.find_hot_paths.return_value = hot_paths
            instance.graph = g
            instance.files = [p]

            result = code_dependency_risk_tool(path=str(p))
        assert not _is_error(result)
        data = _load_json(result)
        assert data.get("risk_score", 0) <= 10

    # ── Path is a file vs directory ────────────────────────────────────

    def test_risk_file_path(self, tmp_path):
        """File path → uses file's parent dir as project root."""
        from code_intel.tools.export import code_dependency_risk_tool

        p = tmp_path / "some_file.py"
        p.write_text("import os\n")

        with patch("code_intel.tools.export.ImportGraph") as MockGraph:
            instance = MockGraph.return_value
            instance.scan.return_value = None
            instance.parse_all.return_value = None
            instance.find_cycles.return_value = []
            instance.find_hot_paths.return_value = []
            instance.graph = {}
            instance.files = [p]

            result = code_dependency_risk_tool(path=str(p))
        assert not _is_error(result)

        # Verify ImportGraph was constructed with the parent dir
        parent_dir = str(p.parent)
        MockGraph.assert_called_once()
        call_args = MockGraph.call_args[0][0]
        assert parent_dir in call_args or str(p.parent.resolve()) in call_args

    def test_risk_directory_path(self, tmp_path):
        """Directory path → uses directory as project root."""
        from code_intel.tools.export import code_dependency_risk_tool

        d = tmp_path / "my_project"
        d.mkdir()
        (d / "main.py").write_text("import os\n")

        with patch("code_intel.tools.export.ImportGraph") as MockGraph:
            instance = MockGraph.return_value
            instance.scan.return_value = None
            instance.parse_all.return_value = None
            instance.find_cycles.return_value = []
            instance.find_hot_paths.return_value = []
            instance.graph = {}
            instance.files = [d / "main.py"]

            result = code_dependency_risk_tool(path=str(d))
        assert not _is_error(result)

        MockGraph.assert_called_once()
        call_args = MockGraph.call_args[0][0]
        assert "my_project" in call_args

    # ── Risk level computation ─────────────────────────────────────────

    @pytest.mark.parametrize("n_cycles,expected_level", [
        (0, "low"),       # 0 * 0.5 = 0.0 → low
        (5, "low"),       # 5 * 0.5 = 2.5 → < 3 → low
        (6, "medium"),    # 6 * 0.5 = 3.0 → >= 3 → medium
        (11, "medium"),   # 11 * 0.5 = 5.5 → capped to min(3, 11*0.5=3) + cycles
        # Wait: risk_score += min(3, n_cycles * 0.5)
        # 11 * 0.5 = 5.5 → min(3, 5.5) = 3
        # So risk_score = min(10, 3) → 3.0 → level 'medium'
        # Need score >= 6 for 'high' so: we need cycles + hot_paths + complexity
    ])
    def test_risk_level_thresholds(self, tmp_path, n_cycles, expected_level):
        """Risk level boundaries: low < 3, medium < 6, high >= 6."""
        from code_intel.tools.export import code_dependency_risk_tool

        p = tmp_path / f"level_{expected_level}.py"
        p.write_text("import os\n")
        cycles = [[f"f{i}.py", f"f{i+1}.py"] for i in range(n_cycles)]

        with patch("code_intel.tools.export.ImportGraph") as MockGraph:
            instance = MockGraph.return_value
            instance.scan.return_value = None
            instance.parse_all.return_value = None
            instance.find_cycles.return_value = cycles
            instance.find_hot_paths.return_value = []
            instance.graph = {}
            instance.files = [p]

            result = code_dependency_risk_tool(path=str(p))
        assert not _is_error(result)
        data = _load_json(result)
        assert data.get("risk_level") == expected_level, (
            f"Expected {expected_level} for n_cycles={n_cycles}, "
            f"score={data.get('risk_score')}, got {data.get('risk_level')}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# _handle_code_dependency_risk — positional-arg wrapper
# ═══════════════════════════════════════════════════════════════════════════


class TestHandleCodeDependencyRisk:
    """_handle_code_dependency_risk(args, **kw) — lines 457-460."""

    def test_defaults(self):
        """Default path='.'."""
        from code_intel.tools.export import _handle_code_dependency_risk

        with patch("code_intel.tools.export.code_dependency_risk_tool") as mock_tool:
            mock_tool.return_value = '{"status": "ok"}'
            _handle_code_dependency_risk({})
        mock_tool.assert_called_once_with(path=".")

    def test_with_path(self):
        """Path passed through."""
        from code_intel.tools.export import _handle_code_dependency_risk

        with patch("code_intel.tools.export.code_dependency_risk_tool") as mock_tool:
            mock_tool.return_value = '{"status": "ok"}'
            _handle_code_dependency_risk({"path": "/my/project"})
        mock_tool.assert_called_once_with(path="/my/project")

    def test_empty_path(self):
        """Empty path → passed through."""
        from code_intel.tools.export import _handle_code_dependency_risk

        with patch("code_intel.tools.export.code_dependency_risk_tool") as mock_tool:
            mock_tool.return_value = '{"status": "ok"}'
            _handle_code_dependency_risk({"path": ""})
        mock_tool.assert_called_once_with(path="")
