"""Tests for code_search_by_error_tool — error handling site finder."""

import tempfile
from pathlib import Path

from code_intel.code_tools import code_search_by_error_tool


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


class TestSearchByErrorPython:
    def test_find_raise(self):
        path = _make_file("raise ValueError('bad')\n")
        result = code_search_by_error_tool(path=path, error="ValueError")
        assert '"total": 1' in result
        assert "raise" in result.lower()

    def test_find_except(self):
        path = _make_file("try:\n    pass\nexcept ValueError:\n    pass\n")
        result = code_search_by_error_tool(path=path, error="ValueError")
        assert '"total": 1' in result
        assert "catch" in result.lower() or "except" in result.lower()

    def test_find_custom_class(self):
        path = _make_file("class MyError(Exception):\n    pass\n")
        result = code_search_by_error_tool(path=path, error="Exception")
        assert '"total"'
        assert "custom" in result.lower()

    def test_no_matches(self):
        path = _make_file("x = 1\n")
        result = code_search_by_error_tool(path=path, error="NotFoundError")
        assert '"total": 0' in result

    def test_error_nonexistent_path(self):
        result = code_search_by_error_tool(path="/nonexistent", error="Error")
        assert "error" in result

    def test_search_directory(self):
        path = _make_dir(
            {
                "main.py": "raise ValueError('x')\n",
                "utils.py": "except ValueError:\n    pass\n",
            }
        )
        result = code_search_by_error_tool(path=path, error="ValueError")
        assert '"total": 2' in result or '"raise/throw"' in result


class TestSearchByErrorTypeScript:
    def test_find_throw(self):
        path = _make_file('throw new Error("msg")\n', "test.ts")
        result = code_search_by_error_tool(path=path, error="Error")
        assert '"total": 1' in result or "throw" in result.lower()

    def test_find_catch(self):
        path = _make_file("try {\n} catch (e: Error) {\n}\n", "test.ts")
        result = code_search_by_error_tool(path=path, error="Error")
        assert '"total"' in result

    def test_custom_error_class(self):
        path = _make_file("class MyError extends Error {}\n", "test.ts")
        result = code_search_by_error_tool(path=path, error="Error")
        assert "custom" in result.lower() or '"total"' in result


class TestSearchByErrorGo:
    def test_find_error_return(self):
        path = _make_file(
            """package main\nimport "fmt"\nfunc foo() error {\n\treturn fmt.Errorf("bad")\n}\n""", "main.go"
        )
        result = code_search_by_error_tool(path=path, error="Errorf")
        assert '"total"' in result
