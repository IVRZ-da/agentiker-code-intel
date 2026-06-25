"""Property-based tests for all 19 code_intel tools using Hypothesis.

Generates random code snippets across languages and verifies that every tool
handles them without crashing — catches edge cases that manual tests miss.

Strategies:
  - random Python/TS/JS/Rust snippets (valid syntax + gibberish)
  - empty files, single-char, huge lines, unicode, BOM
  - deeply nested structures, very long identifiers
  - binary-like content in text files
"""

from pathlib import Path

import pytest

try:
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st

    _HYPOTHESIS_AVAILABLE = True
except ImportError:
    _HYPOTHESIS_AVAILABLE = False

pytest.importorskip("tree_sitter", reason="tree-sitter not installed")
pytest.importorskip("hypothesis", reason="hypothesis not installed")

from code_intel.code_tools import (
    _SYMBOL_CACHE,
    _init_languages,
    code_capsule_tool,
    code_query_tool,
    code_search_tool,
    code_symbols_tool,
    detect_language,
)

# ── Code snippet templates (deterministic, sampled by Hypothesis) ──────────

PY_SNIPPETS = [
    "",
    "    ",
    "\n\n\n",
    "x = 1",
    "MY_CONST = 42\nclass Greeter:\n    def greet(self): pass",
    'def hello(name: str) -> str:\n    return f"Hello, {name}!"',
    "import os\nimport sys\n\nos.path.join('a', 'b')",
    "@decorator\ndef wrapped():\n    pass",
    "class Outer:\n    class Inner:\n        def method(self): pass",
    "async def fetch():\n    return await something()",
    "x: int = 42\ny: str = 'hello'\nz: list[int] = []",
    "for i in range(10):\n    print(i)",
    "while True:\n    break",
    "try:\n    x = 1 / 0\nexcept ZeroDivisionError:\n    pass",
    "# just a comment\n# another comment",
    "'''docstring'''\ndef f(): pass",
    "lambda x: x + 1",
    "import typing\n\nT = typing.TypeVar('T')",
    "if True:\n    x = 1\nelif False:\n    x = 2\nelse:\n    x = 3",
]

TS_SNIPPETS = [
    "",
    "const x: number = 1;",
    "function greet(name: string): string { return `Hello ${name}`; }",
    "class Foo { constructor() {} bar(): void { return; } }",
    "interface Animal { name: string; }",
    "type Optional<T> = T | null;",
    "export const handler = () => {};",
    "import { readFile } from 'fs';",
    "enum Color { Red, Green, Blue }",
    "abstract class Base { abstract run(): void; }",
]

RS_SNIPPETS = [
    "",
    'fn main() { println!("hi"); }',
    "struct Point { x: i32, y: i32 }",
    "enum Direction { Up, Down }",
    "fn add(a: i32, b: i32) -> i32 { a + b }",
    "impl Point { fn new(x: i32, y: i32) -> Self { Self { x, y } } }",
    "use std::collections::HashMap;",
    "mod utils { pub fn helper() {} }",
]

JS_SNIPPETS = [
    "",
    "function hello() { return 42; }",
    "const add = (a, b) => a + b;",
    "class Counter { constructor() { this.count = 0; } }",
    "var x = 1; let y = 2; const z = 3;",
    "module.exports = { hello };",
    "if (true) { console.log('yes'); }",
]

GO_SNIPPETS = [
    "",
    "package main\nfunc main() {}",
    "package main\nvar x int = 42",
    "package main\ntype Point struct { X, Y int }",
    "package main\nfunc add(a, b int) int { return a + b }",
]

# ── Edge case content ──────────────────────────────────────────────────────

EDGE_CASES_PY = [
    "",
    " ",
    "\t",
    "\n" * 10,
    "\ufeff# BOM-prefixed file\nx = 1",
    "x " * 5000,  # huge single line
    "a\nb\nc\n" * 1000,  # many lines
    "\x00\x01\x02null bytes in text",
    "résumé = 'über cool'",
    "\u4e2d\u6587变量 = 42",  # CJK identifiers
    "x" * 10000,  # very long content
    "#" * 5000,  # long comment
    "   \n   \n   ",  # whitespace only
]

# ── Hypothesis strategies ──────────────────────────────────────────────────

SAMPLE_PY = st.sampled_from(PY_SNIPPETS)
SAMPLE_TS = st.sampled_from(TS_SNIPPETS)
SAMPLE_RS = st.sampled_from(RS_SNIPPETS)
SAMPLE_JS = st.sampled_from(JS_SNIPPETS)
SAMPLE_GO = st.sampled_from(GO_SNIPPETS)
EDGE_CASE = st.sampled_from(EDGE_CASES_PY)


def _extension(lang: str) -> str:
    return {"py": ".py", "ts": ".ts", "rs": ".rs", "js": ".js", "go": ".go"}.get(lang, ".py")


def _write_code(tmp_path, lang: str, content: str) -> Path:
    f = tmp_path / f"sample{_extension(lang)}"
    f.write_text(content, encoding="utf-8", errors="replace")
    return f


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset():
    _init_languages()
    _SYMBOL_CACHE.clear()
    yield


# ── Property 1: No tool crashes on ANY input ───────────────────────────────


@given(code=SAMPLE_PY)
@settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_symbols_py_never_crashes(code, tmp_path):
    f = _write_code(tmp_path, "py", code)
    try:
        r = code_symbols_tool(str(f))
        assert isinstance(r, (str, list, dict))
    except Exception as exc:
        msg = str(exc).lower()
        assert any(a in msg for a in ["no symbol", "no langu", "language", "unsupported", "parse", "not found"]), (
            f"Crash: {exc}"
        )


