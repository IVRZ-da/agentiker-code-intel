"""
Shared logging setup for code_intel plugin modules.

Extracted into its own module to avoid circular imports between __init__.py
(which imports code_intel.py and lsp_bridge.py) and the modules themselves
(which need the centralized logger setup).
"""

import logging
from pathlib import Path


def setup_logger(name: str) -> logging.Logger:
    """Create a logger with a dedicated DEBUG-level StreamHandler.

    Hermes core may set its own level on the root logger — this handler
    ensures our DEBUG logs are always visible regardless of parent config.
    Also sets ``propagate=False`` to avoid double-logging.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
        logger.addHandler(handler)
    return logger


def safe_read_text(path: str) -> str:
    """Read a text file with UTF-8 encoding, logging on decode errors.

    Uses ``errors=\"replace\"`` silently in older code. This wrapper
    logs a warning when replacement is triggered, so encoding issues
    are visible in debug output instead of silently corrupting data.
    """
    try:
        return Path(path).read_text("utf-8")
    except UnicodeDecodeError as exc:
        logger = logging.getLogger("code_intel")
        logger.warning("Unicode error in %s: %s — falling back to replace mode", path, exc)
        return Path(path).read_text("utf-8", errors="replace")
    except OSError as exc:
        logger = logging.getLogger("code_intel")
        logger.warning("IO error reading %s: %s", path, exc)
        raise
