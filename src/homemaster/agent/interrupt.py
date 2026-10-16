"""Runtime interrupt coordination."""

from __future__ import annotations

import asyncio
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
        # A ``signal.signal`` handler runs synchronously on the loop thread
        # while the selector is parked inside ``kevent``/``select``. Every
        # wakeup queued above (``task.cancel()`` → ``Future.cancel()`` →
        # ``loop.call_soon``) only appends to ``_ready`` — it does NOT write
        # the self-pipe, and PEP 475 restarts the interrupted selector call
        # with its old timeout. Without an explicit thread-safe wakeup the
        # loop sleeps until the next fd/timer event (e.g. the subprocess
        # child watcher firing on natural exit), so cancellation is applied
        # only after the in-flight tool finishes by itself.
        try:
            asyncio.get_running_loop().call_soon_threadsafe(lambda: None)
        except RuntimeError:
            # No loop running on this thread — nothing to wake.
            pass

    def set_stream(self, stream: Any) -> None:
        self._current_stream = stream

    def clear_stream(self) -> None:
        self._current_stream = None

    def enter_tool(self) -> None:
        self._in_tool = True

    def exit_tool(self) -> None:
        self._in_tool = False
