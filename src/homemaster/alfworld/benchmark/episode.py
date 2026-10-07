"""Shared application/session lifecycle for benchmark episodes and tasksets."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from homemaster.application import RunRequest
from homemaster.application.composition import (
    ApplicationCompositionRequest,
    HomeApplicationBundle,
    compose_application,
)

TransportFactory = Callable[[], Any]


class BenchmarkApplicationLifecycle:
    """Own one composed application and all benchmark sessions on one loop.

    Episode and taskset runners use this same owner. It keeps provider creation,
    application startup, session close, and resource cleanup out of their
    domain-specific orchestration code.
    """

    def __init__(
        self,
        *,
        config: Any,
        memory_mode: str | None = None,
        runtime_root: Path,
        session_root: Path,
        transport_factory: TransportFactory | None,
        event_sink: Any,
        disable_memory_for_worker: bool = False,
    ) -> None:
        self.bundle: HomeApplicationBundle = compose_application(
            ApplicationCompositionRequest(
                config=config,
                profile="alfworld",
                runtime_root=runtime_root,
                session_root=session_root,
                event_sink=event_sink,
                quiet=True,
                console_show_replies=False,
                run_label=runtime_root.name,
                # ``--memory-mode disabled`` must really compose no memory at
                # all — clearing ``config.memory.enabled`` cannot switch off
                # the dependency-free files tier, so the kill-switch is an
                # explicit composition input instead of a config mutation.
                memory_off=disable_memory_for_worker and memory_mode == "disabled",
            )
        )
        self.application = self.bundle.application
        if transport_factory is not None:
            self.application.provider_factory = lambda _request, _run_id: transport_factory()
        self._runner = asyncio.Runner()
        self._closed = False
        self._sessions: dict[str, Any] = {}

    def run(self, request: RunRequest) -> Any:
        if self._closed:
            raise RuntimeError("ALFWorld application runtime is closed")
        return self._runner.run(self.application.run(request))

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("ALFWorld application runtime is closed")
        self._runner.run(self.application.start())

    def begin_session(self, session_id: str, *, exit_reason: str) -> None:
        if session_id in self._sessions:
            raise RuntimeError(f"ALFWorld session is already open: {session_id}")
        self._sessions[session_id] = self.application.session(session_id, exit_reason=exit_reason)

    def end_session(self, session_id: str) -> Any:
        session = self._sessions.pop(session_id)
        return session.close()

    def close(self) -> None:
        if self._closed:
            return
        try:
            for session_id in tuple(self._sessions):
                self.end_session(session_id)
            self._runner.run(self.application.aclose())
        finally:
            self._runner.close()
            self._closed = True


__all__ = ["BenchmarkApplicationLifecycle"]
