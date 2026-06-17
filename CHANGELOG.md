# Changelog

## [0.28.11] — 2026-06-17

### Added
- **Symbol-Level Editing Tools** — Port von Serenas Kern-Features ins Plugin:
  - `code_replace_body`: Ersetzt die vollständige Definition eines Symbols (Funktion, Methode, Klasse) via AST. Unterstützt dry_run (Preview mit Diff), include_decorators und name_path-Syntax (z.B. "MyClass/my_method").
  - `code_safe_delete`: Löscht ein Symbol NUR wenn es keine externen Referenzen hat. Referenz-Check via grep über das Projekt. force=True überschreibt den Check.
  - `code_insert_before`: Fügt Code vor einem Symbol ein. Unterstützt newline-Flag und dry_run.
  - `code_insert_after`: Fügt Code nach einem Symbol ein. Unterstützt newline-Flag und dry_run.
  - `_find_symbol_in_ast`: Neuer Helper für AST-basierte Symbol-Suche mit Byte-genauen Boundaries (start_byte, end_byte). Unterstützt name_path-Parsing.
  - `_invalidate_cache`: Cache-Invalidierung nach Edit-Operationen.
- **Tests**: 36 neue Tests in `tests/test_code_edit_tools.py` (6x _find_symbol_in_ast, 8x replace_body, 8x safe_delete, 7x insert_before, 7x insert_after) — 36/36 pass.
- **31→35 Tools** im Plugin.
- **code_overview** — Kompakte Tree-Übersicht aller Symbole in einer Datei/Verzeichnis.
  depth=0 für Top-Level, depth=1 (default) inkl. Methoden, depth=2 für tiefere Nesting.
- **Tests**: 36 → 45 Tests in test_code_edit_tools.py + 9 Tests in test_code_overview.py
- **35→36 Tools** im Plugin.


### Added
- **26 E2E Tests** in 3 Phasen (A: Real-Tool-Calls, B: Cross-Workflows, C: Lifecycle)
  Phase A (14 Tests): AST+LSP+Edge Cases auf Plugin-eigene Quelldateien — echte Tools, keine Mocks
  Phase B (6 Tests): Workflow-Ketten wie code_search_by_error → code_definition → code_call_hierarchy
  Phase C (6 Tests): Plugin-Load, Registry, LSP-Init (pyright/tsserver), 31 Tools verifiziert
  Ausführung via `E2E_TEST=1 pytest tests/test_e2e_*.py -v`
- **generate_readme.py repariert + erweitert**: Version liest aus plugin.yaml, TOOLSETS-Anchor,
  pytest stdout, AST Languages aus _EXT_TO_LANG, META-Marker im Header, Hermes-Venv-Auto-Detection
- **Pre-Commit Hook**: README-Check von Warning→Blocking (generiert + staged README).
  Woodpecker CI: neuer `readme`-Step (`generate_readme.py --check`)
- **Skill-Audit**: 13 Skills auf 31 Tools aktualisiert — tool-choice-priorities, codebase-intelligence,
  skill-preflight, serena-code-review, codebase-audit, systematic-debugging, simplify-code, writing-plans,
  pre-commit-workflow-code-intel, debugging-workflow, requesting-code-review, execution-workflow,
  test-driven-development (+8 🟢 Projekt-Skills)

### Changed
- **Tests**: 1142 → 1176 (Unit) + 26 (E2E) = 1202 total
- **Pre-Commit**: README blocking bei Generator-Fehler
- **Woodpecker CI**: `readme`-Step vor lint/test
- **__init__.py**: Duplikate bereinigt (code_complexity 2x, type_hierarchy 2x), 4 fehlende Tools ergänzt

## [0.28.09] — 2026-06-17

### Added
- **code_pr_impact Tool**: Neues Hybrid-Tool für PR-Impact-Analyse.
  Kombiniert git diff (geänderte Files), ImportGraph (Blast Radius),
  Test-Coverage-Prüfung und git blame (Reviewer-Vorschläge).
  Parameter: base_branch (default: main), max_files (default: 10).
  Registriert als 31. Tool (13 AST + 18 LSP).
- **Tests**: 5 neue code_pr_impact Tests

### Changed
- **Tests**: 1137 → 1142 (+5 code_pr_impact Tests)

## [0.28.08] — 2026-06-17

