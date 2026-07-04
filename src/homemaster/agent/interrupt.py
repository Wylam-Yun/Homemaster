"""Runtime interrupt coordination."""

from __future__ import annotations

from typing import Any


class InterruptController:
    """SIGINT coordination for LLM stream, tool execution, and iteration boundaries."""

    def __init__(self, *, abort_llm_stream: bool = True) -> None:
        self.cancelled = False
        self.abort_llm_stream = abort_llm_stream
        self._current_stream: Any = None
        self._in_tool = False

    @property
    def in_tool(self) -> bool:
        return self._in_tool

    def handle_sigint(self, signum: int, frame: Any) -> None:
        self.cancelled = True
        if self.abort_llm_stream and self._current_stream is not None:
            close = getattr(self._current_stream, "close", None)
            if callable(close):
                close()

    def set_stream(self, stream: Any) -> None:
        self._current_stream = stream

    def clear_stream(self) -> None:
        self._current_stream = None

    def enter_tool(self) -> None:
        self._in_tool = True

    def exit_tool(self) -> None:
        self._in_tool = False
