"""Black-box regressions for path permissions and resource transactions."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from homemaster.agent.messages import ToolCall
from homemaster.permissions import PermissionChecker, PermissionMode, PermissionSettingsConfig
from homemaster.tools import FunctionTool, ToolExecutionContext, ToolRegistry, ToolResult
from homemaster.tools.executor import ToolExecutor
from homemaster.tools.paths import path_resource_key, resolve_context_tool_path


def test_relative_path_permission_and_execution_stay_anchored_after_cwd_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    working_directory = tmp_path / "locked-workspace"
    working_directory.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    context = ToolExecutionContext(working_directory)
    monkeypatch.chdir(elsewhere)

    resolved = resolve_context_tool_path(context, "blocked/sentinel.txt")
    assert resolved == working_directory / "blocked" / "sentinel.txt"

    checker = PermissionChecker(
        PermissionSettingsConfig(
            mode=PermissionMode.FULL_AUTO,
            path_rules=({"pattern": f"{working_directory}/blocked/*", "allow": False},),
        )
    )
    decision = checker.evaluate_tool(
        tool_name="write_file",
        is_read_only=False,
        required_capabilities=(),
        arguments={"path": "blocked/sentinel.txt"},
        context=context,
    )

    assert decision.allowed is False
    assert "deny rule" in decision.reason


@pytest.mark.asyncio
async def test_path_lease_covers_executor_and_independent_verifier(tmp_path: Path) -> None:
    working_directory = tmp_path / "workspace"
    working_directory.mkdir()
    entered = asyncio.Event()
    release = asyncio.Event()
    paths: list[str] = []
    verifier_calls = 0

    async def execute(arguments, context):
        nonlocal verifier_calls
        del context
        paths.append(str(arguments["path"]))
        verifier_calls += 1
        if verifier_calls == 1:
            entered.set()
            await release.wait()
        return ToolResult("verified", metadata={"status": "success"})

    class Resources:
        def __init__(self) -> None:
            self._lock = asyncio.Lock()
            self.keys: list[str] = []
            self.active = 0

        @asynccontextmanager
        async def acquire(self, resource_key, context):
            del context
            async with self._lock:
                self.keys.append(resource_key)
                self.active += 1
                try:
                    yield
                finally:
                    self.active -= 1

    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            name="write_file",
            description="Write a UTF-8 file.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
            execute=execute,
            concurrency_policy="resource_key",
            resource_key="filesystem:placeholder",
            resource_key_resolver=path_resource_key,
        )
    )
    resources = Resources()
    executor = ToolExecutor(registry, resource_manager=resources)

    def context(call_id: str) -> ToolExecutionContext:
        return ToolExecutionContext(working_directory, metadata={"tool_call_id": call_id})

    first = asyncio.create_task(
        executor.execute(
            ToolCall(id="call-1", name="write_file", arguments={"path": "target.txt"}),
            context("call-1"),
        )
    )
    await entered.wait()
    second = asyncio.create_task(
        executor.execute(
            ToolCall(id="call-2", name="write_file", arguments={"path": "target.txt"}),
            context("call-2"),
        )
    )
    await asyncio.sleep(0)

    assert paths == ["target.txt"]
    assert resources.active == 1
    release.set()
    first_result, second_result = await asyncio.gather(first, second)

    expected_key = f"filesystem:{(working_directory / 'target.txt').as_posix()}"
    assert first_result.is_error is False
    assert second_result.is_error is False
    assert verifier_calls == 2
    assert resources.keys == [expected_key, expected_key]
    assert paths == ["target.txt", "target.txt"]
    assert resources.active == 0
