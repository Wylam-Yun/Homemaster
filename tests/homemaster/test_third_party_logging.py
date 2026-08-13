from __future__ import annotations

import logging
import stat
import sys
import warnings
from pathlib import Path

import pytest
import structlog

from homemaster.cli.composition import create_home_application
from homemaster.config import HomeMasterConfig
from homemaster.events.third_party_logging import ThirdPartyLogCapture


def test_capture_routes_third_party_output_to_private_log(
    tmp_path: Path,
    capsys,
) -> None:
    logger_names = (
        "mindmemos.pipeline",
        "LiteLLM",
        "jieba",
        "neo4j.notifications",
        "qdrant_client",
    )
    loggers = [logging.getLogger(name) for name in logger_names]
    original_handlers = {logger.name: tuple(logger.handlers) for logger in loggers}
    terminal_handler = logging.StreamHandler(sys.stderr)
    for logger in loggers:
        logger.handlers[:] = [terminal_handler]
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
    handlers_at_start = {logger.name: tuple(logger.handlers) for logger in loggers}

    log_path = tmp_path / "run" / "third_party.log"
    capture = ThirdPartyLogCapture(log_path)
    capture.start()
    try:
        logging.getLogger("mindmemos.pipeline").debug("mindmemos-debug")
        logging.getLogger("LiteLLM").warning("litellm-warning")
        logging.getLogger("jieba").debug("jieba-debug")
        logging.getLogger("neo4j.notifications").warning("neo4j-warning")
        logging.getLogger("qdrant_client").warning("qdrant-warning")
        structlog.stdlib.get_logger("mindmemos.search").info("structlog-info")
        warnings.warn("qdrant-user-warning", UserWarning, stacklevel=1)
    finally:
        capture.close()

    terminal = capsys.readouterr()
    assert terminal.out == ""
    assert terminal.err == ""
    contents = log_path.read_text(encoding="utf-8")
    for expected in (
        "mindmemos-debug",
        "litellm-warning",
        "jieba-debug",
        "neo4j-warning",
        "qdrant-warning",
        "structlog-info",
        "qdrant-user-warning",
    ):
        assert expected in contents
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
    for logger in loggers:
        assert tuple(logger.handlers) == handlers_at_start[logger.name]
        logger.handlers[:] = list(original_handlers[logger.name])


def test_capture_close_restores_warning_hook_and_does_not_stack_handlers(
    tmp_path: Path,
) -> None:
    logger = logging.getLogger("LiteLLM")
    original_handlers = tuple(logger.handlers)
    original_showwarning = warnings.showwarning
    log_path = tmp_path / "third_party.log"

    for iteration in range(2):
        capture = ThirdPartyLogCapture(log_path)
        capture.start()
        logging.getLogger("LiteLLM").warning("iteration-%s", iteration)
        capture.close()
        assert tuple(logger.handlers) == original_handlers
        assert warnings.showwarning is original_showwarning

    contents = log_path.read_text(encoding="utf-8")
    assert contents.count("iteration-0") == 1
    assert contents.count("iteration-1") == 1


def test_capture_reclaims_loggers_installed_during_dependency_import(
    tmp_path: Path,
    capsys,
) -> None:
    logger = logging.getLogger("jieba.import")
    handlers_before = tuple(logger.handlers)
    log_path = tmp_path / "third_party.log"
    capture = ThirdPartyLogCapture(log_path)
    capture.start()
    try:
        with capture.capture_dependency_imports():
            print("direct-import-output", file=sys.stderr)
            logger.handlers[:] = [logging.StreamHandler(sys.stderr)]
            logger.warning("import-handler-output")
        logger.warning("post-import-output")
    finally:
        capture.close()

    terminal = capsys.readouterr()
    assert terminal.out == ""
    assert terminal.err == ""
    contents = log_path.read_text(encoding="utf-8")
    assert "direct-import-output" in contents
    assert "import-handler-output" in contents
    assert "post-import-output" in contents
    assert tuple(logger.handlers) == handlers_before


@pytest.mark.asyncio
async def test_home_application_owns_third_party_capture_lifecycle(
    tmp_path: Path,
) -> None:
    config = HomeMasterConfig(
        memory={"enabled": False},
        runtime={"runtime_root": tmp_path / "runs"},
    )
    logger = logging.getLogger("neo4j.notifications")
    handlers_before = tuple(logger.handlers)
    bundle = create_home_application(
        config=config,
        run_label="third-party-capture",
        tool_environment=None,
    )

    binding_names = {binding.name for binding in bundle.application.resource_scope.bindings}
    assert "third-party-log-capture" in binding_names
    assert not (bundle.run_dir / "third_party.log").exists()

    await bundle.application.start()
    logger.warning("composition-diagnostic")
    await bundle.application.aclose()

    log_path = bundle.run_dir / "third_party.log"
    assert log_path.is_file()
    assert "composition-diagnostic" in log_path.read_text(encoding="utf-8")
    assert tuple(logger.handlers) == handlers_before


def test_capture_rejects_overlapping_process_global_owner(tmp_path: Path) -> None:
    first = ThirdPartyLogCapture(tmp_path / "first.log")
    second = ThirdPartyLogCapture(tmp_path / "second.log")
    first.start()
    try:
        with pytest.raises(RuntimeError, match="already active"):
            second.start()
    finally:
        first.close()


def test_close_preserves_logging_state_replaced_by_another_owner(
    tmp_path: Path,
) -> None:
    logger = logging.getLogger("LiteLLM")
    handlers_before = tuple(logger.handlers)
    showwarning_before = warnings.showwarning
    replacement_handler = logging.NullHandler()

    def replacement_showwarning(
        message,
        category,
        filename,
        lineno,
        file=None,
        line=None,
    ) -> None:
        del message, category, filename, lineno, file, line

    capture = ThirdPartyLogCapture(tmp_path / "third_party.log")
    capture.start()
    logger.handlers[:] = [replacement_handler]
    warnings.showwarning = replacement_showwarning
    capture.close()
    try:
        assert logger.handlers == [replacement_handler]
        assert warnings.showwarning is replacement_showwarning
    finally:
        logger.handlers[:] = list(handlers_before)
        warnings.showwarning = showwarning_before
