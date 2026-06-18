# Generate Readme — Analyse & Verbesserungsplan

## Aktuelle Bugs (5 🔴, 4 🟡)

| # | Prio | Bug | Beschreibung |
|---|------|-----|-------------|
| B1 | 🔴 | **Version aus falscher Quelle** | Liest aus pyproject.toml statt plugin.yaml. Version=0.28.01 weil Generator pyproject.toml cached hatte → falsche Version in README |
| B2 | 🔴 | **Tool-Liste matcht falschen Block** | Regex `r'"tools":\s*\[(.*?)\]'` matcht non-greedy → erwischt evtl. `new_tools` oder nur Teil der Liste. Fehlende Tools + Duplikate in README |
| B3 | 🔴 | **Duplikate durch doppelte Listen** | `TOOLSETS["code_intel"]["tools"]` + `new_tools` haben gleiche Tools → re.findall sammelt ALLE Strings inkl. `"code_intel"`, `"description"` |
| B4 | 🔴 | **Test-Count via `sys.executable`** | Nutzt den Python-Interpreter der den Generator läuft. Im Pre-Commit-Hook evtl. falscher Python → pytest nicht gefunden → "Tests: ?" |
| B5 | 🔴 | **Nur auto-section wird aktualisiert** | Header (Titel, "21 tools", Features) liegt AUSSERHALB der Marker → wird nie aktualisiert |
| B6 | 🟡 | **Keine AST-Languages** | Nur LSP-Sprachen extrahiert. AST-Sprachen (java etc.) fehlen |
| B7 | 🟡 | **Keine Tool-Kategorien** | Keine Unterscheidung AST/LSP/Hybrid in der README |
| B8 | 🟡 | **Kein `--verbose`** | Keine Debug-Informationen bei Fehlern |
| B9 | 🟡 | **Regex `_LANGUAGE_SERVERS` zerbrechlich** | Der Regex für `_LANGUAGE_SERVERS` ist extrem lang und Fragment-anfällig |

## 🔴 NEU: B10 — `_TOOL_PROFILES` Breaking Change

**Severity:** 🔴 (Generator produziert "0 tools")

**Ursache:** Das Plugin hat in `__init__.py` von einem statischen `TOOLSETS["agentiker_code_intel"]["tools"]`-Block
auf das dynamische `_TOOL_PROFILES`-System umgestellt. `_get_tool_list()` in `generate_readme.py` sucht
aber immer noch nach dem alten Pattern (Zeilen 69-75):

```python
# ALT: sucht nach TOOLSETS-Block — existiert nicht mehr
m = re.search(
    r'TOOLSETS\["agentiker_code_intel"\]\["tools"\]\s*=\s*\[(.*?)\]',
    text, re.DOTALL
)
```

**Lösung:** `_get_tool_list()` muss stattdessen `_TOOL_PROFILES["all"]` aus `__init__.py` parsen:

```python
# NEU: _TOOL_PROFILES["all"] parsen
m = re.search(
    r'_TOOL_PROFILES\s*=\s*\{[^}]*?"all"\s*:\s*\[(.*?)\]',
    text, re.DOTALL
)
```

**Optional:** Sub-Profile (core, search, edit, lsp) könnten in README als neue Zeile
"**Profiles:** all (39), core (11), search (8), edit (7), lsp (16)" angezeigt werden.

## Implementierung

### Phase 1 — `_TOOL_PROFILES` Fix (B10)
- `_get_tool_list()` umschreiben: `TOOLSETS`-Regex → `_TOOL_PROFILES["all"]`-Regex
- README manuell auf "39 tools" gesetzt — Generator zeigt nach Fix korrekte Zahl

### Phase 2 — Profile-Info (optional)
- `_get_profile_count()` für README "**Profiles:** ..."-Zeile
- Übersichtlichkeit der Tool-Kategorien verbessern
