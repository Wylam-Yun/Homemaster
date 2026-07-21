"""Unified application runtime over sessions, model loops, and canonical tools."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any, Protocol

from homemaster.agent.context import ComposedContext, ContextAssembler
from homemaster.agent.generic_runtime import AgentRuntime, GenericRunResult, RuntimeStopDecision
from homemaster.agent.messages import Message, ToolCall, ToolResultMessage
from homemaster.agent.normalized import RunContext
from homemaster.agent.state import AgentState
from homemaster.application.contracts import (
    ResourceBinding,
    ResourceLifetime,
    RunRequest,
    RunResult,
    RunStatus,
)
from homemaster.application.resources import RunResourceScope
from homemaster.application.session import (
    SessionGenerationError,
    SessionManager,
    SessionRuntime,
)
from homemaster.events.bus import EventBus
from homemaster.observations import (
    ObservationLedger,
    ObservationProviderCommitter,
    ObservationService,
)
from homemaster.providers.attempts import ListProviderAttemptSink
from homemaster.task_state.models import TaskStatus
from homemaster.task_state.store import TaskStateStore
from homemaster.tools.catalog import ToolCatalog, ToolLookupStatus, ToolView
from homemaster.tools.contracts import (
    ExecutionProof,
    TerminalRule,
    ToolExecutionContext,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    VerificationStatus,
)
from homemaster.tools.legacy_adapter import LegacyExecutorAdapter, LegacyToolExecutionContext
from homemaster.tools.pipeline import ToolExecutionPipeline


class ProviderFactory(Protocol):
    def __call__(self, request: RunRequest, run_id: str) -> Any: ...


class ContextAssemblerFactory(Protocol):
    def __call__(self, request: RunRequest, provider: Any) -> ContextAssembler: ...


class ToolProfile(Protocol):
    @property
    def catalog(self) -> ToolCatalog: ...

    @property
    def enabled_tool_ids(self) -> tuple[str, ...]: ...


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


class Deadline:
    def __init__(self, timeout_s: float | None) -> None:
        self._expires_at = None if timeout_s is None else time.monotonic() + timeout_s

    def remaining_s(self) -> float | None:
        if self._expires_at is None:
            return None
        return max(0.0, self._expires_at - time.monotonic())


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


class _CompletionGuard:
    def __init__(
        self,
        *,
        ledger: ObservationLedger,
        view: ToolView,
        external_owner: object | None,
    ) -> None:
        self._ledger = ledger
        self._external_owner = external_owner
        self._external_required = any(
            tool.definition.verification_policy.terminal_rule
            is TerminalRule.EXTERNAL_TERMINAL_OWNER
            for tool in view.list_tools()
        )
        self._external_succeeded = False
        self._verification: dict[str, bool] = {}
        self._batch_has_mutation = False

    def begin_batch(self, calls: list[ToolCall], view: ToolView) -> None:
        mutating = 0
        has_completion = False
        for call in calls:
            lookup = view.lookup(call.name)
            tool = lookup.tool if lookup.status is ToolLookupStatus.ENABLED else None
            if tool is None:
                continue
            if tool.definition.state_effects:
                mutating += 1
            if (
                tool.definition.model_alias == "task_progress_check"
                and call.arguments.get("task_status") == "completed"
            ):
                has_completion = True
        self._batch_has_mutation = has_completion and mutating > 0

    def __call__(self) -> ToolExecutionResult | None:
        if self._ledger.observation_debt or self._batch_has_mutation:
            return _blocked_completion(
                ToolExecutionStatus.OBSERVATION_REQUIRED,
                "observation_required",
                "task completion requires a fresh explicit observation",
            )
        if any(not passed for passed in self._verification.values()):
            return _blocked_completion(
                ToolExecutionStatus.VERIFICATION_PENDING,
                "verification_pending",
                "task completion is waiting for required verification",
            )
        if self._external_required and not (
            self._external_succeeded or _external_owner_succeeded(self._external_owner)
        ):
            return _blocked_completion(
                ToolExecutionStatus.VERIFICATION_PENDING,
                "external_terminal_pending",
                "the external terminal owner has not reported success",
            )
        return None

    def record(
        self,
        internal_id: str,
        result: ToolExecutionResult,
        terminal_rule: TerminalRule,
    ) -> None:
        if result.verification.status is VerificationStatus.PASSED:
            self._verification[internal_id] = True
        elif result.verification.status in {
            VerificationStatus.PENDING,
            VerificationStatus.FAILED,
        }:
            self._verification[internal_id] = False
        if (
            terminal_rule is TerminalRule.EXTERNAL_TERMINAL_OWNER
            and result.success
            and result.terminal is not None
        ):
            self._external_succeeded = True


class _CanonicalToolExecutor:
    def __init__(
        self,
        *,
        pipeline: ToolExecutionPipeline,
        view: ToolView,
        runtime: SessionRuntime,
        run_id: str,
        backend: object | None,
        request: RunRequest,
        agent_state: AgentState,
        task_state_store: TaskStateStore,
        ledger: ObservationLedger,
        settings: Any,
        event_sink: EventBus,
    ) -> None:
        self._pipeline = pipeline
        self._view = view
        self._runtime = runtime
        self._run_id = run_id
        self._backend = backend
        self._request = request
        self._agent_state = agent_state
        self._task_state_store = task_state_store
        self._ledger = ledger
        self.evidence_refs = runtime.canonical_evidence_refs
        self._settings = settings
        self._event_sink = event_sink
        self._deadline = Deadline(request.run_policy.deadline_s)
        self._completion = _CompletionGuard(
            ledger=ledger,
            view=view,
            external_owner=request.dependencies.get("external_terminal_owner"),
        )

    def dispatch(
        self,
        *,
        tool_calls: list[ToolCall],
        run_context: RunContext | None = None,
    ) -> list[ToolResultMessage]:
        del run_context
        self._completion.begin_batch(tool_calls, self._view)
        contexts = [self._context_for(call) for call in tool_calls]
        results = asyncio.run(
            self._pipeline.execute_many(list(zip(tool_calls, contexts, strict=True)))
        )
        for _call, context, result in zip(tool_calls, contexts, results, strict=True):
            lookup = self._view.lookup(context.internal_tool_id)
            tool = lookup.tool if lookup.status is ToolLookupStatus.ENABLED else None
            if tool is not None:
                policy = tool.definition.verification_policy
                if policy.execution_proof is not ExecutionProof.NONE:
                    self._completion.record(
                        tool.definition.internal_id,
                        result,
                        policy.terminal_rule,
                    )
            self._record_evidence(result)
        return [
            result.to_message(tool_call_id=call.id, name=call.name)
            for call, result in zip(tool_calls, results, strict=True)
        ]

    def _context_for(self, call: ToolCall) -> ToolExecutionContext:
        lookup = self._view.lookup(call.name)
        if lookup.status is ToolLookupStatus.ENABLED and lookup.tool is not None:
            registered = lookup.tool
            internal_id = registered.definition.internal_id
        else:
            registered = None
            internal_id = call.name if "." in call.name else "runtime.unknown.v1"
        deps = dict(self._request.dependencies)
        deps["task_state_store"] = self._task_state_store
        deps["task_completion_guard"] = self._completion
        deps["observation_ledger"] = self._ledger
        deps["current_tool_call_id"] = call.id
        _bind_profile_backend(deps, self._request.profile, self._backend)
        legacy_run_context = RunContext(
            session_id=self._runtime.session.session_id,
            run_id=self._run_id,
            turn_index=self._agent_state.turn_index,
            settings=self._settings,
            event_sink=self._event_sink,
            deps=deps,
            cancellation_token=self._runtime.cancellation,
        )
        context_backend = self._backend
        if registered is not None and isinstance(registered.executor, LegacyExecutorAdapter):
            context_backend = LegacyToolExecutionContext(
                run_context=legacy_run_context,
                tool_call_id=call.id,
                internal_tool_id=internal_id,
                actual_backend=self._backend,
            )
        return ToolExecutionContext(
            session_id=self._runtime.session.session_id,
            run_id=self._run_id,
            turn_index=self._agent_state.turn_index,
            tool_call_id=call.id,
            internal_tool_id=internal_id,
            tool_view=self._view,
            permission_subject=self._request.permission_subject,
            backend=context_backend,
            deadline=self._deadline,
            cancellation=self._runtime.cancellation,
            observation=self._ledger,
            domain_observer=self._request.dependencies.get("domain_observer"),
        )

    def _record_evidence(self, result: ToolExecutionResult) -> None:
        refs = list(result.evidence_refs)
        refs.extend(result.verification.evidence_refs)
        if result.terminal is not None and result.terminal.evidence_ref is not None:
            refs.append(result.terminal.evidence_ref)
        if not refs:
            return

        self.evidence_refs = tuple(
            dict.fromkeys((*self.evidence_refs, *refs))
        )


class ApplicationRuntime:
    def __init__(
        self,
        *,
        catalog: ToolCatalog,
        profiles: Mapping[str, ToolProfile],
        pipeline: ToolExecutionPipeline,
        observation_service: ObservationService,
        event_bus: EventBus,
        session_manager: SessionManager,
        provider_factory: ProviderFactory,
        context_assembler_factory: ContextAssemblerFactory,
        settings: Any = None,
        resource_scope: RunResourceScope | None = None,
    ) -> None:
        self.catalog = catalog
        self.profiles = dict(profiles)
        self.pipeline = pipeline
        self.observation_service = observation_service
        self.event_bus = event_bus
        self.session_manager = session_manager
        self.provider_factory = provider_factory
        self.context_assembler_factory = context_assembler_factory
        self.settings = settings or SimpleNamespace()
        self.resource_scope = resource_scope or RunResourceScope()

    async def run(self, request: RunRequest) -> RunResult:
        if not isinstance(request, RunRequest):
            raise TypeError("request must be RunRequest")
        profile = self._profile(request.profile)
        backend = request.borrowed_environment
        environment_ref = _backend_id(backend, request.profile)
        session = await self.session_manager.open_or_resume(
            request.session_id,
            resume=request.resume,
            continuous_taskset=request.continuous_taskset,
        )
        session_id = session.session.session_id
        run_id = f"run-{uuid.uuid4().hex[:12]}"

        async with self.session_manager.turn(
            session_id,
            environment_ref=environment_ref,
        ) as (runtime, generation, _):
            bind_run = getattr(backend, "bind_application_run", None)
            if callable(bind_run):
                await _maybe_await(bind_run(run_id, generation))
            runtime.application_control = _control_request(request, session_id)
            view = self._view(request, profile)
            ledger = ObservationLedger(
                run_id=run_id,
                backend_id=_backend_id(backend, request.profile),
                generation=generation,
            )
            runtime.set_observation_reset(ledger.invalidate)
            provider_value = await _maybe_await(self.provider_factory(request, run_id))
            provider_scope = RunResourceScope()
            async with provider_scope:
                provider = provider_scope.bind(
                    _provider_binding(provider_value, run_id=run_id)
                ).resource
                assembler = self.context_assembler_factory(request, provider)
                run_pipeline = replace(self.pipeline, terminal_policy=request.terminal_policy)
                agent_state = runtime.agent_state.model_copy(deep=True)
                agent_state.run_id = run_id
                task_state_store = TaskStateStore.from_snapshot_dict(
                    runtime.task_state_store.to_snapshot_dict()
                )
                executor = _CanonicalToolExecutor(
                    pipeline=run_pipeline,
                    view=view,
                    runtime=runtime,
                    run_id=run_id,
                    backend=backend,
                    request=request,
                    agent_state=agent_state,
                    task_state_store=task_state_store,
                    ledger=ledger,
                    settings=self.settings,
                    event_sink=self.event_bus,
                )
                committer = ObservationProviderCommitter(self.observation_service, ledger)
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
                    model_view_observer=request.dependencies.get("model_view_observer"),
                    provider_commit_observer=committer,
                    provider_attempt_sink_factory=ListProviderAttemptSink,
                )
                worker = asyncio.create_task(
                    asyncio.to_thread(
                        agent.run,
                        fenced_session,
                        request.text,
                        None,
                        event_sink=self.event_bus,
                        run_id=run_id,
                        settings=self.settings,
                        agent_state=agent_state,
                        task_state_store=task_state_store,
                        force_compact=runtime.consume_compaction(generation),
                        tool_view=view,
                    )
                )
                try:
                    generic = await asyncio.shield(worker)
                except asyncio.CancelledError:
                    with contextlib.suppress(Exception):
                        await worker
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
                result = self._commit_result(
                    runtime,
                    generation,
                    agent_state,
                    task_state_store,
                    executor.evidence_refs,
                    generic,
                )
                await self._save_if_configured(session_id, generation)
                return result

    async def compact(self, session_id: str) -> CompactionResult:
        control = self.session_manager.get(session_id).application_control
        request = control if isinstance(control, RunRequest) else None
        if request is None:
            request = RunRequest(
                text="internal compact control",
                session_id=session_id,
                resume=True,
            )
        profile = self._profile(request.profile)
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
                composed: ComposedContext = assembler.prepare(
                    session=runtime.session,
                    agent_state=runtime.agent_state,
                    task_state_store=runtime.task_state_store,
                    tools=list(self._view(request, profile).manifests()),
                    force_compact=runtime.consume_compaction(generation),
                )
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
        try:
            await self.resource_scope.aclose()
        finally:
            for runtime in self.session_manager.sessions:
                runtime.application_control = None

    def _profile(self, name: str) -> ToolProfile:
        try:
            return self.profiles[name]
        except KeyError as exc:
            raise ValueError(f"unknown application profile: {name}") from exc

    def _view(self, request: RunRequest, profile: ToolProfile) -> ToolView:
        enabled = request.enabled_tool_ids or profile.enabled_tool_ids
        return self.catalog.freeze(enabled)

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


def _blocked_completion(
    status: ToolExecutionStatus,
    code: str,
    message: str,
) -> ToolExecutionResult:
    values: dict[str, Any] = {
        "status": status,
        "error": ToolExecutionError(code=code, message=message),
        "backend_attempted": False,
    }
    if status is ToolExecutionStatus.VERIFICATION_PENDING:
        values["verification"] = _pending_verification(message)
    return ToolExecutionResult(**values)


def _pending_verification(message: str):
    from homemaster.tools.contracts import VerificationRecord

    return VerificationRecord(status=VerificationStatus.PENDING, detail=message)


def _external_owner_succeeded(owner: object | None) -> bool:
    if owner is None:
        return False
    value = getattr(owner, "succeeded", None)
    if callable(value):
        value = value()
    if value is None:
        value = getattr(owner, "success", False)
    return bool(value)


def _backend_id(backend: object | None, profile: str) -> str:
    value = getattr(backend, "backend_id", None)
    return str(value) if isinstance(value, str) and value.strip() else f"{profile}:none"


def _bind_profile_backend(deps: dict[str, object], profile: str, backend: object | None) -> None:
    if backend is None:
        return
    if profile == "alfworld":
        deps.setdefault("alfworld_env", backend)
    elif profile == "coworker":
        deps.setdefault("coworker_environment", backend)


def _run_status(value: str) -> RunStatus:
    try:
        return RunStatus(value)
    except ValueError:
        return RunStatus.FAILED


def _stop_condition(request: RunRequest):
    condition = request.run_policy.stop_condition
    if condition is None:
        return None

    def stop(session, results):
        value = condition({"session": session, "tool_results": results})
        if inspect.isawaitable(value):
            value = asyncio.run(value)
        if value:
            return RuntimeStopDecision(status="completed")
        return None

    return stop


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


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
        enabled_tool_ids=request.enabled_tool_ids,
        permission_subject=request.permission_subject,
    )


__all__ = [
    "ApplicationRuntime",
    "CompactionResult",
    "ContextAssemblerFactory",
    "Deadline",
    "ProviderFactory",
    "SessionStatus",
    "ToolProfile",
]
