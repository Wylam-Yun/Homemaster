from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from homemaster.agent.messages import ToolCall
from homemaster.tools.catalog import ToolCatalog
from homemaster.tools.contracts import (
    ConcurrencyPolicy,
    OutcomeCertainty,
    PermissionSubject,
    RegisteredTool,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
)
from homemaster.tools.pipeline import PermissionDecision, RetryPolicy, ToolExecutionPipeline


class Deadline:
    def __init__(self, values) -> None:
        self.values = iter(values)

    def remaining_s(self):
        return next(self.values)


class Cancellation:
    def __init__(self, cancelled=False) -> None:
        self.cancelled = cancelled


class Permission:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, definition, arguments, context):
        del definition, arguments, context
        self.calls += 1
        return PermissionDecision(allowed=True)


class Resource:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    @asynccontextmanager
    async def acquire(self, key, context):
        del key, context
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            yield
        finally:
            self.active -= 1


class Executor:
    def __init__(self, results=None, *, wait_s=0) -> None:
        self.results = list(results or [])
        self.wait_s = wait_s
        self.calls = 0

    async def execute(self, arguments, context):
        del arguments, context
        self.calls += 1
        if self.wait_s:
            await asyncio.sleep(self.wait_s)
        return self.results.pop(0)


class Ledger:
    def __init__(self) -> None:
        self.attempts = []

    async def record_execution(self, tool_call, result, context, attempt_index):
        del tool_call, context
        self.attempts.append((attempt_index, result.status))


class Events:
    def __init__(self) -> None:
        self.attempts = []

    async def publish(self, tool_call, result, context, attempt_index):
        del tool_call, context
        self.attempts.append((attempt_index, result.status))


def make(*, state_effects=("none",), deadline=None, cancellation=None, executor=None):
    definition = ToolDefinition(
        internal_id="test.retry.v1",
        model_alias="retry",
        description="Retry test.",
        input_schema={"type": "object"},
        output_schema={},
        verification_policy=VerificationPolicy(),
        provenance=ToolProvenance(source="test", reference="retry"),
        version="1.0.0",
        concurrency_policy=ConcurrencyPolicy.PARALLEL,
        state_effects=state_effects,
    )
    executor = executor or Executor()
    catalog = ToolCatalog()
    catalog.register(RegisteredTool(definition, executor))
    view = catalog.freeze([definition.internal_id])
    context = ToolExecutionContext(
        session_id="session",
        run_id="run",
        turn_index=0,
        tool_call_id="call-1",
        internal_tool_id=definition.internal_id,
        tool_view=view,
        permission_subject=PermissionSubject(subject_id="user", channel="test"),
        backend=None,
        deadline=deadline,
        cancellation=cancellation,
        observation=None,
        domain_observer=None,
    )
    ledger = Ledger()
    events = Events()
    resource = Resource()
    pipeline = ToolExecutionPipeline(
        catalog,
        permission_policy=Permission(),
        resource_manager=resource,
        authoritative_ledger=ledger,
        public_event_sink=events,
        retry_policy=RetryPolicy(
            max_attempts=3,
            retryable_internal_ids=frozenset({definition.internal_id}),
        ),
    )
    return pipeline, context, executor, ledger, events, resource


def failure(*, retryable=True):
    return ToolExecutionResult(
        status=ToolExecutionStatus.FAILURE,
        error=ToolExecutionError("temporary", "temporary failure"),
        retryable=retryable,
        backend_attempted=True,
    )


@pytest.mark.asyncio
async def test_retryable_read_failure_records_each_attempt_and_event() -> None:
    executor = Executor(
        [
            failure(),
            ToolExecutionResult(
                status=ToolExecutionStatus.SUCCESS,
                data={"ok": True},
                backend_attempted=True,
            ),
        ]
    )
    pipeline, context, executor, ledger, events, _resource = make(executor=executor)

    result = await pipeline.execute(ToolCall(id="call-1", name="retry", arguments={}), context)

    assert result.status is ToolExecutionStatus.SUCCESS
    assert executor.calls == 2
    assert ledger.attempts == [
        (1, ToolExecutionStatus.FAILURE),
        (2, ToolExecutionStatus.SUCCESS),
    ]
    assert events.attempts == ledger.attempts


@pytest.mark.asyncio
async def test_mutating_outcome_unknown_is_never_automatically_retried() -> None:
    unknown = ToolExecutionResult(
        status=ToolExecutionStatus.OUTCOME_UNKNOWN,
        error=ToolExecutionError("transport_lost", "backend outcome unknown"),
        outcome_certainty=OutcomeCertainty.UNKNOWN,
        backend_attempted=True,
    )
    executor = Executor([unknown, unknown])
    pipeline, context, executor, ledger, _events, _resource = make(
        state_effects=("device.write",),
        executor=executor,
    )

    result = await pipeline.execute(ToolCall(id="call-1", name="retry", arguments={}), context)

    assert result.status is ToolExecutionStatus.OUTCOME_UNKNOWN
    assert executor.calls == 1
    assert ledger.attempts == [(1, ToolExecutionStatus.OUTCOME_UNKNOWN)]


@pytest.mark.asyncio
async def test_retry_stops_when_total_deadline_is_exhausted() -> None:
    executor = Executor([failure(), failure()])
    pipeline, context, executor, ledger, _events, _resource = make(
        deadline=Deadline([10.0, 10.0, 10.0, 0.0]),
        executor=executor,
    )

    result = await pipeline.execute(ToolCall(id="call-1", name="retry", arguments={}), context)

    assert result.status is ToolExecutionStatus.FAILURE
    assert executor.calls == 1
    assert ledger.attempts == [(1, ToolExecutionStatus.FAILURE)]


@pytest.mark.asyncio
async def test_cancel_before_observation_never_acquires_or_executes() -> None:
    executor = Executor([failure()])
    cancellation = Cancellation(cancelled=True)
    pipeline, context, executor, _ledger, _events, resource = make(
        cancellation=cancellation,
        executor=executor,
    )

    result = await pipeline.execute(ToolCall(id="call-1", name="retry", arguments={}), context)

    assert result.status is ToolExecutionStatus.CANCELLED
    assert executor.calls == 0
    assert resource.active == 0


@pytest.mark.asyncio
async def test_mutating_timeout_returns_unknown_and_releases_resource() -> None:
    executor = Executor(
        [
            ToolExecutionResult(
                status=ToolExecutionStatus.SUCCESS,
                data={"ok": True},
                backend_attempted=True,
            )
        ],
        wait_s=0.05,
    )
    pipeline, context, executor, ledger, _events, resource = make(
        state_effects=("device.write",),
        deadline=Deadline([10.0, 10.0, 0.001, 0.0]),
        executor=executor,
    )

    result = await pipeline.execute(ToolCall(id="call-1", name="retry", arguments={}), context)

    assert result.status is ToolExecutionStatus.OUTCOME_UNKNOWN
    assert result.outcome_certainty.value == "unknown"
    assert executor.calls == 1
    assert resource.active == 0
    assert ledger.attempts[0][0] == 1
