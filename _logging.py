"""
Shared logging setup for code_intel plugin modules.

Extracted into its own module to avoid circular imports between __init__.py
(which imports code_intel.py and lsp_bridge.py) and the modules themselves
(which need the centralized logger setup).

Provides a single shared ``StreamHandler`` to avoid byte-level interleaving
when multiple modules write to ``sys.stderr`` concurrently.
"""

import logging
from pathlib import Path

_shared_handler = None


def get_stderr_handler() -> logging.Handler:
    """Return the shared stderr StreamHandler singleton.

    Both ``code_intel.py`` and ``lsp_bridge.py`` previously created their
    own ``StreamHandler`` instances writing to the same ``sys.stderr``.
    Under concurrent I/O, CPython can release the GIL during ``fwrite()``,
    causing byte-level interleaving (corrupted logger names like
    ``text5ocument`` instead of ``textDocument``).

    A single shared handler serializes all log writes via its own internal
    lock, eliminating the interleaving.
    """
    global _shared_handler
    if _shared_handler is None:
        _shared_handler = logging.StreamHandler()
        _shared_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        ))
    return _shared_handler


def setup_logger(name: str) -> logging.Logger:
    """Create a logger with a shared DEBUG-level StreamHandler.

    Hermes core may set its own level on the root logger — this handler
    ensures our DEBUG logs are always visible regardless of parent config.
    Also sets ``propagate=False`` to avoid double-logging.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(get_stderr_handler())
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
