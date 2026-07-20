from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from homemaster.agent.messages import ToolCall
from homemaster.tools.catalog import CatalogOverrideAuthorization, ToolCatalog
from homemaster.tools.contracts import (
    ConcurrencyPolicy,
    ExecutionProof,
    PermissionSubject,
    RegisteredTool,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
    VerificationRecord,
    VerificationStatus,
)
from homemaster.tools.pipeline import PermissionDecision, ToolExecutionPipeline


def definition(**overrides) -> ToolDefinition:
    values = {
        "internal_id": "test.action.v1",
        "model_alias": "action",
        "description": "Run an action.",
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        "verification_policy": VerificationPolicy(
            execution_proof=ExecutionProof.EXTERNAL_STATE
        ),
        "provenance": ToolProvenance(source="test", reference="pipeline"),
        "version": "1.0.0",
        "concurrency_policy": ConcurrencyPolicy.RESOURCE_KEY,
        "resource_key": "device:typed",
        "state_effects": ("device.write",),
    }
    values.update(overrides)
    return ToolDefinition(**values)


class Executor:
    def __init__(self, order, *, result=None, error=None) -> None:
        self.order = order
        self.result = result or ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            data={"ok": True},
            evidence_refs=("execution/1",),
            backend_attempted=True,
        )
        self.error = error
        self.calls = 0

    async def execute(self, arguments, context):
        del arguments, context
        self.order.append("execute")
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


class Verifier:
    def __init__(self, order) -> None:
        self.order = order
        self.calls = 0

    async def verify(self, result, context):
        del result, context
        self.order.append("verify")
        self.calls += 1
        return VerificationRecord(
            status=VerificationStatus.PASSED,
            detail="backend matched",
            evidence_refs=("verification/1",),
        )


class Permission:
    def __init__(self, order, allowed=True) -> None:
        self.order = order
        self.allowed = allowed
        self.calls = 0

    async def evaluate(self, definition, arguments, context):
        del definition, arguments, context
        self.order.append("permission")
        self.calls += 1
        return PermissionDecision(allowed=self.allowed, reason="policy decision")


class Observation:
    def __init__(self, order, before=True) -> None:
        self.order = order
        self.before = before
        self.after_calls = 0

    async def before_action(self, definition, context):
        del definition, context
        self.order.append("pre_observation")
        return self.before

    async def after_action(self, definition, result, context):
        del definition, result, context
        self.order.append("post_observation_debt")
        self.after_calls += 1


class Resources:
    def __init__(self, order) -> None:
        self.order = order
        self.keys = []
        self.active = 0

    @asynccontextmanager
    async def acquire(self, key, context):
        del context
        self.order.append("lock")
        self.keys.append(key)
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1
            self.order.append("unlock")


class Ledger:
    def __init__(self, order) -> None:
        self.order = order
        self.results = []

    async def record_permission(self, tool_call, decision, context, attempt_index):
        del tool_call, decision, context, attempt_index
        self.order.append("permission_evidence")

    async def record_execution(self, tool_call, result, context, attempt_index):
        del tool_call, context, attempt_index
        self.order.append("authoritative_ledger")
        self.results.append(result)


class Events:
    def __init__(self, order) -> None:
        self.order = order

    async def publish(self, tool_call, result, context, attempt_index):
        del tool_call, result, context, attempt_index
        self.order.append("public_event")


class Terminal:
    def __init__(self, order, result=None) -> None:
        self.order = order
        self.result = result

    async def check(self, tool_call, context):
        del tool_call, context
        self.order.append("terminal")
        return self.result


class RecordingValidator:
    def __init__(self, delegate, order) -> None:
        self.delegate = delegate
        self.order = order

    def check_definition(self, value):
        return self.delegate.check_definition(value)

    def validate_input(self, value, arguments):
        self.order.append("input_validation")
        return self.delegate.validate_input(value, arguments)

    def validate_output(self, value, result):
        self.order.append("result_validation")
        return self.delegate.validate_output(value, result)


