"""op_meta.py — Op-Metadaten-Registry: die einzige Quelle der Wahrheit.

Warum
-----
Schema-Narrowing (0.6.33), ``op=help``, Fehlerobjekte mit ``fix.call`` (0.6.35) und
Output-Caps (0.6.36) brauchen alle dieselbe Auskunft: welcher op existiert, welchen
Tier er hat, welche Felder er verlangt, wie viel Output er darf und was als naechster
Schritt sinnvoll ist. Ohne eine Tabelle waeren das vier getrennte Basteleien.

Aufbau
------
``_ROWS`` haelt pro op ein Achtertupel::

    (op, group, tier, required, optional, max_items, max_tokens, returns, next_ops)

``required``/``optional`` sind aus dem echten Registrierungs-Catalog (den Handlern in
``tools/*.py`` und ``lsp/handlers.py``) generiert und danach eingefroren — nicht neu
erfunden. ``validate_against(catalog)`` prueft die Drift gegen die Handler-Schemata,
damit die Registry nicht still veraltet.

Tier
----
1 = Alltagsfall (lesen, navigieren, kleine Edits), 2 = nuetzlich, 3 = Spezialfall.
Der Tier steuert, welche Felder ins Schema kommen (0.6.33) und was ``op=help`` zeigt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

__all__ = [
    "OpMeta",
    "OP_META",
    "TIER_1",
    "TIER_2",
    "TIER_3",
    "build_registry",
    "caps_for",
    "get",
    "help_text",
    "op_names",
    "validate_against",
]

# (op, group, tier, required, optional, max_items, max_tokens, returns, next_ops)
_ROWS: tuple[tuple[Any, ...], ...] = (
    ("search", "code_search", 1, ("path",), ("language", "max_results", "pattern", "preset", "query"), 50, 1500, "Trefferliste: path, line, text je Match", ("symbols", "capsule")),
    ("symbols", "code_search", 1, ("path",), ("include_body", "kind", "language", "max_results", "pattern"), 200, 1500, "Symboltabelle: name, kind, line, end_line", ("capsule", "definition")),
    ("overview", "code_search", 1, ("path",), ("depth", "language"), 80, 2000, "Datei-Uebersicht: Klassen/Funktionen je Sprache, Zaehler", ("symbols",)),
    ("query", "code_search", 1, ("intent",), ("language", "line", "path"), 50, 2000, "Treffer nach Intent (symbols/text/graph), je Fundstelle", ("capsule", "definition")),
    ("search_by_error", "code_search", 2, ("path", "error"), ("language", "max_files", "max_findings", "timeout"), 50, 2000, "Treffer als JSON-Liste", ()),
    ("todo_finder", "code_search", 1, ("path",), ("include_extensions", "include_patterns"), 200, 1500, "Treffer als JSON-Liste", ()),
    ("capsule", "code_search", 1, ("path", "line"), ("include_tests", "language"), 1, 1500, "Signatur + Docstring + Top-Referenzen", ("explain", "references")),
    ("explain", "code_search", 1, ("path", "line"), ("character", "language"), 1, 1200, "Symbol mit Kontext: Signatur, Body-Ausschnitt, Aufrufer", ("references", "callers")),
    ("definition", "code_lsp", 1, ("path", "line"), ("character", "language"), 50, 2000, "Definitionsort: path, line, character", ("references", "callers")),
    ("references", "code_lsp", 1, ("path", "line"), ("character", "group_by_file", "include_declaration", "language", "max_results"), 100, 1500, "Referenzliste: path, line, character, group_by_file", ("impact", "callers")),
    ("hover", "code_lsp", 1, ("path", "line"), ("character", "language"), 1, 800, "Hover-Text (Markdown/Plaintext)", ()),
    ("implementations", "code_lsp", 3, ("path", "line"), ("character", "language"), 50, 2000, "LSP-Ergebnis: position/range/contents als JSON", ()),
    ("type_definition", "code_lsp", 3, ("path", "line"), ("character", "language"), 50, 2000, "LSP-Ergebnis: position/range/contents als JSON", ()),
    ("type_hierarchy", "code_lsp", 3, ("path", "line"), ("character", "direction", "language"), 50, 2000, "LSP-Ergebnis: position/range/contents als JSON", ()),
    ("diagnostics", "code_lsp", 1, ("path",), ("language",), 50, 1500, "Diagnosen: severity, path, line, message", ("tests_for_symbol",)),
    ("document_symbols", "code_lsp", 3, ("path",), ("language",), 50, 2000, "LSP-Ergebnis: position/range/contents als JSON", ()),
    ("document_links", "code_lsp", 3, ("path",), ("language",), 50, 2000, "LSP-Ergebnis: position/range/contents als JSON", ()),
    ("completion", "code_lsp", 3, ("path", "line"), ("character", "language"), 50, 2000, "LSP-Ergebnis: position/range/contents als JSON", ()),
    ("signatures", "code_lsp", 3, ("path", "line"), ("character", "language"), 50, 2000, "LSP-Ergebnis: position/range/contents als JSON", ()),
    ("highlight", "code_lsp", 3, ("path", "line"), ("character",), 50, 2000, "LSP-Ergebnis: position/range/contents als JSON", ()),
    ("inlay_hints", "code_lsp", 3, ("path",), ("end_line", "start_line"), 50, 2000, "LSP-Ergebnis: position/range/contents als JSON", ()),
    ("inline_values", "code_lsp", 3, ("path",), ("language",), 50, 2000, "LSP-Ergebnis: position/range/contents als JSON", ()),
    ("selection_range", "code_lsp", 3, ("path", "line"), ("character", "language"), 50, 2000, "LSP-Ergebnis: position/range/contents als JSON", ()),
    ("folding_range", "code_lsp", 3, ("path",), ("language",), 50, 2000, "LSP-Ergebnis: position/range/contents als JSON", ()),
    ("semantic_tokens", "code_lsp", 3, ("path",), ("language",), 50, 2000, "LSP-Ergebnis: position/range/contents als JSON", ()),
    ("code_lens", "code_lsp", 3, ("path",), ("language",), 50, 2000, "LSP-Ergebnis: position/range/contents als JSON", ()),
    ("linked_editing", "code_lsp", 3, ("path", "line"), ("character", "language"), 50, 2000, "LSP-Ergebnis: position/range/contents als JSON", ()),
    ("prepare_rename", "code_lsp", 3, ("path", "line"), ("character", "language"), 50, 2000, "LSP-Ergebnis: position/range/contents als JSON", ()),
    ("callers", "code_graph", 1, ("path", "line"), ("character", "group_by_file", "language"), 100, 1500, "Graph-Ergebnis: Knoten, Kanten, Zaehler", ("impact",)),
    ("callees", "code_graph", 1, ("path", "line"), ("language",), 100, 1500, "Graph-Ergebnis: Knoten, Kanten, Zaehler", ("impact",)),
    ("call_hierarchy", "code_graph", 2, ("path", "line"), ("character", "direction", "language", "max_callers_per_level", "max_depth"), 50, 2000, "Graph-Ergebnis: Knoten, Kanten, Zaehler", ()),
    ("blast_radius", "code_graph", 2, ("path", "line"), ("character", "depth", "language", "test_coverage"), 50, 2000, "Graph-Ergebnis: Knoten, Kanten, Zaehler", ()),
    ("impact", "code_graph", 1, ("path",), ("language", "line"), 100, 1500, "Betroffene Symbole/Dateien, Risikostufe, Testabdeckung", ("tests_for_symbol",)),
    ("dependency_graph", "code_graph", 2, ("path",), ("depth", "direction", "format", "module_level"), 200, 2000, "Graph-Ergebnis: Knoten, Kanten, Zaehler", ("cycle_detector",)),
    ("graph_query", "code_graph", 2, ("path", "query"), ("symbol", "top_n"), 50, 2000, "Graph-Ergebnis: Knoten, Kanten, Zaehler", ()),
    ("cycle_detector", "code_graph", 2, ("path",), ("depth", "max_cycles"), 20, 1500, "Graph-Ergebnis: Knoten, Kanten, Zaehler", ("dependency_graph",)),
    ("hot_paths", "code_graph", 2, ("path",), ("depth", "top_n"), 20, 1500, "Graph-Ergebnis: Knoten, Kanten, Zaehler", ("complexity",)),
    ("metrics", "code_graph", 2, (), ("depth", "path"), 80, 1500, "Zaehler: files, lines, functions, classes, complexity", ()),
    ("complexity", "code_graph", 2, ("path",), ("directory", "function", "language", "line"), 50, 2000, "Hotspots: symbol, path, line, score", ("metrics",)),
    ("diagram_symbol", "code_graph", 3, ("path", "line"), ("character", "depth", "language"), 50, 2000, "Graph-Ergebnis: Knoten, Kanten, Zaehler", ()),
    ("refactor", "code_edit", 1, ("path", "pattern", "rewrite"), ("context_lines", "dry_run", "file_glob", "language"), 50, 2000, "Diff je Datei: path, hunks, dry_run", ("diagnostics",)),
    ("batch_refactor", "code_edit", 2, ("path", "pattern", "rewrite"), ("dry_run", "file_glob", "language"), 50, 2000, "Diff-Protokoll: files, hunks, dry_run", ()),
    ("rename", "code_edit", 1, ("path", "line", "new_name"), ("character", "dry_run", "language"), 50, 2000, "Diff je Datei + neue Referenzen", ("references", "diagnostics")),
    ("replace_body", "code_edit", 1, ("path", "symbol", "new_body"), ("dry_run", "include_decorators", "language"), 50, 2000, "Diff-Protokoll: files, hunks, dry_run", ("diagnostics",)),
    ("insert_before", "code_edit", 1, ("path", "symbol", "code"), ("dry_run", "language", "newline"), 50, 2000, "Diff-Protokoll: files, hunks, dry_run", ("diagnostics",)),
    ("insert_after", "code_edit", 1, ("path", "symbol", "code"), ("dry_run", "language", "newline"), 50, 2000, "Diff-Protokoll: files, hunks, dry_run", ("diagnostics",)),
    ("move", "code_edit", 1, ("source", "symbol", "target"), ("dry_run", "language"), 50, 2000, "Diff-Protokoll: files, hunks, dry_run", ("diagnostics",)),
    ("safe_delete", "code_edit", 2, ("path", "symbol"), ("dry_run", "force", "language"), 50, 2000, "Diff-Protokoll: files, hunks, dry_run", ("diagnostics",)),
    ("format", "code_edit", 1, ("path",), ("dry_run", "language"), 50, 2000, "Diff-Protokoll: files, hunks, dry_run", ("diagnostics",)),
    ("action", "code_edit", 3, ("path", "line"), ("apply_index", "end_line", "language", "only_kinds"), 50, 2000, "verfuegbare Code-Actions: title, kind, index", ()),
    ("migration", "code_edit", 2, ("path", "rules"), ("dry_run", "rules_file"), 50, 2000, "Diff je Datei + Regel-ID", ()),
    ("review_assistant", "code_review", 2, ("path",), ("base", "head", "max_files"), 50, 2000, "Befunde: severity, path, line, rule, message", ("diff_analysis",)),
    ("diff_analysis", "code_review", 2, ("path",), ("base", "head", "max_files"), 50, 2000, "Befunde je geaenderter Datei", ("security_scan",)),
    ("pr_impact", "code_review", 2, (), ("auto_detect", "base_branch", "max_files", "path"), 50, 2000, "Befundliste: severity, path, line, message", ()),
    ("security_scan", "code_review", 2, ("path",), ("pattern_ids", "severity"), 100, 2000, "Funde: pattern_id, severity, path, line", ("unused_finder",)),
    ("unused_finder", "code_review", 2, ("path",), ("depth", "kinds", "max_files", "timeout"), 100, 2000, "ungenutzte Symbole/Importe", ()),
    ("duplicates", "code_review", 2, ("path",), ("max_files", "min_lines", "similarity_threshold", "timeout", "top_n"), 50, 2000, "Gruppen aehnlicher Bloecke: score, locations", ()),
    ("tests_for_symbol", "code_review", 2, ("path", "line"), ("language",), 50, 1500, "Testdateien/-funktionen, die das Symbol abdecken", ()),
    ("git_blame", "code_git", 1, ("path",), ("limit", "line"), 100, 1500, "Zeilen mit commit, author, date", ("git_log_symbol",)),
    ("git_diff_file", "code_git", 1, ("path",), ("context_lines", "staged"), 50, 2000, "Hunks mit alt/neu-Zeilen", ()),
    ("git_log_symbol", "code_git", 2, ("path", "line"), ("max_count",), 50, 2000, "Git-Ergebnis: commit, author, line", ("timeline",)),
    ("merge_conflict_finder", "code_git", 2, ("path",), (), 50, 2000, "Git-Ergebnis: commit, author, line", ()),
    ("timeline", "code_git", 2, ("path", "line"), ("max_commits",), 50, 2000, "Git-Ergebnis: commit, author, line", ()),
    ("index", "code_meta", 2, ("path",), ("depth", "force_rescan"), 50, 2000, "Index-Status: files, symbols, duration", ("workspace_summary",)),
    ("export", "code_meta", 3, ("path",), ("fmt", "kind"), 50, 2000, "Struktur-/Exportdaten als JSON", ()),
    ("dependency_risk", "code_meta", 2, ("path",), (), 50, 2000, "Struktur-/Exportdaten als JSON", ()),
    ("workspace_symbols", "code_meta", 1, ("query",), ("kind", "language", "limit", "path"), 100, 1500, "Symboltreffer: name, kind, path, line", ()),
    ("workspace_summary", "code_meta", 1, ("path",), ("depth",), 1, 1500, "Sprachen, Top-Verzeichnisse, Symbolzaehlung", ("workspace_symbols",)),
    ("docstring_generate", "code_meta", 3, ("path", "line"), ("style",), 50, 2000, "Docstring-Entwurf (Code)", ()),
    ("generate_tests", "code_meta", 3, ("path", "line"), ("framework", "language"), 50, 2000, "Testentwurf (Code) + Zielpfad", ("tests_for_symbol",)),

)

TIER_1 = 1
TIER_2 = 2
TIER_3 = 3


@dataclass(frozen=True, slots=True)
class OpMeta:
    """Metadaten eines einzelnen op."""

    op: str
    group: str
    tier: int
    required: tuple[str, ...]
    optional: tuple[str, ...]
    max_items: int
    max_tokens: int
    returns: str
    next_ops: tuple[str, ...] = ()

    @property
    def caps(self) -> dict[str, int]:
        """Output-Caps, wie 0.6.36 sie liest."""
        return {"max_items": self.max_items, "max_tokens": self.max_tokens}

    def as_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "group": self.group,
            "tier": self.tier,
            "required": list(self.required),
            "optional": list(self.optional),
            "caps": self.caps,
            "returns": self.returns,
            "next": list(self.next_ops),
        }


def _build(rows: Iterable[tuple[Any, ...]]) -> dict[str, OpMeta]:
    registry: dict[str, OpMeta] = {}
    for row in rows:
        meta = OpMeta(
            op=row[0],
            group=row[1],
            tier=int(row[2]),
            required=tuple(row[3]),
            optional=tuple(row[4]),
            max_items=int(row[5]),
            max_tokens=int(row[6]),
            returns=str(row[7]),
            next_ops=tuple(row[8]),
        )
        if meta.op in registry:
            raise ValueError(f"op_meta: doppelter Eintrag fuer {meta.op!r}")
        registry[meta.op] = meta
    return registry


OP_META: dict[str, OpMeta] = _build(_ROWS)


def op_names() -> tuple[str, ...]:
    """Alle registrierten op-Namen in Tabellenreihenfolge."""
    return tuple(OP_META)


def get(op: str) -> OpMeta | None:
    return OP_META.get(op)


def caps_for(op: str, default: Mapping[str, int] | None = None) -> dict[str, int]:
    """Caps eines op; fuer unbekannte Ops der Default (fail-open fuer Lesepfade)."""
    meta = OP_META.get(op)
    if meta is not None:
        return meta.caps
    return dict(default or {"max_items": 50, "max_tokens": 2000})


def build_registry(catalog: Mapping[str, Any] | None = None) -> dict[str, OpMeta]:
    """Registry zurueckgeben; mit ``catalog`` wird vorher fail-closed validiert."""
    if catalog is not None:
        problems = validate_against(catalog)
        if problems:
            raise ValueError("op_meta: Registry passt nicht zum Catalog:\n  - " + "\n  - ".join(problems))
    return dict(OP_META)


def _fmt_values(values: tuple[str, ...] = ()) -> str:
    return ", ".join(values) if values else "-"


def help_text(op: str, *, include_caps: bool = True) -> str:
    """Kurztext fuer ``op=help`` — genau ein kopierbares Beispiel plus Grenzen."""
    meta = OP_META.get(op)
    if meta is None:
        known = ", ".join(op_names()[:12])
        return f"unbekannter op {op!r}. Beispiele: {known}, ... (op=help ohne Argument listet alle)"
    lines = [
        f"op={meta.op}  (tool {meta.group}, tier {meta.tier})",
        f"  required: {_fmt_values(meta.required)}",
        f"  optional: {_fmt_values(meta.optional)}",
        f"  example:  {example_for(meta.op)}",
        f"  returns:  {meta.returns}",
        f"  next:     {_fmt_values(meta.next_ops)}",
    ]
    if include_caps:
        lines.append(f"  limits:   max_items={meta.max_items}, max_tokens={meta.max_tokens}")
    return "\n".join(lines)


_FIELD_DEFAULTS: dict[str, Any] = {
    "path": "src/app.py",
    "source": "src/a.py",
    "target": "src/b.py",
    "line": 42,
    "start_line": 1,
    "end_line": 40,
    "character": 1,
    "symbol": "parse",
    "query": "parse_config",
    "intent": "where is the retry logic",
    "error": "ImportError",
    "pattern": "$A.$B()",
    "rewrite": "$B($A)",
    "new_name": "new_name",
    "new_body": "return None",
    "code": "# note",
    "rules": [{"pattern": "old_call($A)", "rewrite": "new_call($A)"}],
    "rules_file": "migration-rules.yml",
}

_EXAMPLE_OVERRIDES: dict[str, dict[str, Any]] = {
    "metrics": {"path": "src/"},
    "pr_impact": {"base_branch": "main"},
    "workspace_symbols": {"query": "parse_config"},
    "query": {"intent": "where is the retry logic"},
    "graph_query": {"path": ".", "query": "callers", "symbol": "parse_config"},
    "search_by_error": {"path": ".", "error": "ImportError"},
    "review_assistant": {"path": ".", "base": "main", "head": "HEAD"},
    "diff_analysis": {"path": ".", "base": "main"},
    "migration": {"path": "src/", "rules": [{"pattern": "old_call($A)", "rewrite": "new_call($A)"}]},
    "hot_paths": {"path": "src/", "top_n": 10},
    "duplicates": {"path": "src/", "min_lines": 8},
    "unused_finder": {"path": "src/"},
    "security_scan": {"path": "src/"},
    "dependency_graph": {"path": "src/", "depth": 2},
    "workspace_summary": {"path": "."},
    "export": {"path": ".", "kind": "all", "fmt": "json"},
}


def example_for(op: str) -> dict[str, Any]:
    """Konkretes Input-Beispiel (immer mit ``op``) fuer Schema/Help."""
    meta = OP_META.get(op)
    if meta is None:
        return {"op": op}
    if op in _EXAMPLE_OVERRIDES:
        return {"op": op, **_EXAMPLE_OVERRIDES[op]}
    example: dict[str, Any] = {"op": op}
    for field in meta.required:
        example[field] = _FIELD_DEFAULTS.get(field, f"<{field}>")
    return example


def validate_against(catalog: Mapping[str, Any]) -> list[str]:
    """Drift zwischen Registry und echtem Registrierungs-Catalog.

    Prueft: jeder Catalog-op hat eine Meta, jede Meta einen Handler, required und
    optional stimmen mit dem Handler-Schema ueberein, tier/caps sind plausibel.
    Rueckgabe ist eine Liste von Problemen — leer heisst gruen.
    """
    problems: list[str] = []
    catalog_ops = {name[5:] for name in catalog if name.startswith("code_")}

    for op in sorted(catalog_ops - set(OP_META)):
        problems.append(f"{op}: im Catalog registriert, aber ohne op_meta-Eintrag")
    for op in sorted(set(OP_META) - catalog_ops):
        problems.append(f"{op}: op_meta-Eintrag ohne registrierten Handler")

    for op, meta in OP_META.items():
        if meta.tier not in (TIER_1, TIER_2, TIER_3):
            problems.append(f"{op}: tier {meta.tier} nicht in (1, 2, 3)")
        if meta.max_items <= 0 or meta.max_tokens <= 0:
            problems.append(f"{op}: caps muessen positiv sein ({meta.caps})")
        tool = catalog.get(f"code_{op}")
        if tool is None:
            continue
        params = ((tool.get("schema") or {}).get("parameters") or {})
        required = tuple(params.get("required") or ())
        properties = tuple(sorted((params.get("properties") or {})))
        if set(required) != set(meta.required):
            problems.append(
                f"{op}: required-Drift — Handler {list(required)} vs. Registry {list(meta.required)}"
            )
        expected_optional = {p for p in properties if p not in required and p != "op"}
        if expected_optional != set(meta.optional):
            problems.append(
                f"{op}: optional-Drift — Handler {sorted(expected_optional)} vs. Registry {sorted(meta.optional)}"
            )
        if meta.group and meta.op in meta.next_ops:
            problems.append(f"{op}: next_ops darf sich nicht selbst nennen")
    return problems
