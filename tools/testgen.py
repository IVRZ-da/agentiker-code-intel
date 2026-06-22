"""tools/testgen.py — Unit test scaffolding generator.

Provides code_generate_tests_tool, CODE_GENERATE_TESTS_SCHEMA, and
_handle_code_generate_tests for generating test templates from source
symbol signatures via AST analysis.

Supports:
- Python → pytest (with async support)
- TypeScript/JavaScript → vitest/jest
- Go → testing (go-test)
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import List, Optional

from .._fmt import fmt_err  # noqa: E402
from ..tools.base import _get_parser, detect_language

# ── Python AST helpers ────────────────────────────────────────────────────


def _find_function_at_line(source: str, line: int) -> Optional[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Find a function definition in Python AST at the given 1-based line."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
            if node.lineno <= line <= end_line:
                return node
    return None


def _parse_python_function(source: str, line: int) -> Optional[dict]:
    """Parse a Python function definition and return signature info.

    Returns a dict with keys: name, is_async, args, return_type.
    Each arg is a dict with name, annotation, and optional default.
    """
    func = _find_function_at_line(source, line)
    if func is None:
        return None

    args = []
    defaults = func.args.defaults if hasattr(func, "args") else []
    num_no_default = len(func.args.args) - len(defaults)

    for i, arg in enumerate(func.args.args):
        arg_info: dict = {
            "name": arg.arg,
            "annotation": ast.unparse(arg.annotation) if arg.annotation else None,
        }
        if i >= num_no_default:
            idx = i - num_no_default
            try:
                arg_info["default"] = ast.unparse(defaults[idx])
            except (IndexError, ValueError):
                pass
        args.append(arg_info)

    if func.args.vararg:
        args.append({
            "name": f"*{func.args.vararg.arg}",
            "annotation": (
                ast.unparse(func.args.vararg.annotation)
                if func.args.vararg.annotation
                else None
            ),
        })

    for arg in func.args.kwonlyargs:
        args.append({
            "name": arg.arg,
            "annotation": ast.unparse(arg.annotation) if arg.annotation else None,
        })

    if func.args.kwarg:
        args.append({
            "name": f"**{func.args.kwarg.arg}",
            "annotation": (
                ast.unparse(func.args.kwarg.annotation)
                if func.args.kwarg.annotation
                else None
            ),
        })

    return {
        "name": func.name,
        "is_async": isinstance(func, ast.AsyncFunctionDef),
        "args": args,
        "return_type": ast.unparse(func.returns) if func.returns else None,
    }


# ── Tree-sitter helpers (non-Python languages) ────────────────────────────


def _find_ts_function_node(node, target_line: int):
    """Recursively find a function declaration node containing target_line.

    Walks the tree-sitter AST tree searching for function-like nodes
    that enclose the given 1-based line number.
    """
    if node is None:
        return None
    start_row = node.start_point[0] + 1
    end_row = node.end_point[0] + 1

    if not (start_row <= target_line <= end_row):
        return None

    node_type = node.type if hasattr(node, "type") else ""
    if node_type in (
        "function_declaration",
        "function_definition",
        "method_declaration",
        "function_item",
        "method_definition",
        "arrow_function",
    ):
        return node

    for child in node.children if hasattr(node, "children") else []:
        result = _find_ts_function_node(child, target_line)
        if result:
            return result
    return None


def _extract_ts_params(node, source: str) -> List[dict]:
    """Extract parameter names from a tree-sitter function node."""
    params = []
    for child in node.children if hasattr(node, "children") else []:
        child_type = child.type if hasattr(child, "type") else ""
        if child_type in ("parameters", "formal_parameters", "parameter_list"):
            for param in child.children if hasattr(child, "children") else []:
                pt = param.type if hasattr(param, "type") else ""
                if pt in (
                    "parameter",
                    "required_parameter",
                    "optional_parameter",
                    "identifier",
                    "spread_element",
                    "pattern",
                ):
                    try:
                        param_text = source[param.start_byte : param.end_byte]
                        # Extract just the name (before :type or =default)
                        pname = param_text.split(":")[0].split("=")[0].strip()
                        params.append({"name": pname})
                    except Exception:
                        pass
    return params


def _parse_function_via_tree_sitter(path: Path, line: int, lang_key: str) -> Optional[dict]:
    """Parse a function using tree-sitter (non-Python languages).

    Returns the same shape as _parse_python_function.
    """
    parser = _get_parser(lang_key)
    if parser is None:
        return None

    source = path.read_text("utf-8", errors="replace")
    tree = parser.parse(bytes(source, "utf-8"))
    root_node = tree.root_node

    func_node = _find_ts_function_node(root_node, line)
    if func_node is None:
        return None

    name = ""
    for child in func_node.children if hasattr(func_node, "children") else []:
        child_type = child.type if hasattr(child, "type") else ""
        if child_type in ("name",) and hasattr(child, "text"):
            try:
                name = child.text.decode("utf-8")
                break
            except Exception:
                pass
        if child_type in ("identifier", "field_identifier", "property_identifier"):
            try:
                name = child.text.decode("utf-8")
                break
            except Exception:
                pass

    params = _extract_ts_params(func_node, source)

    # Extract return type from signature line
    return_type = None
    if func_node.start_point:
        source_lines = source.split("\n")
        sig_line = source_lines[func_node.start_point[0]]
        if lang_key in ("typescript", "tsx", "javascript"):
            # function name(params): ReturnType
            m = re.search(r"\)\s*:\s*(\S[^{=]*?)(?:\s*=>|\s*{|\s*;|$)", sig_line)
            if m:
                return_type = m.group(1).strip().rstrip("{").strip()
        elif lang_key == "go":
            m = re.search(r"\)\s+(\S[^{]*?)\s*{", sig_line)
            if m:
                return_type = m.group(1).strip()

    return {
        "name": name or f"func_at_line_{line}",
        "is_async": False,
        "args": params,
        "return_type": return_type,
    }


# ── Test framework generators ─────────────────────────────────────────────


def _generate_pytest_test(func_info: dict) -> str:
    """Generate a pytest test function from parsed function info."""
    name = func_info["name"]
    is_async = func_info["is_async"]
    args = func_info["args"]
    return_type = func_info["return_type"]

    test_name = f"test_{name}"

    parts = []

    if is_async:
        parts.append("import pytest\n")

    parts.append("")
    if is_async:
        parts.append("@pytest.mark.asyncio")
    parts.append(f"def {test_name}():")
    parts.append('    """Test {0}.'.format(name))
    parts.append("")
    parts.append("    Assumptions:")
    parts.append("        TODO: document test assumptions")
    parts.append("")
    parts.append('    """')

    # Arrange
    parts.append("    # Arrange")
    skip_names = {"self", "cls", "ctx", "request"}
    for arg in args:
        raw = arg["name"]
        # Strip leading */** for display
        clean = raw.lstrip("*")
        if raw in skip_names or clean in skip_names:
            continue
        annot = f": {arg['annotation']}" if arg.get("annotation") else ""
        default = f" = {arg['default']}" if arg.get("default") else ""
        parts.append(f"    # {raw}{annot}{default} = ...")

    parts.append(f"    # sut = {name}(...)")
    parts.append("")

    # Act
    parts.append("    # Act")
    prefix = "    # result = await " if is_async else "    # result = "
    if return_type:
        parts.append(f"{prefix}sut")
    else:
        parts.append("    # await sut" if is_async else "    # sut()")
    parts.append("")

    # Assert
    parts.append("    # Assert")
    parts.append("    # assert result is not None  # TODO: verify behavior")

    return "\n".join(parts)


