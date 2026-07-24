"""ALFWorld outer composition adapter for the unified application runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from homemaster.adapters.profiles import build_alfworld_profile
from homemaster.application import (
    ResourceBinding,
    ResourceLifetime,
    RunRequest,
    RunResourceScope,
    RunResult,
    SessionManager,
)
from homemaster.application.factory import create_application
from homemaster.config import HomeMasterConfig
from homemaster.events.bus import EventBus


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
        profile = build_alfworld_profile(
            memory_mode=memory_mode,
            runtime_memory_root=runtime_root / "memory",
        )
        bus = EventBus()
        scope = RunResourceScope()
        unsubscribe = bus.subscribe(event_sink.emit)
        scope.bind(
            ResourceBinding.owned(
                "alfworld-event-subscription",
                unsubscribe,
                lifetime=ResourceLifetime.APPLICATION,
                release=lambda callback: callback(),
            )
        )
        self.application = create_application(
            config=config,
            profiles={"alfworld": profile},
            catalog=profile.catalog,
            event_bus=bus,
            session_manager=SessionManager(session_root=session_root),
            provider_factory=(
                (lambda request, run_id: transport_factory())
                if transport_factory is not None
                else None
            ),
            resource_scope=scope,
        )
        self.application.settings.runtime_root = runtime_root
        self.application.settings.debug_root = runtime_root / "debug"
        self.application.settings.results_root = runtime_root / "results"
        self.application.settings.config_path = config.config_path
        self.application.settings.prompts = config.prompts
        self.application.settings.observability = config.observability
        self.application.settings.embedding_provider_name = (
            config.runtime_defaults.default_embedding_provider_name
        )
        self._runner = asyncio.Runner()
        self._closed = False

    def run(self, request: RunRequest) -> RunResult:
        if self._closed:
            raise RuntimeError("ALFWorld application entry is closed")
        return self._runner.run(self.application.run(request))

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._runner.run(self.application.aclose())
        finally:
            self._runner.close()
            self._closed = True


__all__ = ["AlfworldApplicationEntry"]