### Added
- **code_blast_radius Tool**: Neues Hybrid-Tool für Blast-Radius-Analyse.
  Kombiniert LSP callHierarchy (direkte Caller), ImportGraph (transitive
  Caller via Datei-Import-Graph) und code_tests_for_symbol (Test-Coverage).
  Impact-Klassifikation (HIGH/MEDIUM/LOW) mit Empfehlungen.
  Registriert als 30. Tool (12 AST + 18 LSP).
- **Tests**: 9 neue code_blast_radius Tests

### Changed
- **Tests**: 1128 → 1137 (+9 code_blast_radius Tests)

## [0.28.07] — 2026-06-17

### Added
- **code_hot_paths Tool**: Neues Tool zur Hot-Path-Erkennung mittels ImportGraph.
  Scannt ein Projektverzeichnis, parst alle Importe und rankt Dateien nach
  transitiven Caller-Counts. Parameter: top_n (default 10), depth (default 5).
  Registriert als 29. Tool (11 AST + 18 LSP).
- **Tests**: 5 neue code_hot_paths Tests

### Changed
- **Tests**: 1123 → 1128 (+5 code_hot_paths Tests)

## [0.28.06] — 2026-06-17

### Added
- **code_search_by_error Tool**: Neues AST-Tool zum Finden von Error-Handling-Stellen.
  Unterstützt Python (raise/except/custom-classes), TypeScript/TSX (throw/catch/extends),
  Go (fmt.Errorf) und Rust (Err/Result). Sucht rekursiv in Verzeichnissen oder
  einzelnen Dateien. Gruppiert Ergebnisse in raise/throw, catch/except und custom_classes.
  Registriert als 28. Tool (10 AST + 18 LSP).
- **Tests**: 10 neue code_search_by_error Tests

### Changed
- **Tests**: 1113 → 1123 (+10 code_search_by_error Tests)

## [0.28.05] — 2026-06-17

### Added
- **code_complexity Tool**: Neues AST-Tool für zyklomatische Komplexitätsanalyse.
  Unterstützt Python, TypeScript, TSX, Go und Rust. Zählt Branches (if/switch),
  Loops (for/while), Exceptions (try/catch) und Early Returns. Reports total
  mit Breakdown und Rank (A-E). Tool-Funktion via `code_complexity(path, function/line)`.
  Registriert als 27. Tool (9 AST + 18 LSP).
- **Tests**: 9 neue code_complexity Tests

### Changed
- **Tests**: 1104 → 1113 (+9 code_complexity Tests)

## [0.28.04] — 2026-06-17

### Added
- **code_type_hierarchy Tool**: Neues LSP-Tool (`textDocument/typeHierarchy`)
  zum Finden der Typ-Hierarchie eines Symbols. Nutzt LSP typeHierarchy
  für Java/C#/Swift, AST-basierte Analyse für Python/TypeScript (da pyright
  und tsserver TypeHierarchy nicht unterstützen). Richtungen: supertypes,
  subtypes, both. Registriert als 26. Tool (8 AST + 18 LSP).
- **LSP Bridge**: Neue Methoden `type_supertypes()` und `type_subtypes()`
  in LSPBridge für `prepareTypeHierarchy` + `supertypes`/`subtypes` Requests.

### Changed
- **Tests**: 1104 → 1104 (+9 type_hierarchy, -9 entfernte ImportGraph-Debug-Logs)
- **LSP Capabilities**: `typeHierarchy` im initialize-Request deklariert

## [0.28.03] — 2026-06-17

### Added
- **code_call_hierarchy Tool**: Neues LSP-Tool (`textDocument/callHierarchy`)
  zum Finden der Call-Hierarchy eines Symbols. Unterstützt incoming/outgoing
  Calls mit konfigurierbarer transitiver Tiefe (max_depth=1-5), Begrenzung
  pro Level (max_callers_per_level=20) und formatierter Tree-Ausgabe.
  Nutzt existierende `incoming_calls()`/`outgoing_calls()` Bridge-Methoden.
  Registriert als 25. Tool (8 AST + 17 LSP).
- **Tests**: 9 neue code_call_hierarchy Tests

### Changed
- **Tests**: 1095 → 1104 (+9 code_call_hierarchy Tests)
- **LSP Capabilities**: `callHierarchy` im initialize-Request deklariert

## [0.28.02] — 2026-06-17

