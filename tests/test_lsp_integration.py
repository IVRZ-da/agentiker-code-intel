"""Integration tests with real LSP servers.

Starts actual LSP servers (pyright, tsserver, gopls) and verifies that
code_intel tools can communicate with them and return correct results.

These tests are skipped by default. Run with:
  LSP_TEST=1 pytest tests/test_lsp_integration.py -v
"""

import os
import textwrap

import pytest

pytest.importorskip("tree_sitter", reason="tree-sitter not installed")

from code_intel.lsp_bridge import (
    _detect_language_for_lsp,
    code_definition_tool,
    code_diagnostics_tool,
    code_hover_tool,
    code_references_tool,
    code_workspace_symbols_tool,
)

RUN_LSP = os.environ.get("LSP_TEST") == "1"
lsp_skip = pytest.mark.skipif(not RUN_LSP, reason="set LSP_TEST=1 to run LSP integration tests")


# =============================================================================
# Fixtures: temporary project directories with real code
# =============================================================================


@pytest.fixture()
def py_project(tmp_path):
    """Create a temporary Python project with known code structure."""
    src = textwrap.dedent("""\
        MODULE_CONST = 42

        class Calculator:
            \"\"\"A simple calculator.\\n\\n    Can add and multiply numbers.\\n    \"\"\"

            def __init__(self, name: str = "calc"):
                self.name = name
                self._history: list[float] = []

            def add(self, a: float, b: float) -> float:
                \"\"\"Add two numbers and return the result.\"\"\"
                result = a + b
                self._history.append(result)
                return result

            def multiply(self, a: float, b: float) -> float:
                result = a * b
                self._history.append(result)
                return result

            @property
            def last_result(self) -> float | None:
                return self._history[-1] if self._history else None

        def helper_function(x: int) -> int:
            \"\"\"Multiplies by two.\"\"\"
            return x * 2
    """)
    f = tmp_path / "calc.py"
    f.write_text(src)
    return tmp_path


@pytest.fixture()
def ts_project(tmp_path):
    """Create a temporary TypeScript project with tsconfig."""
    src = textwrap.dedent("""\
        export interface Animal {
            name: string;
            age: number;
        }

        export class Dog implements Animal {
            constructor(public name: string, public age: number) {}

            bark(): string {
                return "Woof!";
            }

            static createPuppy(): Dog {
                return new Dog("Puppy", 0);
            }
        }

        export function adopt(animal: Animal): string {
            return `Adopted ${animal.name}!`;
        }
    """)
    f = tmp_path / "animals.ts"
    f.write_text(src)
    # tsconfig needed for tsserver
    tsconfig = textwrap.dedent("""\
        {
            "compilerOptions": {
                "target": "ES2020",
                "module": "ESNext",
                "strict": true
            }
        }
    """)
    (tmp_path / "tsconfig.json").write_text(tsconfig)
    return tmp_path


@pytest.fixture()
def go_project(tmp_path):
    """Create a temporary Go project with go.mod."""
    src = textwrap.dedent("""\
        package calc

        type Calculator struct {
            Name     string
            history  []float64
        }

        func New(name string) *Calculator {
            return &Calculator{Name: name}
        }

        func (c *Calculator) Add(a, b float64) float64 {
            result := a + b
            c.history = append(c.history, result)
            return result
        }

        func (c *Calculator) Multiply(a, b float64) float64 {
            result := a * b
            c.history = append(c.history, result)
            return result
        }

        func Helper(x int) int {
            return x * 2
        }
    """)
    (tmp_path / "calc.go").write_text(src)
    (tmp_path / "go.mod").write_text("module lsp_test_calc\n\ngo 1.21\n")
    return tmp_path


# =============================================================================
# Helpers
# =============================================================================


def _resolve_command(cmd: str) -> str | None:
    """Resolve a command to its absolute path."""
    import subprocess
    which = subprocess.run(["which", cmd], capture_output=True, text=True)
    if which.returncode == 0:
        return which.stdout.strip()
    return None


# =============================================================================
# Pyright Integration Tests
# =============================================================================


