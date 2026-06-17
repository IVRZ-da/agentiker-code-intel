"""Tests for code_replace_body_tool and code_safe_delete_tool.

These tests use temporary directories and files. They test the AST-based
symbol editing by creating real Python/TypeScript source files and
verifying the tools' output without mocking the file system.
"""

import json
import tempfile
from pathlib import Path

from code_intel.code_intel import (
    code_replace_body_tool,
    code_safe_delete_tool,
    code_insert_before_tool,
    code_insert_after_tool,
    _find_symbol_in_ast,
)


def _make_file(content: str, name: str = "test.py") -> str:
    tmp = tempfile.mkdtemp()
    f = Path(tmp) / name
    f.write_text(content)
    return str(f)


# =========================================================================
# _find_symbol_in_ast helper tests
# =========================================================================


class TestFindSymbolInAst:
    def test_find_function(self):
        path = _make_file("def hello():\n    return 42\n")
        result = _find_symbol_in_ast(path, "hello")
        assert result is not None
        assert result["name"] == "hello"
        assert result["kind"] == "function"
        assert result["start_line"] == 1
        assert result["end_line"] == 2
        assert result["start_byte"] >= 0
        assert result["end_byte"] > result["start_byte"]
        assert "return 42" in result["body"]

    def test_find_class(self):
        path = _make_file("class MyClass:\n    pass\n")
        result = _find_symbol_in_ast(path, "MyClass")
        assert result is not None
        assert result["name"] == "MyClass"
        assert result["kind"] == "class"

    def test_find_method_via_namepath(self):
        path = _make_file(
            "class Calculator:\n"
            "    def add(self, a, b):\n"
            "        return a + b\n"
        )
        result = _find_symbol_in_ast(path, "Calculator/add")
        assert result is not None
        assert result["name"] == "add"
        assert result["kind"] == "method"

    def test_symbol_not_found(self):
        path = _make_file("x = 1\n")
        result = _find_symbol_in_ast(path, "does_not_exist")
        assert result is None

    def test_nonexistent_file(self):
        result = _find_symbol_in_ast("/tmp/nonexistent_file_xyz.py", "foo")
        assert result is None

    def test_unsupported_language(self):
        """Files without supported extension should return None."""
        path = _make_file("some content", "test.xyz")
        result = _find_symbol_in_ast(path, "foo")
        assert result is None


# =========================================================================
# code_replace_body_tool tests
# =========================================================================