### Added
- **ImportGraph Foundation**: Neue Utility `_import_graph.py` für
  AST-basierten Import-Graphen über Python/TypeScript/Go/Rust.
  Methoden: `scan()`, `parse_imports()`, `parse_all()`, `find_cycles()`,
  `find_hot_paths()`, `analyze_blast_radius()`, `to_mermaid()`, `to_tree()`.
  Wiederverwendet von code_cycle_detector, code_dependency_graph,
  code_unused_finder, code_hot_paths, code_blast_radius, code_pr_impact.
- **Tests**: 35 neue ImportGraph-Tests

### Changed
- **Tests**: 1060 → 1095 (35 neue ImportGraph-Tests)

## [0.28.01] — 2026-06-17

### Added
- **code_document_symbols Tool**: Neues LSP-Tool (`textDocument/documentSymbol`) zum
  Abrufen ALLER Symbole einer Datei (Funktionen, Klassen, Variablen, Konstanten,
  Typ-Aliase) als hierarchischen Baum. Ergänzt das AST-basierte code_symbols mit
  LSP-Ebene-Informationen und korrekter Verschachtelung.
  Registriert als 24. Tool (8 AST + 16 LSP).
- **TSX: React-Komponenten-Erkennung**: PascalCase-Funktionen in `.tsx`-Dateien
  werden als `component` klassifiziert (statt `function`). `useXxx`-Funktionen
  als `hook`.
- **TSX: "use client"/"use server" Directives**: Werden als `directive`-Symbol
  in code_symbols erfasst (erkennbar an Zeile 1 der Datei).
- **TSX: `_SYMBOL_QUERIES["tsx"]` erweitert**: `enum_declaration`,
  `export default function/class` und Directive-Queries hinzugefügt.

### Changed
- **Tools**: 23 → 24 (8 AST + 16 LSP)
- **Tests**: 1060 passed, 34 skipped (keine Regression)

### Fixed
- **Registration-Log**: Enthält jetzt alle 16 LSP-Tools inkl. code_inlay_hints
  (fehlte seit v0.28.00) und code_document_symbols (v0.28.01 neu).

## [0.28.00] — 2026-06-17

### Added
- **code_highlight Tool**: Neues LSP-Tool (`textDocument/documentHighlight`) zum
  Finden ALLER Vorkommen eines Symbols in der aktuellen Datei (file-local).
  Schneller als code_references für lokale Matches. Unterscheidet kind (text/read/write).
  Registriert als 22. Tool.
- **code_inlay_hints Tool**: Neues LSP-Tool (`textDocument/inlayHint`) für
  inferierte Typ-Hints inline (`: string`, `: number[]`). Unterstützt Type/Parameter-Kinds.
  Registriert als 23. Tool.
- **Sub-Projekt-Roots (Infrastruktur A)**: `_find_workspace_root()` erkennt jetzt
  Sub-Projekt-Marker (`next.config.ts`, `medusa-config.ts`, `tsconfig.json+package.json`)
  bevor es zum Monorepo-Root springt. Überspringt Monorepo-Roots (`package.json` mit
  `workspaces`-Feld) zugunsten spezifischerer Sub-Projekt-Roots. Mit LRU-Cache (TTL 300s).
- **tree-sitter-typescript + tree-sitter-javascript**: Pip-Packages installiert —
  TSX/JSX-Parser und LSP-Support jetzt aktiv (waren zuvor stumm tot).

### Changed
- **Tools**: 21 → 23 (8 AST + 15 LSP)
- **Tests**: 1055 → 1060 passed (34 skipped)
- **Workspace-Root-Cache**: Neue `_WORKSPACE_ROOT_CACHE` verhindert wiederholte
  Filesystem-Scans für wiederholte LSP-Operationen
- **Registration-Log**: Aktualisiert auf alle 15 LSP-Tools

### Fixed
- **TSX/JSX war stumm tot**: `tree-sitter-typescript` und `tree-sitter-javascript`
  waren nicht im Hermes-Venv installiert → TSX-Parser und LSP-Support wurden nie
  geladen. (Bug seit v2.0.0, niemandem aufgefallen)
- **`find_references` regression**: Vereinfachte Version (v0.28.00-dev) verlor
  Normalisierung + Retry-Logik für TypeScript — wiederhergestellt

