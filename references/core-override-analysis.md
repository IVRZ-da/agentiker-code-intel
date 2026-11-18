# Core-Override-Analyse (Hermes v0.23.1+1026.g78d683e)

Grundlage: `hermes plugins validate` meldet für v0.6.25

    ✗ no core override — rebinds Hermes core at runtime:
        toolsets.TOOLSETS[...] (__init__.py:170)

Zusätzlich als Warnungen: private Imports `toolsets` (`__init__.py:8`), `tools.delegate_tool`
(:532), `tools.delegate_tool_toolsets` (:533) sowie der Hinweis, dass der Patch von
`tools.delegate_tool._build_child_system_prompt` (:580) auf einer Host-Kopie landen könne.

Ziel dieses Dokuments: für jede der fünf Rebinds entscheiden, ob sie in v0.23.1 **noch
gebraucht** wird, und wenn ja, ob es eine öffentliche Plugin-API als Ersatz gibt. P5 darf
nur entfernen, was hier `replaceable` ist.

Die fünf Stellen in `__init__.py`:

| # | Zeile | Code | Zweck |
|---|-------|------|-------|
| a | 170 | `toolsets.TOOLSETS["agentiker_code_intel"] = {...}` | Toolset im statischen Dict sichtbar machen |
| b | 182 | `toolsets._HERMES_CORE_TOOLS.append(t)` | Tools in jedes Core-Bundle aufnehmen |
| c | 183–188 | `TOOLSETS[preset]["tools"].append(t)` für `hermes-acp`, `hermes-api-server` | Tools in ACP-/API-Server-Sessions |
| d | 541 | `delegate_tool_toolsets.DEFAULT_TOOLSETS.append(...)` | Tools in Subagenten ohne geerbte Toolsets |
| e | 570–580 | Patch von `delegate_tool._build_child_system_prompt` | Steering-Text im Subagenten-System-Prompt |

---

### (a) `toolsets.TOOLSETS["agentiker_code_intel"] = {...}`

Wirkung: Eintrag im **statischen** Dict. Gelesen wird es nur von Pfaden mit
`include_registry=False`:

* `resolve_toolset(name, include_registry=False)` → `get_toolset(name, include_registry=False)`
  (`toolsets.py:385-386`). Ohne den Eintrag liefert das eine leere Liste.
* `_expand_parent_toolsets()` (`tools/delegate_tool_toolsets.py:38-54`) arbeitet ausschließlich
  mit `include_registry=False` und iteriert `for ts_name in TOOLSETS` — ein reines
  Registry-Toolset wird dort nie expandiert.
* `resolve_toolset("all")` (`toolsets.py:374-379`) nutzt `get_toolset_names()`, das Registry-
  Toolsets **enthält** (`_plugin_display_names()` → `_get_plugin_toolset_names()` → Registrierung,
  `toolsets.py:402-419`).

Für Anzeige und Auswahl (`get_all_toolsets()`, `hermes tools list`) ist der Eintrag also
redundant — `hermes tools list` zeigt `agentiker_code_intel` bereits aus der Registry.

Echter Verlust beim Entfernen: Ein Subagent kann das Toolset nicht mehr **explizit** anfordern
(`toolsets=["agentiker_code_intel"]`), weil `_expand_parent_toolsets` es nicht kennt; er erbt es
aber weiter über das Parent-Set.

VERDICT (a): replaceable → Registry (`registry.get_registered_toolset_names()` über
`toolsets.get_toolset_names()` / `get_all_toolsets()`); Verhaltens-Delta nur bei expliziter
Kind-Anforderung, per Gegenprobe in P5 abzusichern.

### (b) `toolsets._HERMES_CORE_TOOLS.append(t)`

Wirkung empirisch **null** für die Bundles. Probe im Hermes-venv:

    hermes-cli tools vor append: 41 | enthaelt code_search: False
    toolsets._HERMES_CORE_TOOLS.append("PROBE_TOOL")
    nach append -> hermes-cli enthaelt PROBE_TOOL: False

Grund: jedes Bundle entsteht beim **Modul-Import** über `_bundle()` → `_ts(..., _HERMES_CORE_TOOLS + extras)`
(`toolsets.py:51-53`, `196-220`) und arbeitet danach auf einer Kopie. Ein Append nach dem
Plugin-Load kann daran nichts mehr ändern.

Restwirkung: `_HERMES_CORE_TOOLS` wird zur Laufzeit noch als Klassifikationsliste gelesen
(`agent/memory_manager.py:320,583`, `tools/tool_search_validation.py:243`,
`tui_gateway/methods_tools_plugin_servers.py:106`, `hermes_cli/tools_config.py:108`) — dort
entscheidet die Liste, ob ein Tool als "core" oder als Plugin-/Deferred-Tool gilt.

