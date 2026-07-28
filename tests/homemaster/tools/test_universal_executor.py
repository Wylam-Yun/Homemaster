import asyncio
import json
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from homemaster.agent.messages import ToolCall
from homemaster.permissions import PermissionChecker
from homemaster.permissions.config import PermissionMode, PermissionSettingsConfig
from homemaster.tools import FunctionTool, ToolExecutionContext, ToolRegistry, ToolResult
from homemaster.tools.contracts import PermissionSubject
from homemaster.tools.executor import ToolExecutor


def _registry(*, read_only: bool = False) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            name="echo",
            description="Echo exact input.",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            execute=lambda arguments, context: ToolResult(arguments["value"]),
            read_only=read_only,
        )
    )
    return registry


@pytest.mark.asyncio
async def test_executor_resolves_only_ordinary_names_and_preserves_output(tmp_path: Path) -> None:
    executor = ToolExecutor(_registry())
    context = ToolExecutionContext(tmp_path)

    result = await executor.execute(
        ToolCall(id="1", name="echo", arguments={"value": "/host/path?token=exact"}),
        context,
    )
    stable = await executor.execute(
        ToolCall(id="2", name="homemaster.echo.v1", arguments={"value": "x"}),
        context,
    )

    assert result == ToolResult("/host/path?token=exact")
    assert stable.is_error and stable.metadata["status"] == "unknown_tool"


@pytest.mark.asyncio
async def test_permission_checker_uses_ordinary_names(tmp_path: Path) -> None:
    settings = PermissionSettingsConfig(
        mode=PermissionMode.FULL_AUTO,
        denied_tools=("echo",),
    )
    executor = ToolExecutor(_registry(), permission_checker=PermissionChecker(settings))

    result = await executor.execute(
        ToolCall(id="1", name="echo", arguments={"value": "blocked"}),
        ToolExecutionContext(tmp_path),
    )

    assert result.is_error
    assert result.output == "echo is explicitly denied"


@pytest.mark.asyncio
async def test_permission_checker_preserves_principal_capability_authorization(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            name="run_process",
            description="Run one process.",
            input_schema={"type": "object", "additionalProperties": False},
            execute=lambda arguments, context: ToolResult("ran"),
            required_capabilities=("process.exec",),
        )
    )
    executor = ToolExecutor(
        registry,
        permission_checker=PermissionChecker(
            PermissionSettingsConfig(mode=PermissionMode.FULL_AUTO)
        ),
    )

    denied = await executor.execute(
        ToolCall(id="1", name="run_process", arguments={}),
        ToolExecutionContext(
            tmp_path,
            metadata={
                "permission_subject": PermissionSubject(
                    "reader",
                    "test",
                    capabilities=("tool.read",),
                )
            },
        ),
    )
    allowed = await executor.execute(
        ToolCall(id="2", name="run_process", arguments={}),
        ToolExecutionContext(
            tmp_path,
            metadata={
                "permission_subject": PermissionSubject(
                    "operator",
                    "test",
                    capabilities=("process.exec",),
                )
            },
        ),
    )

    assert denied.is_error is True
    assert denied.output == "principal lacks required capability: process.exec"
    assert allowed == ToolResult("ran")


@pytest.mark.asyncio
async def test_session_plan_mode_blocks_mutation_but_allows_exit(tmp_path: Path) -> None:
    class PlanMode:
        def enabled(self, session_id: str) -> bool:
            return session_id == "session-1"

    registry = ToolRegistry()
    for name in ("write_note", "exit_plan_mode"):
        registry.register(
            FunctionTool(
                name=name,
                description=name,
                input_schema={"type": "object", "additionalProperties": False},
                execute=lambda arguments, context, value=name: ToolResult(value),
            )
        )
    executor = ToolExecutor(
        registry,
        permission_checker=PermissionChecker(
            PermissionSettingsConfig(mode=PermissionMode.FULL_AUTO)
        ),
    )
    context = ToolExecutionContext(
        tmp_path,
        metadata={"services": {"plan_mode": PlanMode()}, "session_id": "session-1"},
    )

    blocked = await executor.execute(ToolCall(id="1", name="write_note", arguments={}), context)
    exited = await executor.execute(ToolCall(id="2", name="exit_plan_mode", arguments={}), context)

    assert blocked.is_error is True
    assert blocked.output == "plan mode blocks mutating tools"
    assert exited == ToolResult("exit_plan_mode")


