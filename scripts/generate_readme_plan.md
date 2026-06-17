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

## Implementierung

Ich repariere alle 5 🔴 Bugs und füge 3 🟡 Improvements hinzu.