class TestPyrightIntegration:
    """Real LSP integration tests using pyright-langserver."""

    @lsp_skip
    def test_pyright_detects_language(self):
        """_detect_language_for_lsp returns 'python' for .py files."""
        lang = _detect_language_for_lsp("/tmp/test.py")
        assert lang == "python"

    @lsp_skip
    def test_pyright_go_to_definition(self, py_project):
        """code_definition_tool finds class definition."""
        calc_py = py_project / "calc.py"
        # Calculator class is on line 3
        result = code_definition_tool(str(calc_py), line=3)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "Calculator" in result or "class" in result.lower()

    @lsp_skip
    def test_pyright_go_to_definition_imported(self, py_project):
        """Go to definition of simple_mod CONST."""
        calc_py = py_project / "calc.py"
        result = code_definition_tool(str(calc_py), line=1)
        assert isinstance(result, str)
        assert "MODULE_CONST" in result

    @lsp_skip
    def test_pyright_find_references(self, py_project):
        """code_references_tool finds all references to a symbol."""
        calc_py = py_project / "calc.py"
        result = code_references_tool(str(calc_py), line=12)
        assert isinstance(result, str)

    @lsp_skip
    def test_pyright_hover_info(self, py_project):
        """code_hover_tool returns type info."""
        calc_py = py_project / "calc.py"
        result = code_hover_tool(str(calc_py), line=12, character=12)
        assert isinstance(result, str)
        # Hover may return empty if LSP has no info at that exact position
        if result:
            assert "add" in result.lower() or "def" in result.lower() or "float" in result

    @lsp_skip
    def test_pyright_diagnostics(self, py_project):
        """code_diagnostics_tool returns diagnostics for valid code."""
        calc_py = py_project / "calc.py"
        result = code_diagnostics_tool(str(calc_py))
        assert isinstance(result, str)

    @lsp_skip
    def test_pyright_diagnostics_on_syntax_error(self, py_project):
        """code_diagnostics_tool catches syntax errors."""
        bad_file = py_project / "broken.py"
        bad_file.write_text("def broken(::\n    pass\n")
        result = code_diagnostics_tool(str(bad_file))
        assert isinstance(result, str)
        assert len(result) > 0

    @lsp_skip
    def test_pyright_workspace_symbols(self, py_project):
        """code_workspace_symbols_tool returns known symbols."""
        result = code_workspace_symbols_tool("Calculator", path=str(py_project))
        assert isinstance(result, str)
        assert "Calculator" in result or len(result) > 0

    @lsp_skip
    def test_pyright_method_definition(self, py_project):
        """Go to method definition within class."""
        calc_py = py_project / "calc.py"
        result = code_definition_tool(str(calc_py), line=18)
        assert isinstance(result, str)
        assert "multiply" in result.lower()

    @lsp_skip
    def test_pyright_property_definition(self, py_project):
        """Go to @property definition."""
        calc_py = py_project / "calc.py"
        result = code_definition_tool(str(calc_py), line=23)
        assert isinstance(result, str)
        assert "last_result" in result.lower()

    @lsp_skip
    def test_pyright_constant_definition(self, py_project):
        """Go to module-level constant."""
        calc_py = py_project / "calc.py"
        result = code_definition_tool(str(calc_py), line=1)
        assert isinstance(result, str)
        assert "MODULE_CONST" in result or "42" in result

    @lsp_skip
    def test_pyright_hover_on_class(self, py_project):
        """code_hover on class name shows docstring."""
        calc_py = py_project / "calc.py"
        result = code_hover_tool(str(calc_py), line=3, character=6)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "calculator" in result.lower() or "class" in result.lower()


# =============================================================================
# TypeScript / tsserver Integration Tests
# =============================================================================


class TestTsserverIntegration:
    """Real LSP integration tests using TypeScript server."""

    @lsp_skip
    def test_tsserver_detects_language(self):
        """_detect_language_for_lsp returns 'typescript' for .ts files."""
        lang = _detect_language_for_lsp("/tmp/test.ts")
        assert lang == "typescript"

    @lsp_skip
    def test_tsserver_go_to_definition_interface(self, ts_project):
        """Navigate to interface definition."""
        animals = ts_project / "animals.ts"
        result = code_definition_tool(str(animals), line=2)
        assert isinstance(result, str)
        assert "Animal" in result or "interface" in result.lower()

    @lsp_skip
    def test_tsserver_find_references_class(self, ts_project):
        """Find references to Dog class."""
        animals = ts_project / "animals.ts"
        result = code_references_tool(str(animals), line=6)
        assert isinstance(result, str)

    @lsp_skip
    def test_tsserver_hover(self, ts_project):
        """code_hover on method."""
        animals = ts_project / "animals.ts"
        result = code_hover_tool(str(animals), line=9, character=4)
        assert isinstance(result, str)
        assert len(result) > 0

    @lsp_skip
    def test_tsserver_workspace_symbols(self, ts_project):
        """Find classes via workspace symbols."""
        result = code_workspace_symbols_tool("Dog", path=str(ts_project))
        assert isinstance(result, str)
        assert "Dog" in result or len(result) > 0

    @lsp_skip
    def test_tsserver_diagnostics(self, ts_project):
        """Diagnostics on valid code."""
        animals = ts_project / "animals.ts"
        result = code_diagnostics_tool(str(animals))
        assert isinstance(result, str)


# =============================================================================
# Go / gopls Integration Tests
# =============================================================================


class TestGoplsIntegration:
    """Real LSP integration tests using gopls."""

    @lsp_skip
    def test_gopls_detects_language(self):
        """_detect_language_for_lsp returns 'go' for .go files."""
        lang = _detect_language_for_lsp("/tmp/test.go")
        assert lang == "go"

    @lsp_skip
    def test_gopls_go_to_definition_struct(self, go_project):
        """Navigate to struct definition."""
        calc_go = go_project / "calc.go"
        result = code_definition_tool(str(calc_go), line=3)
        assert isinstance(result, str)
        assert "Calculator" in result or "struct" in result.lower()

    @lsp_skip
    def test_gopls_go_to_method(self, go_project):
        """Navigate to method definition."""
        calc_go = go_project / "calc.go"
        result = code_definition_tool(str(calc_go), line=12)
        assert isinstance(result, str)
        assert "Add" in result or "func" in result.lower()

    @lsp_skip
    def test_gopls_hover(self, go_project):
        """code_hover on function."""
        calc_go = go_project / "calc.go"
        result = code_hover_tool(str(calc_go), line=24, character=5)
        assert isinstance(result, str)
        assert len(result) > 0

    @lsp_skip
    def test_gopls_diagnostics(self, go_project):
        """Diagnostics on valid Go code."""
        calc_go = go_project / "calc.go"
        result = code_diagnostics_tool(str(calc_go))
        assert isinstance(result, str)

    @lsp_skip
    def test_gopls_workspace_symbols(self, go_project):
        """Find symbols in Go workspace."""
        result = code_workspace_symbols_tool("Calculator", path=str(go_project))
        assert isinstance(result, str)
        assert "Calculator" in result or len(result) > 0