VERDICT (b): replaceable → none (ersatzlos streichen); die Klassifikationswirkung ist kein
Funktionsverlust der Tools, sondern nur eine Einordnung.

### (c) Preset-Appends in `hermes-acp` / `hermes-api-server`

Wirkung empirisch **vorhanden**:

    toolsets.TOOLSETS["hermes-acp"]["tools"].append("PROBE2")
    direkter Preset-Append wirkt: True

Das ist der einzige Mechanismus, der Plugins in die bei Import gebauten Platform-Bundles
bekommt. `_select_tool_names()` (`model_tools.py:333-355`) löst eine Session mit
`toolsets: [hermes-cli]` ausschließlich über `resolve_toolset()` auf — Plugin-Toolsets werden
dort nicht automatisch addiert.

Öffentliche Alternative: keine API. Es gibt `registry.register_toolset_alias()` (nur Alias,
kein Mitglied) und `ctx.subagent_lifecycle` (nur Lifecycle-Handles). Praktikabler Ersatz ist
**Konfiguration**: `toolsets: [hermes-cli, agentiker_code_intel]` — explizit statt implizit,
aber eine Änderung beim Nutzer.

VERDICT (c): needed → none (keine Plugin-API); Ablösung nur durch dokumentierte
Config-Empfehlung `toolsets: [..., agentiker_code_intel]`. Ohne diese Entscheidung bleibt
`no core override` rot.

### (d) `delegate_tool_toolsets.DEFAULT_TOOLSETS.append(...)`

Wirkung: nur im Fallback-Zweig von `_resolve_child_toolsets()` (`delegate_tool_toolsets.py:77-123`).
Der Normalfall ist Vererbung: `child_toolsets = parent_enabled` (Zeile 106-107) bzw. die aus
`valid_tool_names` abgeleiteten Parent-Toolsets (89-94). `DEFAULT_TOOLSETS` greift nur, wenn der
Parent **weder** `enabled_toolsets` **noch** `valid_tool_names` hat (96) oder die Menge leer ist (109).

Nebenwirkung heute: In genau diesem Fall bekommt **jedes** Kind die code-Tools in den Prompt,
ohne dass der Parent sie nutzt (unnötiger Schema-Ballast).

VERDICT (d): replaceable → none (streichen); Vererbung deckt den realen Betrieb ab.

### (e) Patch von `tools.delegate_tool._build_child_system_prompt`

Implementierung: `tools/delegate_tool_progress.py:181-217`. Der Child-Prompt wird dort komplett
gebaut (`"You are a focused subagent..."`) — `ctx.register_system_prompt_section` wirkt nur auf
den Session-Prompt des Hauptagenten und wird hier nicht konsultiert.

Der Patch trifft: `tools/delegate_tool.py` importiert das Symbol (Zeile 41-43) und ruft es als
Modulattribut `_build_child_system_prompt(...)` auf (Zeile 257) — `dt._build_child_system_prompt`
ist also genau das aufgerufene Objekt. Funktional wirkt der Patch; er ist nur eine
Core-Attribut-Mutation.

Öffentliche Alternative: der bereits registrierte Hook `pre_llm_call`
(`ctx.register_hook("pre_llm_call", on_pre_llm_call)`, `__init__.py:155`) wird im Agent-Loop
auch für jeden Child-Turn gefeuert. Steering-Text dort injizieren (nur wenn Subagent-Kontext
erkannt wird) ersetzt den Prompt-Patch vollständig — Preis: Injektion pro Turn statt einmalig im
System-Prompt.

VERDICT (e): replaceable → `ctx.register_hook("pre_llm_call")` (bestehender Hook, erweitert).

---

## Konsequenz für P5

Entfernbar ohne Funktionsverlust: **(a), (b), (d), (e)** — mit Gegenprobe
(`tests/test_runtime_visibility.py`): Toolset-Mitgliedschaft aus der Registry, Subagent erbt die
Tools, Steering-Text erreicht den Child-Turn.

Nicht per API ablösbar: **(c)**. Zwei Wege, Entscheidung gehört in P5:

1. **Config-Weg (Ziel: validate 10/10 grün):** Rebinds (a),(b),(d),(e) entfernen, (c) ebenfalls,
   dafür `toolsets: [hermes-cli, agentiker_code_intel]` in der README dokumentieren. Katalog-fähig.
2. **Konservativ:** (c) behalten, Rest entfernen. `no core override` bleibt rot, Katalog-Aufnahme
   bleibt blockiert; dafür funktionieren ACP-/API-Server-Sessions ohne Config-Änderung.

Ohne (c)-Entscheidung ist P5 nicht abschließbar — das Gate-Skript (`scripts/validate_gate.py`)
liest die VERDICT-Zeilen und lässt nur die dort als `needed` markierten Checks rot.