def _generate_vitest_test(func_info: dict) -> str:
    """Generate a vitest/jest test block from parsed function info."""
    name = func_info["name"]
    is_async = func_info["is_async"]
    args = func_info["args"]
    return_type = func_info["return_type"]

    lines = []
    lines.append("import { describe, it, expect } from 'vitest';")
    lines.append("import { " + name + " } from './" + name + "';")
    lines.append("")
    lines.append("describe('" + name + "', () => {")

    async_prefix = "async " if is_async else ""
    lines.append(f"  it('should {name} correctly', {async_prefix}() => {{")
    lines.append("    // Arrange")

    for arg in args:
        raw = arg["name"]
        if raw in ("self", "this"):
            continue
        lines.append(f"    // const {raw} = ...;")

    lines.append(f"    // const sut = {name}(...);")
    lines.append("")
    lines.append("    // Act")
    if return_type:
        assign = "const result = await " if is_async else "const result = "
        lines.append(f"    {assign}sut;")
    else:
        call = "await sut;" if is_async else "sut;"
        lines.append(f"    {call}")
    lines.append("")
    lines.append("    // Assert")
    if return_type:
        lines.append("    // expect(result).toBeDefined();")
        lines.append("    // expect(result).toEqual(/* expected */);")
    else:
        lines.append("    // expect(sut).toBeDefined();")

    lines.append("  });")
    lines.append("});")

    return "\n".join(lines)