## [0.27.02] — 2026-06-17

### Added
- **code_format Tool**: Neues LSP-Tool (`textDocument/formatting`) für automatische
  Code-Formatierung via pyright/tsserver/gopls. Mit diff-preview (dry_run=True) und
  safe-apply mit reverse-order editing. Registriert als 20. Tool.
- **code_implementations Tool**: Neues LSP-Tool (`textDocument/implementation`)
  zum Finden von Interface-Implementierungen, abstrakten Methoden und Overrides.
  Registriert als 21. Tool.

### Fixed
- **5 Bugs via Fuzzing**: `_dispatch()` crashte bei `window/logMessage` mit
  `params=None`, bei `publishDiagnostics` mit `uri=None`, `diagnostics='string'`
  oder `diagnostics=[None]`. `_uri_to_path()` crashte bei `uri=None`.
  `_format_definitions`/`_format_references` crashten bei fehlenden Keys.
- **_import_detect_language()**: Fehlende relative Import-Stufe ergänzt (4 statt 3)

### Changed
- **Complexity reduziert**: `_ast_fallback_definition` (C=25→entfällt) durch
  Wiederverwendung von `_import_detect_language()` und `_extract_identifier()`.
  `code_symbols_tool` (C=25→entfällt) durch Extraktion von
  `_symbols_extract_single()` und `_symbols_scan_directory()` (letzte Session).
- **Tools**: 19 → 21 (8 AST + 13 LSP)
- **Tests**: 1055 passed (default), 1089 (mit LSP_TEST=1)

## [0.27.01] — 2026-06-17

### Added
- **Property-based tests (Hypothesis)**: 11 neue Tests in `test_property_based.py`
  — generiert random Code-Snippets (py/ts/rs/js/go) + Edge Cases,
  prüft dass `code_symbols_tool`/`code_search_tool`/`code_capsule_tool`/`code_query_tool`
  nie crashen
- **Integration tests mit echten LSP-Servern**: 24 Tests in `test_lsp_integration.py`
  — pyright-langserver (12 Tests), tsserver (6), gopls (6)
  — echte go-to-definition, references, hover, diagnostics, workspace_symbols
  — übersprungen ohne `LSP_TEST=1`
- **Nightly Cron-Job**: `nightly_plugin_check.py` läuft täglich 3:00,
  meldet nur bei Regressionen (Tests, Ruff, Health, Benchmarks, Git-Status)

### Changed
- **Tests**: 942 → 953 (ohne LSP-Integration) / 977 (mit LSP-Integration)

## [0.27.00] — 2026-06-17

### Changed
- **Version scheme**: 2.7.0 → 0.27.00 — neues Schema:
  `0.{major2stell}{minor2stell}.{patch2stell}`,
  Patch zählt +1 pro Release
  (0.27.00 → 0.27.01 → ... → 0.27.99 → 0.28.00)

## [2.7.0] — 2026-06-16

### Added
- **LSP Detection für Rust, Go, Java, C/C++**: `_detect_language_for_lsp()` mapped jetzt
  `.rs→rust`, `.go→go`, `.java→java`, `.c→c`, `.cpp→cpp` (🔴 Bugfix, Phase A)
- **4 neue Tests**: Rust, Go, Java, C/C++ LSP-Detection
- **`_logging.py` 100% Coverage**: 4 Tests für `safe_read_text()` Exception-Pfade + `setup_logger()`
- **`scripts/generate_readme.py`**: README Auto-Generation aus Code
- **Shared Logging Handler**: `get_stderr_handler()` eliminiert byte-level stderr Interleaving

### Changed
- **`code_capsule_tool` refactored** (C=33→9): 5 Sub-Funktionen extrahiert
- **`code_tests_for_symbol_tool` refactored** (C=30→6): 4 Sub-Funktionen (find/score/calc)
- **`code_workspace_symbols_tool` refactored** (C=28→C<12): Anchor-Probing + Result-Formatierung
- **`_ast_fallback_references` refactored** (C=27→6): 3 Sub-Funktionen (import/identifier/rg)
- **`_handle_code_intel_slash` refactored** (C=25→entfällt): 2 Sub-Funktionen für `/code-intel status`
- **`code_intel.py` + `lsp_bridge.py`**: Nutzen jetzt `_logging.get_stderr_handler()` (shared handler)
- **Ruff**: `# noqa` Directive korrigiert + `except Exception as exc` → `except Exception` (2 Fixes)
- **Health Check**: Stale Log-Einträge bereinigt (3 Warnings eliminiert)

