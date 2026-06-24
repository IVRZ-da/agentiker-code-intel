---
name: agentiker-code-intel
description: "agentiker-code-intel — 69 Tools, ~1315 Tests, tools/ + lsp/ Subpackages, LSP 3.18, Git-Tools, Custom-Tools."
version: 0.6.2
---

# Code-Intel Plugin Maintenance Patterns

Consolidated from systematic improvement sessions (2026-06-16 through 2026-06-21).
Covers: LSP fuzzing, property-based testing, complexity refactoring,
LSP tool creation, Tool-Profile-System (5 profiles), nightly watchdog cron,
generate_readme updates, CI/CD (.woodpecker.yml), scripts/version_check.py,
conftest.py Hermes mock infrastructure, Skill Hub-Split, analysis-plugin sync,
*v0.6.2: Versionierung auf 0.00.01-Schema. 69 Tools, ~1326 Tests,*
and all earlier patterns. Applies after plugin relocation, when adding language
support, or before release.

**Bug Hunts:** `references/bughunt-2026-06-18.md` (14 Findings),
`references/bughunt-2026-06-20.md` (9 Findings — TOCTOU Regression, shutdown
Logging Error, fmt-Migration, close_document Race, orphan Events).
**⚠️ Stale-Finding Warning:** Findings aus `bughunt-2026-06-20.md` teilweise veraltet
— 4/10 in v0.3.1 gefixt. Siehe `references/bughunt-2026-06-20-verification-2026-06-21.md`
für aktuellen Stand.
**Regression:** `references/regression-verification-pattern.md`

## Related Skills
- `codebase-audit` — systematic codebase assessment
- `codebase-intelligence` — using the code-intel tools
- `pre-commit-workflow-code-intel` — pre-commit hook setup
- `skill-preflight` — mandatory preflight checklist

## Pattern 1: Health-Check-Script nach Plugin-Relocation

**Trigger:** `scripts/health_check.py` zeigt "file not found" oder LSP-Subprozesse timeoouten.

**Fix (4 Stellen):**
1. **Pfade:** `PLUGIN_DIR = Path("~/.hermes/plugins/code_intel")`
2. **sys.path:** `sys.path.insert(0, str(PLUGIN_DIR.parent))`
3. **cwd:** `cwd=str(PLUGIN_DIR)` bei subprocess.run()
4. **Imports:** `from code_intel.code_intel` / `code_intel.lsp_bridge`

**LSP-Subprozess:** pyright braucht `["--stdio"]` args; goto_definition column ≥ 16.

## Pattern 2: Fehlende LSP-Server-Konfiguration ergänzen

**Trigger:** `code_definition`/`code_references` fallen für Sprache auf AST-Fallback.

**Fix — 4 Stellen prüfen:**
1. `_LANGUAGE_SERVERS` in `lsp_bridge.py`
2. `_detect_language_for_lsp()` in `lsp_bridge.py` — die `lang_map` muss Extension→Key abbilden
3. `_find_workspace_root()` generic_markers (Cargo.toml, go.mod etc.)
4. `detect_language()` in `code_intel.py` (extension mapping)

**🔴 Gatekeeper-Falle:** `_detect_language_for_lsp()` (lsp_bridge.py ~1463) ist der GATEKEEPER. Wenn Extension hier fehlt, wird `lang=None` und der LSP-Pfad in ALLEN Tools **stumm übersprungen** (AST-Fallback, kein Log-Hinweis). Rust/Go LSP waren 6 Releases unsichtbar, weil `.rs→rust`/`.go→go` hier fehlte. Immer zuerst alle 4 Stellen prüfen.

**🔴 TSX/JSX Fallstrick:** `tree-sitter-typescript` + `tree-sitter-javascript` nicht installiert → `code_symbols` auf `.tsx` gibt leere Resultate (Plugin-Code ist korrekt). Fix:
```bash
~/.hermes/hermes-agent/venv/bin/pip install tree-sitter-typescript tree-sitter-javascript
```
Verifikation: `$VENV/bin/python3 -c "from tree_sitter import Language; import tree_sitter_typescript as tsts; print('tsx:', Language(tsts.language_tsx()))"`

## Pattern 3: Sleep-Akkumulation in LSP-Bridge

**Problem:** Jede LSP-Operation hatte dupliziertes `if-else-time.sleep()`.

**Fix — zentraler Helper:**
```python
def _wait_for_document_ready(self, is_first_request: bool = False) -> None:
    if self.language_id in ("typescript", "javascript", ...):
        delay = 0.5 if is_first_request else 0.05
    elif self.language_id == "python":
        delay = 0.05 if is_first_request else 0.01
    else: delay = 0.01
    time.sleep(delay)
```
Nach `open_document()` NUR diesen Helper — nie `time.sleep()` inline. Ausnahme: workspace_symbol retry (1.0s nach leerem Resultat).

## Pattern 4: README Auto-Generation

