"""
Shared logging setup for code_intel plugin modules.

Extracted into its own module to avoid circular imports between __init__.py
(which imports code_intel.py and lsp_bridge.py) and the modules themselves
(which need the centralized logger setup).
"""

import logging


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