class TestReplaceBody:
    def test_replace_function_body(self):
        path = _make_file(
            "def greet():\n"
            '    return "Hello"\n'
        )
        new_body = "def greet():\n    return \"Bonjour\"\n"
        result = code_replace_body_tool(
            path=path, symbol="greet", new_body=new_body, dry_run=False
        )
        data = json.loads(result)
        assert data.get("success") is True
        assert data["symbol"] == "greet"
        assert data["kind"] == "function"
        # Verify file content
        content = Path(path).read_text()
        assert 'return "Bonjour"' in content
        assert 'return "Hello"' not in content

    def test_replace_method(self):
        path = _make_file(
            "class Calc:\n"
            "    def add(self, a, b):\n"
            "        return a + b\n"
        )
        new_body = "    def add(self, a, b):\n        return a * b\n"
        result = code_replace_body_tool(
            path=path, symbol="Calc/add", new_body=new_body, dry_run=False
        )
        data = json.loads(result)
        assert data.get("success") is True
        content = Path(path).read_text()
        assert "return a * b" in content

    def test_replace_class_body(self):
        path = _make_file("class Empty:\n    pass\n")
        new_body = "class Empty:\n    \"\"\"Now has a docstring.\"\"\"\n    pass\n"
        result = code_replace_body_tool(
            path=path, symbol="Empty", new_body=new_body, dry_run=False
        )
        data = json.loads(result)
        assert data.get("success") is True
        content = Path(path).read_text()
        assert "docstring" in content

    def test_dry_run_returns_diff(self):
        path = _make_file("def foo():\n    return 1\n")
        new_body = "def foo():\n    return 42\n"
        result = code_replace_body_tool(
            path=path, symbol="foo", new_body=new_body, dry_run=True
        )
        data = json.loads(result)
        assert data.get("dry_run") is True
        assert "diff" in data
        assert "+    return 42" in data["diff"]
        # File must not be changed
        content = Path(path).read_text()
        assert "return 1" in content

    def test_symbol_not_found_error(self):
        path = _make_file("x = 1\n")
        result = code_replace_body_tool(
            path=path, symbol="does_not_exist", new_body="x = 2", dry_run=False
        )
        data = json.loads(result)
        assert "error" in data
        assert "not found" in data["error"].lower()

    def test_file_not_found_error(self):
        result = code_replace_body_tool(
            path="/tmp/nonexistent_xyz.py", symbol="foo", new_body="",
            dry_run=False,
        )
        data = json.loads(result)
        assert "error" in data
        assert "not found" in data["error"].lower()

    def test_decorated_function_include_decorators(self):
        path = _make_file(
            "@staticmethod\n"
            "def util():\n"
            "    return 1\n"
        )
        new_body = "@staticmethod\ndef util():\n    return 99\n"
        result = code_replace_body_tool(
            path=path, symbol="util", new_body=new_body,
            dry_run=False, include_decorators=True,
        )
        data = json.loads(result)
        assert data.get("success") is True
        content = Path(path).read_text()
        assert "return 99" in content
        assert "@staticmethod" in content

    def test_ts_file_replacement(self):
        path = _make_file(
            "function greet(name: string): string {\n"
            '  return "Hello " + name;\n'
            "}\n",
            name="greet.ts",
        )
        new_body = (
            "function greet(name: string): string {\n"
            '  return "Hi " + name;\n'
            "}\n"
        )
        result = code_replace_body_tool(
            path=path, symbol="greet", new_body=new_body, dry_run=False,
        )
        data = json.loads(result)
        assert data.get("success") is True
        content = Path(path).read_text()
        assert 'return "Hi ' in content


# =========================================================================
# code_safe_delete_tool tests
# =========================================================================


class TestSafeDelete:
    def test_delete_unused_function(self):
        path = _make_file(
            "def unused():\n"
            "    return 42\n"
            "\n"
            "def used():\n"
            "    return 1\n"
        )
        # Delete 'unused' — it has no callers in the file
        result = code_safe_delete_tool(
            path=path, symbol="unused", dry_run=False
        )
        data = json.loads(result)
        assert data.get("success") is True
        assert data["symbol"] == "unused"
        content = Path(path).read_text()
        assert "unused" not in content
        assert "used" in content  # Other symbol preserved

    def test_refuse_delete_referenced(self):
        path = _make_file(
            "def helper():\n"
            "    return 42\n"
            "\n"
            "def caller():\n"
            "    return helper()\n"
        )
        result = code_safe_delete_tool(
            path=path, symbol="helper", dry_run=False
        )
        data = json.loads(result)
        # Should find that helper() is called by caller()
        assert data.get("safe") is False
        assert data.get("references_found", 0) > 0

    def test_force_delete_referenced(self):
        path = _make_file(
            "def helper():\n"
            "    return 42\n"
            "\n"
            "def caller():\n"
            "    return helper()\n"
        )
        result = code_safe_delete_tool(
            path=path, symbol="helper", force=True, dry_run=False
        )
        data = json.loads(result)
        assert data.get("success") is True
        content = Path(path).read_text()
        # Definition removed, but call site (caller) still references it
        assert "def helper" not in content
        assert "def caller" in content

    def test_dry_does_not_delete(self):
        path = _make_file("def foo():\n    return 1\n")
        result = code_safe_delete_tool(
            path=path, symbol="foo", dry_run=True
        )
        data = json.loads(result)
        assert data.get("dry_run") is True
        # File must not be changed
        content = Path(path).read_text()
        assert "def foo" in content

    def test_delete_class(self):
        path = _make_file(
            "class TempHelper:\n"
            "    def run(self):\n"
            "        pass\n"
            "\n"
            "class RealClass:\n"
            "    pass\n"
        )
        result = code_safe_delete_tool(
            path=path, symbol="TempHelper", dry_run=False
        )
        data = json.loads(result)
        assert data.get("success") is True
        content = Path(path).read_text()
        assert "TempHelper" not in content
        assert "RealClass" in content

    def test_delete_from_ts_file(self):
        path = _make_file(
            "function helper(): number {\n"
            "  return 42;\n"
            "}\n"
            "\n"
            "function main(): number {\n"
            "  return 1;\n"
            "}\n",
            name="test.ts",
        )
        result = code_safe_delete_tool(
            path=path, symbol="helper", dry_run=False
        )
        data = json.loads(result)
        assert data.get("success") is True
        content = Path(path).read_text()
        assert "helper" not in content
        assert "main" in content

    def test_delete_method_via_namepath(self):
        path = _make_file(
            "class Worker:\n"
            "    def _internal(self):\n"
            "        return 1\n"
            "\n"
            "    def run(self):\n"
            "        return self._internal()\n"
        )
        # _internal is referenced by run
        result = code_safe_delete_tool(
            path=path, symbol="Worker/_internal", dry_run=False
        )
        data = json.loads(result)
        assert data.get("safe") is False
        assert data.get("references_found", 0) > 0

    def test_symbol_not_found(self):
        path = _make_file("x = 1\n")
        result = code_safe_delete_tool(
            path=path, symbol="does_not_exist", dry_run=False
        )
        data = json.loads(result)
        assert "error" in data