**Generator:** `scripts/generate_readme.py`. Datenquellen: `plugin.yaml` (Version — Single-Source-of-Truth), `__init__.py` (Tools), `lsp_bridge.py` (LSP-Sprachen), `code_intel.py` (AST-Sprachen), CHANGELOG.md, pytest.

**🔴 Pitfall:** Generator hat KEINE Validierung — sagt "is current" auch bei falschen Quellen. Nie blind vertrauen.
**Quick-Check:** `grep "Version:" README.md | head -1 && grep -oP 'Tools \(\K\d+' README.md | head -1 && grep "Tests:" README.md`

**Usage:** `python scripts/generate_readme.py` / `--check` (CI) / `--verbose`
**Details:** `references/readme-generator-bugs.md` (6 bekannte Bugs, 5 Verbesserungen)

## Pattern 5: Pre-Commit Hook Enhancement

**Selective Testing:**

| Geänderte Datei | Relevante Tests |
|----------------|-----------------|
| `code_intel.py` | `test_code_intel*`, `test_code_intel_tools*` |
| `lsp_bridge.py` | `test_lsp_bridge*`, `test_lsp_lifecycle`, `test_lsp_manager` |
| `_import_graph.py` | `test_import_graph*`, `test_code_cycle_detector*`, `test_code_dependency_graph*` |
| `__init__.py` | `test_plugin_init*`, `test_plugin_init_registry*` |
| Mehrere Module | `pytest -q -x` (voll) |
| Nur Docs/MD | README-Check + Version-Consistency, keine Tests |

**Pre-Commit ist blocking:** README generieren → bei Erfolg stage → bei Fehler Commit-Abbruch. `--no-verify` zum Überspringen.

**Version-Consistency Check:** `plugin.yaml == pyproject.toml == CHANGELOG.md`
```python
versions = {"plugin.yaml": ..., "pyproject.toml": ..., "CHANGELOG.md": ...}
if len(set(versions.values())) > 1: print("⚠️ VERSION DRIFT")
```

## Pattern 6: CI/CD Pipeline (Woodpecker)

**Context:** ivory.green Infrastruktur verwendet Woodpecker CI (`.woodpecker.yml`), nicht Forgejo Actions. Server läuft auf Hetzner (kein öffentliches Web-UI).

```yaml
steps:
  - name: lint / typecheck / test / release (nur bei tag)
    image: python:3.12
    commands:
      - pip install ruff pyright
      - ruff check . && pyright .
      - pip install uv && uv pip install --system ".[dev]"
      - python -m pytest tests/ -v --tb=short
```

**Pitfalls:** Keine `runs-on`/`uses:` Syntax — direkt `image:` + `commands:`. `when:` auf Stufen-Ebene für bedingte Ausführung (`event: tag`). Secrets via Woodpecker UI, Referenz: `from_secret: secret_name`.

## Pattern 7: Version Consolidation

**Schema:** `0.{major2stell}{minor2stell}.{patch2stell}`. Patch zählt +1 pro Release. Bei `0.27.99` → Minor springt auf `28` (`0.28.00`).

**Release-Workflow:**
```bash
# 1. CHANGELOG.md aktualisieren  2. Version pyproject.toml+plugin.yaml
# 3. python scripts/generate_readme.py  4. git add -A && git commit
# 5. git tag -a vX.Y.Z -m "..."  6. git push && git push --tags
```

## Pattern 8: Fork Rename (Branding)

| Ändern | Nicht ändern (zu invasiv) |
|--------|--------------------------|
| `pyproject.toml`: name + authors | Python-Paketname `code_intel` (Plugin-ID) — bricht alle Imports |
| `plugin.yaml`: version, author, repo | Verzeichnis `~/.hermes/plugins/code_intel/` |
| `README.md`: Titel, Fork-Notice, Credits | `.git/config` `core.hooksPath` |
| `CHANGELOG.md`: Rename-Eintrag | `ORIGIN` remote (bleibt auf Upstream) |
| Git Remote: `ivory-git` URL aktualisieren | |

**🔴 Critical: `code_intel.py` hat 18 eigene `toolset="code_intel"` Einträge** — beide Dateien prüfen: `grep -rn 'toolset="code_intel"' code_intel.py lsp_bridge.py`

**🔴 Test-Assertions patchen:** Nach Toolset-Rename schlagen 20+ Assertions in 8 Test-Dateien fehl. Patches zwischen Code-Änderung und Hermes-Neustart ausführen.

**Deep Rename (Package-Umbenennung):** Siehe `references/deep-rename-full.md` — 10-Touchpoint-Analyse, Risk-Klassifizierung, Bulk-Replaces.

## Pattern 9: gopls Installation auf Debian

**Problem:** `go install gopls@latest` scheitert auf Go 1.25+ (Build-Fehler in `tokeninternal`).
**Fix:** `sudo apt-get install -y gopls` (v0.16.1+). Keine Code-Änderung nötig — `_LANGUAGE_SERVERS` hat bereits `"go"`.

## Pattern 10: Health Check Log-Scan Warnings bereinigen

