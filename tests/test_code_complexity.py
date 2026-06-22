"""Tests for code_complexity_tool — cyclomatic complexity analysis."""

import tempfile
from pathlib import Path

from code_intel.code_tools import code_complexity_tool


def _make_py_file(content: str) -> str:
    tmp = tempfile.mkdtemp()
    f = Path(tmp) / "test.py"
    f.write_text(content)
    return str(f)


class TestCodeComplexity:
    def test_simple_function_returns_rank_a(self):
        path = _make_py_file("def foo():\n    pass\n")
        result = code_complexity_tool(path=path)
        assert '"rank": "A"' in result
        assert '"total": 1' in result

    def test_if_else_increases_complexity(self):
        path = _make_py_file("def foo(x):\n    if x > 0:\n        return 1\n    elif x < 0:\n        return -1\n    return 0\n")
        result = code_complexity_tool(path=path)
        # 1 (base) + 2 (if/elif) = C=3
        import json
        data = json.loads(result)
        assert data["total"] >= 3
        assert data["function"] == "foo"

    def test_loop_increases_complexity(self):
        path = _make_py_file("def foo(items):\n    for item in items:\n        print(item)\n")
        result = code_complexity_tool(path=path)
        import json
        data = json.loads(result)
        assert data["breakdown"]["loops"] >= 1

    def test_exception_handling_counted(self):
        path = _make_py_file("def foo():\n    try:\n        bar()\n    except ValueError:\n        pass\n")
        result = code_complexity_tool(path=path)
        import json
        data = json.loads(result)
        assert data["breakdown"]["exceptions"] >= 1

    def test_error_for_nonexistent_path(self):
        result = code_complexity_tool(path="/nonexistent/file.py")
        assert "error" in result

    def test_function_by_name(self):
        path = _make_py_file("def bar():\n    pass\ndef baz():\n    pass\n")
        result = code_complexity_tool(path=path, function="baz")
        import json
        data = json.loads(result)
        assert data["function"] == "baz"

    def test_function_by_line(self):
        path = _make_py_file("def foo():\n    pass\ndef bar():\n    pass\n")
        result = code_complexity_tool(path=path, line=3)
        import json
        data = json.loads(result)
        assert data["function"] == "bar"

    def test_empty_file_returns_error(self):
        path = _make_py_file("")
        result = code_complexity_tool(path=path)
        assert "error" in result

    def test_high_complexity_rank(self):
        path = _make_py_file("def foo(x):\n" + "    if x == 1:\n        pass\n" * 20)
        result = code_complexity_tool(path=path)
        import json
        data = json.loads(result)
        assert data["total"] >= 20
        assert data["rank"] in ("C", "D", "E")