# =========================================================================
# code_insert_before_tool tests
# =========================================================================


class TestInsertBefore:
    def test_insert_before_function(self):
        path = _make_file(
            "def existing():\n"
            "    return 1\n"
        )
        result = code_insert_before_tool(
            path=path, symbol="existing",
            code="def new_func():\n    return 0",
            dry_run=False,
        )
        data = json.loads(result)
        assert data.get("success") is True
        content = Path(path).read_text()
        assert "def new_func" in content
        assert "def existing" in content
        # new_func must come before existing
        assert content.index("def new_func") < content.index("def existing")

    def test_insert_before_dry_run(self):
        path = _make_file("def foo():\n    return 1\n")
        result = code_insert_before_tool(
            path=path, symbol="foo",
            code="def bar():\n    return 0",
            dry_run=True,
        )
        data = json.loads(result)
        assert data.get("dry_run") is True
        # File unchanged
        content = Path(path).read_text()
        assert "def foo" in content
        assert "def bar" not in content

    def test_insert_before_with_newline_false(self):
        path = _make_file("def foo():\n    return 1\n")
        result = code_insert_before_tool(
            path=path, symbol="foo",
            code="# comment",
            dry_run=False,
            newline=False,
        )
        data = json.loads(result)
        assert data.get("success") is True
        content = Path(path).read_text()
        assert "# comment" in content

    def test_insert_before_first_symbol(self):
        """Insert before the ONLY symbol in a file should work."""
        path = _make_file("def only_one():\n    pass\n")
        result = code_insert_before_tool(
            path=path, symbol="only_one",
            code="def first():\n    pass",
            dry_run=False,
        )
        data = json.loads(result)
        assert data.get("success") is True
        content = Path(path).read_text()
        assert "def first" in content

    def test_insert_before_symbol_not_found(self):
        path = _make_file("x = 1\n")
        result = code_insert_before_tool(
            path=path, symbol="does_not_exist",
            code="x = 2",
            dry_run=False,
        )
        data = json.loads(result)
        assert "error" in data

    def test_insert_before_method_via_namepath(self):
        path = _make_file(
            "class Calc:\n"
            "    def sub(self, a, b):\n"
            "        return a - b\n"
            "    def add(self, a, b):\n"
            "        return a + b\n"
        )
        result = code_insert_before_tool(
            path=path, symbol="Calc/add",
            code="# before add",
            dry_run=False,
            newline=True,
        )
        data = json.loads(result)
        assert data.get("success") is True
        content = Path(path).read_text()
        assert "# before add" in content
        # The comment must be BEFORE the add method
        add_idx = content.index("def add")
        comment_idx = content.index("# before add")
        assert comment_idx < add_idx

    def test_insert_before_ts_file(self):
        path = _make_file(
            "function main(): void {\n"
            "  console.log('hello');\n"
            "}\n",
            name="test.ts",
        )
        result = code_insert_before_tool(
            path=path, symbol="main",
            code="function helper(): void {\n  return;\n}",
            dry_run=False,
        )
        data = json.loads(result)
        assert data.get("success") is True
        content = Path(path).read_text()
        assert "function helper" in content
        assert content.index("function helper") < content.index("function main")