**Fix:** Nach Problembehebung alte Log-Einträge aus `~/.hermes/logs/errors.log` entfernen:
```bash
head -1 "$LOGFILE" > /tmp/clean && grep -v -i "no lsp bridge\|timeout\|lsp_error\|timed out" "$LOGFILE" >> /tmp/clean && mv /tmp/clean "$LOGFILE"
```
Alternative: `: > ~/.hermes/logs/errors.log && pkill hermes && hermes`

## Pattern 11: Ruff Cleanup als Pre-Release Step

```bash
cd ~/.hermes/plugins/code_intel
$VENV/bin/python3 -m ruff check --fix . && ruff check --unsafe-fixes --fix . && ruff check . --statistics
```

**Sonderfälle:** `# noqa: E402` für bedingte Imports (Import nach `pytest.importorskip()`). `# noqa: F401` für Verfügbarkeitsprüfungen — NUR Code, kein freier Text.

## Pattern 12: Complex Function Refactoring

**Prinzip:** Funktionen extrahieren bis keine Sub-Funktion C>10 hat. Jede macht GENAU EINE Sache mit EINEM Rückgabetyp.

**Extraktionsregeln:** Sub-Funktion C<10, keine shared mutable state, max 4 Sub-Funktionen pro Haupt-Funktion, Duplikate zuerst eliminieren.

**🔴 Orphaned-Exception Pitfall:** Nach Extraktion von `subprocess`/I/O Code werden `try`/`except` Blöcke in der Parent verwaist → SyntaxError. Nach jeder Extraktion prüfen:
```bash
python3 -c "import ast; ast.parse(open('file.py').read()); print('OK')"
```
**Validierung:** `pytest -q --tb=short && ruff check . --statistics && radon cc`

**Historie:** `references/complexity-refactoring-history.md` — 11 Phasen, von 19→3 C>15 Hotspots.

## Pattern 13: Shared Logging Handler (stderr Byte Interleaving)

**Problem:** Zwei separate `StreamHandler`-Instanzen → Byte-Level-Vermischung.
**Fix:** `_logging.py` — Singleton `get_stderr_handler()` + `setup_logger()` mit `logger.propagate = False`.

## Pattern 15: CHANGELOG.md Line-Number Contamination

**Problem:** `read_file`-Output (`1|content`) via `write_file`/`patch` zurückgeschrieben → embedded Prefixe.
**Fix:** `re.sub(r'^\d+\|', '', content, flags=re.MULTILINE)`
**Prävention:** `read_file`-Output nie direkt als `write_file`/`patch`-Input verwenden.

## Pattern 16: Property-Based Testing

**Settings:** `suppress_health_check=[HealthCheck.function_scoped_fixture]`, `deadline=None`, `max_examples=20-50`.
**Pitfall:** `.example()` NUR für interaktives Erkunden. In `@given`-Tests `data.draw()` oder Schleife nutzen.

## Pattern 17: Integration Tests mit echten LSP-Servern

**Steuerung:** `LSP_TEST=1` → `@pytest.mark.skipif(...)`. Fixtures pro Sprache (py_project, ts_project, go_project).
**Pitfalls:** `code_workspace_symbols_tool(query, path=...)` NICHT `(project, query=...)`. LSP-Init 3-5s beim ersten Test. Pro Klasse neuer tmp_path.

## Pattern 18: Nightly Watchdog Cron-Job

**Design:** `hermes cron create --name code-intel-nightly-health --schedule "0 3 * * *" --no-agent`. Watchdog: Silent bei Erfolg, loud on failure. Checks: Tests, Ruff, Health, Error-Log (>10/24h → Warnung), Git-Status (uncommitted/unpushed).

## Pattern 19: LSP-Bridge Fuzzing

**Pattern:** `_make_bridge()` mit gemocktem `_process` + parametrisierte Tests mit `MALFORMED_MSGS`.
**🔴 Real bugs found (v0.27.01):** 5 Bugs — `_dispatch` (NoneType.get ×2), `_uri_to_path` (None.startswith), `_dispatch` (str.get), `_format_definitions` (KeyError). Alle durch `isinstance()` guards gefixt.

## Pattern 20: Dispatch-Extraktion für if/elif-Ketten

**Fix:** Jede Notification in eigene `_handle_<method>(self, msg: dict) -> None` extrahieren. C=24→7. Fuzz-Tests decken alle Zweige ab.

## Pattern 21: Surgical Refactoring via str.replace

**Wann patch() schlägt:** Escaped Quotes (`\"`) oder Indentation-Drift → Python via terminal:
```python
content = open('file.py').read()
content = content.replace(old, new, 1)  # count==1 vorher prüfen!
open('file.py', 'w').write(content)
```

**🔴 @dataclass Orphaned:** Neue Funktion zwischen `@dataclass` und `class LSPBridge:` → 280+ Failures. Fix: `@dataclass` zur Class zurückverschieben.

## Pattern 22: Feature Audit and Prioritization

