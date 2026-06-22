# Split-Plan: code_tools.py + lsp/tools.py

## 1. code_tools.py (5135 Zeilen, 87 Funktionen)

### Bereits extrahiert (in tools/ Subpackage):
- `tools/symbols.py` — Symbol-Extraktion
- `tools/search.py` — AST-Suche
- `tools/complexity.py` — Complexity-Analyse
- `tools/impact.py` — Impact-Analyse
- `tools/capsule.py` — Symbol-Capsule
- `tools/unused.py` — Unused-Finder
- `tools/diagram.py` — Mermaid-Diagramme
- `tools/git.py` — Git-basierte Tools
- `tools/batch.py` — Batch-Refactoring
- `tools/security.py` — Security-Scan
- `tools/blame.py` — Git-Blame
- `tools/testgen.py` — Test-Generator
- `tools/overview.py` — Code-Overview

### Noch in code_tools.py (zu extrahieren):

**Phase A — Cache + Language (L1-700):**
→ `tools/cache.py`: persist_symbol_cache, load_symbol_cache, clear_symbol_cache, get_symbol_cache_stats, _set_cache, _invalidate_cache, _set_dir_cache, _find_project_root, _cache_key_for_path, _project_cache_path
→ `tools/language.py`: _init_languages, _get_language, _get_parser, detect_language, _classify_node

**Phase B — Type Hierarchy (L701-930):**
→ `tools/type_hierarchy.py`: _ast_type_hierarchy_supertypes, _ast_type_hierarchy_subtypes

**Phase C — Search (L1485-1810):**
→ Bleibt in code_tools.py (zu klein für eigenes Modul)

**Phase D — Workspace (L2136-2311):**
→ `tools/workspace.py`: code_workspace_summary_tool, _detect_lang_for_summary, _find_lang_folders, _count_extensions, _scan_workspace, _detect_monorepo_markers

**Phase E — Metrics (L2318-2510):**
→ `tools/metrics.py`: code_metrics_tool (bereits als modules/ benannt, nicht zu verwechseln mit tools/impact.py etc.)

**Phase F — SearchByError (L2624-2802):**
→ `tools/search_by_error.py`: code_search_by_error_tool + _handle_

**Phase G — HotPaths/Cycles/DepGraph (L2809-3051):**
→ `tools/graph_analysis.py`: code_hot_paths_tool, code_cycle_detector_tool, code_dependency_graph_tool

**Phase H — TestsForSymbol (L3079-3202):**
→ `tools/test_coverage.py`: code_tests_for_symbol_tool + alle _tests_* helpers

**Phase I — ReplaceBody/SafeDelete/Insert (L3212-4090):**
→ `tools/ast_edit.py`: code_replace_body_tool, code_safe_delete_tool, code_insert_before_tool, code_insert_after_tool + _find_symbol_in_ast, _ast_search_references

**Phase J — Move/Duplicates (L4109-4608):**
→ `tools/ast_move.py`: code_move_tool, code_duplicates_tool, _normalize

**Phase K — Export/Docstring/DepRisk (L4620-5054):**
→ `tools/export.py`: code_export_tool, code_docstring_generate_tool, code_dependency_risk_tool

### Re-Export Facade:
Nach Extraktion: Alle `from .tools.xxx import *` ans ENDE von code_tools.py.
code_tools.py wird zum reinen Re-Export-Facade (~200 Zeilen).

---

## 2. lsp/tools.py (3619 Zeilen, 27+ Tools)

### Bereits sauber strukturiert:
- `lsp/bridge.py` — LSP-Bridge-Klasse (2139 Zeilen, aber kohärent)
- `lsp/handlers.py` — Tool-Registration (123 Zeilen)
- `lsp/tools.py` — 27 Tool-Schemas + Implementierungen (3619 Zeilen)

### Extraktionsvorschlag:

**Phase A — Schemas + Tools pro Kategorie:**
→ `lsp/tools/__init__.py` — Re-Export Facade
→ `lsp/tools/schemas.py` — Alle CODE_*_SCHEMA Definitionen (können gemeinsam bleiben, ca. 1000 Zeilen)
→ `lsp/tools/navigation.py` — code_definition, code_references, code_diagnostics, code_hover, code_implementations, code_type_definition
→ `lsp/tools/hierarchy.py` — code_call_hierarchy, code_type_hierarchy, code_callers, code_callees
→ `lsp/tools/edit.py` — code_rename, code_format, code_action, code_completion, code_code_lens
→ `lsp/tools/display.py` — code_document_symbols, code_workspace_symbols, code_highlight, code_inlay_hints
→ `lsp/tools/folding.py` — code_folding_range, code_selection_range, code_linked_editing, code_prepare_rename
→ `lsp/tools/advanced.py` — code_semantic_tokens, code_document_links, code_inline_values, code_signatures

---

## Prioritärer Split (nächste Schritte)

1. **code_tools.py Phase A** — `tools/cache.py` + `tools/language.py` (~500 Zeilen entfernt)
   → Geringstes Risiko, keine Test-Anpassungen nötig (nur Importe)

2. **code_tools.py Phase D** — `tools/workspace.py` (~300 Zeilen entfernt)
   → Niedriges Risiko, klare Abhängigkeiten

3. **lsp/tools.py nach Kategorien** — Höheres Risiko durch viele Cross-Referenzen
   → Vorher Dependency-Analyse laufen lassen
