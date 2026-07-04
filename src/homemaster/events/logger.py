"""Minimal logging setup for HomeMaster.

Provides a single logger 'homemaster' with stderr StreamHandler at INFO level.
Call setup_logging() once at CLI entry; library code just uses get_logger().
"""

from __future__ import annotations

import logging
import sys

_LOGGER_NAME = "homemaster"


def get_logger() -> logging.Logger:
    """Return the HomeMaster logger. Safe to call before setup_logging()."""
    return logging.getLogger(_LOGGER_NAME)


def setup_logging(level: str = "INFO") -> None:
    """Configure the HomeMaster logger with a stderr handler.

    Always updates level (so --log-level works on repeated calls).
    Only adds handler once to avoid duplicate output.
    """
    logger = get_logger()
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