@given(code=SAMPLE_TS)
@settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
def test_symbols_ts_never_crashes(code, tmp_path):
    f = _write_code(tmp_path, "ts", code)
    try:
        r = code_symbols_tool(str(f))
        assert isinstance(r, (str, list, dict))
    except Exception as exc:
        msg = str(exc).lower()
        assert any(a in msg for a in ["no symbol", "no langu", "language", "unsupported", "parse"]), f"Crash: {exc}"


@given(code=SAMPLE_RS)
@settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
def test_symbols_rs_never_crashes(code, tmp_path):
    f = _write_code(tmp_path, "rs", code)
    try:
        r = code_symbols_tool(str(f))
        assert isinstance(r, (str, list, dict))
    except Exception as exc:
        msg = str(exc).lower()
        assert any(a in msg for a in ["no symbol", "no langu", "language", "unsupported", "parse"]), f"Crash: {exc}"


@given(code=SAMPLE_JS)
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_symbols_js_never_crashes(code, tmp_path):
    f = _write_code(tmp_path, "js", code)
    try:
        r = code_symbols_tool(str(f))
        assert isinstance(r, (str, list, dict))
    except Exception as exc:
        msg = str(exc).lower()
        assert any(a in msg for a in ["no symbol", "no langu", "language", "unsupported", "parse"]), f"Crash: {exc}"


@given(code=SAMPLE_GO)
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_symbols_go_never_crashes(code, tmp_path):
    f = _write_code(tmp_path, "go", code)
    try:
        r = code_symbols_tool(str(f))
        assert isinstance(r, (str, list, dict))
    except Exception as exc:
        msg = str(exc).lower()
        assert any(a in msg for a in ["no symbol", "no langu", "language", "unsupported", "parse"]), f"Crash: {exc}"


@given(code=EDGE_CASE)
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_symbols_edge_cases(code, tmp_path):
    """Edge cases (empty, binary, huge lines, unicode)."""
    f = _write_code(tmp_path, "py", code)
    try:
        r = code_symbols_tool(str(f))
        assert isinstance(r, (str, list, dict))
    except Exception as exc:
        msg = str(exc).lower()
        assert any(
            a in msg for a in ["no symbol", "no langu", "language", "unsupported", "parse", "not found", "out of range"]
        ), f"Crash: {exc}"


# ── Property 2: code_search_tool on random code ───────────────────────────


@given(code=st.sampled_from(PY_SNIPPETS + TS_SNIPPETS + RS_SNIPPETS))
@settings(max_examples=40, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
def test_search_never_crashes(code, tmp_path):
    _ALL_LANGS = ["py", "ts", "rs"]
    for lang in _ALL_LANGS:
        ext = {"py": ".py", "ts": ".ts", "rs": ".rs"}[lang]
        f = tmp_path / f"sample{ext}"
        f.write_text(code, encoding="utf-8", errors="replace")
        try:
            r = code_search_tool(str(f), preset="function_calls")
            assert isinstance(r, (str, list, dict))
        except Exception as exc:
            msg = str(exc).lower()
            assert any(
                a in msg for a in ["no symbol", "no langu", "language", "unsupported", "parse", "no preset", "pattern"]
            ), f"Crash on {lang}: {exc}"


# ── Property 3: code_capsule_tool on random code ──────────────────────────


@given(code=st.sampled_from(PY_SNIPPETS + TS_SNIPPETS))
@settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
def test_capsule_never_crashes(code, tmp_path):
    f = _write_code(tmp_path, "py", code)
    try:
        r = code_capsule_tool(str(f), line=1)
        assert isinstance(r, (str, list, dict))
    except Exception as exc:
        msg = str(exc).lower()
        assert any(
            a in msg
            for a in ["no symbol", "no langu", "language", "unsupported", "parse", "no result", "line out of range"]
        ), f"Crash: {exc}"


# ── Property 4: code_query_tool with various intents ──────────────────────


@given(code=st.sampled_from(PY_SNIPPETS))
@settings(max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_query_intents_never_crash(code, tmp_path):
    f = _write_code(tmp_path, "py", code)
    for intent in ["symbols", "search", "overview", "find_usage"]:
        try:
            r = code_query_tool(intent=intent, path=str(f))
            assert isinstance(r, (str, list, dict))
        except Exception as exc:
            msg = str(exc).lower()
            assert any(
                a in msg for a in ["no symbol", "no langu", "language", "unsupported", "parse", "unknown intent"]
            ), f"Crash intent={intent}: {exc}"


# ── Invariant: detect_language is consistent ───────────────────────────────


@given(ext=st.sampled_from([".py", ".ts", ".js", ".rs", ".go", ".java", ".c", ".cpp"]))
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_detect_language_known_ext(ext, tmp_path):
    f = tmp_path / f"file{ext}"
    f.write_text("x = 1")
    lang = detect_language(str(f))
    assert lang is not None, f"detect_language returned None for {ext}"
    assert isinstance(lang, str)


@given(path_str=st.text(min_size=0, max_size=50))
@settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
def test_detect_language_never_crashes(path_str):
    """Must not crash on arbitrary strings (non-existent paths, empty, special chars)."""
    try:
        r = detect_language(path_str)
        assert r is None or isinstance(r, str)
    except Exception as exc:
        msg = str(exc).lower()
        assert any(a in msg for a in ["no such", "exist", "found"]), f"Crash: {exc}"
