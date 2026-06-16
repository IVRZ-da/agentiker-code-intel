1|# Changelog
2|
3|## [2.1.0] — 2026-06-16

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
4|
5|### Added
6|- LSP Server für Rust (`rust-analyzer`) und Go (`gopls`) in `_LANGUAGE_SERVERS`
7|- `_wait_for_document_ready()` Hilfsmethode für zentrales Delay-Management
8|- LSP Call Hierarchy für `code_callers` (incomingCalls) und `code_callees` (outgoingCalls)
9|- `_logging.py` — zentrale Logger-Factory (ersetzt Duplikate)
10|- `_reconcile_close_uris` LRU Bounded (max 1000 Einträge)
11|- 10 neue Tests (code_query intents, Rust/Go Configs, AST-Fallback)
12|- Health Check Script: Auto-Discovery für TS-Test-Dateien, pyright-langserver Support
13|- `pyproject.toml` mit Metadaten, Coverage-Config, Test-Filtern
14|- Thread-Safety: `_dispatch()` + `shutdown()` unter `self._lock`
15|- 16 neue code_query Intents (hover, signature, type_definition, quick_fix, workspace_search)
16|
17|### Changed
18|- `code_intel.py` + `lsp_bridge.py`: Dupliziertes Logging-Setup durch `_logging.setup_logger()` ersetzt
19|- `code_impact_tool`: Regex-basierte Import-Extraktion durch tree-sitter `code_search` ersetzt (Python, TS, Rust, Go, Java)
20|- `_QUERY_INTENT_MAP`: `rename` → `code_rename` (LSP, scope-aware) statt `code_refactor`
21|- `_reconcile_close_uris`: Dict → OrderedDict mit LRU-Eviction
22|- `register()` in `__init__.py`: 1 Monsterfunktion → 6 Sub-Funktionen
23|- Silent Exception Handler: 4 mit `logger.debug()` versehen
24|- Health Check Script: Vollständig überarbeitet (10 Checks, auto-discover)
25|- **28 `time.sleep()`** auf **2 reduziert** (zentraler Helper + workspace retry)
26|
27|### Fixed
28|- Health Check Script: Pfade von `HERMES_AGENT/tools/` nach `PLUGIN_DIR` korrigiert
29|- Health Check Script: Hardcodiertes Monorepo durch Auto-Discovery ersetzt
30|- Thread-Safety Race in `_dispatch()` (Reader-Thread vs Sender-Thread)
31|- Thread-Safety in `shutdown()` (Shared-State unter `self._lock`)
32|- code_impact: Fehler bei `Path.read_text`-Mock (Test angepasst)
33|- `_read_loop` outer exception: korrekt mit `logger.debug()` versehen
34|
35|### Removed
36|- Dupliziertes Logging-Setup (24 Zeilen × 2 Module → 1× _logging.py)
37|- "Gateway Restart Required" Warnung im Bundled Skill (obsolet)
38|- Alte `MONOREPO = Path("~/GIT/AgentSelly/monorepo")` hardcodierung
39|
40|---
41|
42|## [1.0.0] — 2026-04-16
43|
44|Initial release des Plugins als Fork von `rewasa/hermes-code-intel-plugin`.
45|19 Tools (8 AST + 11 LSP), initiale Test-Suite.
46|