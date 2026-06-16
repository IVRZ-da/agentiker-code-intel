# Changelog

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
