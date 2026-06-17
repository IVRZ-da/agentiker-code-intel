"""Tests for code_implementations_tool (LSP textDocument/implementation).

Skip by default. Run with:
  LSP_TEST=1 pytest tests/test_code_implementations.py -v
"""

import os
import textwrap

import pytest

pytest.importorskip("tree_sitter", reason="tree-sitter not installed")

from code_intel.lsp_bridge import code_implementations_tool

RUN_LSP = os.environ.get("LSP_TEST") == "1"
lsp_skip = pytest.mark.skipif(not RUN_LSP, reason="set LSP_TEST=1 for real LSP tests")


class TestCodeImplementations:
    """Integration tests for code_implementations_tool."""

    @lsp_skip
    def test_implementations_on_interface(self, tmp_path):
        """Find implementations of an interface."""
        src = textwrap.dedent("""\
            from typing import Protocol

            class Animal(Protocol):
                def speak(self) -> str: ...

            class Dog:
                def speak(self) -> str:
                    return "Woof!"

            class Cat:
                def speak(self) -> str:
                    return "Meow!"
        """)
        f = tmp_path / "animals.py"
        f.write_text(src)
        # On 'Animal' class name (line 3, character 6)
        result = code_implementations_tool(str(f), line=3, character=6)
        assert isinstance(result, str)

    @lsp_skip
    def test_implementations_nonexistent_file(self):
        """Non-existent path returns error."""
        result = code_implementations_tool("/no/such/file.py", line=1)
        assert "error" in result.lower()
        assert "not found" in result.lower()

    @lsp_skip
    def test_implementations_empty_file(self, tmp_path):
        """Empty file should not crash."""
        f = tmp_path / "empty.py"
        f.write_text("")
        result = code_implementations_tool(str(f), line=1)
        assert isinstance(result, str)

    @lsp_skip
    def test_implementations_on_function(self, tmp_path):
        """Find implementations — may return empty."""
        src = textwrap.dedent("""\
            def add(a: int, b: int) -> int:
                return a + b
        """)
        f = tmp_path / "math.py"
        f.write_text(src)
        result = code_implementations_tool(str(f), line=1, character=4)
        assert isinstance(result, str)
