"""Coworker outer composition adapter for the unified application runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from homemaster.adapters.profiles import build_coworker_profile
from homemaster.agent.context import ContextAssembler
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
from homemaster.observations import ObservationService
from homemaster.providers.llm_client import LLMClient
from homemaster.providers.sync_adapter import SyncProviderAdapter


class DeadlineAwareTransport:
    """Provider transport that checks the benchmark-owned deadline budget."""

    def __init__(self, client: LLMClient, budget: Any, outcome: Any) -> None:
        self.client = client
        self.budget = budget
        self.outcome = outcome

    @property
    def token_estimator(self):
        return self.client.token_estimator

    def stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        self.budget.before_external(self.outcome)
        for delta in self.client.stream(*args, **kwargs):
            if self.budget.remaining_s <= 0:
                raise TimeoutError("coworker deadline expired during provider stream")
            yield delta

    def complete(self, *args: Any, **kwargs: Any):
        self.budget.before_external(self.outcome)
        return self.client.complete(*args, **kwargs)

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()

    async def aclose(self) -> None:
        close = getattr(self.client, "aclose", None)
        if callable(close):
            await close()
            return
        self.close()


def build_coworker_transport_factory(
    *,
    provider_profile: Any,
    budget: Any,
    outcome: Any,
    timeout_s: float,
) -> Callable[[str], DeadlineAwareTransport]:
    def build(run_id: str) -> DeadlineAwareTransport:
        return DeadlineAwareTransport(
            SyncProviderAdapter(
                LLMClient(provider_profile, timeout_s=timeout_s, run_id=run_id)
            ),
            budget,
            outcome,
        )

    return build


class CoworkerApplicationEntry:
    """Synchronous Coworker facade backed by one application runtime."""

    def __init__(
        self,
        *,
        config: HomeMasterConfig,
        provider_profile: Any,
        system_prompt: str,
        run_root: Path,
        transport_factory: Callable[[str], Any],
        event_sink: Any,
    ) -> None:
        observation = ObservationService()
        profile = build_coworker_profile(observation_service=observation)
        bus = EventBus()
        scope = RunResourceScope()
        unsubscribe = bus.subscribe(event_sink.emit)
        scope.bind(
            ResourceBinding.owned(
                "coworker-event-subscription",
                unsubscribe,
                lifetime=ResourceLifetime.APPLICATION,
                release=lambda callback: callback(),
            )
        )

        def provider_factory(_request: RunRequest, run_id: str) -> ResourceBinding:
            return ResourceBinding.owned(
                f"coworker-provider:{run_id}",
                transport_factory(run_id),
                lifetime=ResourceLifetime.RUN,
            )

        def context_factory(_request: RunRequest, provider: Any) -> ContextAssembler:
            return ContextAssembler(
                provider=provider_profile,
                policy=config.context,
                system_prompt=system_prompt,
                summary_client=provider,
            )

        self.application = create_application(
            config=config,
            profiles={"coworker": profile},
            catalog=profile.catalog,
            observation_service=observation,
            event_bus=bus,
            session_manager=SessionManager(session_root=run_root / "agent/session"),
            provider_factory=provider_factory,
            context_assembler_factory=context_factory,
            resource_scope=scope,
        )
        self.application.settings.runtime_root = run_root / "agent/runtime"
        self.application.settings.debug_root = run_root / "agent/debug"
        self.application.settings.results_root = run_root / "agent/results"
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
            raise RuntimeError("Coworker application entry is closed")
        return self._runner.run(self.application.run(request))

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._runner.run(self.application.aclose())
        finally:
            self._runner.close()
            self._closed = True


__all__ = [
    "CoworkerApplicationEntry",
    "DeadlineAwareTransport",
    "build_coworker_transport_factory",
]
