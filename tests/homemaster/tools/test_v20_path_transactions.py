"""Black-box regressions for the V2.0 file-tool transaction foundation."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from homemaster.agent.messages import ToolCall
from homemaster.permissions import HomePermissionPolicy, PermissionSettingsConfig
from homemaster.tools.catalog import ToolCatalog
from homemaster.tools.contracts import (
    ConcurrencyPolicy,
    ExecutionProof,
    PermissionSubject,
    RegisteredTool,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
    VerificationRecord,
    VerificationStatus,
)
from homemaster.tools.paths import path_resource_key, resolve_context_tool_path
from homemaster.tools.pipeline import ToolExecutionPipeline


class _Executor:
    def __init__(self) -> None:
        self.paths: list[str] = []

    async def execute(self, arguments, context):
        del context
        self.paths.append(arguments["path"])
        return ToolExecutionResult(status=ToolExecutionStatus.SUCCESS, backend_attempted=True)


class _BlockingVerifier:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def verify(self, result, context):
        del result, context
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        return VerificationRecord(
            status=VerificationStatus.PASSED,
            evidence_refs=(f"verification/{self.calls}",),
        )


class _Resources:
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


def _definition(*, mutating: bool = True) -> ToolDefinition:
    return ToolDefinition(
        internal_id="openharness.write_file.v1",
        model_alias="write_file",
        description="Write a UTF-8 file.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        verification_policy=VerificationPolicy(
            execution_proof=ExecutionProof.EXTERNAL_STATE if mutating else ExecutionProof.NONE
        ),
        provenance=ToolProvenance(source="test", reference="v20-path-transaction"),
        version="2.0.0",
        concurrency_policy=(
            ConcurrencyPolicy.RESOURCE_KEY if mutating else ConcurrencyPolicy.PARALLEL
        ),
        resource_key="filesystem:placeholder" if mutating else None,
        state_effects=("filesystem.write",) if mutating else (),
    )


def _context(
    catalog: ToolCatalog,
    working_directory: Path,
    *,
    call_id: str,
) -> ToolExecutionContext:
    definition = catalog.get("openharness.write_file.v1").definition
    return ToolExecutionContext(
        session_id="session",
        run_id="run",
        turn_index=0,
        tool_call_id=call_id,
        internal_tool_id=definition.internal_id,
        tool_view=catalog.freeze((definition.internal_id,)),
        permission_subject=PermissionSubject(
            subject_id="operator",
            channel="test",
            capabilities=("tool.read", "tool.mutate", "tool.auto"),
        ),
        backend=None,
        deadline=None,
        cancellation=None,
        domain_observer=None,
        working_directory=working_directory,
    )


def test_relative_path_permission_and_execution_stay_anchored_after_cwd_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    working_directory = tmp_path / "locked-workspace"
    working_directory.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    catalog = ToolCatalog()
    catalog.register(RegisteredTool(_definition(mutating=False), _Executor()))
    context = _context(catalog, working_directory, call_id="call")
    monkeypatch.chdir(elsewhere)

    resolved = resolve_context_tool_path(context, "blocked/sentinel.txt")
    assert resolved == working_directory / "blocked" / "sentinel.txt"

    policy = HomePermissionPolicy(
        PermissionSettingsConfig(
            path_rules=(
                {"pattern": f"{working_directory}/blocked/*", "allow": False},
            )
        )
    )
    decision = policy.evaluate(
        _definition(mutating=False),
        {"path": "blocked/sentinel.txt"},
        context,
    )

    assert decision.allowed is False
    assert "deny rule" in decision.reason


@pytest.mark.asyncio
async def test_path_lease_covers_executor_and_independent_verifier(tmp_path: Path) -> None:
    working_directory = tmp_path / "workspace"
    working_directory.mkdir()
    catalog = ToolCatalog()
    executor = _Executor()
    verifier = _BlockingVerifier()
    catalog.register(
        RegisteredTool(
            _definition(),
            executor,
            verifier,
            resource_key_resolver=path_resource_key,
        )
    )
    resources = _Resources()
    pipeline = ToolExecutionPipeline(catalog, resource_manager=resources)

    first = asyncio.create_task(
        pipeline.execute(
            ToolCall(id="call-1", name="write_file", arguments={"path": "target.txt"}),
            _context(catalog, working_directory, call_id="call-1"),
        )
    )
    await verifier.entered.wait()
    second = asyncio.create_task(
        pipeline.execute(
            ToolCall(id="call-2", name="write_file", arguments={"path": "target.txt"}),
            _context(catalog, working_directory, call_id="call-2"),
        )
    )
    await asyncio.sleep(0)

    assert executor.paths == ["target.txt"]
    assert resources.active == 1
    verifier.release.set()
    first_result, second_result = await asyncio.gather(first, second)

    expected_key = f"filesystem:{(working_directory / 'target.txt').as_posix()}"
    assert first_result.status is ToolExecutionStatus.SUCCESS
    assert second_result.status is ToolExecutionStatus.SUCCESS
    assert verifier.calls == 2
    assert resources.keys == [expected_key, expected_key]
    assert executor.paths == ["target.txt", "target.txt"]
    assert resources.active == 0
