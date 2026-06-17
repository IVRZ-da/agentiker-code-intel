"""Tests for code_overview_tool — compact file overview."""

import json
import tempfile
from pathlib import Path

from code_intel.code_intel import code_overview_tool


def _make_file(content: str, name: str = "test.py") -> str:
    tmp = tempfile.mkdtemp()
    f = Path(tmp) / name
    f.write_text(content)
    return str(f)


def _make_dir(files: dict) -> str:
    tmp = tempfile.mkdtemp()
    for name, content in files.items():
        p = Path(tmp) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return str(tmp)


class TestOverviewPython:
    def test_python_file_with_classes(self):
        path = _make_file(
            "class Calculator:\n"
            "    def add(self, a, b):\n"
            "        return a + b\n"
            "\n"
            "    def sub(self, a, b):\n"
            "        return a - b\n"
            "\n"
            "def helper():\n"
            "    return 42\n"
        )
        result = code_overview_tool(path=path)
        assert "Calculator" in result
        assert "helper" in result
        assert "add" in result
        assert "sub" in result
        assert "python" in result.lower()
        assert "class" in result

    def test_depth_0_shows_only_top_level(self):
        path = _make_file(
            "class Foo:\n"
            "    def bar(self):\n"
            "        pass\n"
            "\n"
            "def top():\n"
            "    pass\n"
        )
        result = code_overview_tool(path=path, depth=0)
        assert "Foo" in result
        assert "top" in result
        assert "bar" not in result  # depth=0 hides class members

    def test_empty_file(self):
        path = _make_file("")
        result = code_overview_tool(path=path)
        assert "no symbols" in result.lower()

    def test_single_function(self):
        path = _make_file("def hello():\n    return 'world'\n")
        result = code_overview_tool(path=path)
        assert "hello" in result
        assert "ƒ" in result or "function" in result

    def test_nonexistent_file(self):
        result = code_overview_tool(path="/tmp/does_not_exist_xyz.py")
        data = json.loads(result)
        assert "error" in data

    def test_unsupported_language(self):
        path = _make_file("some content", "file.xyz")
        result = code_overview_tool(path=path)
        data = json.loads(result)
        assert "error" in data

    def test_ts_file(self):
        path = _make_file(
            "interface User {\n"
            "  name: string;\n"
            "  age: number;\n"
            "}\n"
            "\n"
            "function greet(u: User): string {\n"
            '  return \"Hello \" + u.name;\n'
            "}\n",
            name="test.ts",
        )
        result = code_overview_tool(path=path)
        assert "User" in result
        assert "greet" in result
        assert "typescript" in result.lower() or "tsx" in result.lower() or "type" in result.lower()

    def test_directory_scan(self):
        d = _make_dir({
            "mod1.py": "def func_a(): pass\n",
            "sub/mod2.py": "class MyClass:\n    def method(self): pass\n",
        })
        result = code_overview_tool(path=d)
        assert "func_a" in result
        assert "MyClass" in result
        assert "method" in result

    def test_rust_file(self):
        path = _make_file(
            "struct Point {\n"
            "    x: i32,\n"
            "    y: i32,\n"
            "}\n"
            "\n"
            "fn distance(p1: Point, p2: Point) -> f64 {\n"
            "    0.0\n"
            "}\n",
            name="test.rs",
        )
        result = code_overview_tool(path=path)
        assert "Point" in result
        assert "distance" in result
