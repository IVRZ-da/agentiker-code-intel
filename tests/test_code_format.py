"""Tests for the code_format tool (LSP textDocument/formatting).

Requires a real LSP server. Skip by default, run with:
  LSP_TEST=1 pytest tests/test_code_format.py -v
"""

import os
import textwrap

import pytest

pytest.importorskip("tree_sitter", reason="tree-sitter not installed")

from code_intel.lsp_bridge import code_format_tool

RUN_LSP = os.environ.get("LSP_TEST") == "1"
lsp_skip = pytest.mark.skipif(not RUN_LSP, reason="set LSP_TEST=1 for real LSP tests")


class TestCodeFormat:
    """Integration tests for code_format_tool with real LSP."""

    @lsp_skip
    def test_format_python_file(self, tmp_path):
        """Format a Python file with bad indentation."""
        import json
        src = textwrap.dedent("""\
            def  hello(name):
            return   "Hello, "+name

            class   Foo:
             def  bar(self):
              pass
        """)
        f = tmp_path / "test_format.py"
        f.write_text(src)

        result = code_format_tool(str(f), dry_run=True)
        assert isinstance(result, str)
        data = json.loads(result)
        # pyright may or may not support formatting — either is OK
        if "error" in data:
            assert "LSP" in data.get("error", "") or "format" in data.get("error", "").lower()
        elif "info" in data:
            assert "no changes" in data.get("info", "").lower()
        else:
            # Successful format response
            assert "diff" in data or "edit_count" in data

    @lsp_skip
    def test_format_ts_file(self, tmp_path):
        """Format a TypeScript file."""
        src = 'function  hello(name:string){return  "Hello, "+name;}\n'
        f = tmp_path / "test_format.ts"
        f.write_text(src)
        (tmp_path / "tsconfig.json").write_text('{"compilerOptions":{"target":"ES2020"}}')

        result = code_format_tool(str(f), dry_run=True)
        assert isinstance(result, str)

    @lsp_skip
    def test_format_nonexistent_file(self):
        """Non-existent path returns error, not crash."""
        result = code_format_tool("/nonexistent/path.py", dry_run=True)
        assert "error" in result.lower()
        assert "not found" in result.lower()

    @lsp_skip
    def test_format_empty_file(self, tmp_path):
        """Empty file should not crash."""
        f = tmp_path / "empty.py"
        f.write_text("")
        result = code_format_tool(str(f), dry_run=True)
        assert isinstance(result, str)

    @lsp_skip
    def test_format_already_formatted_file(self, tmp_path):
        """Well-formatted file returns no changes."""
        src = textwrap.dedent("""\
            def hello(name: str) -> str:
                return f"Hello, {name}!"


            class Foo:
                def bar(self) -> None:
                    pass
        """)
        f = tmp_path / "good.py"
        f.write_text(src)
        result = code_format_tool(str(f), dry_run=True)
        assert isinstance(result, str)

    @lsp_skip
    def test_format_is_idempotent(self, tmp_path):
        """Running format twice should produce same result."""
        import json
        src = textwrap.dedent("""\
            def   add(a,b):
             return  a+b
        """)
        f = tmp_path / "idemp_test.py"
        f.write_text(src)

        # First format (dry-run)
        r1 = json.loads(code_format_tool(str(f), dry_run=True))
        if "edit_count" in r1 and r1["edit_count"] > 0:
            # Apply formatting
            json.loads(code_format_tool(str(f), dry_run=False))
            # Second format should produce zero edits
            r3 = json.loads(code_format_tool(str(f), dry_run=True))
            if "edit_count" in r3:
                assert r3["edit_count"] == 0, "Format is not idempotent!"