def build_pipeline(
    order,
    *,
    tool_definition=None,
    executor=None,
    verifier=None,
    permission=None,
    observation=None,
    terminal=None,
):
    tool_definition = tool_definition or definition()
    executor = executor or Executor(order)
    verifier = verifier or Verifier(order)
    catalog = ToolCatalog()
    catalog.register(
        RegisteredTool(
            definition=tool_definition,
            executor=executor,
            verifier=verifier,
        )
    )
    resources = Resources(order)
    ledger = Ledger(order)
    pipeline = ToolExecutionPipeline(
        catalog,
        permission_policy=permission or Permission(order),
        resource_manager=resources,
        observation_service=observation or Observation(order),
        authoritative_ledger=ledger,
        public_event_sink=Events(order),
        terminal_policy=terminal or Terminal(order),
    )
    pipeline._validator = RecordingValidator(pipeline._validator, order)  # noqa: SLF001
    view = catalog.freeze([tool_definition.internal_id])
    context = ToolExecutionContext(
        session_id="session",
        run_id="run",
        turn_index=0,
        tool_call_id="call-1",
        internal_tool_id=tool_definition.internal_id,
        tool_view=view,
        permission_subject=PermissionSubject(subject_id="user", channel="test"),
        backend=None,
        deadline=None,
        cancellation=None,
        observation=None,
        domain_observer=None,
    )
    return pipeline, context, executor, verifier, resources, ledger


