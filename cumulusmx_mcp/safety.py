"""Seguridad de las escrituras: interruptor ENABLE_WRITE_TOOLS."""
import os


from . import config


def _check_write_enabled() -> str | None:
    val = os.environ.get("ENABLE_WRITE_TOOLS", "true").strip().lower()
    if val in ("false", "0", "no", "off"):
        return config.WRITE_DISABLED_MSG
    return None
