#!/usr/bin/env python3
"""
README Auto-Generator for agentiker-code-intel-plugin.

Regenerates sections between ``<!-- AUTO-GENERATED -->`` / ``<!-- END AUTO-GENERATED -->``
markers in README.md from the actual plugin code.

Also updates metadata in the header between ``<!-- META -->`` / ``<!-- END META -->`` markers.

Usage:
    python scripts/generate_readme.py          # update README.md in place
    python scripts/generate_readme.py --check  # exit 1 if README is stale (for CI)
    python scripts/generate_readme.py --verbose  # show debug info
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
README_PATH = PLUGIN_DIR / "README.md"
PLUGIN_YAML_PATH = PLUGIN_DIR / "plugin.yaml"
PYPROJECT_PATH = PLUGIN_DIR / "pyproject.toml"
CHANGELOG_PATH = PLUGIN_DIR / "CHANGELOG.md"
LSP_BRIDGE_PATH = PLUGIN_DIR / "lsp_bridge.py"
CODE_INTEL_PATH = PLUGIN_DIR / "code_intel.py"
INIT_PATH = PLUGIN_DIR / "__init__.py"

VERBOSE = False


def log(msg: str) -> None:
    if VERBOSE:
        print(f"[debug] {msg}")


# ---------------------------------------------------------------------------
# Version — single source of truth: plugin.yaml, fallback pyproject.toml
# ---------------------------------------------------------------------------

def _get_version() -> str:
    """Extrahiere Version aus plugin.yaml (primary) oder pyproject.toml (fallback)."""
    if PLUGIN_YAML_PATH.exists():
        text = PLUGIN_YAML_PATH.read_text("utf-8")
        m = re.search(r'^version:\s*([^\s]+)', text, re.MULTILINE)
        if m:
            return m.group(1)
    # Fallback
    text = PYPROJECT_PATH.read_text("utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else "unknown"


# ---------------------------------------------------------------------------
# Tools — aus __init__.py, dedupliziert
# ---------------------------------------------------------------------------

def _get_tool_list() -> list[str]:
    """Extrahiere Tool-Liste aus __init__.py — neues _TOOL_PROFILES-System.

    Sucht nach _TOOL_PROFILES["all"] = [...], dem Haupt-Profil.
    Der Generator ist READ-ONLY: er analysiert nur das "all"-Profil,
    unabhängig davon welches Profil aktuell aktiv ist (CODE_INTEL_TOOL_PROFILE).
    Fallback: altes TOOLSETS["agentiker_code_intel"]["tools"]-Pattern.
    """
    text = INIT_PATH.read_text("utf-8")

    # Strategie 1: _TOOL_PROFILES["all"] Block (aktuelles System)
    # Format: _TOOL_PROFILES: dict = { "all": [ ... ], ... }
    m = re.search(
        r'_TOOL_PROFILES(?:\s*:\s*dict)?\s*=\s*\{(?:[^}]*?)"all"\s*:\s*\[(.*?)\]',
        text, re.DOTALL
    )
    if not m:
        # Hack: falls der Block zu komplex für einen Regex ist, versuche
        # nur nach "all": [...] zu suchen (nachdem das Dict geöffnet ist)
        m = re.search(r'"all"\s*:\s*\[(.*?)\]', text, re.DOTALL)
    if not m:
        # Fallback: altes TOOLSETS-System (Legacy)
        m = re.search(
            r'TOOLSETS\["agentiker_code_intel"\]\["tools"\]\s*=\s*\[(.*?)\]',
            text, re.DOTALL
        )
    if not m:
        # Letzter Fallback: allgemeiner "tools": [ Match
        m = re.search(r'"tools":\s*\[(.*?)\]', text, re.DOTALL)
        if not m:
            return []
    raw = m.group(1)

    # Alle quoted Strings extrahieren
    all_strings = re.findall(r'"([^"]+)"', raw)

    # Bekannte Nicht-Tools rausfiltern
    NON_TOOLS = {
        "agentiker_code_intel", "description",
    }
    tools = [s for s in all_strings if s not in NON_TOOLS and not s.startswith("AST-aware")]

    # Deduplizieren (Reihenfolge erhalten)
    seen = set()
    deduped = []
    for t in tools:
        if t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped


# ---------------------------------------------------------------------------
# Profile Info — aus _TOOL_PROFILES in __init__.py
# ---------------------------------------------------------------------------

def _get_profile_info() -> str:
    """Extrahiere Profile-Infos aus _TOOL_PROFILES in __init__.py.

    Gibt kompakt-formatierten String zurück z.B.:
    \"all (39), core (11), search (8), edit (7), lsp (16)\"
    """
    text = INIT_PATH.read_text("utf-8")

    # _TOOL_PROFILES Block finden — non-greedy bis zur schließenden }
    # Unterstützt _TOOL_PROFILES = { ... } und _TOOL_PROFILES: dict = { ... }
    m = re.search(r'_TOOL_PROFILES(?:\s*:\s*dict)?\s*=\s*\{(.*?)\}', text, re.DOTALL)
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


# ---------------------------------------------------------------------------
# LSP Languages — aus _LANGUAGE_SERVERS Dict
# ---------------------------------------------------------------------------

def _get_lsp_languages() -> list[str]:
    """Extrahiere LSP-Sprachen-Liste aus lsp_bridge.py."""
    text = LSP_BRIDGE_PATH.read_text("utf-8")

    # Robuster Regex: findet _LANGUAGE_SERVERS = { ... }
    # Sucht nach dem öffnenden { und sammelt Keys bis zur schließenden }
    m = re.search(
        r'_LANGUAGE_SERVERS\s*(?::\s*Dict.*?)?=\s*\{(.*?)^\}',
        text, re.MULTILINE | re.DOTALL
    )
    if not m:
        log("_LANGUAGE_SERVERS pattern not found — trying fallback")
        # Fallback: finde alle quoted Strings nach _LANGUAGE_SERVERS
        m = re.search(r'_LANGUAGE_SERVERS.*?\{(.*?)\}', text, re.DOTALL)
        if not m:
            return []
    body = m.group(1)
    langs = re.findall(r'^\s+"([^"]+)":', body, re.MULTILINE)
    return langs


# ---------------------------------------------------------------------------
# AST Languages — aus code_intel.py _EXT_TO_LANG
# ---------------------------------------------------------------------------

def _get_ast_languages() -> list[str]:
    """Extrahiere AST-Sprachen aus code_intel.py (_EXT_TO_LANG Dict)."""
    text = CODE_INTEL_PATH.read_text("utf-8")
    m = re.search(r'_EXT_TO_LANG\s*=\s*\{(.*?)^\}', text, re.MULTILINE | re.DOTALL)
    if not m:
        return []
    body = m.group(1)
    # Extrahiere die Values (language names), nicht die Keys (extensions)
    langs = re.findall(r':\s*"([^"]+)"', body)
    # Deduplizieren
    seen = set()
    deduped = []
    for lang in langs:
        if lang not in seen:
            seen.add(lang)
            deduped.append(lang)
    return deduped


# ---------------------------------------------------------------------------
# CHANGELOG
# ---------------------------------------------------------------------------

def _get_last_changelog_entries(count: int = 3) -> str:
    """Lese die letzten count CHANGELOG-Einträge."""
    text = CHANGELOG_PATH.read_text("utf-8") if CHANGELOG_PATH.exists() else ""
    entries = re.findall(r'^##\s+\[.*?\].*?(?=^##\s+\[|\Z)', text, re.MULTILINE | re.DOTALL)
    selected = []
    for e in entries[:count]:
        lines = e.strip().split("\n")
        selected.append("\n".join(lines[:15]))
    return "\n\n".join(selected)


# ---------------------------------------------------------------------------
# Test Stats — mit korrektem Venv-Pfad
# ---------------------------------------------------------------------------

def _get_test_stats() -> str:
    """Führe pytest --collect-only im Hermes-Venv aus."""
    # Hermes-Venv Python finden
    hermes_venv_python = _find_hermes_python()
    if not hermes_venv_python:
        log("Hermes-Venv nicht gefunden, versuche sys.executable")
        hermes_venv_python = sys.executable

    try:
        result = subprocess.run(
            [hermes_venv_python, "-m", "pytest", "tests/", "--collect-only", "-q"],
            capture_output=True, text=True, timeout=30, cwd=str(PLUGIN_DIR),
        )
        for line in result.stdout.strip().split("\n"):
            m = re.search(r'(\d+)\s+(tests?\s+)?(collected|selected)', line)
            if m:
                count = m.group(1)
                log(f"Test count: {count}")
                return count + " tests"
        log(f"pytest output: {result.stdout[:200]}")
        return "?"
    except FileNotFoundError:
        log("pytest not found")
        return "?"
    except subprocess.TimeoutExpired:
        log("pytest timed out")
        return "?"
    except Exception as exc:
        log(f"pytest error: {exc}")
        return "?"


def _find_hermes_python() -> str:
    """Finde den Python-Interpreter des Hermes-Venv."""
    candidates = list(Path.home().glob(".hermes/hermes-agent/venv/bin/python3*"))
    if candidates:
        return str(candidates[0])
    # Alternative: venv im Plugin-Verzeichnis
    candidates = list(PLUGIN_DIR.glob(".venv/bin/python3*"))
    if candidates:
        return str(candidates[0])
    return ""


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

def generate_auto_section() -> str:
    """Generiere den Inhalt zwischen den AUTO-GENERATED Markern."""
    version = _get_version()
    tools = _get_tool_list()
    lsp_langs = _get_lsp_languages()
    ast_langs = _get_ast_languages()
    changelog = _get_last_changelog_entries(3)
    test_count = _get_test_stats()

    lines = [
        "<!-- AUTO-GENERATED -->",
        "",
        f"**Version:** {version}",
        f"**Tests:** {test_count}",
        f"**Tools ({len(tools)}):** {', '.join(tools)}",
    ]

    profile_info = _get_profile_info()
    if profile_info:
        lines.append(f"**Profiles:** {profile_info}")

    if lsp_langs:
        lines.append(f"**LSP Languages:** {', '.join(sorted(set(lsp_langs)))}")
    if ast_langs:
        lines.append(f"**AST Languages:** {', '.join(sorted(set(ast_langs)))}")
    lines.extend([
        "",
        "### Recent Changelog",
        "",
        changelog,
        "",
        "<!-- END AUTO-GENERATED -->",
    ])
    return "\n".join(lines)


def generate_meta_section() -> str:
    """Generiere den Header-Metadaten-Block (24→31 Tools etc.)."""
    tools = _get_tool_list()
    lsp_langs = _get_lsp_languages()
    ast_langs = _get_ast_languages()

    lsp_count = sum(1 for t in tools if _is_lsp_tool(t))
    ast_count = len(tools) - lsp_count

    profile_info = _get_profile_info()
    profile_count = len(profile_info.split(", ")) if profile_info else 0
    profile_suffix = f", {profile_count} profiles" if profile_count > 0 else ""

    lines = [
        "<!-- META -->",
        f"**{len(tools)} tools** ({ast_count} AST + {lsp_count} LSP{profile_suffix}) — {', '.join(sorted(set(lsp_langs + ast_langs)))}",
        "<!-- END META -->",
    ]
    return "\n".join(lines)


def _is_lsp_tool(tool_name: str) -> bool:
    """Heuristik: LSP-Tools sind die in lsp_bridge.py registrierten."""
    text = LSP_BRIDGE_PATH.read_text("utf-8")
    return f'name="{tool_name}"' in text or f'"{tool_name}"' in text


# ---------------------------------------------------------------------------
# README Update
# ---------------------------------------------------------------------------

def update_readme() -> bool:
    """Update README.md zwischen den Markern. Returns True wenn geändert."""
    if not README_PATH.exists():
        print(f"README not found: {README_PATH}")
        return False

    old_text = README_PATH.read_text("utf-8")
    auto_section = generate_auto_section()
    meta_section = generate_meta_section()

    changes = False

    # 1. AUTO-GENERATED Block ersetzen
    if "<!-- END AUTO-GENERATED -->" not in old_text:
        print("No END AUTO-GENERATED marker found. Appending.")
        old_text = old_text.rstrip() + "\n\n" + auto_section + "\n"
        changes = True
    else:
        new_text = re.sub(
            r'<!-- AUTO-GENERATED -->.*?<!-- END AUTO-GENERATED -->',
            auto_section,
            old_text,
            flags=re.DOTALL,
        )
        if new_text != old_text:
            changes = True
        old_text = new_text

    # 2. META Block ersetzen (optional)
    if "<!-- END META -->" in old_text:
        new_text = re.sub(
            r'<!-- META -->.*?<!-- END META -->',
            meta_section,
            old_text,
            flags=re.DOTALL,
        )
        if new_text != old_text:
            changes = True
        old_text = new_text

    if changes:
        README_PATH.write_text(old_text, "utf-8")
    return changes


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Generate README auto sections")
    parser.add_argument("--check", action="store_true", help="Exit 1 if README is stale")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show debug info")
    args = parser.parse_args()

    global VERBOSE
    VERBOSE = args.verbose

    # Validierung: Haben wir Zugriff auf die Dateien?
    for name, path in [
        ("plugin.yaml", PLUGIN_YAML_PATH),
        ("__init__.py", INIT_PATH),
        ("lsp_bridge.py", LSP_BRIDGE_PATH),
        ("code_intel.py", CODE_INTEL_PATH),
        ("CHANGELOG.md", CHANGELOG_PATH),
    ]:
        if not path.exists():
            print(f"⚠️  {name} not found at {path}")

    changed = update_readme()

    if args.check and changed:
        print("❌ README.md is stale — regenerate with: python scripts/generate_readme.py")
        return 1
    if changed:
        print(f"✅ README.md updated ({README_PATH})")
    else:
        print("✅ README.md is current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