# =========================================================================
# code_insert_after_tool tests
# =========================================================================


class TestInsertAfter:
    def test_insert_after_function(self):
        path = _make_file(
            "def existing():\n"
            "    return 1\n"
        )
        result = code_insert_after_tool(
            path=path, symbol="existing",
            code="def new_func():\n    return 0",
            dry_run=False,
        )
        data = json.loads(result)
        assert data.get("success") is True
        content = Path(path).read_text()
        assert "def new_func" in content
        # new_func must come after existing
        assert content.index("def existing") < content.index("def new_func")

    def test_insert_after_dry_run(self):
        path = _make_file("def foo():\n    return 1\n")
        result = code_insert_after_tool(
            path=path, symbol="foo",
            code="def bar():\n    return 0",
            dry_run=True,
        )
        data = json.loads(result)
        assert data.get("dry_run") is True
        content = Path(path).read_text()
        assert "def bar" not in content

    def test_insert_after_class(self):
        path = _make_file(
            "class First:\n"
            "    pass\n"
        )
        result = code_insert_after_tool(
            path=path, symbol="First",
            code="class Second:\n    pass",
            dry_run=False,
        )
        data = json.loads(result)
        assert data.get("success") is True
        content = Path(path).read_text()
        assert "class Second" in content
        assert content.index("class First") < content.index("class Second")

    def test_insert_after_with_newline_false(self):
        path = _make_file("def foo():\n    return 1\n\ndef bar():\n    return 2\n")
        # Insert between foo and bar
        result = code_insert_after_tool(
            path=path, symbol="foo",
            code="# between",
            dry_run=False, newline=False,
        )
        data = json.loads(result)
        assert data.get("success") is True
        content = Path(path).read_text()
        assert "# between" in content

    def test_insert_after_symbol_not_found(self):
        path = _make_file("x = 1\n")
        result = code_insert_after_tool(
            path=path, symbol="does_not_exist",
            code="x = 2",
            dry_run=False,
        )
        data = json.loads(result)
        assert "error" in data

    def test_insert_after_method_via_namepath(self):
        path = _make_file(
            "class Calc:\n"
            "    def add(self, a, b):\n"
            "        return a + b\n"
        )
        result = code_insert_after_tool(
            path=path, symbol="Calc/add",
            code="    def sub(self, a, b):\n        return a - b",
            dry_run=False,
        )
        data = json.loads(result)
        assert data.get("success") is True
        content = Path(path).read_text()
        assert "def sub" in content
        # sub must come after add
        assert content.index("def add") < content.index("def sub")

    def test_insert_after_ts_file(self):
        path = _make_file(
            "function helper(): void {\n"
            "  return;\n"
            "}\n",
            name="test.ts",
        )
        result = code_insert_after_tool(
            path=path, symbol="helper",
            code="function main(): void {\n  console.log('hi');\n}",
            dry_run=False,
        )
        data = json.loads(result)
        assert data.get("success") is True
        content = Path(path).read_text()
        assert "function main" in content
        assert content.index("function helper") < content.index("function main")
