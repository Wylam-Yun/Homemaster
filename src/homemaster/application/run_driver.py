"""Per-run assembly and execution driver.

``ApplicationRuntime`` owns session/turn/generation fencing and the final
commit+save policy; ``RunDriver`` owns everything inside one run: the tool
view, provider resource scope, context assembler, tool executor, AgentScope
agent wiring, and automatic recall. The driver is constructed per run from
the runtime's *current* public attributes — callers may rebind
``provider_factory``/``context_assembler_factory``/``settings``/
``artifact_publisher``/``session_manager`` between runs and the next run
must observe the new values.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol

from homemaster.agent.context import ContextAssembler
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
from homemaster.application.memory_recall import (
    AutomaticRecallRunDeadlineExceeded,
    AutomaticRecallService,
)
from homemaster.application.resources import RunResourceScope
from homemaster.application.session import (
    SessionGenerationError,
    SessionManager,
    SessionRuntime,
)
from homemaster.application.tool_executor import ApplicationToolExecutor
from homemaster.artifacts import ArtifactPublisher
from homemaster.memory.feedback_context import bind_feedback_contexts
from homemaster.providers.attempts import ListProviderAttemptSink
from homemaster.task_state.store import TaskStateStore
from homemaster.tools.base import ToolRegistry
from homemaster.tools.executor import ToolExecutor


class ProviderFactory(Protocol):
    def __call__(self, request: RunRequest, run_id: str) -> Any: ...


class ContextAssemblerFactory(Protocol):
    def __call__(self, request: RunRequest, provider: Any) -> ContextAssembler: ...


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


class RunDriver:
    """Assemble and execute one run; never decides commit policy.

    The generation-fenced commit+save itself stays orchestrator-owned: it is
    invoked through the injected ``commit_fn`` at the original position —
    *inside* the provider and tool-view scopes, so a provider/browser
    cleanup failure still attaches to (and cannot preempt) an in-flight
    commit error.
    """

    def __init__(
        self,
        *,
        provider_factory: ProviderFactory,
        context_assembler_factory: ContextAssemblerFactory,
        settings: Any,
        artifact_publisher: ArtifactPublisher | None,
        session_manager: SessionManager,
        registry: ToolRegistry,
        tool_executor: ToolExecutor,
        working_directory: Path,
        save_fn: Callable[[str, int], Awaitable[int]],
        commit_fn: Callable[..., Awaitable[RunResult]],
        browser_run_scopes: set[RunResourceScope],
    ) -> None:
        self._provider_factory = provider_factory
        self._context_assembler_factory = context_assembler_factory
        self._settings = settings
        self._artifact_publisher = artifact_publisher
        self._session_manager = session_manager
        self._registry = registry
        self._tool_executor = tool_executor
        self._working_directory = working_directory
        self._save_fn = save_fn
        self._commit_fn = commit_fn
        self._browser_run_scopes = browser_run_scopes
        self._recall = AutomaticRecallService(settings)

    async def execute(
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
            provider_value = await _maybe_await(self._provider_factory(request, run_id))
            provider_scope = RunResourceScope()
            async with provider_scope:
                provider = provider_scope.bind(
                    _provider_binding(provider_value, run_id=run_id)
                ).resource
                assembler = self._context_assembler_factory(request, provider)
                assembler.bind_working_directory(self._working_directory)
                agent_state = runtime.agent_state.model_copy(deep=True)
                agent_state.run_id = run_id
                current_user_evidence = self._recall.register_user_evidence(
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
                    settings=self._settings,
                    event_sink=run_event_sink,
                    artifact_publisher=self._artifact_publisher,
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
                    ) = await self._recall.recall(
                        request=request,
                        runtime=runtime,
                        generation=generation,
                        run_id=run_id,
                        task_state_store=task_state_store,
                        event_sink=run_event_sink,
                        deadline=executor.deadline,
                    )
                    if recall_attempted:
                        await self._save_fn(session_id, generation)
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
                    settings=self._settings,
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
                    self._session_manager,
                    runtime,
                    generation,
                )
                if not _is_agentscope_provider(provider):
                    raise TypeError(
                        "provider must expose the AgentScope chat model interface (chat_model())"
                    )
                from homemaster.substrate.runtime import AsAgentRuntime
                from homemaster.substrate.toolkit import (
                    HomeToolAdapter,
                    RunScope,
                )

                scope = RunScope(
                    session_id=session_id,
                    run_id=run_id,
                    permission_subject=request.permission_subject,
                    working_directory=self._working_directory,
                    deadline=executor.deadline,
                    cancellation=runtime.cancellation,
                    backend=backend,
                    domain_observer=request.dependencies.get("domain_observer"),
                    services={
                        **request.dependencies,
                        "task_state_store": task_state_store,
                        "run_context": run_context,
                    },
                    turn_index=agent_state.turn_index,
                )
                adapters = [HomeToolAdapter(tool, executor, scope) for tool in run_tools]
                agent = AsAgentRuntime(
                    model=provider.chat_model(),
                    system_prompt=getattr(assembler, "_system_prompt", ""),
                    tools=adapters,
                    max_tool_iterations=request.run_policy.max_tool_iterations,
                    stop_condition=_stop_condition(request),
                    context_assembler=assembler,
                    provider_attempt_sink_factory=request.dependencies.get(
                        "provider_attempt_sink_factory",
                        ListProviderAttemptSink,
                    ),
                    model_api_format=getattr(provider, "api_format", ""),
                )

                async def rearm_recall_after_compaction(_metrics: Any) -> None:
                    # C+ (design-phase3 review): re-arm the generation-fenced
                    # flag, then recall + bind INLINE so the post-compaction
                    # continuation sees fresh memories on the next prepare —
                    # identical semantics on both engines. The bound context
                    # lands on the next ``prepare``, not the triggering call.
                    runtime.require_recall_after_compaction(generation)
                    (
                        recall_attempted,
                        post_memory_context,
                        post_recalled,
                    ) = await self._recall.recall(
                        request=request,
                        runtime=runtime,
                        generation=generation,
                        run_id=run_id,
                        task_state_store=task_state_store,
                        event_sink=run_event_sink,
                        deadline=executor.deadline,
                    )
                    if post_memory_context:
                        rebind_context = getattr(assembler, "bind_automatic_memory_context", None)
                        if callable(rebind_context):
                            rebind_context(post_memory_context)
                    rebind_recalled = getattr(assembler, "bind_automatic_recalled_memories", None)
                    if callable(rebind_recalled):
                        rebind_recalled(post_recalled)
                    run_context.deps["automatic_recalled_memories"] = post_recalled
                    if recall_attempted:
                        await self._save_fn(session_id, generation)

                try:
                    generic = await agent.run(
                        fenced_session,
                        request.text,
                        run_context,
                        event_sink=run_event_sink,
                        run_id=run_id,
                        settings=self._settings,
                        agent_state=agent_state,
                        task_state_store=task_state_store,
                        force_compact=runtime.consume_compaction(generation),
                        tool_registry=run_registry,
                        cancellation_token=runtime.cancellation,
                        deadline=executor.deadline,
                        on_compaction=rearm_recall_after_compaction,
                        engine_state=runtime.engine_state,
                        scope=scope,
                        # Keep the session fence and recall deadline
                        # surfacing raw (stale_generation → CANCELLED; recall
                        # deadline raises to the caller).
                        propagate_exceptions=(
                            SessionGenerationError,
                            AutomaticRecallRunDeadlineExceeded,
                        ),
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
                        # A user cancel bumps the generation — report the
                        # cause, not the fence mechanism that caught it.
                        error_code=_generation_error_code(runtime),
                    )
                if generic.engine_state is not None:
                    runtime.engine_state = generic.engine_state
                if generic.status == "cancelled":
                    return RunResult(
                        run_id=run_id,
                        session_id=session_id,
                        status=RunStatus.CANCELLED,
                        error_code=generic.error_code or "user_interrupted",
                        events=tuple(generic.events),
                    )
                try:
                    return await self._commit_fn(
                        runtime,
                        generation,
                        agent_state,
                        task_state_store,
                        executor.evidence_refs,
                        generic,
                    )
                except SessionGenerationError:
                    return RunResult(
                        run_id=run_id,
                        session_id=session_id,
                        status=RunStatus.CANCELLED,
                        error_code=_generation_error_code(runtime),
                    )

    @asynccontextmanager
    async def _run_tool_view(self, request: RunRequest, run_id: str):
        factory = request.dependencies.get("browser_session_factory")
        if factory is None:
            yield self._registry, self._tool_executor
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
                registry = build_browser_run_registry(self._registry, session)
                executor = ToolExecutor(
                    registry,
                    permission_checker=self._tool_executor.permission_checker,
                    confirmation_handler=self._tool_executor.confirmation_handler,
                    resource_manager=self._tool_executor.resource_manager,
                    permission_store=getattr(self._tool_executor, "permission_store", None),
                    physical_owner=getattr(self._tool_executor, "physical_owner", None),
                )
                yield registry, executor
        finally:
            self._browser_run_scopes.discard(scope)


def _is_agentscope_provider(provider: object) -> bool:
    """True when the provider seam carries a vendored ``ChatModelBase`` —
    i.e. the Phase-2 AgentScope agent path."""
    return callable(getattr(provider, "chat_model", None))


def _stop_condition(request: RunRequest):
    condition = request.run_policy.stop_condition

    async def stop(session, results):
        for result in results:
            data = getattr(result, "data", None)
            if not isinstance(data, Mapping):
                continue
            inner = data.get("data")
            marker = inner if isinstance(inner, Mapping) else data
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


def _generation_error_code(runtime: Any) -> str:
    """Report why a generation-fenced run ended.

    A user cancel bumps the session generation, so a fence rejection can be
    either the user's interrupt (cancellation source set) or a newer run
    superseding this one (stale_generation).
    """
    cancellation = getattr(runtime, "cancellation", None)
    if cancellation is not None and getattr(cancellation, "cancelled", False):
        return "user_interrupted"
    return "stale_generation"


def _provider_binding(value: Any, *, run_id: str) -> ResourceBinding:
    if isinstance(value, ResourceBinding):
        return value
    return ResourceBinding.owned(
        f"provider:{run_id}",
        value,
        lifetime=ResourceLifetime.RUN,
    )
