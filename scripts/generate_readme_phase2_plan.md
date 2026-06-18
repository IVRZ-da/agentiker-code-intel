# Phase 2 (Optional): Profile-Info in README

## Ziel

Nach dem B10-Fix (Phase 1) zeigt der Generator wieder korrekt 39 Tools.
Phase 2 fügt eine **Profile-Zeile** im AUTO-GENERATED Bereich hinzu,
damit Leser auf einen Blick sehen welche Tool-Profile existieren.

## Änderungen

### 1. Neue Funktion `_get_profile_info()` in `generate_readme.py`

```python
def _get_profile_info() -> str:
    """Extrahiere Profile-Infos aus _TOOL_PROFILES in __init__.py.
    
    Gibt kompakt-formatierten String zurück z.B.:
    "all (39), core (11), search (8), edit (7), lsp (16)"
    """
    text = INIT_PATH.read_text("utf-8")
    
    # _TOOL_PROFILES Block finden
    m = re.search(r'_TOOL_PROFILES\s*=\s*\{(.*)\}', text, re.DOTALL)
    if not m:
        return ""
    
    body = m.group(1)
    
    # Alle Profile-Namen + Tool-Count extrahieren
    profiles = re.findall(r'"(\w+)"\s*:\s*\[(.*?)\]', body, re.DOTALL)
    
    parts = []
    for name, tools_raw in profiles:
        count = len(re.findall(r'"([^"]+)"', tools_raw))
        parts.append(f"{name} ({count})")
    
    return ", ".join(parts)
```

### 2. Integration in `generate_auto_section()`

Nach der **Tools:**-Zeile eine neue Zeile einfügen:

```python
def generate_auto_section() -> str:
    ...
    lines = [
        "<!-- AUTO-GENERATED -->",
        "",
        f"**Version:** {version}",
        f"**Tests:** {test_count}",
        f"**Tools ({len(tools)}):** {', '.join(tools)}",
    ]
    
    profile_info = _get_profile_info()
    if profile_info:
        lines.insert(4, f"**Profiles:** {profile_info}")

    ...
```

**Ergebnis in README:**

```
**Version:** 0.29.00
**Tests:** 1291 tests
**Tools (39):** code_symbols, code_search, ...
**Profiles:** all (39), core (11), search (8), edit (7), lsp (16)
```

### 3. Optional: META-Block um Profile ergänzen

Aktuell steht in `<!-- META -->`:
```
**39 tools** (21 AST + 18 LSP) — c, cpp, go, java, javascript, jsx, python, rust, tsx, typescript
```

Könnte erweitert werden auf:
```
**39 tools** (21 AST + 18 LSP, 5 profiles) — c, cpp, go, java, javascript, jsx, python, rust, tsx, typescript
```

Dafür in `generate_meta_section()`:
```python
profile_info = _get_profile_info()
profile_count = len(profile_info.split(", ")) if profile_info else 0
profile_suffix = f", {profile_count} profiles" if profile_count > 0 else ""
```

## Entscheidungen

| Frage | Vorschlag | Begründung |
|-------|-----------|------------|
| Profile im AUTO-GENERATED oder META? | **Beide** | AUTO-GENERATED für Vollständigkeit, META für Header-Kompaktheit |
| Nur Count oder auch Tool-Namen pro Profil? | Nur Count | Tool-Namen würden >5 Zeilen brauchen — zu viel für den kompakten README-Header |
| Reihenfolge der Profile | Wie in `_TOOL_PROFILES` | all, core, search, edit, lsp — logisch vom Generellen zum Spezifischen |
| Ausgabe wenn _TOOL_PROFILES nicht existiert | Leerer String | Generator bricht nicht ab, zeigt einfach keine Profile-Zeile |

## Risiken

1. **Regex-Breakage**: `_TOOL_PROFILES\s*=\s*\{(.*)\}` könnte zu gierig sein wenn `}` auch in Strings vorkommt. Im aktuellen `__init__.py` nicht der Fall, aber bei zukünftigen Änderungen potenziell.
   - **Mitigation**: `(.*)` durch `(.*?)(?=^})` ersetzen (non-greedy bis Zeilenanfang + `}`)

2. **Profil-Name Regex**: `r'"(\w+)"\s*:\s*\[(.*?)\]'` matched auch verschachtelte Dicts falls ein Profil-Wert kein Array ist — im aktuellen Code sind alle Profile Arrays.

3. **Sortierung**: Wenn Profile alphabetisch sortiert werden sollen statt nach Quellcode-Reihenfolge:
   ```python
   profiles = sorted(profiles, key=lambda p: p[0])
   ```

## Test

Nach Implementation:
```bash
cd ~/.hermes/plugins/code_intel
python3 scripts/generate_readme.py --verbose
```

Erwartete Ausgabe: `**Profiles:** all (39), core (11), search (8), edit (7), lsp (16)`

## Zeitaufwand

- Implementation: ~10 Minuten (neue Funktion + 2 Zeilen in `generate_auto_section`)
- Test: ~2 Minuten
- **Total: ~12 Minuten**
