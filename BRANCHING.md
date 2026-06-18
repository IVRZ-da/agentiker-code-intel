# Branching Convention for agentiker Hermes Plugins

## Branch Naming

All branches MUST use one of these prefixes + a short kebab-case description:

| Prefix     | Verwendung                  | Beispiel                              |
|------------|-----------------------------|---------------------------------------|
| `feat/`    | Neue Features / Tools       | `feat/add-code-format-tool`           |
| `fix/`     | Bugfixes                    | `fix/honcho-recursion`                |
| `refactor/`| Refactoring / Code-Cleanup  | `refactor/extract-conftest`           |
| `chore/`   | CI, Dependencies, Infra     | `chore/ci-workflow`                   |
| `docs/`    | Dokumentation               | `docs/api-reference`                  |
| `test/`    | Tests-Only Changes          | `test/coverage-gaps`                  |
| `release/` | Release-Vorbereitung        | `release/v0.1.5`                      |

## Workflow

1. Immer von `main` ausgehen: `git checkout main && git pull`
2. Branch erstellen: `git checkout -b <prefix>/<kurzbeschreibung>`
3. Commits machen, pushen: `git push -u origin <branch>`
4. PR auf `main` erstellen (via Forgejo Web UI oder `cb.py pulls create`)
5. Nach Merge: Branch lokal + remote löschen

## Anti-Patterns (verboten)

- ❌ `dev`-Branch — es wird direkt auf Feature-Branches entwickelt
- ❌ Namen ohne Prefix — `fix-xyz` statt `fix/xyz`
- ❌ Gemischte Sprachen — nur Englisch für Branch-Namen
- ❌ Versionsnummern im Branch-Namen — dafür gibt es Tags
- ❌ CamelCase oder Underscores — nur kebab-case

## Versioning

Dieses Plugin folgt **0.1.x** Versionierung. Jeder Merge auf `main` bumped die
Patch-Version um 1. Keine Minor/Major-Sprünge (0.2.x, 1.x, 2.x) mehr, bis das
Plugin wirklich stabil und ausgereift ist.

```
v0.1.0 → v0.1.1 → v0.1.2 → ... → v0.1.N
```

Tags folgen dem Schema `v0.1.<patch>`.
