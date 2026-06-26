# Registration helper
from ..bridge import logger


def _safe_register(name, toolset, schema, handler, check_fn=None, emoji=""):
    """Register a tool with error handling — one failure won't kill all registrations."""
    from tools.registry import registry

    try:
        registry.register(
            name=name,
            toolset=toolset,
            schema=schema,
            handler=handler,
            check_fn=check_fn,
            emoji=emoji,
        )
    except Exception as e:
        logger.warning("Failed to register tool '%s': %s", name, e)
