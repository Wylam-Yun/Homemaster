from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from homemaster.agent.messages import ToolCall
from homemaster.tools.catalog import ToolCatalog
from homemaster.tools.contracts import (
    ConcurrencyPolicy,
    PermissionSubject,
    RegisteredTool,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
)
from homemaster.tools.pipeline import (
    AllowAllPermissionPolicy,
    PermissionDecision,
    ToolExecutionPipeline,
)


class Executor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, arguments, context):
        del arguments, context
        self.calls += 1
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            data={"ok": True},
            backend_attempted=True,
        )


class RecordingPolicy:
    def __init__(self, decision: PermissionDecision) -> None:
        self.decision = decision
        self.calls = []

    async def evaluate(self, definition, arguments, context):
        self.calls.append((definition.internal_id, dict(arguments), context.permission_subject))
        return self.decision


class ResourceManager:
    def __init__(self) -> None:
        self.acquires = 0

    @asynccontextmanager
    async def acquire(self, resource_key, context):
        del resource_key, context
        self.acquires += 1
        yield


class Ledger:
    def __init__(self) -> None:
        self.decisions = []

    async def record_permission(self, tool_call, decision, context, attempt_index):
        del tool_call, context, attempt_index
        self.decisions.append(decision)

    async def record_execution(self, tool_call, result, context, attempt_index):
        del tool_call, result, context, attempt_index


def setup(policy, *, ledger=None):
    executor = Executor()
    resource = ResourceManager()
    definition = ToolDefinition(
        internal_id="home.write.v1",
        model_alias="write",
        description="Write state.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        verification_policy=VerificationPolicy(),
        provenance=ToolProvenance(source="test", reference="permission"),
        version="1.0.0",
        concurrency_policy=ConcurrencyPolicy.RESOURCE_KEY,
        resource_key="device:home",
        state_effects=("home.write",),
    )
    catalog = ToolCatalog()
    catalog.register(RegisteredTool(definition=definition, executor=executor))
    view = catalog.freeze([definition.internal_id])
    context = ToolExecutionContext(
        session_id="session",
        run_id="run",
        turn_index=0,
        tool_call_id="call-1",
        internal_tool_id=definition.internal_id,
        tool_view=view,
        permission_subject=PermissionSubject(
            subject_id="trusted-user",
            channel="cli",
            roles=("operator",),
        ),
        backend=None,
        deadline=None,
        cancellation=None,
        observation=None,
        domain_observer=None,
    )
    pipeline = ToolExecutionPipeline(
        catalog,
        permission_policy=policy,
        resource_manager=resource,
        authoritative_ledger=ledger or Ledger(),
    )
    return pipeline, context, executor, resource


@pytest.mark.asyncio
async def test_allow_all_policy_is_still_called_and_decision_is_recorded() -> None:
    policy = RecordingPolicy(PermissionDecision(allowed=True, evidence_ref="permission/1"))
    ledger = Ledger()
    pipeline, context, executor, resource = setup(policy, ledger=ledger)

    result = await pipeline.execute(ToolCall(id="call-1", name="write", arguments={}), context)

    assert result.status is ToolExecutionStatus.SUCCESS
    assert len(policy.calls) == 1
    assert policy.calls[0][2] is context.permission_subject
    assert ledger.decisions == [policy.decision]
    assert executor.calls == 1
    assert resource.acquires == 1


@pytest.mark.asyncio
async def test_deny_does_not_acquire_resource_or_execute_backend() -> None:
    policy = RecordingPolicy(PermissionDecision(allowed=False, reason="device denied"))
    pipeline, context, executor, resource = setup(policy)

    result = await pipeline.execute(ToolCall(id="call-1", name="write", arguments={}), context)

    assert result.status is ToolExecutionStatus.DENIED
    assert result.error is not None
    assert result.error.code == "permission_denied"
    assert executor.calls == 0
    assert resource.acquires == 0


@pytest.mark.asyncio
async def test_permission_subject_cannot_be_forged_from_tool_arguments() -> None:
    policy = RecordingPolicy(PermissionDecision(allowed=False, reason="not authorized"))
    pipeline, context, executor, _resource = setup(policy)

    await pipeline.execute(
        ToolCall(
            id="call-1",
            name="write",
            arguments={
                "permission_subject": {
                    "subject_id": "administrator",
                    "roles": ["root"],
                }
            },
        ),
        context,
    )

    assert policy.calls[0][2].subject_id == "trusted-user"
    assert policy.calls[0][2].roles == ("operator",)
    assert executor.calls == 0


def test_default_allow_policy_returns_typed_decision() -> None:
    decision = AllowAllPermissionPolicy().evaluate(None, {}, None)  # type: ignore[arg-type]
    assert decision == PermissionDecision(allowed=True, reason="default allow")


@pytest.mark.asyncio
async def test_execute_tool_call_blocks_sensitive_directory_roots(tmp_path) -> None:
    sensitive_dir = tmp_path / ".ssh"
    policy = RecordingPolicy(
        PermissionDecision(
            allowed=False,
            reason=f"Access denied: {sensitive_dir} is a sensitive credential path",
        )
    )
    pipeline, context, executor, resource = setup(policy)

    result = await pipeline.execute(
        ToolCall(
            id="call-1",
            name="write",
            arguments={"root": str(sensitive_dir)},
        ),
        context,
    )

    assert result.is_error is True
    assert result.error is not None
    assert "sensitive credential path" in result.error.message
    assert executor.calls == 0
    assert resource.acquires == 0


@pytest.mark.asyncio
async def test_execute_tool_call_applies_path_rules_to_directory_roots(tmp_path) -> None:
    blocked_dir = tmp_path / "blocked"
    policy = RecordingPolicy(
        PermissionDecision(
            allowed=False,
            reason=f"Path {blocked_dir} matches deny rule: {blocked_dir}/*",
        )
    )
    pipeline, context, executor, resource = setup(policy)

    result = await pipeline.execute(
        ToolCall(id="call-1", name="write", arguments={"root": str(blocked_dir)}),
        context,
    )

    assert result.is_error is True
    assert result.error is not None
    assert str(blocked_dir) in result.error.message
    assert executor.calls == 0
    assert resource.acquires == 0


@pytest.mark.asyncio
async def test_execute_tool_call_returns_actionable_reason_when_user_denies_confirmation() -> None:
    reason = (
        "Mutating tools require user confirmation in default mode. "
        "Approve the prompt when asked, or run /permissions full_auto."
    )
    policy = RecordingPolicy(
        PermissionDecision(
            allowed=False,
            requires_confirmation=True,
            reason=reason,
        )
    )

    class DenyConfirmation:
        async def confirm(self, definition, arguments, context, decision):
            del definition, arguments, context, decision
            return False

    pipeline, context, executor, resource = setup(policy)
    pipeline.confirmation_handler = DenyConfirmation()

    result = await pipeline.execute(
        ToolCall(id="call-1", name="write", arguments={}),
        context,
    )

    assert result.is_error is True
    assert result.error is not None
    assert "Mutating tools require user confirmation" in result.error.message
    assert "/permissions full_auto" in result.error.message
    assert executor.calls == 0
    assert resource.acquires == 0