**7-stufige Methode:**
1. **Ist-Zustand**: Tool-Anzahl, letzte Tools, LSP-Sprachen, Gesundheit
2. **CHANGELOG lesen**: Was zuletzt hinzugefügt? Welche Kategorie fehlt?
3. **Code-Analyse**: `__init__.py` (Tools), `code_intel.py` (AST), `lsp_bridge.py` (LSP)
4. **Market Research**: `references/code-intel-market-landscape-2026.md`, `references/codegraph-comparison-2026-06-18.md`
5. **Stack-Relevanz bewerten**: User-Tech-Stack (Medusa TS, Go, Python)
6. **Impact-Matrix**: Aufwand ⭐-⭐⭐⭐ × Nutzen 🔥/🟡/🟢 × Stack-Relevanz
7. **Priorisieren**: Top-Priorität (hoher Nutzen, geringer Aufwand) zuerst

**LSP 3.18 Gap Analysis:** 18/26 Methoden implementiert. Siehe `references/lsp-gap-analysis.md`.

## Pattern 23: Workspace-Root-Erkennung für Monorepos

**Problem:** `_find_workspace_root()` bleibt beim Monorepo-`package.json` statt Sub-Projekt.
**Fix:** Zwei Durchläufe: (1) Sub-Projekt-Marker (`tsconfig.json`, `next.config.ts`, `medusa-config.ts`) → (2) Nächsten Nicht-Monorepo-Root (überspringt `package.json` mit `"workspaces"`).
**Cache:** Dict mit 300s TTL.
**🔴 Pitfalls:** pyright/tsserver brauchen korrekten Root (falsche tsconfig); Symlinks → `Path.resolve()` muss reale Pfade liefern.

## Pattern 24: Shared ImportGraph für Import-Analyse

**File:** `code_intel/_import_graph.py`. Methoden: scan(), parse_imports(), parse_all(), find_cycles(), analyze_blast_radius(), find_hot_paths(), to_mermaid(), to_tree().

**Sub-Pattern: Neues AST Tool aus ImportGraph (3 Steps):**
1. **Tool-Funktion** in `code_intel.py` — ImportGraph importieren, Methode aufrufen, JSON return
2. **Schema + Handler + Registry** direkt nach der Funktion
3. **__init__.py** in BEIDE Listen eintragen (TOOLSETS + new_tools) + Sync-Check

**🔴 Pitfall:** `_import_graph.py` vor Plan-Erstellung prüfen — wurde als existierend angenommen aber fehlte (Phase 0 nachträglich).

## Pattern 25: Pre-Existing Bridge Code Detection

**Problem:** tree-sitter `Query.captures()` entfernt in v0.23+.
**Fix:** `QueryCursor().matches()` — liefert `(pattern_idx, captures_dict)` Paare statt `(node, name)` Tupel.

## Pattern 27: AST Tool Creation

**Seit v0.3.0 (2026-06-20):** Tools werden NICHT mehr via `if registry: registry.register()` auf Modulebene registriert (P0 Bug-Fix). Stattdessen:
1. Tool-Funktion + Schema + Handler in `code_tools.py` definieren
2. KEIN `registry.register()` in `code_tools.py` einfügen
3. In `__init__.py._register_ast_tools()` das Schema+Handler-Paar in die `_AST_TOOL_REGISTRATIONS`-Liste eintragen
4. Wrapper in `tools/analysis.py` (oder entsprechendem Modul) für Re-Export hinzufügen
5. ggf. auch in `tools/__init__.py` importieren

**Debug-Print Pitfall:** `print()` wird von pytest ohne `-vvs` unterdrückt → `logging.debug()` verwenden.

## Pattern 29: __init__.py Tool-Registration (DEPRECATED in v0.3.0)

**Seit v0.3.0:** Es gibt keine `TOOLSETS["code_intel"]`-Liste mehr und keine `new_tools`-Liste.
Die Registrierung läuft zentral in `__init__.py._register_ast_tools()`.
Siehe Pattern 34 (Central Registration).

## Pattern 33: Subpackage Split (code_tools.py → tools/)

**Trigger:** Eine Plugin-Modul-Datei ist über 3.000 Zeilen gewachsen.

**Zwei Varianten:**

**Variante A — Vollständige Extraktion (empfohlen für reine Infrastruktur):**
1. Ziel-Modul (`tools/base.py`) mit write_file erstellen
2. Funktionen aus dem Monolithen kopieren
3. Imports anpassen (`. _fmt` → `.. _fmt`)
4. Aus dem Monolithen entfernen
5. `__all__` im neuen Modul definieren

**Variante B — Re-Export Wrapper (sicher für Tool-Funktionen):**
1. wrapper-Modul (`tools/search.py`) erstellen
2. Import: `from code_intel.code_tools import code_search_tool`
3. `__all__ = ["code_search_tool", ...]`
4. Monolith bleibt unverändert (Tool-Funktionen werden nur re-exportiert)
5. Optional: `tools/__init__.py` importiert aus allen wrapper-Modulen

