"""Tests for P3: minimal logging."""

from __future__ import annotations

import logging

from homemaster.logger import get_logger, setup_logging


class TestLogger:
    def test_get_logger_name(self) -> None:
        assert get_logger().name == "homemaster"

    def test_setup_logging_idempotent(self) -> None:
        logger = get_logger()
        logger.handlers.clear()
        setup_logging()
        setup_logging()
        assert len(logger.handlers) == 1
        logger.handlers.clear()

    def test_setup_logging_updates_level(self) -> None:
        logger = get_logger()
        logger.handlers.clear()
        setup_logging("INFO")
        assert logger.level == logging.INFO
        setup_logging("DEBUG")
        assert logger.level == logging.DEBUG
        assert len(logger.handlers) == 1
        logger.handlers.clear()

    def test_default_level_info(self) -> None:
        logger = get_logger()
        logger.handlers.clear()
        setup_logging()
        assert logger.level == logging.INFO
        logger.handlers.clear()
