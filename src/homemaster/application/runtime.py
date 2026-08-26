"""Unified application runtime over sessions, model loops, and canonical tools."""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

from homemaster.agent.compact import strip_old_images
from homemaster.agent.context import ComposedContext, ContextAssembler
from homemaster.agent.generic_runtime import AgentRuntime, GenericRunResult
from homemaster.agent.messages import Message
from homemaster.agent.normalized import RunContext
from homemaster.agent.state import AgentState
from homemaster.application.contracts import (
    ResourceBinding,
    ResourceLifetime,
    RunRequest,
    RunResult,
    RunStatus,
    RuntimeStopDecision,
)
from homemaster.application.resources import ResourceCleanupError, RunResourceScope
from homemaster.application.session import (
    SessionGenerationError,
    SessionManager,
    SessionRuntime,
)
from homemaster.application.tool_executor import ApplicationToolExecutor
from homemaster.artifacts import ArtifactPublisher
from homemaster.events.bus import EventBus
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.extensions.contracts import AggregatedHookResult, HookEvent
from homemaster.extensions.hook_runner import HookRunner
from homemaster.memory.automatic_recall import (
    build_automatic_recall_context,
    build_automatic_recall_query,
    build_mindmemos_request_context,
)
from homemaster.memory.feedback_context import bind_feedback_contexts
from homemaster.providers.attempts import ListProviderAttemptSink
from homemaster.task_state.models import TaskStatus
from homemaster.task_state.store import TaskStateStore
from homemaster.tools.base import ToolRegistry
from homemaster.tools.executor import ToolExecutor
from homemaster.tools.paths import resolve_working_directory


class ProviderFactory(Protocol):
    def __call__(self, request: RunRequest, run_id: str) -> Any: ...


class ContextAssemblerFactory(Protocol):
    def __call__(self, request: RunRequest, provider: Any) -> ContextAssembler: ...


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


class Deadline:
    def __init__(self, timeout_s: float | None) -> None:
        self._expires_at = None if timeout_s is None else time.monotonic() + timeout_s

    def remaining_s(self) -> float | None:
        if self._expires_at is None:
            return None
        return max(0.0, self._expires_at - time.monotonic())


class AutomaticRecallRunDeadlineExceeded(TimeoutError):
    """The shared run deadline expired while automatic recall was pending."""


