"""Application-owned capture for embedded third-party diagnostics."""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import warnings
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

import structlog

_LOGGER_ROOTS = (
    "mindmemos",
    "LiteLLM",
    "LiteLLM Router",
    "LiteLLM Proxy",
    "jieba",
    "neo4j",
    "neo4j.notifications",
    "qdrant_client",
    "py.warnings",
)

_CAPTURE_LOCK = threading.RLock()
_ACTIVE_CAPTURE = None


@dataclass(frozen=True)
class _LoggerState:
    handlers: tuple[logging.Handler, ...]
    level: int
    propagate: bool
    disabled: bool


class ThirdPartyLogCapture:
    """Redirect known embedded dependency logs and warnings to one private file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._active = False
        self._handler: logging.Handler | None = None
        self._stream: Any | None = None
        self._logger_states: dict[str, _LoggerState] = {}
        self._showwarning: Any | None = None
        self._structlog_was_configured = False
        self._structlog_config: dict[str, Any] = {}
        self._owned_structlog_config: dict[str, Any] = {}
        self._warning_hook: Any | None = None

    def start(self) -> None:
        global _ACTIVE_CAPTURE
        with _CAPTURE_LOCK:
            if _ACTIVE_CAPTURE is not None and _ACTIVE_CAPTURE is not self:
                raise RuntimeError("third-party log capture is already active")
            if self._active:
                return
            _ACTIVE_CAPTURE = self
            try:
                self._start()
            except BaseException:
                if _ACTIVE_CAPTURE is self:
                    _ACTIVE_CAPTURE = None
                raise

    def _start(self) -> None:
        if self._active:
            return
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(
            self.path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        self._stream = os.fdopen(descriptor, "a", encoding="utf-8", buffering=1)
        handler = logging.StreamHandler(self._stream)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        )
        self._handler = handler
        self._showwarning = warnings.showwarning
        self._structlog_was_configured = structlog.is_configured()
        self._structlog_config = dict(structlog.get_config())
        try:
            self._warning_hook = self._capture_warning
            warnings.showwarning = self._warning_hook
            self._configure_structlog()
            self._owned_structlog_config = dict(structlog.get_config())
            self._capture_loggers()
            self._active = True
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        global _ACTIVE_CAPTURE
        with _CAPTURE_LOCK:
            try:
                self._close()
            finally:
                if _ACTIVE_CAPTURE is self:
                    _ACTIVE_CAPTURE = None

    def _close(self) -> None:
        if not self._active and self._handler is None:
            return
        if (
            self._warning_hook is not None
            and warnings.showwarning is self._warning_hook
            and self._showwarning is not None
        ):
            warnings.showwarning = self._showwarning
        if structlog.get_config() == self._owned_structlog_config:
            if self._structlog_was_configured:
                structlog.configure(**self._structlog_config)
            else:
                structlog.reset_defaults()
        for name, state in self._logger_states.items():
            logger = logging.getLogger(name)
            if self._handler is not None and logger.handlers == [self._handler]:
                logger.handlers[:] = list(state.handlers)
            elif self._handler is not None and self._handler in logger.handlers:
                logger.handlers[:] = [
                    handler for handler in logger.handlers if handler is not self._handler
                ]
            if logger.level == logging.DEBUG:
                logger.setLevel(state.level)
            if logger.propagate is False:
                logger.propagate = state.propagate
            if logger.disabled is False:
                logger.disabled = state.disabled
        self._logger_states.clear()
        if self._handler is not None:
            self._handler.close()
        if self._stream is not None:
            self._stream.close()
        self._handler = None
        self._stream = None
        self._active = False

    def __enter__(self) -> ThirdPartyLogCapture:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()

    @contextlib.contextmanager
    def capture_dependency_imports(self) -> Any:
        """Capture direct import output, then reclaim dependency loggers."""

        if not self._active or self._stream is None:
            yield
            return
        try:
            with (
                contextlib.redirect_stdout(self._stream),
                contextlib.redirect_stderr(self._stream),
            ):
                yield
        finally:
            self._capture_loggers()

    def _capture_warning(
        self,
        message: Warning | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        file: Any | None = None,
        line: str | None = None,
    ) -> None:
        del file
        rendered = warnings.formatwarning(message, category, filename, lineno, line)
        logging.getLogger("py.warnings").warning(rendered.rstrip())

    def _capture_loggers(self) -> None:
        assert self._handler is not None
        for name in self._logger_names():
            logger = logging.getLogger(name)
            if name not in self._logger_states:
                self._logger_states[name] = _LoggerState(
                    handlers=tuple(logger.handlers),
                    level=logger.level,
                    propagate=logger.propagate,
                    disabled=logger.disabled,
                )
            logger.handlers[:] = [self._handler]
            logger.setLevel(logging.DEBUG)
            logger.propagate = False
            logger.disabled = False

    @staticmethod
    def _configure_structlog() -> None:
        structlog.configure(
            processors=[
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(ensure_ascii=False),
            ],
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
            cache_logger_on_first_use=False,
        )

    @staticmethod
    def _logger_names() -> tuple[str, ...]:
        names = set(_LOGGER_ROOTS)
        for name in logging.root.manager.loggerDict:
            if any(name == root or name.startswith(f"{root}.") for root in _LOGGER_ROOTS):
                names.add(name)
        return tuple(sorted(names))


__all__ = ["ThirdPartyLogCapture"]
