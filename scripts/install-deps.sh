#!/usr/bin/env bash
set -euo pipefail
PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${HERMES_PYTHON:-}"
if [ -z "$VENV_PYTHON" ]; then
  if [ -f "$HOME/.hermes/hermes-agent/venv/bin/python3" ]; then
    VENV_PYTHON="$HOME/.hermes/hermes-agent/venv/bin/python3"
  else
    VENV_PYTHON="python3"
  fi
fi
echo "Installiere code_intel plugin dependencies..."
"$VENV_PYTHON" -m pip install -e "$PLUGIN_DIR" 2>/dev/null || {
  echo "Venv nicht schreibbar, versuche --user Fallback..."
  "$VENV_PYTHON" -m pip install --user -e "$PLUGIN_DIR"
}
echo "✅ code_intel plugin dependencies installed"