**🔴 Pitfall — Off-by-One bei Zeilenbasierter Extraktion:**
Bei `sections = {"bridge.py": (0, 1918), "tools.py": (1919, ...)}` sind die Werte
**1-indexed**, aber Python-Listen sind **0-indexed**. `lines[1918:4730]` = Index 1918-4729
= 1-indexed Zeilen 1919-4730. Verwechslung führt zu abgeschnittenen Funktionen.
→ Lösung: Kommentar im Splitter: `# tools.py start: line 1919 (1-indexed) = index 1918 (0-indexed)`

**🔴 Pitfall — Fehlende Imports in extrahierten Modulen:**
Wenn du einen Zeilenbereich aus einem Monolithen extrahierst, fehlen die Imports
aus dem Header. Jedes extrahierte Modul braucht eigene `from __future__ import annotations`,
`from typing import ...`, und relative Imports (`from .._fmt import ...`).

**🔴 Pitfall — `import *` exportiert keine `_`-prefixed Namen + bricht Tests:**
Nach einem Subpackage-Split wird oft eine Re-Export-Facade (`lsp_bridge.py`) als
Wildcard-Import angelegt: `from code_intel.lsp.bridge import *`. **Das exportiert
KEINE Namen mit führendem Unterstrich (`_WORKSPACE_ROOT_CACHE`, `_handle_*`).**

```python
# lsp_bridge.py — Re-Export Facade
from code_intel.lsp.bridge import *  # ❌ Exportiert _WORKSPACE_ROOT_CACHE NICHT
```

Das killt Tests, die diese `_`-Symbole importieren:
```python
# tests/test_root_discovery.py
from code_intel.lsp_bridge import _WORKSPACE_ROOT_CACHE  # ❌ ImportError
```

**Drei Fix-Optionen (priorisiert):**
1. **Explizite Imports in der Facade:** Alle benötigten `_`-Symbole in `lsp_bridge.py` einzeln importieren → kein wildcard-Import nötig
2. **`__all__` in bridge.py/tools.py:** `_WORKSPACE_ROOT_CACHE` in `__all__` aufnehmen → `import *` exportiert es dann
3. **Tests umstellen:** Tests importieren direkt aus dem Subpackage (`code_intel.lsp.bridge` statt `code_intel.lsp_bridge`) — aber das bricht die Abwärtskompatibilität, wenn andere Module auf den Pfad angewiesen sind

**🔴 Pitfall — monkeypatch.setattr Pfade brechen nach Split:**
`monkeypatch.setattr("code_intel.code_intel.funcname", ...)` und
`monkeypatch.setattr("code_intel.lsp_bridge.time.monotonic", ...)` brechen
nach dem Split, weil:
- `code_intel.code_intel` als Pfad nicht mehr existiert (jetzt `code_tools` + tools/)
- `code_intel.lsp_bridge` ist kein package mehr (sondern eine Datei)
- monkeypatch/pytest versuchen, über den dotted-name ein Sub-Modul zu importieren
  → `ModuleNotFoundError: No module named 'code_intel.lsp_bridge.time'`

**Lösung:** Alle monkeypatch.setattr-Pfade auf die neue Modul-Struktur umstellen:
```python
# ALT (crasht):
monkeypatch.setattr("code_intel.code_intel._cache_key_for_path", mock)

# NEU:
monkeypatch.setattr("code_intel.code_tools._cache_key_for_path", mock)

# Noch besser: direkt auf das Ziel-Modul patchen:
from code_intel import code_tools
monkeypatch.setattr(code_tools, "_cache_key_for_path", mock)
```

**🔴 Pitfall — KEIN Bulk-Replace bei Mock-Pfaden:**
`patch("code_intel.lsp_bridge.name")` via Bulk-Replace durch
`patch("code_intel.lsp.bridge.name")` zu ersetzen ist gefährlich, weil:
- Facade-Tests (`patch.object(_lsp_bridge, "name")`) werden fälschlich auf Submodul geändert
- Tools-Modul-Tests (`patch.object(_lsp_tools, "name")`) werden fälschlich auf Submodul geändert

Jeder Mock-Pfad muss einzeln nach dem 3-Tier-System beurteilt werden
(siehe `references/subpackage-split-mock-3-tier.md`).

**🔴 Pitfall — Production-Code nicht für Test-Kompatibilität ändern:**
Wenn ein Test `json.loads(tool_result)["key"]` macht, aber die Tool-Funktion
`fmt_ok({...})` (rich-formatiert) zurückgibt, ist die Versuchung gross,
`fmt_ok` durch `json.dumps` zu ersetzen. **Nicht machen!** `fmt_ok` ist für
User-Output, `json.dumps` für interne API. Stattdessen die Test-Expectation
anpassen: `assert '"key": value' in str(result)`.

## Pattern 34: Central Registration (v0.3.0+)

**Trigger:** Plugin hat module-level `if registry: registry.register()` Aufrufe (P0-Sicherheitslücke).