@pytest.mark.asyncio
async def test_pipeline_runs_policy_stages_in_fixed_order() -> None:
    order = []
    pipeline, context, _executor, _verifier, resources, _ledger = build_pipeline(order)

    result = await pipeline.execute(
        ToolCall(id="call-1", name="action", arguments={"value": 1}),
        context,
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.verification.status is VerificationStatus.PASSED
    assert order == [
        "terminal",
        "input_validation",
        "permission",
        "pre_observation",
        "lock",
        "execute",
        "unlock",
        "result_validation",
        "verify",
        "post_observation_debt",
        "permission_evidence",
        "authoritative_ledger",
        "public_event",
    ]
    assert resources.keys == ["device:typed"]


@pytest.mark.asyncio
async def test_terminal_fence_short_circuits_all_later_stages() -> None:
    order = []
    terminal_result = ToolExecutionResult(
        status=ToolExecutionStatus.FAILURE,
        error=ToolExecutionError("terminal", "run already terminated"),
        backend_attempted=False,
    )
    pipeline, context, executor, verifier, resources, _ledger = build_pipeline(
        order,
        terminal=Terminal(order, terminal_result),
    )

    result = await pipeline.execute(
        ToolCall(id="call-1", name="action", arguments={"value": 1}),
        context,
    )

    assert result is terminal_result
    assert order == ["terminal"]
    assert executor.calls == 0
    assert verifier.calls == 0
    assert resources.keys == []


@pytest.mark.asyncio
async def test_invalid_input_short_circuits_permission_lock_and_executor() -> None:
    order = []
    permission = Permission(order)
    pipeline, context, executor, verifier, resources, _ledger = build_pipeline(
        order, permission=permission
    )

    result = await pipeline.execute(
        ToolCall(id="call-1", name="action", arguments={"value": "wrong"}),
        context,
    )

    assert result.status is ToolExecutionStatus.INVALID
    assert order == ["terminal", "input_validation"]
    assert permission.calls == 0
    assert executor.calls == 0
    assert verifier.calls == 0
    assert resources.active == 0


@pytest.mark.asyncio
async def test_pre_observation_failure_does_not_lock_or_execute() -> None:
    order = []
    observation = Observation(order, before=False)
    pipeline, context, executor, verifier, resources, ledger = build_pipeline(
        order,
        observation=observation,
    )

    result = await pipeline.execute(
        ToolCall(id="call-1", name="action", arguments={"value": 1}),
        context,
    )

    assert result.status is ToolExecutionStatus.OBSERVATION_REQUIRED
    assert executor.calls == 0
    assert verifier.calls == 0
    assert resources.keys == []
    assert ledger.results == [result]
    assert order[-2:] == ["authoritative_ledger", "public_event"]


@pytest.mark.asyncio
async def test_policy_none_does_not_call_optional_verifier() -> None:
    order = []
    no_proof = definition(verification_policy=VerificationPolicy())
    executor = Executor(order)
    verifier = Verifier(order)
    pipeline, context, _executor, _verifier, resources, _ledger = build_pipeline(
        order,
        tool_definition=no_proof,
        executor=executor,
        verifier=verifier,
    )

    result = await pipeline.execute(
        ToolCall(id="call-1", name="action", arguments={"value": 1}),
        context,
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert verifier.calls == 0
    assert resources.active == 0


@pytest.mark.asyncio
async def test_pending_verification_preserves_backend_attempt_and_receipt() -> None:
    order: list[str] = []

    class PendingVerifier:
        async def verify(self, result, context):
            del result, context
            return VerificationRecord(
                status=VerificationStatus.PENDING,
                detail="external evidence is delayed",
            )

    executor = Executor(
        order,
        result=ToolExecutionResult(
            status=ToolExecutionStatus.FAILURE,
            error=ToolExecutionError("backend_failed", "backend failed after attempt"),
            data={"receipt": "r1"},
            evidence_refs=("receipt/r1",),
            backend_attempted=True,
        ),
    )
    pipeline, context, _executor, _verifier, _resources, _ledger = build_pipeline(
        order,
        executor=executor,
        verifier=PendingVerifier(),
    )
    result = await pipeline.execute(
        ToolCall(id="call-1", name="action", arguments={"value": 1}),
        context,
    )
    assert result.status is ToolExecutionStatus.VERIFICATION_PENDING
    assert result.backend_attempted is True
    assert result.data["receipt"] == "r1"
    assert result.evidence_refs == ("receipt/r1",)


@pytest.mark.asyncio
async def test_frozen_view_keeps_original_executor_after_catalog_override() -> None:
    old_order: list[str] = []
    new_order: list[str] = []
    old_definition = definition(verification_policy=VerificationPolicy())
    old_tool = RegisteredTool(old_definition, Executor(old_order))
    catalog = ToolCatalog()
    catalog.register(old_tool)
    frozen_view = catalog.freeze([old_definition.internal_id])

    replacement_definition = replace(
        old_definition,
        description="Replacement executor.",
        provenance=ToolProvenance(source="test", reference="replacement"),
    )
    replacement = RegisteredTool(replacement_definition, Executor(new_order))
    catalog.register(
        replacement,
        override=CatalogOverrideAuthorization(
            internal_id=old_definition.internal_id,
            existing_snapshot_sha256=old_definition.snapshot_sha256,
            replacement_snapshot_sha256=replacement_definition.snapshot_sha256,
            existing_provenance=old_definition.provenance,
            replacement_provenance=replacement_definition.provenance,
            authorized_by="test-suite",
            reason="verify frozen run isolation",
        ),
    )
    pipeline = ToolExecutionPipeline(catalog)
    context = ToolExecutionContext(
        session_id="session",
        run_id="run",
        turn_index=0,
        tool_call_id="call-old",
        internal_tool_id=old_definition.internal_id,
        tool_view=frozen_view,
        permission_subject=PermissionSubject(subject_id="user", channel="test"),
        backend=None,
        deadline=None,
        cancellation=None,
        observation=None,
        domain_observer=None,
    )
    result = await pipeline.execute(
        ToolCall(id="call-old", name="action", arguments={"value": 1}),
        context,
    )
    assert result.status is ToolExecutionStatus.SUCCESS
    assert old_order == ["execute"]
    assert new_order == []


@pytest.mark.asyncio
async def test_resource_key_comes_from_definition_not_model_arguments() -> None:
    order = []
    pipeline, context, _executor, _verifier, resources, _ledger = build_pipeline(order)

    await pipeline.execute(
        ToolCall(
            id="call-1",
            name="action",
            arguments={"value": 1, "resource_key": "attacker:override"},
        ),
        context,
    )

    # additionalProperties rejects the forged field before a lease is acquired.
    assert resources.keys == []


@pytest.mark.asyncio
async def test_exception_releases_resource_and_mutating_outcome_is_unknown() -> None:
    order = []
    executor = Executor(order, error=RuntimeError("backend disconnected"))
    pipeline, context, _executor, verifier, resources, _ledger = build_pipeline(
        order,
        executor=executor,
    )

    result = await pipeline.execute(
        ToolCall(id="call-1", name="action", arguments={"value": 1}),
        context,
    )

    assert result.status is ToolExecutionStatus.OUTCOME_UNKNOWN
    assert result.outcome_certainty.value == "unknown"
    assert result.backend_attempted is True
    assert verifier.calls == 0
    assert resources.active == 0
    assert order.index("unlock") < order.index("post_observation_debt")


@pytest.mark.asyncio
async def test_query_engine_synthesizes_tool_result_when_parallel_tool_raises() -> None:
    order = []
    good_definition = definition(
        internal_id="test.good.v1",
        model_alias="good",
        verification_policy=VerificationPolicy(),
        concurrency_policy=ConcurrencyPolicy.PARALLEL,
        resource_key=None,
        state_effects=("none",),
    )
    bad_definition = definition(
        internal_id="test.bad.v1",
        model_alias="bad",
        verification_policy=VerificationPolicy(),
        concurrency_policy=ConcurrencyPolicy.PARALLEL,
        resource_key=None,
        state_effects=("none",),
    )
    catalog = ToolCatalog()
    catalog.register(RegisteredTool(good_definition, Executor(order)))
    catalog.register(
        RegisteredTool(bad_definition, Executor(order, error=RuntimeError("boom")))
    )
    view = catalog.freeze([good_definition.internal_id, bad_definition.internal_id])
    pipeline = ToolExecutionPipeline(catalog)

    def context(internal_id, call_id):
        return ToolExecutionContext(
            session_id="session",
            run_id="run",
            turn_index=0,
            tool_call_id=call_id,
            internal_tool_id=internal_id,
            tool_view=view,
            permission_subject=PermissionSubject(subject_id="user", channel="test"),
            backend=None,
            deadline=None,
            cancellation=None,
            observation=None,
            domain_observer=None,
        )

    calls = [
        (
            ToolCall(id="call-good", name="good", arguments={"value": 1}),
            context("test.good.v1", "call-good"),
        ),
        (
            ToolCall(id="call-bad", name="bad", arguments={"value": 1}),
            context("test.bad.v1", "call-bad"),
        ),
    ]

    results = await pipeline.execute_many(calls)

    assert [result.status for result in results] == [
        ToolExecutionStatus.SUCCESS,
        ToolExecutionStatus.FAILURE,
    ]
    assert [
        result.to_message(tool_call_id=call.id, name=call.name).tool_call_id
        for (call, _context), result in zip(calls, results, strict=True)
    ] == ["call-good", "call-bad"]


@pytest.mark.asyncio
async def test_query_engine_synthesizes_tool_result_when_single_tool_raises() -> None:
    order = []
    read_definition = definition(
        verification_policy=VerificationPolicy(),
        concurrency_policy=ConcurrencyPolicy.PARALLEL,
        resource_key=None,
        state_effects=("none",),
    )
    executor = Executor(order, error=RuntimeError("boom"))
    pipeline, context, _executor, _verifier, resources, _ledger = build_pipeline(
        order,
        tool_definition=read_definition,
        executor=executor,
        verifier=None,
    )

    result = await pipeline.execute(
        ToolCall(id="call-1", name="action", arguments={"value": 1}),
        context,
    )

    assert result.status is ToolExecutionStatus.FAILURE
    assert result.error is not None
    assert "RuntimeError: boom" in result.error.message
    assert resources.active == 0


def test_sync_bridge_reuses_async_pipeline_and_rejects_nested_loop() -> None:
    order = []
    pipeline, context, _executor, _verifier, _resources, _ledger = build_pipeline(order)
    result = pipeline.execute_sync(
        ToolCall(id="call-1", name="action", arguments={"value": 1}),
        context,
    )
    assert result.status is ToolExecutionStatus.SUCCESS


@pytest.mark.asyncio
async def test_sync_bridge_rejects_active_event_loop() -> None:
    order = []
    pipeline, context, _executor, _verifier, _resources, _ledger = build_pipeline(order)
    with pytest.raises(RuntimeError, match="active event loop"):
        pipeline.execute_sync(
            ToolCall(id="call-1", name="action", arguments={"value": 1}),
            context,
        )
