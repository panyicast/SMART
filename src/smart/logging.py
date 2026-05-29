"""Centralized logging for the SMART application.

Usage::

    from smart.logging import get_logger

    log = get_logger(__name__)
    log.debug("SPICE fallback: %s", reason)
    log.warning("Kernel load failed: %s", exc)

The default level is ``WARNING``.  Set the ``SMART_LOG_LEVEL`` environment
variable to ``DEBUG``, ``INFO``, or any standard level name to override.
"""

from __future__ import annotations

import logging
import os
import sys
import threading

_CONFIGURED = False
_CONFIG_LOCK = threading.Lock()

_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%H:%M:%S"


def _configure_root() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    with _CONFIG_LOCK:
        if _CONFIGURED:
            return

        level_name = os.environ.get("SMART_LOG_LEVEL", "WARNING").upper()
        level = getattr(logging, level_name, logging.WARNING)

        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT, datefmt=_DATE_FORMAT))

        root = logging.getLogger("smart")
        root.setLevel(level)
        root.propagate = False
        if not root.handlers:
            root.addHandler(handler)
        _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ``smart`` namespace.

    Parameters
    ----------
    name:
        Typically ``__name__`` of the calling module.  If *name* does not
        already start with ``smart.``, it is prefixed automatically so all
        SMART loggers share a common hierarchy.
    """
    _configure_root()
    if not name.startswith("smart."):
        name = f"smart.{name}"
    return logging.getLogger(name)