def _generate_go_test(func_info: dict) -> str:
    """Generate a Go test function from parsed function info."""
    name = func_info["name"]
    args = func_info["args"]
    return_type = func_info["return_type"]

    # PascalCase test name
    test_name = "Test" + name[0].upper() + name[1:] if name else "TestFunction"

    lines = []
    lines.append("package XXX // TODO: set correct package")
    lines.append("")
    lines.append('import "testing"')
    if return_type:
        lines.append('import "fmt"')
    lines.append("")
    lines.append(f"func {test_name}(t *testing.T) {{")
    lines.append("    // Arrange")

    for arg in args:
        raw = arg["name"]
        if raw in ("self", "this"):
            continue
        lines.append(f"    // var {raw} = ...")

    lines.append(f"    // sut := {name}(...)")
    lines.append("")
    lines.append("    // Act")
    if return_type:
        lines.append("    // result := sut")
    else:
        lines.append("    // sut()")
    lines.append("")
    lines.append("    // Assert")
    lines.append("    // if result == nil {")
    lines.append("    //     t.Fatal(\"unexpected nil\")")
    lines.append("    // }")
    lines.append("}")

    return "\n".join(lines)


def _detect_framework(path: Path, lang_key: str) -> str:
    """Auto-detect test framework from file extension."""
    mapping = {
        "python": "pytest",
        "typescript": "vitest",
        "tsx": "vitest",
        "javascript": "vitest",
        "go": "go-test",
    }
    return mapping.get(lang_key, "pytest")


# ── Public API ─────────────────────────────────────────────────────────────


def code_generate_tests_tool(
    path: str,
    line: int,
    framework: str = "auto",
    language: str = "",
) -> str:
    """Generate unit test scaffolding from a source symbol's signature.

    Analyzes the function at the given *path* and *line* via AST, detects the
    function signature, parameters, and return type, and generates a test
    template in the appropriate framework.

    Framework detection (when ``"auto"``):
        - .py  → pytest
        - .ts / .tsx → vitest
        - .go → go-test (standard ``testing`` package)
        - Can be overridden via the *framework* parameter.

    Args:
        path: Absolute file path containing the function.
        line: 1-based line number where the function appears.
        framework: Test framework.  ``"auto"`` (default) detects from the
            file extension.  One of ``"auto"``, ``"pytest"``, ``"vitest"``,
            ``"jest"``, ``"go-test"``.
        language: Optional language override.  Auto-detected from extension
            when empty.

    Returns:
        Generated test code as a plain string (or an error message wrapped
        via ``fmt_err`` on failure).
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return fmt_err(f"Path not found: {path}")

    lang = language or detect_language(str(target))
    if lang is None:
        return fmt_err(f"Unsupported language or unrecognized extension: '{path}'")

    lang_key = lang.lower()

    # Determine test framework
    effective_framework = framework
    if effective_framework == "auto":
        effective_framework = _detect_framework(target, lang_key)

    # Parse function signature
    if lang_key == "python":
        source = target.read_text("utf-8", errors="replace")
        func_info = _parse_python_function(source, line)
    else:
        func_info = _parse_function_via_tree_sitter(target, line, lang_key)

    if func_info is None:
        return fmt_err(f"No function found at {path}:{line}")

    # Generate test code
    if effective_framework == "pytest":
        test_code = _generate_pytest_test(func_info)
    elif effective_framework in ("vitest", "jest"):
        test_code = _generate_vitest_test(func_info)
    elif effective_framework == "go-test":
        test_code = _generate_go_test(func_info)
    else:
        return fmt_err(f"Unsupported framework: {effective_framework}")

    return test_code


CODE_GENERATE_TESTS_SCHEMA = {
    "name": "code_generate_tests",
    "description": (
        "Generate unit test scaffolding from a source symbol's signature. "
        "Analyzes the function at the given path+line via AST, detects the "
        "function signature, parameters, and return type, and generates a "
        "test template in pytest (Python), vitest (TypeScript), or go-test "
        "(Go).  Supports async Python functions and TypeScript arrow functions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute file path containing the function",
            },
            "line": {
                "type": "integer",
                "description": "1-based line number where the function appears",
            },
            "framework": {
                "type": "string",
                "enum": ["auto", "pytest", "vitest", "jest", "go-test"],
                "description": (
                    "Test framework to use.  'auto' (default) detects from "
                    "file extension: .py → pytest, .ts/.tsx → vitest, .go → go-test."
                ),
                "default": "auto",
            },
            "language": {
                "type": "string",
                "description": (
                    "Language override (optional).  Auto-detected from "
                    "file extension when empty."
                ),
            },
        },
        "required": ["path", "line"],
    },
}


def _handle_code_generate_tests(**kwargs):
    """Handler for code_generate_tests tool dispatch.

    Accepts ``**kwargs`` (from the schema-driven dispatch) and delegates
    to ``code_generate_tests_tool``.
    """
    return code_generate_tests_tool(
        path=kwargs.get("path", ""),
        line=kwargs.get("line", 0),
        framework=kwargs.get("framework", "auto"),
        language=kwargs.get("language", ""),
    )


__all__ = [
    "code_generate_tests_tool",
    "CODE_GENERATE_TESTS_SCHEMA",
    "_handle_code_generate_tests",
]
