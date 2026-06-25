"""Tests for analysis code_intel tools — complexity, duplicates, hot_paths,
cycle_detector, metrics, unused, and all edge cases.

Split from test_code_intel_tools.py — analysis tools domain.
"""

import textwrap

import pytest

# ---------------------------------------------------------------------------
# Skip entire module if tree-sitter is not installed
# ---------------------------------------------------------------------------
pytest.importorskip("tree_sitter", reason="tree-sitter not installed")


# ===========================================================================
# Fixtures – small source files (same as test_code_tools.py for consistency)
# ===========================================================================


@pytest.fixture()
def tmp_py(tmp_path):
    src = textwrap.dedent("""\
        MY_CONST = 42

        class Greeter:
            \"\"\"Say hello.\"\"\"

            def greet(self, name: str) -> str:
                return f"Hello, {name}!"

            @staticmethod
            def farewell() -> str:
                return "Goodbye!"

        def top_level_fn(x: int) -> int:
            return x * 2

        async def async_fn() -> None:
            pass
    """)
    f = tmp_path / "sample.py"
    f.write_text(src)
    return f


@pytest.fixture()
def tmp_ts(tmp_path):
    src = textwrap.dedent("""\
        export interface Animal {
            name: string;
        }

        export class Dog implements Animal {
            constructor(public name: string) {}

            bark(): string {
                return "woof";
            }
        }

        export function createDog(name: string): Dog {
            return new Dog(name);
        }

        const arrowFn = (x: number): number => x + 1;
    """)
    f = tmp_path / "sample.ts"
    f.write_text(src)
    return f


@pytest.fixture()
def tmp_js(tmp_path):
    src = textwrap.dedent("""\
        class Counter {
            constructor() { this.count = 0; }
            increment() { this.count++; }
        }

        function reset(counter) { counter.count = 0; }
        const double = (n) => n * 2;
    """)
    f = tmp_path / "sample.js"
    f.write_text(src)
    return f


@pytest.fixture()
def tmp_rs(tmp_path):
    src = textwrap.dedent("""\
        pub struct Point {
            pub x: f64,
            pub y: f64,
        }

        impl Point {
            pub fn new(x: f64, y: f64) -> Self {
                Point { x, y }
            }

            pub fn distance(&self, other: &Point) -> f64 {
                ((self.x - other.x).powi(2) + (self.y - other.y).powi(2)).sqrt()
            }
        }

        pub fn origin() -> Point {
            Point::new(0.0, 0.0)
        }

        pub trait Shape {
            fn area(&self) -> f64;
        }
    """)
    f = tmp_path / "sample.rs"
    f.write_text(src)
    return f


@pytest.fixture()
def tmp_go(tmp_path):
    src = textwrap.dedent("""\
        package main

        type Rectangle struct {
            Width  float64
            Height float64
        }

        func (r Rectangle) Area() float64 {
            return r.Width * r.Height
        }

        func NewRectangle(w, h float64) Rectangle {
            return Rectangle{Width: w, Height: h}
        }

        type Stringer interface {
            String() string
        }
    """)
    f = tmp_path / "sample.go"
    f.write_text(src)
    return f


@pytest.fixture()
def tmp_java(tmp_path):
    src = textwrap.dedent("""\
        public class Hello {
            private String msg;

            public Hello(String msg) {
                this.msg = msg;
            }

            public void greet() {
                System.out.println(msg);
            }
        }
    """)
    f = tmp_path / "Hello.java"
    f.write_text(src)
    return f


# ===========================================================================
# Analysis tool test classes
# ===========================================================================
# The following test class groups are reserved for analysis tool tests:
#
# - TestCodeComplexity*   — cyclomatic complexity analysis
# - TestCodeDuplicates*   — duplicate code detection
# - TestCodeHotPaths*     — hot path / import chain analysis
# - TestCodeCycleDetector*— circular dependency detection
# - TestCodeMetrics*      — project-level metrics
# - TestCodeUnusedFinder* — unused imports / functions
#
# These classes were not present in the original test_code_intel_tools.py
# but belong in this file when added. Add them below this block.