### Fixed
- **🔴 Critical**: `_detect_language_for_lsp()` mappte `.rs→None`, `.go→None` — LSP wurde für
  Rust/Go/Java/C/C++ nie probiert (stummer AST-Fallback, seit v2.0.0)

### Infrastructure
- `_logging._shared_handler` Singleton: Ein StreamHandler für alle Module
- `generate_readme.py` mit `--check` Flag für CI
- `.gitignore` bereits korrekt (`.coverage`, `.ruff_cache/` ausgeschlossen)

## [2.6.0] — 2026-06-16

### Added
- **LICENSE**: Dual copyright (Johannes Lettner + Renato Wasescha Fork-Notice)
- **gopls installiert**: v0.16.1 via apt — Go LSP jetzt verfügbar

### Changed
- **`extract_symbols` refactored** (C=38→~6): In 4 Sub-Funktionen aufgespalten (`_setup_query`, `_classify_symbol_kind`, `_detect_if_method`, `_extract_candidate`). Logik unverändert, Testbarkeit verbessert.
- **`_ast_fallback_diagnostics` refactored** (C=34→~4): In 5 Sub-Funktionen aufgespalten (`_read_file_safe`, `_python_ast_analyze`, `_build_unused_import_diags`, `_tsjs_import_heuristic`, `_format_diagnostics_result`).
- **`code_callers_tool` refactored** (C=28→~5): In 4 Sub-Funktionen aufgespalten (`_resolve_target_and_lang`, `_try_lsp_callers`, `_fallback_reference_callers`, `_group_by_file`).
- **`_ast_fallback_callees`**: Nutzt jetzt `_read_file_safe` (reuse statt Duplikat)
- **Ruff Lint**: Von 109 auf 0 Errors reduziert (82 auto-fixed, 26 unsafe-fixed, 1 noqa)

### Fixed
- **3 Trailing-Whitespace/Blank-Line Warnings** in Test-Dateien (W291/W293)
- **README Title v2.1.0→v2.5.0, Test-Count 917+→934+** (Version-Drift behoben)

## [2.5.0] — 2026-06-16

### Fixed
- **P0-1 Thread-Safety**: Lock-Race in `lsp_bridge._send_request()` — `_responses.pop()` und `_pending.pop()` außerhalb des Locks. Race zwischen Dispatch-Thread (schreibt) und Hermes-Thread (liest/konsumiert). Gremium: Alle 3 Zugriffe (`responses.pop`, 2x `pending.pop`) jetzt unter `self._lock`.
- **P0-2 Logger NoneType**: 5 Logger mit `%d` für `character` (kann `None` sein) → `%s` geändert. Betroffen: `goto_definition`, `find_references`, `hover` (bridge) + `code_references_tool`, `code_rename` (tool). `code_definition_tool` war bereits korrekt (%s).
- **P1-7 plugin.yaml hooks**: `pre_llm_call` Hook deklariert (war aktiv aber nicht dokumentiert)

### Changed
- **P1-6 .gitignore**: `.coverage` und `.ruff_cache/` hinzugefügt

## [2.4.0] — 2026-06-16

### Added
- **Pre-Commit Hook v2**: 12 Checks statt 4 — Ruff Lint, Merge-Conflict-Detection, Secret-Scanner, Trailing-Whitespace, YAML/TOML-Validation, Large-File-Warning, CHANGELOG-Discipline
- **Pre-Commit-Workflow-Skill**: `pre-commit-workflow-code-intel` Skill dokumentiert alle 12 Checks, Ausführungsreihenfolge, Wann-welche-Checks-Tabelle, Troubleshooting

## [2.3.0] — 2026-06-16

### Added
- **CI/CD Pipeline**: Woodpecker CI (.woodpecker.yml) — Lint (ruff), TypeCheck (pyright), Test (pytest + coverage), Release (build on tag)

### Changed
- Fork-Rename: `agentiker-code-intel-plugin` (vorher: `hermes-code-intel-plugin`)
- pyproject.toml: name + authors auf agentiker.de / ivory.green Team aktualisiert
- plugin.yaml: version 2.1.0, author, repo auf ivory.green
- README.md: Titelleiste + Fork-Notice + Upstream-Referenz aktualisiert

