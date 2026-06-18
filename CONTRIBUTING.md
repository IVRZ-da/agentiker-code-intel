# Contributing to agentiker-code-intel-plugin

## Branch-Strategie

- **`main`** — stabil, geschützt. Nur via Pull-Request.
- **`dev`** — für Entwicklung und PRs von Contributors.

## Workflow

1. Fork das Repo (oder branch von `dev`)
2. Änderungen committen
3. Pre-Commit Hook aktivieren: `git config core.hooksPath .githooks`
4. Lokal testen:

   ```bash
   ruff check . --select F,E,T,W,S
   python3 -m pytest tests/ -q --tb=short
   ```

5. `CHANGELOG.md` aktualisieren
6. PR auf `dev` stellen
7. CI läuft automatisch (Ruff Lint, Security-Scan, pytest)
8. Review durch Maintainer → Merge in `main`

## Was wir erwarten

| Check | MUSS | Erklärung |
|-------|------|-----------|
| Ruff Lint | ✅ | `ruff check --select F,E,T,W,S` |
| Tests | ✅ | `pytest tests/ -q` |
| CHANGELOG | ✅ | Neuen Eintrag unter `[Unreleased]` |
| Keine Secrets | ✅ | Pre-Commit Hook prüft automatisch |

## Was wir NICHT wollen

- Commits mit persönlichen Daten (Email, System-Pfade)
- Direkte Pushs auf `main` (blocked durch Branch-Protection)
- PRs ohne CHANGELOG-Eintrag

## Security

Bei Sicherheitslücken: **Kein öffentliches Issue.** Siehe `SECURITY.md`.