**Fix (3 Schritte):**
1. **Entfernen:** Alle `if registry: registry.register(...)` Blöcke aus den Modulen löschen
   ```bash
   grep -n "if registry: registry.register(" module.py  # 21 Stellen in code_tools.py
   ```
2. **Entfernen:** Das `try: from tools.registry import registry / except: registry = None` Pattern
3. **Hinzufügen:** Zentrale `_register_ast_tools()` in `__init__.py`:
   ```python
   def _register_ast_tools():
       from . import code_tools as ct
       from tools.registry import registry
       _AST_TOOL_REGISTRATIONS = [
           (ct.CODE_X_SCHEMA, ct._handle_code_x),
           ...
       ]
       for schema, handler in _AST_TOOL_REGISTRATIONS:
           try:
               registry.register(name=schema["name"], toolset="agentiker_code_intel",
                                 schema=schema, handler=handler, check_fn=ct._check_fn, emoji="🔍")
           except Exception as e:
               logging.warning(f"Failed: {schema['name']}: {e}")
   ```

**Vorteil:** Wenn registry nicht verfügbar ist, crasht nur die Registration-Funktion,\nnicht der gesamte Plugin-Import. Ein Tool-Fehler killt nicht alle 21 Registrierungen.

**🔴 Pitfall — Registry-Tests brechen wenn `_register_ast_tools()` nicht läuft:**\nTests die `import code_intel.code_tools` aufrufen in der Hoffnung das\n`_register_ast_tools()` die Tools im MockRegistry registriert, FEHLEN — weil\n`_register_ast_tools()` nur vom Plugin-Einstiegspunkt (`__init__.py::register()`)\nund nicht beim direkten `code_tools`-Import aufgerufen wird. Das MockRegistry\nbleibt leer.\n\n**Fix:** Tools direkt im Test registrieren statt auf Auto-Registration zu hoffen:\n\n```python\n# ❌ Bricht — _register_ast_tools() wurde nie aufgerufen\nfrom tools.registry import registry\nimport code_intel.code_tools  # noqa: F401\nassert \"code_search\" in registry.get_all_tool_names()  # → leer\n\n# ✅ Tools explizit registrieren\nfrom tools.registry import registry\nregistry.register(\"code_search\", toolset=\"code_intel\", schema={})\nassert \"code_search\" in registry.get_all_tool_names()\n```\n\n**Prävention:** `grep -rn \"import code_intel.code_tools\" tests/` nach jedem\nHinzufügen neuer Registry-Tests prüfen. Tests die auf `get_all_tool_names()`\noder `get_entry()` zugreifen, müssen das Tool vorher registrieren.

## Pattern 35: LSP Integration Tests Fixen — Mock-Namespace nach Subpackage-Split

**Trigger:** LSP-Integrationstests schlagen fehl weil Mock-Namespace nicht trifft.

Nach einem Subpackage-Split gibt es **3 verschiedene Module** auf die Mocks zielen können
(Facade, Submodul, Tools-Modul). Falsches Targeting → Mock wird nie aufgerufen → echter
LSP-Server läuft.

**Ursache:** `mock.patch("code_intel.lsp_bridge.get_lsp_manager")` zielt auf die Facade.
`lsp/tools.py` importiert `get_lsp_manager` via `from .bridge import get_lsp_manager`
auf **Modulebene** — die Referenz ist im tools-Namespace, nicht in der Facade.
→ Der Mock wirkt nicht, der echte pyright-langserver läuft trotz Mocks.

**Vollständige Entscheidungsmatrix siehe:** `references/subpackage-split-mock-3-tier.md`
(3 Tiers: Facade `patch.object()`, Submodul `patch("...lsp.bridge.*")`, Tools `patch.object()`)

**Kurzfassung:**
1. **Facade (`code_intel.lsp_bridge`):** `patch.object(_lsp_bridge, "name")` — für lazy Imports
2. **Submodul (`code_intel.lsp.bridge`):** `patch("code_intel.lsp.bridge.name")` — für LOAD_GLOBAL in bridge.py
3. **Tools (`code_intel.lsp.tools`):** `patch.object(_lsp_tools, "name")` — für module-level Imports

**Weitere Fixes (wenn Mock-Namespace nicht das Problem ist):**
1. **Assertions lockern:** `assert "error" in result` → `assert "status" in result`
2. **@pytest.mark.xfail** für Tests die echte LSP-Infrastruktur brauchen

## Pattern 30: E2E Testing mit E2E_TEST=1

**Gating:** `os.environ.get("E2E_TEST") == "1"`. Läuft nie in CI (10x langsamer).
**Kategorien:** A — Real-Tool-Calls (14 Tests), B — Cross-Workflows (6), C — Lifecycle (6).
**⚠️ Tool-Count:** Nach jedem neuen Tool `test_e2e_lifecycle.py` `len(tools)==N` + `expected`-Set aktualisieren.
**Details:** `references/e2e-testing-detailed.md`

## Pattern 31: Symbol-Level Editing Tools

**Tools:** `code_replace_body`, `code_safe_delete`, `code_insert_before`, `code_insert_after` (alle AST-basiert, kein LSP).