class _FencedAgentSession:
    """AgentSession-shaped facade that rejects writes from stale workers."""

    def __init__(self, manager: SessionManager, runtime: SessionRuntime, generation: int) -> None:
        self._manager = manager
        self._runtime = runtime
        self._generation = generation
        self.session_id = runtime.session.session_id

    @property
    def messages(self) -> list[Message]:
        return self._manager.apply(
            self.session_id,
            self._generation,
            lambda runtime: runtime.session.messages,
        )

    def append(self, message: Message) -> None:
        self._manager.append_message(self.session_id, self._generation, message)

    def replace_messages(self, messages: list[Message]) -> None:
        self._manager.apply(
            self.session_id,
            self._generation,
            lambda runtime: runtime.session.replace_messages(messages),
        )

    def clear(self) -> None:
        self._manager.apply(
            self.session_id,
            self._generation,
            lambda runtime: runtime.session.clear(),
        )

    def to_snapshot_dict(
        self,
        *,
        agent_state: AgentState,
        task_state_store: TaskStateStore,
        model: str,
        system_prompt: str,
        strip_images: bool = True,
        preserve_image_tool_call_ids: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        return self._manager.apply(
            self.session_id,
            self._generation,
            lambda runtime: runtime.session.to_snapshot_dict(
                agent_state=agent_state,
                task_state_store=task_state_store,
                model=model,
                system_prompt=system_prompt,
                strip_images=strip_images,
                preserve_image_tool_call_ids=preserve_image_tool_call_ids,
            ),
        )


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
        self._completion_requires_external_owner = any(
            tool.external_terminal_owner for tool in self.registry.list_tools()
        )
        self._verification_required_tool_names = frozenset(
            tool.name for tool in self.registry.list_tools() if tool.verification_required
        )
        self.event_bus = event_bus
        self.session_manager = session_manager
        self.provider_factory = provider_factory
        self.context_assembler_factory = context_assembler_factory
        self.settings = settings or SimpleNamespace()
        self.resource_scope = resource_scope or RunResourceScope()
        self._application_starter = application_starter
        self.extension_runner = extension_runner
        self.artifact_publisher = artifact_publisher
        self.session_end_handler = session_end_handler
        self._working_directory = resolve_working_directory(
            getattr(self.settings, "working_directory", Path.cwd())
        )
        self._start_lock = asyncio.Lock()
        self._started = False
        self._extension_stop_lock = asyncio.Lock()
        self._extension_stop_started = False
        self._extensions_closed = False
        self._browser_run_scopes: set[RunResourceScope] = set()

    def session(self, session_id: str, *, exit_reason: str = "session_end") -> ApplicationSession:
        """Return the explicit semantic session boundary used by entry points."""
        return ApplicationSession(self, session_id, exit_reason)

    @property
    def started(self) -> bool:
        return self._started

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
                    hook_result = await self._execute_extension_hooks(
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
                    await self._close_extensions()
                except BaseException as extension_cleanup_error:
                    exc.add_note(str(extension_cleanup_error))
                extensions_released = self.extension_runner is None or self.extension_runner.closed
                if extensions_released:
                    try:
                        await self.resource_scope.aclose()
                    except ResourceCleanupError as cleanup_error:
                        exc.add_note(str(cleanup_error))
                        exc.cleanup_error = cleanup_error  # type: ignore[attr-defined]
                raise
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

        async with self._extension_turn(
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
            return await self._execute_run(
                request=request,
                runtime=runtime,
                generation=generation,
                run_id=run_id,
                session_id=session_id,
                backend=backend,
                run_event_sink=run_event_sink,
            )

    async def _execute_run(
        self,
        *,
        request: RunRequest,
        runtime: SessionRuntime,
        generation: int,
        run_id: str,
        session_id: str,
        backend: object | None,
        run_event_sink: Any,
    ) -> RunResult:
        async with self._run_tool_view(request, run_id) as (
            run_registry,
            run_tool_executor,
        ):
            provider_value = await _maybe_await(self.provider_factory(request, run_id))
            provider_scope = RunResourceScope()
            async with provider_scope:
                provider = provider_scope.bind(
                    _provider_binding(provider_value, run_id=run_id)
                ).resource
                assembler = self.context_assembler_factory(request, provider)
                assembler.bind_working_directory(self._working_directory)
                agent_state = runtime.agent_state.model_copy(deep=True)
                agent_state.run_id = run_id
                current_user_evidence = self._register_user_memory_evidence(
                    request=request,
                    session_id=session_id,
                    run_id=run_id,
                    turn_index=agent_state.turn_index,
                )
                task_state_store = TaskStateStore.from_snapshot_dict(
                    runtime.task_state_store.to_snapshot_dict()
                )
                run_tools = run_registry.list_tools()
                executor = ApplicationToolExecutor(
                    executor=run_tool_executor,
                    registry=run_registry,
                    runtime=runtime,
                    run_id=run_id,
                    backend=backend,
                    request=request,
                    agent_state=agent_state,
                    task_state_store=task_state_store,
                    settings=self.settings,
                    event_sink=run_event_sink,
                    artifact_publisher=self.artifact_publisher,
                    working_directory=self._working_directory,
                    completion_requires_external_owner=any(
                        tool.external_terminal_owner for tool in run_tools
                    ),
                    verification_required_tool_names=frozenset(
                        tool.name for tool in run_tools if tool.verification_required
                    ),
                    initial_memory_evidence_refs=current_user_evidence,
                )
                try:
                    (
                        recall_attempted,
                        automatic_memory_context,
                        automatic_recalled_memories,
                    ) = await self._automatic_recall(
                        request=request,
                        runtime=runtime,
                        generation=generation,
                        run_id=run_id,
                        task_state_store=task_state_store,
                        event_sink=run_event_sink,
                        deadline=executor.deadline,
                    )
                    if recall_attempted:
                        await self._save_if_configured(session_id, generation)
                except asyncio.CancelledError:
                    raise
                except SessionGenerationError:
                    raise
                if automatic_memory_context:
                    bind_automatic_memory_context = getattr(
                        assembler, "bind_automatic_memory_context", None
                    )
                    if callable(bind_automatic_memory_context):
                        bind_automatic_memory_context(automatic_memory_context)
                bind_automatic_recalled_memories = getattr(
                    assembler, "bind_automatic_recalled_memories", None
                )
                if callable(bind_automatic_recalled_memories):
                    bind_automatic_recalled_memories(automatic_recalled_memories)
                run_context = RunContext(
                    session_id=session_id,
                    run_id=run_id,
                    turn_index=agent_state.turn_index,
                    settings=self.settings,
                    event_sink=run_event_sink,
                    deps={
                        "task_state_store": task_state_store,
                        "automatic_recalled_memories": automatic_recalled_memories,
                        "recalled_memories_by_tool_call_id": {},
                        "memory_feedback_context_by_tool_call_id": {},
                        "provider_attempt_context_binder": bind_feedback_contexts,
                    },
                    cancellation_token=runtime.cancellation,
                )
                fenced_session = _FencedAgentSession(
                    self.session_manager,
                    runtime,
                    generation,
                )
                agent = AgentRuntime(
                    transport=provider,
                    tool_executor=executor,
                    max_tool_iterations=request.run_policy.max_tool_iterations,
                    stop_condition=_stop_condition(request),
                    context_assembler=assembler,
                    system_prompt=getattr(assembler, "_system_prompt", ""),
                    provider_attempt_sink_factory=request.dependencies.get(
                        "provider_attempt_sink_factory",
                        ListProviderAttemptSink,
                    ),
                )

                async def rearm_recall_after_compaction(_metrics: Any) -> None:
                    runtime.require_recall_after_compaction(generation)
                    await self._save_if_configured(session_id, generation)

                try:
                    generic = await agent.run(
                        fenced_session,
                        request.text,
                        run_context,
                        event_sink=run_event_sink,
                        run_id=run_id,
                        settings=self.settings,
                        agent_state=agent_state,
                        task_state_store=task_state_store,
                        force_compact=runtime.consume_compaction(generation),
                        tool_registry=run_registry,
                        cancellation_token=runtime.cancellation,
                        deadline=executor.deadline,
                        on_compaction=rearm_recall_after_compaction,
                    )
                except asyncio.CancelledError:
                    return RunResult(
                        run_id=run_id,
                        session_id=session_id,
                        status=RunStatus.CANCELLED,
                        error_code="user_interrupted",
                    )
                except SessionGenerationError:
                    return RunResult(
                        run_id=run_id,
                        session_id=session_id,
                        status=RunStatus.CANCELLED,
                        error_code="stale_generation",
                    )
                if generic.status == "cancelled":
                    return RunResult(
                        run_id=run_id,
                        session_id=session_id,
                        status=RunStatus.CANCELLED,
                        error_code=generic.error_code or "user_interrupted",
                        events=tuple(generic.events),
                    )
                try:
                    result = self._commit_result(
                        runtime,
                        generation,
                        agent_state,
                        task_state_store,
                        executor.evidence_refs,
                        generic,
                    )
                    await self._save_if_configured(session_id, generation)
                except SessionGenerationError:
                    return RunResult(
                        run_id=run_id,
                        session_id=session_id,
                        status=RunStatus.CANCELLED,
                        error_code="stale_generation",
                    )
                return result

    async def _automatic_recall(
        self,
        *,
        request: RunRequest,
        runtime: SessionRuntime,
        generation: int,
        run_id: str,
        task_state_store: TaskStateStore,
        event_sink: Any,
        deadline: Any,
    ) -> tuple[bool, str | None, tuple[Any, ...]]:
        if not runtime.consume_recall(generation):
            return False, None, ()

        services = getattr(self.settings, "application_services", {})
        service = services.get("mindmemos") if isinstance(services, Mapping) else None
        search = getattr(service, "search", None)
        if not callable(search):
            await _emit_automatic_recall_event(
                event_sink,
                session_id=runtime.session.session_id,
                run_id=run_id,
                status="unavailable",
                count=0,
            )
            return True, None, ()

        query = build_automatic_recall_query(
            current_user_message=request.text,
            messages=runtime.session.messages,
            task_state_store=task_state_store,
        )
        context = build_mindmemos_request_context(
            request_id=f"automatic-recall:{run_id}",
            tenant_id=request.permission_subject.tenant_id,
            session_id=runtime.session.session_id,
        )
        try:
            result = await _await_with_remaining_deadline(
                search(
                    query,
                    context,
                    top_k=3,
                    search_pipeline="vanilla",
                    rerank=False,
                    filters=None,
                ),
                deadline,
            )
        except AutomaticRecallRunDeadlineExceeded:
            raise
        except (asyncio.CancelledError, SessionGenerationError):
            raise
        except Exception as exc:
            await _emit_automatic_recall_event(
                event_sink,
                session_id=runtime.session.session_id,
                run_id=run_id,
                status="error",
                count=0,
                error=str(exc),
            )
            return True, None, ()

        memories = list(getattr(result, "memories", ()))[:3]
        await _emit_automatic_recall_event(
            event_sink,
            session_id=runtime.session.session_id,
            run_id=run_id,
            status="ok" if memories else "empty",
            count=len(memories),
        )
        return True, build_automatic_recall_context(memories), tuple(memories)

    def _register_user_memory_evidence(
        self,
        *,
        request: RunRequest,
        session_id: str,
        run_id: str,
        turn_index: int,
    ) -> tuple[str, ...]:
        services = getattr(self.settings, "application_services", {})
        ledger = services.get("memory_evidence_ledger") if isinstance(services, Mapping) else None
        register = getattr(ledger, "register", None)
        if not callable(register):
            return ()
        evidence = register(
            kind="user_statement",
            tenant_id=request.permission_subject.tenant_id,
            session_id=session_id,
            run_id=run_id,
            turn_id=f"turn-{turn_index}",
        )
        return (evidence.ref,)

    @asynccontextmanager
    async def _run_tool_view(self, request: RunRequest, run_id: str):
        factory = request.dependencies.get("browser_session_factory")
        if factory is None:
            yield self.registry, self.tool_executor
            return
        create = getattr(factory, "create", None)
        if not callable(create):
            raise TypeError("browser_session_factory must provide create()")
        scope = RunResourceScope()
        self._browser_run_scopes.add(scope)
        try:
            async with scope:
                session = await _maybe_await(create(run_id=run_id))
                from homemaster.browser.contracts import audit_browser_session_implementation
                from homemaster.tools.browser import build_browser_run_registry

                scope.bind(
                    ResourceBinding.owned(
                        f"browser-session:{run_id}",
                        session,
                        lifetime=ResourceLifetime.RUN,
                    )
                )
                audit_browser_session_implementation(session)
                registry = build_browser_run_registry(self.registry, session)
                executor = ToolExecutor(
                    registry,
                    permission_checker=self.tool_executor.permission_checker,
                    confirmation_handler=self.tool_executor.confirmation_handler,
                    resource_manager=self.tool_executor.resource_manager,
                )
                yield registry, executor
        finally:
            self._browser_run_scopes.discard(scope)

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
        await self._close_extensions()
        try:
            browser_cleanup_errors: list[BaseException] = []
            for scope in tuple(self._browser_run_scopes):
                try:
                    await scope.aclose()
                except BaseException as exc:
                    browser_cleanup_errors.append(exc)
            await self.resource_scope.aclose()
            if browser_cleanup_errors:
                raise ResourceCleanupError(tuple(browser_cleanup_errors))
        finally:
            try:
                await self.event_bus.aclose()
            finally:
                for runtime in self.session_manager.sessions:
                    runtime.application_control = None

    async def _close_extensions(self) -> None:
        if self.extension_runner is None or self._extensions_closed:
            return
        async with self._extension_stop_lock:
            if self._extensions_closed:
                return
            if not self._extension_stop_started:
                quiesce_diagnostics = await self.extension_runner.quiesce()
                if quiesce_diagnostics:
                    raise RuntimeError("; ".join(quiesce_diagnostics))
                self._extension_stop_started = True
                await self._execute_extension_hooks(
                    HookEvent.APPLICATION_STOP,
                    {"event": HookEvent.APPLICATION_STOP.value},
                    session_id="application",
                    run_id="application",
                    best_effort=True,
                )
            cleanup_diagnostics = await self.extension_runner.aclose()
            await self.event_bus.aemit(
                RuntimeEvent(
                    type="extension.cleanup_completed",
                    session_id="application",
                    run_id="application",
                    turn_index=None,
                    payload={
                        "generation": self.extension_runner.generation.generation,
                        "success": self.extension_runner.closed and not cleanup_diagnostics,
                        "diagnostics": list(cleanup_diagnostics),
                    },
                )
            )
            self._extensions_closed = self.extension_runner.closed
            if not self._extensions_closed:
                raise RuntimeError(
                    "extension callbacks remain active; application resources were not closed"
                )

    @asynccontextmanager
    async def _extension_turn(
        self,
        turn_context: Any,
        *,
        request: RunRequest,
        run_id: str,
    ):
        async with turn_context as (runtime, generation, resumed):
            blocked_reason = ""
            try:
                if self.extension_runner is not None:
                    result = await self._execute_extension_hooks(
                        HookEvent.RUN_START,
                        {
                            "event": HookEvent.RUN_START.value,
                            "run_id": run_id,
                            "session_id": runtime.session.session_id,
                            "generation": generation,
                            "profile": request.profile,
                            "prompt": request.text,
                        },
                        session_id=runtime.session.session_id,
                        run_id=run_id,
                        principal_capabilities=request.permission_subject.capabilities,
                    )
                    blocked_reason = result.reason if result.blocked else ""
                yield runtime, generation, resumed, blocked_reason
            finally:
                if self.extension_runner is not None:
                    await self._execute_extension_hooks(
                        HookEvent.RUN_END,
                        {
                            "event": HookEvent.RUN_END.value,
                            "run_id": run_id,
                            "session_id": runtime.session.session_id,
                            "generation": generation,
                            "profile": request.profile,
                        },
                        session_id=runtime.session.session_id,
                        run_id=run_id,
                        principal_capabilities=request.permission_subject.capabilities,
                        best_effort=True,
                    )

    async def _execute_extension_hooks(
        self,
        event: HookEvent,
        payload: Mapping[str, object],
        *,
        session_id: str,
        run_id: str,
        principal_capabilities: tuple[str, ...] = (),
        best_effort: bool = False,
    ) -> AggregatedHookResult:
        runner = self.extension_runner
        if runner is None:
            return AggregatedHookResult()
        started = time.monotonic()
        result = await runner.execute(
            event,
            payload,
            principal_capabilities=principal_capabilities,
            best_effort=best_effort,
        )
        await self.event_bus.aemit(
            RuntimeEvent(
                type="extension.hook_completed",
                session_id=session_id,
                run_id=run_id,
                turn_index=None,
                payload={
                    "event": event.value,
                    "generation": runner.generation.generation,
                    "blocked": result.blocked,
                    "results": [
                        {
                            "extension_id": item.extension_id,
                            "hook_id": item.hook_id,
                            "success": item.success,
                            "blocked": item.blocked,
                            "timed_out": item.timed_out,
                            "stale_generation": item.stale_generation,
                            "reason": item.reason,
                            "output": item.output,
                        }
                        for item in result.results
                    ],
                },
                duration_ms=(time.monotonic() - started) * 1000,
            )
        )
        return result

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


def _backend_generation(backend: object | None, fallback: int) -> int:
    value = getattr(backend, "backend_generation", None)
    if callable(value):
        value = value()
    if value is None:
        value = getattr(backend, "generation", fallback)
        if callable(value):
            value = value()
    return (
        value if not isinstance(value, bool) and isinstance(value, int) and value >= 0 else fallback
    )


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


def _stop_condition(request: RunRequest):
    condition = request.run_policy.stop_condition

    async def stop(session, results):
        for result in results:
            data = getattr(result, "data", {})
            marker = data.get("data") if isinstance(data.get("data"), Mapping) else data
            if marker.get("waiting_user") is True:
                question = str(marker.get("question") or "Input required")
                return RuntimeStopDecision(
                    status="waiting_user",
                    final_reply=question,
                    payload={
                        "question": question,
                        "tool_call_id": marker.get("tool_call_id"),
                    },
                )
        if condition is None:
            return None
        value = condition({"session": session, "tool_results": results})
        if inspect.isawaitable(value):
            value = await value
        if isinstance(value, RuntimeStopDecision):
            return value
        if value:
            return RuntimeStopDecision(status="completed")
        return None

    return stop


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _await_with_remaining_deadline(awaitable: Any, deadline: Any) -> Any:
    remaining = deadline.remaining_s() if deadline is not None else None
    if remaining is None:
        return await awaitable
    if remaining <= 0:
        if inspect.iscoroutine(awaitable):
            awaitable.close()
        raise AutomaticRecallRunDeadlineExceeded("automatic recall exceeded the run deadline")
    timeout = asyncio.timeout(remaining)
    try:
        async with timeout:
            return await awaitable
    except TimeoutError as exc:
        if not timeout.expired():
            raise
        raise AutomaticRecallRunDeadlineExceeded(
            "automatic recall exceeded the run deadline"
        ) from exc


async def _emit_automatic_recall_event(
    event_sink: Any,
    *,
    session_id: str,
    run_id: str,
    status: str,
    count: int,
    error: str | None = None,
) -> None:
    if event_sink is None:
        return
    event = RuntimeEvent(
        type="memory.automatic_recall",
        session_id=session_id,
        run_id=run_id,
        turn_index=None,
        payload={"status": status, "count": count, "error": error},
    )
    aemit = getattr(event_sink, "aemit", None)
    if callable(aemit):
        await aemit(event)
        return
    emit = getattr(event_sink, "emit", None)
    if callable(emit):
        result = emit(event)
        if inspect.isawaitable(result):
            await result


def _provider_binding(value: Any, *, run_id: str) -> ResourceBinding:
    if isinstance(value, ResourceBinding):
        return value
    return ResourceBinding.owned(
        f"provider:{run_id}",
        value,
        lifetime=ResourceLifetime.RUN,
    )


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
    "CompactionResult",
    "ContextAssemblerFactory",
    "Deadline",
    "ProviderFactory",
    "SessionStatus",
]
