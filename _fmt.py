"""_fmt.py — Rich-basierte Formatierungs-Helper für code_intel.

Nutzt `rich` (global installiert) für farbige, strukturierte Terminal-Ausgaben.
Design-System: siehe plugin-terminal-formatting Skill.

Verwendung in Tool-Handlern:
    from ._fmt import fmt_ok, fmt_err, fmt_table, fmt_tree

    def my_tool(args, **kwargs) -> str:
        return fmt_ok({"result": "data"})
        return fmt_err("Something went wrong")
"""

import json
from typing import Any, Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.tree import Tree
from rich import box as rich_box


# ─── Globale Console (erkennt Terminal-Breite automatisch) ──────
_console = Console()

# ─── Theme-Farben ─────────────────────────────────────────────────
STYLE_TITLE     = "bold cyan"
STYLE_OK        = "green"
STYLE_WARN      = "yellow"
STYLE_ERROR     = "bold red"
STYLE_INFO      = "blue"
STYLE_DIM       = "dim white"
STYLE_HIGHLIGHT = "magenta"
STYLE_PATH      = "italic cyan"
STYLE_LINE      = "yellow"
STYLE_ACTIVE    = "bright_green"
STYLE_PENDING   = "bright_black"


def _capture(renderable) -> str:
    """Render a rich object and return the ANSI-formatted string."""
    with _console.capture() as capture:
        _console.print(renderable)
    return capture.get()


# ═══════════════════════════════════════════════════════════════════
# Standard-Responses: fmt_ok / fmt_err
# ═══════════════════════════════════════════════════════════════════

def fmt_ok(data: dict, title: str = "✅ Success") -> str:
    """Format success response as green ROUNDED Panel."""
    return _capture(Panel(
        _dict_to_table(data, title=""),
        title=title,
        border_style=STYLE_OK,
        box=rich_box.ROUNDED,
    ))


def fmt_err(msg: str, title: str = "❌ Error") -> str:
    """Format error response as red ROUNDED Panel."""
    return _capture(Panel(
        msg,
        title=title,
        border_style=STYLE_ERROR,
        box=rich_box.ROUNDED,
    ))


def fmt_warn(msg: str, title: str = "⚠️ Warning") -> str:
    """Format warning response as yellow ROUNDED Panel."""
    return _capture(Panel(
        msg,
        title=title,
        border_style=STYLE_WARN,
        box=rich_box.ROUNDED,
    ))


def fmt_info(msg: str, title: str = "📝 Info") -> str:
    """Format info response as blue ROUNDED Panel."""
    return _capture(Panel(
        msg,
        title=title,
        border_style=STYLE_INFO,
        box=rich_box.ROUNDED,
    ))


# ═══════════════════════════════════════════════════════════════════
# Tabellen
# ═══════════════════════════════════════════════════════════════════

def fmt_table(rows: list[dict],
              columns: Optional[list[str]] = None,
              title: str = "",
              header_style: str = STYLE_TITLE) -> str:
    """Format a list of dicts as a rich Table."""
    if not rows:
        return _capture(Panel("[dim]Keine Daten[/dim]", border_style=STYLE_DIM))

    cols = columns or list(rows[0].keys())
    table = Table(title=title or None, box=rich_box.ROUNDED,
                  header_style=header_style)

    for col in cols:
        table.add_column(col)

    for row in rows:
        table.add_row(*[str(row.get(col, "")) for col in cols])

    return _capture(table)


def fmt_table_simple(rows: list,
                     columns: list[str],
                     title: str = "") -> str:
    """Kompakte Tabelle ohne Rahmen (SIMPLE box)."""
    table = Table(title=title or None, box=rich_box.SIMPLE,
                  header_style=STYLE_TITLE)
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[str(c) for c in row])
    return _capture(table)


# ═══════════════════════════════════════════════════════════════════
# Bäume (Hierarchien)
# ═══════════════════════════════════════════════════════════════════

def fmt_tree(label: str, symbols: list[dict],
             kind_style: str = "cyan",
             name_style: str = "bold") -> str:
    """Format a symbol tree as rich Tree."""
    tree = Tree(label, guide_style=STYLE_DIM)
    for sym in symbols:
        _add_symbol_node(tree, sym, kind_style, name_style)
    return _capture(tree)


def _add_symbol_node(parent, sym: dict,
                     kind_style: str, name_style: str) -> None:
    """Rekursiv: Füge Symbol-Knoten zum Baum hinzu."""
    icon = {"function": "ƒ", "method": "ƒ", "class": "⊞",
            "interface": "⊟", "struct": "⊡", "enum": "⊡",
            "type": "τ", "variable": "v", "constant": "c",
            "module": "⊟", "trait": "τ"}.get(sym.get("kind", ""), "•")
    line_info = f"L{sym['line']}" + (f"-{sym['end_line']}"
                                      if sym.get('end_line') and sym['end_line'] != sym['line']
                                      else "")

    label = (f"[{kind_style}]{icon} {sym['kind']}[/] "
             f"[{name_style}]{sym['name']}[/] "
             f"[{STYLE_DIM}]({line_info})[/]")
    node = parent.add(label)

    for child in sym.get("children", []):
        _add_symbol_node(node, child, kind_style, name_style)


# ═══════════════════════════════════════════════════════════════════
# Code / Syntax
# ═══════════════════════════════════════════════════════════════════

def fmt_code(code: str, lang: str = "python",
             line_numbers: bool = True,
             theme: str = "monokai") -> str:
    """Format code with Syntax highlighting."""
    return _capture(Syntax(
        code, lang, theme=theme, line_numbers=line_numbers,
    ))


# ═══════════════════════════════════════════════════════════════════
# Markdown
# ═══════════════════════════════════════════════════════════════════

def fmt_markdown(md: str) -> str:
    """Render Markdown string via rich."""
    try:
        return _capture(Markdown(md))
    except Exception:
        return md


# ═══════════════════════════════════════════════════════════════════
# Hilfsfunktionen
# ═══════════════════════════════════════════════════════════════════

def _dict_to_table(data: dict, title: str = "") -> Table:
    """Convert a dict to a two-column Table (MINIMAL box)."""
    table = Table(title=title or None, box=rich_box.MINIMAL,
                  show_header=False)
    table.add_column("Key", style=STYLE_TITLE)
    table.add_column("Value")
    for key, value in data.items():
        table.add_row(str(key), str(value))
    return table


def fmt_json(data: Any) -> str:
    """Format JSON data with Syntax highlighting."""
    return fmt_code(
        json.dumps(data, indent=2, ensure_ascii=False),
        lang="json", line_numbers=False,
    )


def _strip_ansi(text: str) -> str:
    """Remove ANSI codes from formatted output (für Tests)."""
    from rich.ansi import AnsiDecoder
    decoder = AnsiDecoder()
    return "".join(segment.plain for segment in decoder.decode(text))