**Key Helper:** `_find_symbol_in_ast()` — Byte-exakte Boundaries via tree-sitter. **🔴 Method Detection:** `_classify_symbol_kind()` erkennt Methoden nicht → immer `_detect_if_method()` nachschalten.
**Key Helper:** `_invalidate_cache()` nach jedem Write — sonst stale Symbol-Daten.
**Safety:** Backup-then-Write (`.bak`). `dry_run=True` als Default.

**🔴 str.replace Kollateral:** `str.replace()` bei Ruff-Fixes matcht in ALLEN Funktionen → 46 Failures. Fix: `count == 1` prüfen.

## Pattern 32: Unused Import Detection

**Zwei-Phasen:**
1. **Import-Statements parsen** via tree-sitter (Python + TS + JS Queries)
2. **Body-Referenz-Check** mit `\b` Word-Boundary Regex

**Skip-Liste:** `{"typing", "TYPE_CHECKING", "Any", "Optional", "List", "Dict", "Set", "Tuple"}`
**Details:** `references/symbol-editing-tools-detailed.md`

## Pattern 36: TTL-Guard für Lock-Release-Races (close_document/open_document)

**Problem:** `close_document()` muss `self._lock` freigeben bevor es `didClose` sendet,
weil `_send_notification` → `_write_message` ebenfalls `self._lock` nimmt (Deadlock bei
nicht-reentrant Lock). Aber zwischen Lock-Release und Notification kann `open_document()`
für die gleiche URI laufen und das Dokument neu öffnen. Die spät eintreffende `didClose`
Notification killt das frisch geöffnete Dokument.

**Ursprünglicher (unvollständiger) Fix — Second-Check mit zweitem Lock:**
```python
def close_document(self, file_path: str) -> None:
    uri = f"file://{file_path}"
    with self._lock:                           # Lock 1
        self._open_documents.discard(uri)
        self._closing_uris.add(uri)
    self._send_notification("didClose", ...)   # Lock frei — RACE!
    with self._lock:                           # Lock 2 — cleanup
        self._closing_uris.discard(uri)
```
Die Race zwischen `_send_notification` und Lock 2 bleibt bestehen — `open_document`
kann zwischen Lock 1 Release und Lock 2 Acquisition laufen.

**TTL-Guard Fix — Dict mit Timestamp statt Set:**
```python
# __init__:
_closing_uris: Dict[str, float]  # URI → time.monotonic() timestamp

# close_document() — kein zweiter Lock nötig:
def close_document(self, file_path: str) -> None:
    uri = f"file://{file_path}"
    with self._lock:
        if uri not in self._open_documents:
            return
        self._open_documents.discard(uri)
        self._closing_uris[uri] = time.monotonic()  # Timestamp setzen
    self._send_notification("didClose", ...)
    # _closing_uris Eintrag verfällt automatisch via TTL

# open_document() — TTL-basierter Guard statt Set-Prüfung:
for _wait in range(50):  # max ~0.5s spin-wait
    with self._lock:
        ts = self._closing_uris.get(uri)
        if ts is None or (time.monotonic() - ts) > 0.5:
            self._closing_uris.pop(uri, None)
            break
    time.sleep(0.01)
```

**Vorteile:**
- Kein zweiter Lock nötig — schliesst die Race ohne Deadlock-Risiko
- `_closing_uris` Einträge verfallen automatisch nach 0.5s
- Garbage Collection via `pop()` beim nächsten Zugriff (kein Memory-Leak)
- `_send_notification` bleibt ausserhalb des Locks (kein Deadlock mit `_write_message`)

**Pitfalls:**
- TTL muss länger sein als die maximale didClose-Latenz (500ms passen für lokale LSP-Prozesse)
- Bei Netzwerk-LSP (Remote-Server) TTL erhöhen
- `time.monotonic()` verwenden, nicht `time.time()` (kein Systemzeit-Problem)

**Trigger:** Wenn LSP-Tests sporadisch `"Can't open already open document"` oder
URI-Corruption (`s4ore` statt `store`) zeigen.

**Fundort:** Bug-Hunt 2026-06-21 — `lsp/bridge.py:1083-1100`.

## v0.4.0 — LSP 3.18, Git-Tools & Custom-Tools (2026-06-21)

**Version:** 0.4.0 | **Tools:** 57 (+14 seit v0.3.1) | **Tests:** 1307

**6 neue LSP 3.18 Methoden** in `lsp/bridge.py` (22 gesamt) + **6 Bridge-Methoden**:

| LSP 3.18 Tool | Bridge-Methode | Domain |
|---------------|----------------|--------|
| `code_completion` | `_text_document_completion()` | Autovervollständigung |
| `code_code_lens` | `_text_document_code_lens()` | Code Lenses (Run, Debug, etc.) |
| `code_folding_range` | `_text_document_folding_range()` | Code-Faltung |
| `code_selection_range` | `_text_document_selection_range()` | Selektionsbereiche |
| `code_linked_editing` | `_text_document_linked_editing()` | Gekoppeltes Editieren (CSS-Klassen, JSX-Tags) |
| `code_prepare_rename` | `_text_document_prepare_rename()` | Prepare-Rename mit Default-Verhalten |

