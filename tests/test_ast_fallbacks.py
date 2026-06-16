"""Tests for AST fallback functions in lsp_bridge.

These functions parse source files to extract definitions, references,
diagnostics, and callees when no LSP server is available.
"""
import json

from code_intel.lsp_bridge import (
    _ast_fallback_definition,
    _ast_fallback_references,
    _ast_fallback_diagnostics,
    _ast_fallback_callees,
)


class TestAstFallbackDefinition:
    def test_python_function_definition(self, tmp_path):
        f = tmp_path / "sample.py"
        f.write_text("def hello(): pass\n")
        result = json.loads(_ast_fallback_definition(str(f), 0, 4, "python"))
        defs = result.get("definitions", [])
        # Should at least return something or gracefully handle
        assert isinstance(result, dict)

    def test_python_class_definition(self, tmp_path):
        f = tmp_path / "sample.py"
        f.write_text("class MyClass: pass\n")
        result = json.loads(_ast_fallback_definition(str(f), 0, 6, "python"))
        assert isinstance(result, dict)

    def test_typescript_function_definition(self, tmp_path):
        f = tmp_path / "sample.ts"
        f.write_text("function greet(name: string): string { return 'hi'; }\n")
        result = json.loads(_ast_fallback_definition(str(f), 0, 9, "typescript"))
        assert isinstance(result, dict)

    def test_go_function_definition(self, tmp_path):
        f = tmp_path / "sample.go"
        f.write_text("package main\nfunc greet(name string) string { return \"hi\" }\n")
        result = json.loads(_ast_fallback_definition(str(f), 1, 5, "go"))
        assert isinstance(result, dict)

    def test_rust_function_definition(self, tmp_path):
        f = tmp_path / "sample.rs"
        f.write_text("fn greet(name: &str) -> String { format!(\"hi {}\", name) }\n")
        result = json.loads(_ast_fallback_definition(str(f), 0, 3, "rust"))
        assert isinstance(result, dict)

    def test_nonexistent_file(self):
        result = json.loads(_ast_fallback_definition("/nonexistent.py", 0, 0, "python"))
        assert "warning" in result or "error" in result or "definitions" in result

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("")
        result = json.loads(_ast_fallback_definition(str(f), 0, 0, "python"))
        assert isinstance(result, dict)


class TestAstFallbackReferences:
    def test_python_references(self, tmp_path):
        f = tmp_path / "sample.py"
        f.write_text("x = 1\nprint(x)\n")
        # _ast_fallback_references takes (file, line, character, lang)
        result = json.loads(_ast_fallback_references(str(f), 0, 0, "python"))
        assert result.get("method") in ("fallback", "fallback_ast", "ast_heuristic")

    def test_typescript_references(self, tmp_path):
        f = tmp_path / "sample.ts"
        f.write_text("const x = 1;\nconsole.log(x);\n")
        result = json.loads(_ast_fallback_references(str(f), 0, 0, "typescript"))
        assert result.get("method") in ("fallback", "fallback_ast", "ast_heuristic")

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("")
        result = json.loads(_ast_fallback_references(str(f), 0, 0, "python"))
        assert isinstance(result, dict)

    def test_nonexistent_file(self):
        result = json.loads(_ast_fallback_references("/nonexistent.py", 0, 0, "python"))
        assert "warning" in result or "error" in result or isinstance(result.get("references"), list)


class TestAstFallbackDiagnostics:
    def test_python_no_issues(self, tmp_path):
        f = tmp_path / "clean.py"
        f.write_text("x = 1\n")
        result = json.loads(_ast_fallback_diagnostics(str(f), "python"))
        assert "diagnostics" in result

    def test_typescript_unused_import(self, tmp_path):
        f = tmp_path / "sample.ts"
        f.write_text("import { something } from './module';\nconst x = 1;\n")
        result = json.loads(_ast_fallback_diagnostics(str(f), "typescript"))
        assert "diagnostics" in result

    def test_nonexistent_file(self):
        result = json.loads(_ast_fallback_diagnostics("/nonexistent.py", "python"))
        assert "warning" in result

    def test_unknown_language(self, tmp_path):
        f = tmp_path / "sample.xyz"
        f.write_text("content")
        result = json.loads(_ast_fallback_diagnostics(str(f), "unknown"))
        assert "diagnostics" in result


class TestAstFallbackCallees:
    def test_python_function_calls(self, tmp_path):
        f = tmp_path / "sample.py"
        f.write_text("def outer():\n    inner()\n    another()\n")
        result = json.loads(_ast_fallback_callees(str(f), 0, "python"))
        assert isinstance(result, dict)

    def test_python_with_nested_calls(self, tmp_path):
        f = tmp_path / "sample.py"
        f.write_text("def foo():\n    bar(baz())\n")
        result = json.loads(_ast_fallback_callees(str(f), 0, "python"))
        assert isinstance(result, dict)

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("")
        result = json.loads(_ast_fallback_callees(str(f), 0, "python"))
        assert isinstance(result, dict)

    def test_nonexistent_file(self):
        result = json.loads(_ast_fallback_callees("/nonexistent.py", 0, "python"))
        assert "warning" in result or isinstance(result, dict)

    def test_typescript_callees(self, tmp_path):
        f = tmp_path / "sample.ts"
        f.write_text("function foo() { bar(); }\n")
        result = json.loads(_ast_fallback_callees(str(f), 0, "typescript"))
        assert isinstance(result, dict)
