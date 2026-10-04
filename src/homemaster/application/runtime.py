"""Unified application runtime over sessions, model loops, and canonical tools.

``ApplicationRuntime`` is the session orchestrator: it owns the turn/
generation fence, the cancel/status/compact control plane, application
resource ownership and the final commit+save policy. Per-run assembly and
execution live in :mod:`homemaster.application.run_driver` (``RunDriver``),
extension hooks in :mod:`homemaster.application.extension_lifecycle`
(``ExtensionLifecycle``), and the automatic recall path in
:mod:`homemaster.application.memory_recall` (``AutomaticRecallService``).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homemaster.agent.compact import strip_old_images
from homemaster.agent.context import ComposedContext
from homemaster.agent.runtime_contracts import GenericRunResult
from homemaster.agent.state import AgentState
from homemaster.application.contracts import (
    RunRequest,
    RunResult,
    RunStatus,
)
from homemaster.application.extension_lifecycle import ExtensionLifecycle
from homemaster.application.memory_recall import AutomaticRecallRunDeadlineExceeded
from homemaster.application.resources import ResourceCleanupError, RunResourceScope
from homemaster.application.run_driver import (
    ContextAssemblerFactory,
    ProviderFactory,
    RunDriver,
    _maybe_await,
    _provider_binding,
)
from homemaster.application.run_driver import (
    _FencedAgentSession as _FencedAgentSession,
)
from homemaster.application.session import (
    SessionManager,
    SessionRuntime,
)
from homemaster.artifacts import ArtifactPublisher
from homemaster.events.bus import EventBus
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.extensions.contracts import HookEvent
from homemaster.extensions.hook_runner import HookRunner
from homemaster.task_state.models import TaskStatus
from homemaster.task_state.store import TaskStateStore
from homemaster.tools.base import ToolRegistry
from homemaster.tools.executor import ToolExecutor
from homemaster.tools.paths import resolve_working_directory

ApplicationStarter = Callable[["ApplicationRuntime"], Any]
SessionEndHandler = Callable[[str, str], Any]


@dataclass(frozen=True)
class SessionStatus:
    session_id: str
    generation: int
    revision: int
    status: str
    active: bool
    cancellation_requested: bool
    task_status: TaskStatus | None
    environment_ref: str | None


@dataclass(frozen=True)
class CompactionResult:
    session_id: str
    generation: int
    revision: int
    triggered: bool
    kind: str


class ApplicationSession:
    """Own one semantic session boundary without changing turn execution."""

    def __init__(self, application: ApplicationRuntime, session_id: str, exit_reason: str):
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if not isinstance(exit_reason, str) or not exit_reason.strip():
            raise ValueError("exit_reason must be a non-empty string")
        self._application = application
        self.session_id = session_id
        self.exit_reason = exit_reason
        self._closed = False
        self.receipt: Any | None = None

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self, *, exit_reason: str | None = None) -> Any | None:
        if self._closed:
            return self.receipt
        if exit_reason is not None:
            if not isinstance(exit_reason, str) or not exit_reason.strip():
                raise ValueError("exit_reason must be a non-empty string")
            self.exit_reason = exit_reason
        self._closed = True
        handler = self._application.session_end_handler
        if handler is not None:
            self.receipt = handler(self.session_id, self.exit_reason)
        return self.receipt

    async def __aenter__(self) -> ApplicationSession:
        return self

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        del exc_type, exc_value, traceback
        self.close()
        return False


class _GenerationFencedEventSink:
    """Reject event and tool-publication writes after a run loses ownership."""

    def __init__(
        self,
        runtime: SessionRuntime,
        generation: int,
        event_bus: EventBus,
        gateway_generation: int | None = None,
    ) -> None:
        self._runtime = runtime
        self._generation = generation
        self._event_bus = event_bus
        self._gateway_generation = gateway_generation

    @property
    def events(self) -> list[Any]:
        return self._event_bus.events

    def _guard(self) -> Any:
        return self._runtime.generation_guard(self._generation)

    def emit(self, event: Any) -> None:
        self._event_bus.emit_guarded(self._bind_gateway_generation(event), self._guard)

    async def aemit(self, event: Any) -> None:
        await self._event_bus.aemit_guarded(self._bind_gateway_generation(event), self._guard)

    def _bind_gateway_generation(self, event: Any) -> Any:
        if not isinstance(event, RuntimeEvent) or self._gateway_generation is None:
            return event
        return replace(event, gateway_generation=self._gateway_generation)

    async def publish(self, tool_call: Any, result: Any, context: Any, attempt_index: int) -> None:
        await self._event_bus.publish(
            tool_call,
            result,
            context,
            attempt_index,
            guard=self._guard,
        )


class ApplicationRuntime:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        event_bus: EventBus,
        session_manager: SessionManager,
        provider_factory: ProviderFactory,
        context_assembler_factory: ContextAssemblerFactory,
        settings: Any = None,
        resource_scope: RunResourceScope | None = None,
        application_starter: ApplicationStarter | None = None,
        extension_runner: HookRunner | None = None,
        artifact_publisher: ArtifactPublisher | None = None,
        tool_executor: ToolExecutor | None = None,
        session_end_handler: SessionEndHandler | None = None,
    ) -> None:
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry must be a ToolRegistry")
        self.registry = registry
        self.tool_executor = tool_executor or ToolExecutor(
            registry,
        )
        if self.tool_executor.registry is not self.registry:
            raise ValueError("tool executor must use the application ToolRegistry")
        self.event_bus = event_bus
        self.session_manager = session_manager
        self.provider_factory = provider_factory
        self.context_assembler_factory = context_assembler_factory
        self.settings = settings or SimpleNamespace()
        self.resource_scope = resource_scope or RunResourceScope()
        self._application_starter = application_starter
        self._extensions = ExtensionLifecycle(extension_runner, event_bus)
        self.artifact_publisher = artifact_publisher
        self.session_end_handler = session_end_handler
        self._working_directory = resolve_working_directory(
            getattr(self.settings, "working_directory", Path.cwd())
        )
        self._start_lock = asyncio.Lock()
        self._started = False
        self._browser_run_scopes: set[RunResourceScope] = set()
        self._permission_store_recovered = False
        self._permission_store_closed = False

    def session(self, session_id: str, *, exit_reason: str = "session_end") -> ApplicationSession:
        """Return the explicit semantic session boundary used by entry points."""
        return ApplicationSession(self, session_id, exit_reason)

    @property
    def started(self) -> bool:
        return self._started

    @property
    def extension_runner(self) -> HookRunner | None:
        """Read-only view of the installed runner; lifecycle-owned."""
        return self._extensions.runner

    async def start(self) -> None:
        """Start application-owned resources exactly once on the owner loop."""

        if self._started:
            return
        async with self._start_lock:
            if self._started:
                return
            if self.resource_scope.closed:
                raise RuntimeError("application resource scope is closed")
            try:
                if self._application_starter is not None:
                    await _maybe_await(self._application_starter(self))
                if self.extension_runner is not None:
                    hook_result = await self._extensions.execute(
                        HookEvent.APPLICATION_START,
                        {"event": HookEvent.APPLICATION_START.value},
                        session_id="application",
                        run_id="application",
                    )
                    if hook_result.blocked:
                        raise RuntimeError(
                            f"application_start extension hook blocked: {hook_result.reason}"
                        )
            except BaseException as exc:
                try:
                    await self._extensions.close()
                except BaseException as extension_cleanup_error:
                    exc.add_note(str(extension_cleanup_error))
                extensions_released = self._extensions.released
                if extensions_released:
                    try:
                        await self.resource_scope.aclose()
                    except ResourceCleanupError as cleanup_error:
                        exc.add_note(str(cleanup_error))
                        exc.cleanup_error = cleanup_error  # type: ignore[attr-defined]
                raise
            store = getattr(self.tool_executor, "permission_store", None)
            recover = getattr(store, "recover", None)
            if callable(recover) and not self._permission_store_recovered:
                recover()
                self._permission_store_recovered = True
            self._started = True

    async def run(self, request: RunRequest) -> RunResult:
        if not isinstance(request, RunRequest):
            raise TypeError("request must be RunRequest")
        await self.start()
        await self.event_bus.start()
        backend = request.borrowed_environment
        connection_pool = getattr(self.settings, "device_connection_pool", None)
        if backend is not None and connection_pool is not None:
            backend = connection_pool.bind_borrowed(
                backend,
                tenant_id=request.permission_subject.tenant_id,
            )
        environment_ref = _backend_id(backend, request.profile)
        session = await self.session_manager.open_or_resume(
            request.session_id,
            resume=request.resume,
            continuous_taskset=request.continuous_taskset,
        )
        session_id = session.session.session_id
        run_id = f"run-{uuid.uuid4().hex[:12]}"

        async with self._extensions.turn(
            self.session_manager.turn(
                session_id,
                environment_ref=environment_ref,
            ),
            request=request,
            run_id=run_id,
        ) as (runtime, generation, _, hook_blocked_reason):
            if hook_blocked_reason:
                return RunResult(
                    run_id=run_id,
                    session_id=session_id,
                    status=RunStatus.FAILED,
                    error_code="extension_run_start_blocked",
                    metadata={"reason": hook_blocked_reason},
                )
            run_event_sink = _GenerationFencedEventSink(
                runtime,
                generation,
                self.event_bus,
                gateway_generation=_gateway_generation(request),
            )
            if request.continuous_taskset and runtime.session.messages:
                messages, _ = strip_old_images(
                    runtime.session.messages,
                    keep_recent_images=0,
                )
                runtime.session.replace_messages(messages)
            bind_run = getattr(backend, "bind_application_run", None)
            if callable(bind_run):
                await _maybe_await(bind_run(run_id, generation))
            runtime.application_control = _control_request(request, session_id)
            # Per-run driver from *current* attributes — provider_factory /
            # context_assembler_factory / settings / artifact_publisher /
            # session_manager are rebindable between runs by contract.
            driver = RunDriver(
                provider_factory=self.provider_factory,
                context_assembler_factory=self.context_assembler_factory,
                settings=self.settings,
                artifact_publisher=self.artifact_publisher,
                session_manager=self.session_manager,
                registry=self.registry,
                tool_executor=self.tool_executor,
                working_directory=self._working_directory,
                save_fn=self._save_if_configured,
                commit_fn=self._commit_and_save,
                browser_run_scopes=self._browser_run_scopes,
            )
            return await driver.execute(
                request=request,
                runtime=runtime,
                generation=generation,
                run_id=run_id,
                session_id=session_id,
                backend=backend,
                run_event_sink=run_event_sink,
            )

    async def compact(self, session_id: str) -> CompactionResult:
        await self.start()
        control = self.session_manager.get(session_id).application_control
        request = control if isinstance(control, RunRequest) else None
        if request is None:
            request = RunRequest(
                text="internal compact control",
                session_id=session_id,
                resume=True,
            )
        async with self.session_manager.turn(session_id) as (runtime, generation, _):
            self.session_manager.request_compaction(session_id, generation, "manual")
            provider_value = await _maybe_await(
                self.provider_factory(request, f"compact-{generation}")
            )
            provider_scope = RunResourceScope()
            async with provider_scope:
                provider = provider_scope.bind(
                    _provider_binding(provider_value, run_id=f"compact-{generation}")
                ).resource
                assembler = self.context_assembler_factory(request, provider)
                aprepare = getattr(assembler, "aprepare", None)
                prepare = aprepare if callable(aprepare) else assembler.prepare
                composed: ComposedContext = await _maybe_await(
                    prepare(
                        session=runtime.session,
                        agent_state=runtime.agent_state,
                        task_state_store=runtime.task_state_store,
                        tools=self.registry.to_api_schema(),
                        force_compact=runtime.consume_compaction(generation),
                    )
                )
                if composed.metrics.compaction_triggered:
                    runtime.require_recall_after_compaction(generation)
                revision = await self._save_if_configured(session_id, generation)
                return CompactionResult(
                    session_id=session_id,
                    generation=generation,
                    revision=revision,
                    triggered=composed.metrics.compaction_triggered,
                    kind=composed.metrics.compaction_kind,
                )

    def cancel(self, session_id: str) -> bool:
        return self.session_manager.cancel(session_id)

    def status(self, session_id: str) -> SessionStatus:
        runtime = self.session_manager.get(session_id)
        snapshot = runtime.task_state_store.snapshot
        cancellation = runtime.cancellation
        return SessionStatus(
            session_id=session_id,
            generation=runtime.generation,
            revision=runtime.revision,
            status=runtime.agent_state.status,
            active=runtime.active_task is not None,
            cancellation_requested=bool(cancellation and cancellation.cancelled),
            task_status=snapshot.status if snapshot is not None else None,
            environment_ref=runtime.environment_ref,
        )

    async def aclose(self) -> None:
        await self._extensions.close()
        try:
            browser_cleanup_errors: list[BaseException] = []
            for scope in tuple(self._browser_run_scopes):
                try:
                    await scope.aclose()
                except BaseException as exc:
                    browser_cleanup_errors.append(exc)
            await self.resource_scope.aclose()
            store = getattr(self.tool_executor, "permission_store", None)
            close = getattr(store, "close", None)
            if callable(close) and not self._permission_store_closed:
                self._permission_store_closed = True
                close()
            if browser_cleanup_errors:
                raise ResourceCleanupError(tuple(browser_cleanup_errors))
        finally:
            try:
                await self.event_bus.aclose()
            finally:
                for runtime in self.session_manager.sessions:
                    runtime.application_control = None

    def _commit_result(
        self,
        runtime: SessionRuntime,
        generation: int,
        agent_state: AgentState,
        task_state_store: TaskStateStore,
        evidence_refs: tuple[str, ...],
        generic: GenericRunResult,
    ) -> RunResult:
        status = _run_status(generic.status)
        snapshot = task_state_store.snapshot
        if snapshot is not None and snapshot.status is TaskStatus.COMPLETED:
            status = RunStatus.COMPLETED
        result = RunResult(
            run_id=generic.run_id,
            session_id=runtime.session.session_id,
            status=status,
            final_reply=generic.final_reply,
            error_code=generic.error_code,
            events=tuple(generic.events),
        )

        def commit(current: SessionRuntime) -> None:
            current.agent_state = agent_state
            current.task_state_store = task_state_store
            current.canonical_evidence_refs = evidence_refs
            current.agent_state.status = status.value
            current.last_result = result

        self.session_manager.apply(runtime.session.session_id, generation, commit)
        return result

    async def _commit_and_save(
        self,
        runtime: SessionRuntime,
        generation: int,
        agent_state: AgentState,
        task_state_store: TaskStateStore,
        evidence_refs: tuple[str, ...],
        generic: GenericRunResult,
    ) -> RunResult:
        """Orchestrator-owned fenced commit + save, invoked inside the run
        scopes by the driver so cleanup failures attach to in-flight errors."""
        result = self._commit_result(
            runtime,
            generation,
            agent_state,
            task_state_store,
            evidence_refs,
            generic,
        )
        await self._save_if_configured(runtime.session.session_id, generation)
        return result

    async def _save_if_configured(self, session_id: str, generation: int) -> int:
        try:
            return await self.session_manager.save(session_id, generation=generation)
        except ValueError as exc:
            if "session backend is not configured" not in str(exc):
                raise
            return self.session_manager.get(session_id).revision


def _backend_id(backend: object | None, profile: str) -> str:
    value = getattr(backend, "backend_id", None)
    return str(value) if isinstance(value, str) and value.strip() else f"{profile}:none"


def _gateway_generation(request: RunRequest) -> int | None:
    value = request.metadata.get("gateway_generation")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _run_status(value: str) -> RunStatus:
    try:
        return RunStatus(value)
    except ValueError:
        return RunStatus.FAILED


def _control_request(request: RunRequest, session_id: str) -> RunRequest:
    """Keep only immutable scalar control data needed by a later compact()."""

    return RunRequest(
        text="internal compact control",
        session_id=session_id,
        profile=request.profile,
        provider_name=request.provider_name,
        resume=True,
        permission_subject=request.permission_subject,
    )


__all__ = [
    "ApplicationRuntime",
    "ApplicationStarter",
    "AutomaticRecallRunDeadlineExceeded",
    "CompactionResult",
    "ContextAssemblerFactory",
    "ProviderFactory",
    "SessionStatus",
]
