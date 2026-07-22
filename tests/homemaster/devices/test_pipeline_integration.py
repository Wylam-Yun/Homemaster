from __future__ import annotations

import asyncio

import pytest

from homemaster.agent.messages import ToolCall
from homemaster.devices import (
    DeviceConnectionBinding,
    DeviceConnectionPool,
    DeviceControlReceipt,
    DeviceIdentity,
    DeviceLeaseManager,
    DeviceState,
    DeviceStateObservation,
)
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
from homemaster.tools.pipeline import ToolExecutionPipeline


class Backend:
    def __init__(self) -> None:
        self.device_identity = DeviceIdentity("tenant", "device", "backend")
        self.backend_id = self.device_identity.backend_id
        self.generation = 0
        self.observed_state = DeviceState.READY

    async def emergency_stop(self, *, reason: str, generation: int):
        del reason
        assert generation > self.generation
        self.observed_state = DeviceState.STOPPED
        return DeviceControlReceipt(succeeded=True, return_code="ok")

    async def read_device_state(self):
        return DeviceStateObservation(
            query_succeeded=True,
            state=self.observed_state,
            return_code="ok",
        )


class Executor:
    def __init__(self, entered: asyncio.Event, release: asyncio.Event) -> None:
        self.entered = entered
        self.release = release
        self.calls = 0

    async def execute(self, arguments, context):
        del arguments, context
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            data={"moved": True},
            backend_attempted=True,
        )


def setup(backend: Backend):
    entered = asyncio.Event()
    release = asyncio.Event()
    executor = Executor(entered, release)
    definition = ToolDefinition(
        internal_id="home.robot_move.v1",
        model_alias="robot_move",
        description="Move the robot.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        verification_policy=VerificationPolicy(),
        provenance=ToolProvenance(source="test", reference="device-pipeline"),
        version="1.0.0",
        concurrency_policy=ConcurrencyPolicy.RESOURCE_KEY,
        resource_key="home:backend",
        state_effects=("device.move",),
    )
    catalog = ToolCatalog()
    catalog.register(RegisteredTool(definition=definition, executor=executor))
    view = catalog.freeze((definition.internal_id,))
    manager = DeviceLeaseManager()
    pipeline = ToolExecutionPipeline(catalog, resource_manager=manager)

    def context(call_id: str) -> ToolExecutionContext:
        return ToolExecutionContext(
            session_id="session",
            run_id="run",
            turn_index=0,
            tool_call_id=call_id,
            internal_tool_id=definition.internal_id,
            tool_view=view,
            permission_subject=PermissionSubject(
                subject_id="operator",
                channel="gateway",
                tenant_id="tenant",
                capabilities=("device.control",),
            ),
            backend=backend,
            deadline=None,
            cancellation=None,
            observation=None,
            domain_observer=None,
        )

    return pipeline, manager, executor, entered, release, context


@pytest.mark.asyncio
async def test_stale_generation_is_denied_before_backend_execution() -> None:
    backend = Backend()
    pipeline, manager, executor, _entered, _release, context = setup(backend)
    await manager.fence(
        backend.device_identity,
        generation=1,
        state=DeviceState.DISCONNECTED,
        requested_by="connection-monitor",
        reason="transport closed",
    )

    result = await pipeline.execute(
        ToolCall(id="call-stale", name="robot_move", arguments={}),
        context("call-stale"),
    )

    assert result.status is ToolExecutionStatus.DENIED
    assert result.error is not None and result.error.code == "stale_generation"
    assert result.backend_attempted is False
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_emergency_stop_during_action_returns_outcome_unknown() -> None:
    backend = Backend()
    pipeline, manager, executor, entered, release, context = setup(backend)
    execution = asyncio.create_task(
        pipeline.execute(
            ToolCall(id="call-active", name="robot_move", arguments={}),
            context("call-active"),
        )
    )
    await entered.wait()

    stopped = await manager.emergency_stop(
        backend,
        permission_subject=context("call-stop").permission_subject,
        reason="operator requested",
    )
    release.set()
    result = await execution

    assert stopped.succeeded is True
    assert result.status is ToolExecutionStatus.OUTCOME_UNKNOWN
    assert result.error is not None and result.error.code == "device_generation_changed"
    assert result.backend_attempted is True
    assert executor.calls == 1


@pytest.mark.asyncio
async def test_disconnect_fences_active_and_waiting_pipeline_actions() -> None:
    backend = Backend()
    pipeline, manager, executor, entered, release, context = setup(backend)
    pool = DeviceConnectionPool(manager)
    pool.register(DeviceConnectionBinding(backend.device_identity, backend, generation=0))
    active = asyncio.create_task(
        pipeline.execute(
            ToolCall(id="call-active", name="robot_move", arguments={}),
            context("call-active"),
        )
    )
    await entered.wait()
    waiting = asyncio.create_task(
        pipeline.execute(
            ToolCall(id="call-waiting", name="robot_move", arguments={}),
            context("call-waiting"),
        )
    )
    while manager.waiting_count != 1:
        await asyncio.sleep(0)

    generation = await pool.disconnect(
        backend.device_identity,
        requested_by="connection-monitor",
        reason="transport closed",
    )
    waiting_result = await waiting
    release.set()
    active_result = await active

    assert generation == 1
    assert pool.state(backend.device_identity) is DeviceState.DISCONNECTED
    assert waiting_result.status is ToolExecutionStatus.DENIED
    assert waiting_result.error is not None
    assert waiting_result.error.code == "device_fenced"
    assert waiting_result.backend_attempted is False
    assert active_result.status is ToolExecutionStatus.OUTCOME_UNKNOWN
    assert active_result.backend_attempted is True
    assert executor.calls == 1