**4 neue Git-Tools** in `tools/git.py`:

| Tool | Funktion |
|------|----------|
| `code_todo_finder` | Findet TODO/FIXME/HACK/TEMP-Kommentare im Codebase |
| `code_merge_conflict_finder` | Findet Merge-Konflikte (`<<<<<<<`, `=======`, `>>>>>>>`) |
| `code_git_log_symbol` | Git-Log für ein bestimmtes Symbol (Autor, Datum, Message) |
| `code_git_diff_file` | Git-Diff zwischen aktuellen Änderungen und HEAD für eine Datei |

**4 neue Custom-Tools** in `tools/custom.py`:

| Tool | Funktion |
|------|----------|
| `code_diagram_symbol` | Generiert ASCII/Mermaid-Diagramm für eine Funktion oder Klasse |
| `code_explain` | Erklärt Code-Abschnitt als natürlicher Text (Kontext-optimiert) |
| `code_docstring_generate` | Generiert/fügt Docstrings zu undokumentierten Funktionen |
| `code_dependency_risk` | Bewertet Abhängigkeitsrisiken (zirkuläre Imports, Breite, Tiefe) |

### Profile-Counts (v0.4.0)

| Profil | Tools | Enthaltene Tool-Typen |
|--------|-------|----------------------|
| **all** | 57 | Core + Search + LSP + Git + Custom + Analysis + Style |
| **core** | 16 | AST-Basis-Tools: symbols, search, definition, references, diagnostics, hover, signatures, type_definition, document_symbols, workspace_symbols, inline_hints, implementations, callers, callees, highlight, format |
| **search** | 10 | search, search_by_error, workspace_symbols, workspace_summary, unused_finder, duplicates, hot_paths, cycle_detector, metrics, todo_finder |
| **lsp** | 22 | 16 LSP-Standard + 6 LSP 3.18 (completion, code_lens, folding_range, selection_range, linked_editing, prepare_rename) |

### Tool-Übersicht nach Kategorie

| Kategorie | Tools | Dateien |
|-----------|-------|---------|
| **AST Core** | 16 | `code_tools.py`, `tools/analysis.py` |
| **LSP Standard** | 16 | `lsp/tools.py` |
| **LSP 3.18** | 6 | `lsp/tools_318.py` |
| **Search** | 10 | `tools/search.py` |
| **Git** | 4 | `tools/git.py` |
| **Custom** | 4 | `tools/custom.py` |
| **Style/Format** | 1 | `tools/style.py` |
| **Gesamt** | **57** | |

## Reference Files

| Referenz | Inhalt |
|----------|--------|
| `references/lsp-tool-pattern.md` | 5-Step-Guide für neue LSP-Tools |
| `references/terminal-output-architecture.md` | 10 Display Mechanismen |
| `references/code-intel-market-landscape-2026.md` | Competitive Landscape (14 Tools, Tier 1-4) |
| `references/codegraph-comparison-2026-06-18.md` | CodeGraph Feature-Vergleich (42+27+17 Tools) |
| `references/security-hardening.md` | Security für öffentliches Repo |
| `references/v0.3.0-restructure-patterns.md` | Subpackage Split + Central Registration + LSP Test Fix Patterns |
| `references/lsp-gap-analysis.md` | LSP 3.18 Gap (22/26 Methoden, +6 in v0.4.0) |
| `references/complexity-refactoring-history.md` | 11 Phasen Refactoring, 19→3 C>15 |
| `references/historical-feature-progress.md` | Feature-Plan Status, v0.28.02-v0.29.00 |
| `references/readme-generator-bugs.md` | 6 Generator-Bugs + 5 Verbesserungen |
| `references/e2e-testing-detailed.md` | E2E Test-Code-Beispiele + Pitfalls |
| `references/symbol-editing-tools-detailed.md` | Symbol Editing Code + Unused Import API |
| `references/regression-verification-pattern.md` | Regression-Check nach Threading-Fix |
| `references/bughunt-2026-06-18.md` | 14 Bug-Hunt Findings |
| `references/bughunt-2026-06-20.md` | 9 Bug-Hunt Findings (v0.1.13 Regressions) |
| `references/bughunt-2026-06-20-verification-2026-06-21.md` | ✅ 4/10 Findings aus 2026-06-20 in v0.3.1 gefixt bestätigt |
| `references/monkeypatch-rexport-pattern.md` | Monkeypatch + Re-Export Facade Fixes nach Subpackage-Split |
| `references/post-restructure-test-regression-2026-06-20.md` | 3 Typen von Test-Regression nach Subpackage-Split |
| `references/subpackage-split-mock-3-tier.md` | 3-Tier Mock-System: Facade/Submodul/Tools-Modul + Bulk-Replace-Fallstricke + Test-Isolation |
