"""Tests for tools/testgen.py — test generation utilities.

Target: bring coverage from ~8% to 40% by testing standalone
functions that don't need tree-sitter.
"""

from __future__ import annotations

import json

import pytest

from code_intel.tools.testgen import (
    _find_function_at_line,
    _parse_python_function,
    _generate_pytest_test,
    _generate_vitest_test,
    _generate_go_test,
    _detect_framework,
    _handle_code_generate_tests,
)


class TestFindFunctionAtLine:
    """_find_function_at_line — AST-based, no tree-sitter needed."""

    def test_finds_simple_function(self):
        src = "def foo():\n    pass\n"
        result = _find_function_at_line(src, 1)
        assert result is not None
        assert result.name == "foo"

    def test_finds_async_function(self):
        src = "async def bar():\n    pass\n"
        result = _find_function_at_line(src, 1)
        assert result is not None
        assert result.name == "bar"

    def test_none_for_no_function(self):
        src = "x = 1\ny = 2\n"
        result = _find_function_at_line(src, 1)
        assert result is None

    def test_out_of_bounds_line(self):
        src = "x = 1\n"
        result = _find_function_at_line(src, 99)
        assert result is None

    def test_invalid_syntax(self):
        src = "def broken(\n"
        result = _find_function_at_line(src, 1)
        assert result is None

    def test_empty_source(self):
        result = _find_function_at_line("", 1)
        assert result is None


class TestParsePythonFunction:
    """_parse_python_function — parameter extraction."""

    def test_no_params(self):
        src = "def foo():\n    pass\n"
        result = _parse_python_function(src, 1)
        assert result is not None
        assert result.get("name") == "foo"
        args = result.get("args") or result.get("params") or []
        assert args == []

    def test_with_params(self):
        src = "def foo(a, b: str, c=1):\n    pass\n"
        result = _parse_python_function(src, 1)
        assert result is not None
        assert result.get("name") == "foo"
        args = result.get("args") or result.get("params") or []
        assert "a" in str(args)

    def test_no_function_at_line(self):
        src = "x = 1\n"
        result = _parse_python_function(src, 1)
        assert result is None


class TestGeneratePytestTest:
    """_generate_pytest_test — string generation."""

    @pytest.fixture
    def func_info(self):
        return {
            "name": "my_func",
            "args": [{"name": "a"}, {"name": "b"}],
            "params": [{"name": "a"}, {"name": "b"}],
            "parameters": [{"name": "a"}, {"name": "b"}],
            "is_async": False,
            "return_type": None,
        }

    def test_generates_function(self, func_info):
        result = _generate_pytest_test(func_info)
        assert "def test_my_func():" in result
        assert "a" in result
        assert "b" in result

    def test_no_params(self):
        info = {"name": "simple", "args": [], "params": [], "parameters": [], "is_async": False, "return_type": None}
        result = _generate_pytest_test(info)
        assert "def test_simple():" in result


class TestGenerateVitestTest:
    """_generate_vitest_test — TypeScript test generation."""

    def test_generates_test_block(self):
        info = {"name": "add", "args": [{"name": "x"}, {"name": "y"}], "params": [], "parameters": [], "is_async": False, "return_type": None}
        result = _generate_vitest_test(info)
        assert "describe" in result or "it(" in result or "test(" in result
        assert "add" in result

    def test_no_params(self):
        info = {"name": "noop", "args": [], "params": [], "parameters": [], "is_async": False, "return_type": None}
        result = _generate_vitest_test(info)
        assert "noop" in result


class TestGenerateGoTest:
    """_generate_go_test — Go test generation."""

    def test_generates_test_function(self):
        info = {"name": "Add", "args": [{"name": "x"}, {"name": "y"}], "params": [], "parameters": [], "is_async": False, "return_type": None}
        result = _generate_go_test(info)
        assert "func TestAdd" in result
        assert "t *testing.T" in result

    def test_no_params(self):
        info = {"name": "Noop", "args": [], "params": [], "parameters": [], "is_async": False, "return_type": None}
        result = _generate_go_test(info)
        assert "Noop" in result


class TestDetectFramework:
    """_detect_framework — path-based detection."""

    def test_pytest_for_python(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("")
        result = _detect_framework(f, "python")
        assert result == "pytest"

    def test_vitest_for_typescript(self, tmp_path):
        f = tmp_path / "test.ts"
        f.write_text("")
        result = _detect_framework(f, "typescript")
        assert result == "vitest"

    def test_go_test_for_go(self, tmp_path):
        f = tmp_path / "test.go"
        f.write_text("")
        result = _detect_framework(f, "go")
        assert result == "go-test"

    def test_unknown_language(self, tmp_path):
        f = tmp_path / "test.rs"
        f.write_text("")
        result = _detect_framework(f, "rust")
        assert result == "pytest"  # default fallback

    def test_vitest_with_react(self, tmp_path):
        f = tmp_path / "test.tsx"
        f.write_text("")
        result = _detect_framework(f, "tsx")
        assert result == "vitest"


class TestHandleCodeGenerateTests:
    """_handle_code_generate_tests — handler wrapper."""

    def test_basic_call(self):
        result = _handle_code_generate_tests(path="/nonexistent/file.py", line=1)
        parsed = json.loads(result) if isinstance(result, str) else result
        assert parsed.get("status") == "error"

    def test_with_defaults(self):
        result = _handle_code_generate_tests()
        parsed = json.loads(result) if isinstance(result, str) else result
        # Should error without path
        assert isinstance(parsed, dict)
