# Changelog

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