@pytest.mark.asyncio
async def test_default_mode_honors_tool_auto_capability(tmp_path: Path) -> None:
    executor = ToolExecutor(
        _registry(),
        permission_checker=PermissionChecker(PermissionSettingsConfig(mode=PermissionMode.DEFAULT)),
    )

    result = await executor.execute(
        ToolCall(id="1", name="echo", arguments={"value": "allowed"}),
        ToolExecutionContext(
            tmp_path,
            metadata={
                "permission_subject": PermissionSubject(
                    "operator",
                    "test",
                    capabilities=("tool.auto",),
                )
            },
        ),
    )

    assert result == ToolResult("allowed")


@pytest.mark.asyncio
async def test_executor_serializes_resource_key_calls(tmp_path: Path) -> None:
    active: set[str] = set()
    overlaps: list[str] = []
    order: list[str] = []

    class ResourceManager:
        @asynccontextmanager
        async def acquire(self, resource_key: str, context: ToolExecutionContext):
            call_id = str(context.metadata["tool_call_id"])
            if resource_key in active:
                overlaps.append(resource_key)
            active.add(resource_key)
            order.append(f"enter:{call_id}")
            try:
                yield
            finally:
                order.append(f"exit:{call_id}")
                active.remove(resource_key)

    async def execute(arguments, context):
        del arguments, context
        await asyncio.sleep(0.01)
        return ToolResult("ok")

    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            name="write_note",
            description="Write a note.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            execute=execute,
            concurrency_policy="resource_key",
            resource_key_resolver=lambda arguments, context: str(arguments["path"]),
        )
    )
    executor = ToolExecutor(registry, resource_manager=ResourceManager())

    results = await executor.execute_many(
        [
            (
                ToolCall(id="1", name="write_note", arguments={"path": "same.txt"}),
                ToolExecutionContext(tmp_path, metadata={"tool_call_id": "1"}),
            ),
            (
                ToolCall(id="2", name="write_note", arguments={"path": "same.txt"}),
                ToolExecutionContext(tmp_path, metadata={"tool_call_id": "2"}),
            ),
        ]
    )

    assert [result.output for result in results] == ["ok", "ok"]
    assert overlaps == []
    assert order == ["enter:1", "exit:1", "enter:2", "exit:2"]


@pytest.mark.asyncio
async def test_denial_and_invalid_input_never_acquire_or_execute(tmp_path: Path) -> None:
    calls = 0
    acquires = 0

    class ResourceManager:
        @asynccontextmanager
        async def acquire(self, resource_key: str, context: ToolExecutionContext):
            nonlocal acquires
            del resource_key, context
            acquires += 1
            yield

    async def run(arguments, context):
        nonlocal calls
        del arguments, context
        calls += 1
        return ToolResult("ran")

    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            name="write_note",
            description="Write a note.",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            execute=run,
            required_capabilities=("notes.write",),
            concurrency_policy="resource_key",
            resource_key="notes",
        )
    )
    executor = ToolExecutor(
        registry,
        permission_checker=PermissionChecker(
            PermissionSettingsConfig(mode=PermissionMode.FULL_AUTO)
        ),
        resource_manager=ResourceManager(),
    )
    context = ToolExecutionContext(
        tmp_path,
        metadata={"permission_subject": PermissionSubject("reader", "test", capabilities=())},
    )

    invalid = await executor.execute(ToolCall(id="1", name="write_note", arguments={}), context)
    denied = await executor.execute(
        ToolCall(id="2", name="write_note", arguments={"value": "x"}), context
    )

    assert invalid.metadata["status"] == "invalid_tool_arguments"
    assert invalid.metadata == {
        "status": "invalid_tool_arguments",
        "error_code": "invalid_tool_arguments",
        "tool": "write_note",
        "backend_attempted": False,
        "received_argument_keys": [],
        "missing_required_arguments": ["value"],
        "issues": [
            {
                "location": [],
                "message": "'value' is a required property",
                "type": "value_error",
            }
        ],
    }
    assert json.loads(invalid.output) == invalid.metadata
    assert denied.metadata["status"] == "permission_denied"
    assert calls == 0
    assert acquires == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [True, False])
async def test_expired_deadline_never_attempts_backend(
    tmp_path: Path,
    read_only: bool,
) -> None:
    class Deadline:
        def remaining_s(self) -> float:
            return 0.0

    registry = _registry(read_only=read_only)
    result = await ToolExecutor(registry).execute(
        ToolCall(id="1", name="echo", arguments={"value": "x"}),
        ToolExecutionContext(tmp_path, metadata={"deadline": Deadline()}),
    )

    assert result.is_error is True
    assert result.metadata == {"status": "deadline_exceeded", "backend_attempted": False}


