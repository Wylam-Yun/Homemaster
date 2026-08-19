"""ALFWorld outer composition adapter for the unified application runtime."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from homemaster.application import RunRequest, RunResult
from homemaster.cli.composition import HomeApplicationBundle, create_home_application
from homemaster.config import HomeMasterConfig


class AlfworldApplicationEntry:
    """Synchronous runner facade backed by one persistent asyncio loop."""

    def __init__(
        self,
        *,
        config: HomeMasterConfig,
        memory_mode: str,
        runtime_root: Path,
        session_root: Path,
        transport_factory: Callable[[], Any] | None,
        event_sink: Any,
    ) -> None:
        if memory_mode != "disabled":
            raise ValueError(
                "legacy ALFWorld memory_mode must remain disabled; embedded MindMemOS is "
                "controlled by memory.enabled"
            )
        self.bundle: HomeApplicationBundle = create_home_application(
            config=config,
            run_label=runtime_root.name,
            quiet=True,
            console_show_replies=False,
            event_sink=event_sink,
            tool_environment="alfworld",
            runtime_root=runtime_root,
            session_root=session_root,
        )
        if self.bundle.mindmemos is None or self.bundle.memory_add_queue is None:
            raise RuntimeError("ALFWorld benchmark requires embedded MindMemOS")
        self.application = self.bundle.application
        if transport_factory is not None:
            self.application.provider_factory = lambda _request, _run_id: transport_factory()
        import asyncio

        self._runner = asyncio.Runner()
        self._closed = False
        self._sessions: dict[str, Any] = {}

    def run(self, request: RunRequest) -> RunResult:
        if self._closed:
            raise RuntimeError("ALFWorld application entry is closed")
        return self._runner.run(self.application.run(request))

    def begin_session(self, session_id: str, *, exit_reason: str = "alfworld_episode_end") -> None:
        if session_id in self._sessions:
            raise RuntimeError(f"ALFWorld session is already open: {session_id}")
        self._sessions[session_id] = self.application.session(session_id, exit_reason=exit_reason)

    def end_session(self, session_id: str) -> Any | None:
        try:
            session = self._sessions.pop(session_id)
        except KeyError as exc:
            raise RuntimeError(f"ALFWorld session is not open: {session_id}") from exc
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


__all__ = ["AlfworldApplicationEntry"]
