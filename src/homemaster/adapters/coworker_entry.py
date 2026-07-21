"""Coworker outer composition adapter for the unified application runtime."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from homemaster.adapters.profiles import build_coworker_profile
from homemaster.agent.context import ContextAssembler
from homemaster.agent.messages import AssistantMessage
from homemaster.application import (
    ResourceBinding,
    ResourceCleanupError,
    ResourceLifetime,
    RunRequest,
    RunResourceScope,
    RunResult,
    SessionManager,
)
from homemaster.application.factory import create_application
from homemaster.benchmarking.coworker_demo.presentation import project_runtime_event
from homemaster.config import HomeMasterConfig
from homemaster.events.bus import EventBus
from homemaster.events.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ErrorEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from homemaster.observations import ObservationService
from homemaster.providers.llm_client import LLMClient


class DeadlineAwareTransport:
    """Provider transport that checks the benchmark-owned deadline budget."""

    def __init__(self, client: LLMClient, budget: Any, outcome: Any) -> None:
        self.client = client
        self.budget = budget
        self.outcome = outcome

    @property
    def token_estimator(self):
        return self.client.token_estimator

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        self.budget.before_external(self.outcome)
        stream = self.client.stream(*args, **kwargs)
        if not hasattr(stream, "__aiter__"):
            raise TypeError("coworker provider stream must be an async iterator")
        async for delta in stream:
            if self.budget.remaining_s <= 0:
                raise TimeoutError("coworker deadline expired during provider stream")
            yield delta

    async def complete(self, *args: Any, **kwargs: Any):
        self.budget.before_external(self.outcome)
        return await self.client.complete(*args, **kwargs)

    async def aclose(self) -> None:
        aclose = getattr(self.client, "aclose", None)
        if callable(aclose):
            await aclose()
            return
        close = getattr(self.client, "close", None)
        if callable(close):
            await asyncio.to_thread(close)


def build_coworker_transport_factory(
    *,
    provider_profile: Any,
    budget: Any,
    outcome: Any,
    timeout_s: float,
) -> Callable[[str], DeadlineAwareTransport]:
    def build(run_id: str) -> DeadlineAwareTransport:
        return DeadlineAwareTransport(
            LLMClient(provider_profile, timeout_s=timeout_s, run_id=run_id),
            budget,
            outcome,
        )

    return build


def build_coworker_stream_projector(*, sensitive_values: tuple[str, ...] = ()):
    """Adapt the Coworker trust-boundary projection to public stream DTOs."""

    def project(event):
        safe = project_runtime_event(event, sensitive_values=sensitive_values)
        if safe is None:
            return None
        event_type = safe.get("runtime_event_type")
        if event_type == "model.public_reply":
            output = safe.get("public_model_output")
            text = output.get("text") if isinstance(output, dict) else ""
            return AssistantTextDelta(text=str(text or ""))
        if event_type == "tool.call_started":
            arguments = safe.get("arguments")
            return ToolExecutionStarted(
                tool_name=str(safe.get("tool_name") or "unknown"),
                tool_input=dict(arguments) if isinstance(arguments, dict) else {},
            )
        if event_type in {"tool.call_completed", "tool.call_failed"}:
            result = safe.get("result")
            return ToolExecutionCompleted(
                tool_name=str(safe.get("tool_name") or "unknown"),
                output="",
                is_error=event_type == "tool.call_failed",
                metadata=dict(result) if isinstance(result, dict) else {},
            )
        if event_type == "runtime.turn_completed":
            return AssistantTurnComplete(message=AssistantMessage(), usage={})
        if isinstance(event_type, str) and event_type.startswith("runtime."):
            return ErrorEvent(message=event_type, recoverable=False)
        return None

    return project


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
        sync_backend_adapter: Any,
    ) -> None:
        observation = ObservationService()
        profile = build_coworker_profile(observation_service=observation)
        sensitive_values = tuple(getattr(event_sink, "sensitive_values", ()))
        bus = EventBus(
            public_projector=build_coworker_stream_projector(
                sensitive_values=sensitive_values,
            )
        )
        scope = RunResourceScope()
        if not callable(getattr(sync_backend_adapter, "submit", None)):
            raise TypeError("Coworker entry requires a thread-owned sync backend adapter")
        self._event_futures: deque[Future[Any]] = deque()
        self._event_errors: list[BaseException] = []

        def submit_event(event: Any) -> None:
            self._drain_completed_event_futures()
            self._event_futures.append(sync_backend_adapter.submit(event_sink.emit, event))

        unsubscribe = bus.subscribe(submit_event)
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
        self._sync_backend_adapter = sync_backend_adapter
        self._closed = False

    def run(self, request: RunRequest) -> RunResult:
        if self._closed:
            raise RuntimeError("Coworker application entry is closed")
        thread_adapter = request.dependencies.get("sync_backend_adapter")
        if thread_adapter is None or not callable(getattr(thread_adapter, "run", None)):
            raise ValueError(
                "Coworker runs require a borrowed thread-owned sync_backend_adapter"
            )
        return self._runner.run(self.application.run(request))

    def close(self) -> None:
        if self._closed:
            return
        errors: list[BaseException] = []
        try:
            try:
                self._runner.run(self.application.aclose())
            except BaseException as exc:
                errors.append(exc)
            errors.extend(self._event_errors)
            self._event_errors.clear()
            while self._event_futures:
                try:
                    self._event_futures.popleft().result()
                except BaseException as exc:
                    errors.append(exc)
            try:
                self._sync_backend_adapter.call(lambda: None)
            except BaseException as exc:
                errors.append(exc)
        finally:
            self._runner.close()
            self._closed = True
        if errors:
            raise ResourceCleanupError(tuple(errors))

    def _drain_completed_event_futures(self) -> None:
        pending: deque[Future[Any]] = deque()
        while self._event_futures:
            future = self._event_futures.popleft()
            if not future.done():
                pending.append(future)
                continue
            try:
                future.result()
            except BaseException as exc:
                self._event_errors.append(exc)
        self._event_futures = pending


__all__ = [
    "CoworkerApplicationEntry",
    "DeadlineAwareTransport",
    "build_coworker_stream_projector",
    "build_coworker_transport_factory",
]