@pytest.mark.asyncio
async def test_mutating_deadline_after_execution_starts_is_outcome_unknown(
    tmp_path: Path,
) -> None:
    class Deadline:
        def __init__(self) -> None:
            self.expires_at = time.monotonic() + 0.02

        def remaining_s(self) -> float:
            return max(0.0, self.expires_at - time.monotonic())

    started = asyncio.Event()

    async def mutate(arguments, context):
        del arguments, context
        started.set()
        await asyncio.sleep(1)
        return ToolResult("late")

    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            name="write_note",
            description="Write a note.",
            input_schema={"type": "object", "additionalProperties": False},
            execute=mutate,
        )
    )
    result = await ToolExecutor(registry).execute(
        ToolCall(id="1", name="write_note", arguments={}),
        ToolExecutionContext(tmp_path, metadata={"deadline": Deadline()}),
    )

    assert started.is_set()
    assert result.is_error is True
    assert result.metadata == {"status": "outcome_unknown", "backend_attempted": True}


@pytest.mark.asyncio
async def test_deadline_bounds_contended_resource_lease_before_execution(tmp_path: Path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    executed = False

    class Deadline:
        def __init__(self) -> None:
            self.expires_at = time.monotonic() + 0.02

        def remaining_s(self) -> float:
            return max(0.0, self.expires_at - time.monotonic())

    class ResourceManager:
        @asynccontextmanager
        async def acquire(self, resource_key: str, context: ToolExecutionContext):
            del resource_key, context
            entered.set()
            await release.wait()
            yield

    async def mutate(arguments, context):
        nonlocal executed
        del arguments, context
        executed = True
        return ToolResult("ran")

    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            name="write_note",
            description="Write a note.",
            input_schema={"type": "object", "additionalProperties": False},
            execute=mutate,
            concurrency_policy="resource_key",
            resource_key="notes",
        )
    )
    result = await asyncio.wait_for(
        ToolExecutor(registry, resource_manager=ResourceManager()).execute(
            ToolCall(id="1", name="write_note", arguments={}),
            ToolExecutionContext(tmp_path, metadata={"deadline": Deadline()}),
        ),
        timeout=0.2,
    )

    assert entered.is_set()
    assert executed is False
    assert result.is_error is True
    assert result.metadata == {"status": "deadline_exceeded", "backend_attempted": False}


@pytest.mark.asyncio
async def test_mutating_cancellation_reports_unknown_while_thread_reaches_external_state(
    tmp_path: Path,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    terminal = tmp_path / "cancelled-thread-terminal.txt"

    def blocking_mutation() -> ToolResult:
        entered.set()
        release.wait(2)
        terminal.write_text("mutation-finished", encoding="utf-8")
        return ToolResult("finished")

    async def mutate(arguments, context):
        del arguments, context
        return await asyncio.to_thread(blocking_mutation)

    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            name="write_note",
            description="Write a note.",
            input_schema={"type": "object", "additionalProperties": False},
            execute=mutate,
        )
    )
    task = asyncio.create_task(
        ToolExecutor(registry).execute(
            ToolCall(id="1", name="write_note", arguments={}),
            ToolExecutionContext(tmp_path),
        )
    )
    assert await asyncio.to_thread(entered.wait, 1)

    task.cancel()
    result = await task

    assert result.is_error is True
    assert result.metadata == {
        "status": "outcome_unknown",
        "error_code": "execution_cancelled",
        "backend_attempted": True,
    }
    assert not terminal.exists()
    release.set()
    for _ in range(100):
        if terminal.exists():
            break
        await asyncio.sleep(0.01)
    assert terminal.read_text(encoding="utf-8") == "mutation-finished"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("read_only", "expected_status"),
    [(True, "tool_error"), (False, "outcome_unknown")],
)
async def test_executor_exception_preserves_mutating_uncertainty(
    tmp_path: Path,
    read_only: bool,
    expected_status: str,
) -> None:
    async def fail(arguments, context):
        del arguments, context
        raise RuntimeError("backend disconnected")

    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            name="read_or_write",
            description="Read or write.",
            input_schema={"type": "object", "additionalProperties": False},
            execute=fail,
            read_only=read_only,
        )
    )

    result = await ToolExecutor(registry).execute(
        ToolCall(id="1", name="read_or_write", arguments={}),
        ToolExecutionContext(tmp_path),
    )

    assert result.is_error is True
    assert result.metadata["status"] == expected_status
    assert result.metadata["exception_type"] == "RuntimeError"
    assert result.metadata["backend_attempted"] is True


def test_legacy_permission_ids_migrate_and_conflicts_fail() -> None:
    with pytest.warns(FutureWarning, match="openharness.echo.v1"):
        settings = PermissionSettingsConfig(denied_tools=("openharness.echo.v1",))
    assert settings.denied_tools == ("echo",)

    with pytest.raises(ValueError, match="both allowed and denied"):
        PermissionSettingsConfig(
            allowed_tools=("home.robot_go_to.v1",),
            denied_tools=("alfworld.robot_go_to.v1",),
        )