## [2.2.0] — 2026-06-16

## [2.1.0] — 2026-06-16

### Added
- **Resource Limits**: RLIMIT_AS (2GB), RLIMIT_RSS (1GB), RLIMIT_CPU (60s) für LSP Subprozesse via `preexec_fn`
- **Startup Crash Detection**: Poll-Loop (0.5s) nach Popen — erkennt immediate crashes
- **LSP Circuit Breaker**: 3 Fehler → exponentielles Backoff (30s, 60s, 120s, … max 600s)
- **Bridge-Evict Kill-Fallback**: Kill + wait(3s) wenn shutdown den LSP-Prozess nicht beendet

### Changed
- `_start_and_init`: `import resource` + `preexec_fn=_set_limits` für LSP Subprozesse
- `ensure_initialized`: Checkt `_lsp_circuit_open()` vor Init-Versuch
- `LSPManager.get_bridge`: Kill-Fallback beim Evict des ältesten Bridges

### Fixed
- `_start_and_init`: `subprocess.Popen` ohne `preexec_fn` konnte unbegrenzt RAM verbrauchen
- LSP Server Zombies: shutdown allein beendete Prozesse nicht immer — jetzt Kill-Fallback
- Wiederholte Fehlversuche: Circuit Breaker verhindert endless retry loops

## [2.0.0] — 2026-06-16

### Added
- LSP Server für Rust (`rust-analyzer`) und Go (`gopls`) in `_LANGUAGE_SERVERS`
- `_wait_for_document_ready()` Hilfsmethode für zentrales Delay-Management
- LSP Call Hierarchy für `code_callers` (incomingCalls) und `code_callees` (outgoingCalls)
- `_logging.py` — zentrale Logger-Factory (ersetzt Duplikate)
- `_reconcile_close_uris` LRU Bounded (max 1000 Einträge)
- 10 neue Tests (code_query intents, Rust/Go Configs, AST-Fallback)
- Health Check Script: Auto-Discovery für TS-Test-Dateien, pyright-langserver Support
- `pyproject.toml` mit Metadaten, Coverage-Config, Test-Filtern
- Thread-Safety: `_dispatch()` + `shutdown()` unter `self._lock`
- 16 neue code_query Intents (hover, signature, type_definition, quick_fix, workspace_search)

### Changed
- `code_intel.py` + `lsp_bridge.py`: Dupliziertes Logging-Setup durch `_logging.setup_logger()` ersetzt
- `code_impact_tool`: Regex-basierte Import-Extraktion durch tree-sitter `code_search` ersetzt (Python, TS, Rust, Go, Java)
- `_QUERY_INTENT_MAP`: `rename` → `code_rename` (LSP, scope-aware) statt `code_refactor`
- `_reconcile_close_uris`: Dict → OrderedDict mit LRU-Eviction
- `register()` in `__init__.py`: 1 Monsterfunktion → 6 Sub-Funktionen
- Silent Exception Handler: 4 mit `logger.debug()` versehen
- Health Check Script: Vollständig überarbeitet (10 Checks, auto-discover)
- **28 `time.sleep()`** auf **2 reduziert** (zentraler Helper + workspace retry)

### Fixed
- Health Check Script: Pfade von `HERMES_AGENT/tools/` nach `PLUGIN_DIR` korrigiert
- Health Check Script: Hardcodiertes Monorepo durch Auto-Discovery ersetzt
- Thread-Safety Race in `_dispatch()` (Reader-Thread vs Sender-Thread)
- Thread-Safety in `shutdown()` (Shared-State unter `self._lock`)
- code_impact: Fehler bei `Path.read_text`-Mock (Test angepasst)
- `_read_loop` outer exception: korrekt mit `logger.debug()` versehen

### Removed
- Dupliziertes Logging-Setup (24 Zeilen × 2 Module → 1× _logging.py)
- "Gateway Restart Required" Warnung im Bundled Skill (obsolet)
- Alte `MONOREPO = Path("~/GIT/AgentSelly/monorepo")` hardcodierung

---

## [1.0.0] — 2026-04-16

Initial release des Plugins als Fork von `rewasa/hermes-code-intel-plugin`.
19 Tools (8 AST + 11 LSP), initiale Test-Suite.
