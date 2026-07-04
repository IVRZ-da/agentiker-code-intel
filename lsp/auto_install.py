"""lsp/auto_install.py — Auto-Installation fehlender LSP Server.

Wird von pool.py get_bridge() aufgerufen, wenn ein LSP Server nicht
auf PATH gefunden wurde. Installiert den Server via pip/npm/rustup/go/apt
und meldet das Ergebnis zurueck.

Bei Erfolg: get_bridge() startet den Server normal.
Bei Fehler: stiller Fallback auf AST-Analyse (wie bisher).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
from typing import Optional

logger = logging.getLogger("code_intel.lsp.auto_install")

# Map: cmd-name → {install commands, timeout, needs_sudo, check_cmd}
# Wird von _auto_install_lsp() verwendet um fehlende Server nachzuinstallieren.
_LSP_INSTALL_MAP: dict[str, dict] = {
    "pylsp": {
        "command": [sys.executable, "-m", "pip", "install", "--user", "python-lsp-server"],
        "check_cmd": "pylsp --help",
        "timeout": 30,
        "needs_sudo": False,
        "package_manager": "pip",
        "hint": "pip install --user python-lsp-server",
    },
    "pyright-langserver": {
        "command": ["npm", "install", "-g", "pyright"],
        "check_cmd": "pyright-langserver --version",
        "timeout": 30,
        "needs_sudo": False,
        "package_manager": "npm",
        "hint": "npm install -g pyright",
    },
    "typescript-language-server": {
        "command": ["npm", "install", "-g", "typescript-language-server", "typescript"],
        "check_cmd": "typescript-language-server --version",
        "timeout": 30,
        "needs_sudo": False,
        "package_manager": "npm",
        "hint": "npm install -g typescript-language-server typescript",
    },
    "rust-analyzer": {
        "command": ["rustup", "component", "add", "rust-analyzer"],
        "check_cmd": "rust-analyzer --version",
        "timeout": 60,
        "needs_sudo": False,
        "package_manager": "rustup",
        "requires": "rustup",
        "hint": "rustup component add rust-analyzer",
    },
    "gopls": {
        "command": ["go", "install", "golang.org/x/tools/gopls@latest"],
        "check_cmd": "gopls version",
        "timeout": 60,
        "needs_sudo": False,
        "package_manager": "go",
        "requires": "go",
        "hint": "go install golang.org/x/tools/gopls@latest",
    },
    "clangd": {
        "command": ["apt-get", "install", "-y", "clangd-18"],
        "check_cmd": "clangd --version",
        "timeout": 60,
        "needs_sudo": True,
        "package_manager": "apt",
        "hint": "sudo apt-get install -y clangd-18",
    },
    "java-language-server": {
        "command": ["npm", "install", "-g", "java-language-server"],
        "check_cmd": "java-language-server --version",
        "timeout": 60,
        "needs_sudo": False,
        "package_manager": "npm",
        "hint": "npm install -g java-language-server",
    },
}

# Thread-Local: einmal installierte Server nicht nochmal probieren
_install_attempted: set[str] = set()
_install_lock = threading.Lock()


def _check_prerequisites(config: dict) -> Optional[str]:
    """Pruefe ob die notwendigen Tools fuer eine Installation vorhanden sind.

    Gibt None zurueck wenn alle Voraussetzungen erfuellt sind,
    sonst eine Fehlermeldung.
    """
    if config.get("needs_sudo"):
        # Pruefe ob sudo verfuegbar + wir root-Rechte haben (oder sudo ohne PW)
        if not shutil.which("sudo"):
            return "sudo not available — cannot install via apt"
        # Sudo -n testet ob sudo ohne Passwort funktioniert
        try:
            r = subprocess.run(
                ["sudo", "-n", "true"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                return "sudo requires password — cannot auto-install via apt"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return "sudo check failed — skipping apt install"

    requires = config.get("requires")
    if requires and not shutil.which(requires):
        return f"required tool '{requires}' not found — cannot install"

    # Pruefe Package-Manager
    pm = config.get("package_manager", "")
    pm_map = {"pip": sys.executable, "npm": "npm", "rustup": "rustup", "go": "go", "apt": "apt-get"}
    pm_binary = pm_map.get(pm)
    if pm_binary and not shutil.which(pm_binary):
        return f"package manager '{pm}' not found — cannot install"

    return None


def _auto_install_lsp(cmd_name: str, language_id: str) -> bool:
    """Installiere einen LSP Server automatisch.

    Returns:
        True:  Installation erfolgreich — Server ist jetzt verfuegbar
        False: Installation fehlgeschlagen oder nicht moeglich — AST-Fallback
    """
    config = _LSP_INSTALL_MAP.get(cmd_name)
    if not config:
        logger.debug("auto_install: no install config for %s", cmd_name)
        return False

    # Bereits versucht? (Idempotenz)
    with _install_lock:
        if cmd_name in _install_attempted:
            logger.debug("auto_install: already attempted %s, skipping", cmd_name)
            return False
        _install_attempted.add(cmd_name)

    # Vorbedingungen pruefen
    prereq_error = _check_prerequisites(config)
    if prereq_error:
        logger.info("auto_install: %s — %s", cmd_name, prereq_error)
        return False

    # Installation durchfuehren
    install_cmd = config["command"]
    timeout = config.get("timeout", 30)
    logger.info("auto_install: installing %s via %s ...", cmd_name, config["package_manager"])

    try:
        r = subprocess.run(
            install_cmd,
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            stderr_preview = r.stderr.strip()[:200] if r.stderr else "(no error output)"
            logger.warning(
                "auto_install: %s failed (rc=%d): %s",
                cmd_name, r.returncode, stderr_preview,
            )
            return False

        # Nach-Installations-Check: ist das Binary jetzt da?
        check_cmd = config.get("check_cmd", cmd_name)
        check_binary = check_cmd.split()[0]
        if not shutil.which(check_binary):
            logger.warning(
                "auto_install: %s reported success but %s still not on PATH",
                cmd_name, check_binary,
            )
            return False

        logger.info("auto_install: %s installed successfully", cmd_name)
        return True

    except subprocess.TimeoutExpired:
        logger.warning("auto_install: %s timed out after %ds", cmd_name, timeout)
        return False
    except FileNotFoundError:
        logger.warning("auto_install: %s command not found", install_cmd[0])
        return False
    except Exception as exc:
        logger.warning("auto_install: %s unexpected error: %s", cmd_name, exc)
        return False


def _get_install_hint(cmd_name: str) -> Optional[str]:
    """Gib einen menschenlesbaren Installations-Hinweis fuer den Server."""
    config = _LSP_INSTALL_MAP.get(cmd_name)
    if not config:
        return None
    hint = config.get("hint", "")
    needs_sudo = config.get("needs_sudo", False)
    if needs_sudo:
        return f"Install via: sudo {hint}"
    return f"Install via: {hint}"


def _install_attempted_for(cmd_name: str) -> bool:
    """Wurde ein Installationsversuch fuer diesen Server bereits gemacht?"""
    with _install_lock:
        return cmd_name in _install_attempted
