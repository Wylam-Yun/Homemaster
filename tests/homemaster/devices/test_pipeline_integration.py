from __future__ import annotations

import asyncio
from pathlib import Path

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
from homemaster.tools import FunctionTool, ToolExecutionContext, ToolRegistry, ToolResult
from homemaster.tools.contracts import PermissionSubject
from homemaster.tools.executor import ToolExecutor


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
        return ToolResult(
            "moved",
            metadata={"moved": True, "status": "success", "backend_attempted": True},
        )


def setup(backend: Backend):
    entered = asyncio.Event()
    release = asyncio.Event()
    executor = Executor(entered, release)
    registry = ToolRegistry()
    registry.register(FunctionTool(
        name="robot_move",
        description="Move the robot.",
        input_schema={"type": "object"},
        execute=executor.execute,
        concurrency_policy="resource_key",
        resource_key="home:backend",
    ))
    manager = DeviceLeaseManager()
    pipeline = ToolExecutor(registry, resource_manager=manager)

    def context(call_id: str) -> ToolExecutionContext:
        return ToolExecutionContext(
            Path.cwd(),
            metadata={
                "session_id": "session",
                "run_id": "run",
                "turn_index": 0,
                "tool_call_id": call_id,
                "internal_tool_id": "homemaster.robot_move.v1",
                "permission_subject": PermissionSubject(
                    subject_id="operator",
                    channel="gateway",
                    tenant_id="tenant",
                    capabilities=("device.control",),
                ),
                "backend": backend,
            },
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

    assert result.metadata["status"] == "denied"
    assert result.metadata["error_code"] == "stale_generation"
    assert result.metadata["backend_attempted"] is False
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
    assert result.metadata["status"] == "outcome_unknown"
    assert result.metadata["error_code"] == "device_generation_changed"
    assert result.metadata["backend_attempted"] is True
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
    assert waiting_result.metadata["status"] == "denied"
    assert waiting_result.metadata["error_code"] == "device_fenced"
    assert waiting_result.metadata["backend_attempted"] is False
    assert active_result.metadata["status"] == "outcome_unknown"
    assert active_result.metadata["backend_attempted"] is True
    assert executor.calls == 1
