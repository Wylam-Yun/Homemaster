"""HomeToolAdapter integration tests (ordinary, non-physical tools)."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

import pytest

from agentscope.message import DataBlock
from agentscope.permission import PermissionBehavior
from agentscope.tool import ToolChunk
from homemaster.permissions import PermissionChecker, PermissionSettingsConfig
from homemaster.permissions.config import PermissionMode
from homemaster.substrate import (
    HomeToolAdapter,
    RunScope,
    RunScopeMiddleware,
    result_to_chunk,
)
from homemaster.substrate.toolkit import _current_tool_call_id
from homemaster.tools.base import FunctionTool, ToolRegistry
from homemaster.tools.contracts import (
    OutcomeCertainty,
    PermissionSubject,
    ResultAttachment,
    ResultImage,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
)
from homemaster.tools.executor import ToolExecutor


def _scope(tmp_path: Path) -> RunScope:
    return RunScope(
        session_id="s1",
        run_id="r1",
        permission_subject=PermissionSubject(
            subject_id="test", channel="pytest"
        ),
        working_directory=tmp_path,
    )


def _executor(*tools: FunctionTool, settings=None) -> ToolExecutor:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return ToolExecutor(
        registry,
        permission_checker=PermissionChecker(
            settings or PermissionSettingsConfig(mode=PermissionMode.FULL_AUTO)
        ),
    )


def _ok(arguments: dict, context: Any) -> ToolExecutionResult:
    return ToolExecutionResult(
        status=ToolExecutionStatus.SUCCESS,
        text=f"echo:{arguments.get('text')}",
        data={"status": "success", "echo": arguments.get("text")},
    )


@pytest.mark.asyncio
async def test_adapter_exposes_ordinary_name_and_schema(tmp_path) -> None:
    tool = FunctionTool(
        name="echo",
        description="Echo.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        execute=_ok,
    )
    adapter = HomeToolAdapter(tool, _executor(tool), _scope(tmp_path))
    assert adapter.name == "echo"
    # Model-facing schema exposes properties but NOT the hidden stable id.
    assert adapter.input_schema["properties"]["text"]["type"] == "string"
    assert "stable_id" not in str(adapter.input_schema)
    assert tool.stable_id == "homemaster.echo.v1"


@pytest.mark.asyncio
async def test_call_runs_executor_and_projects_chunk(tmp_path) -> None:
    tool = FunctionTool(
        name="echo",
        description="Echo.",
        input_schema={"type": "object"},
        execute=_ok,
    )
    adapter = HomeToolAdapter(tool, _executor(tool), _scope(tmp_path))
    chunks = [
        chunk async for chunk in adapter.call(text="hi")
    ]
    assert len(chunks) == 1
    chunk = chunks[0]
    assert isinstance(chunk, ToolChunk)
    assert chunk.state == "success"
    assert chunk.is_last is True
    assert chunk.content[0].text == "echo:hi"
    hm = chunk.metadata["hm"]
    assert hm["status"] == "success"
    assert hm["data"]["echo"] == "hi"


@pytest.mark.asyncio
async def test_tool_call_id_bridged_from_acting_middleware(tmp_path) -> None:
    seen: list[str] = []

    def _record(arguments: dict, context: Any) -> ToolExecutionResult:
        seen.append(context.tool_call_id)
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS, text="ok", data={}
        )

    tool = FunctionTool(
        name="rec", description="r", input_schema={"type": "object"},
        execute=_record,
    )
    adapter = HomeToolAdapter(tool, _executor(tool), _scope(tmp_path))

    class _Call:
        id = "block-42"

        name = "rec"

    mw = RunScopeMiddleware()

    async def _body(**kwargs: Any):
        async for chunk in adapter.call():
            yield chunk

    async def _next(**kwargs: Any):
        async for chunk in _body():
            yield chunk

    chunks = [
        c
        async for c in mw.on_acting(
            agent=None,
            input_kwargs={"tool_call": _Call()},
            next_handler=_next,
        )
    ]
    assert chunks and chunks[0].state == "success"
    assert seen == ["block-42"]
    # contextvar resets after the acting scope ends
    assert _current_tool_call_id.get() == ""


@pytest.mark.asyncio
async def test_denied_at_as_level_maps_to_deny(tmp_path) -> None:
    tool = FunctionTool(
        name="danger", description="d", input_schema={"type": "object"},
        execute=_ok,
    )
    settings = PermissionSettingsConfig(
        mode=PermissionMode.FULL_AUTO, denied_tools=("danger",)
    )
    adapter = HomeToolAdapter(tool, _executor(tool, settings=settings), _scope(tmp_path))
    decision = await adapter.check_permissions({}, context=None)
    assert decision.behavior == PermissionBehavior.DENY
    assert decision.message


@pytest.mark.asyncio
async def test_concurrency_and_read_only_flags(tmp_path) -> None:
    parallel = FunctionTool(
        name="p", description="d", input_schema={"type": "object"},
        execute=_ok, read_only=True, concurrency_policy="parallel",
    )
    serialized = FunctionTool(
        name="s", description="d", input_schema={"type": "object"},
        execute=_ok, concurrency_policy="serialized",
    )
    adapter_p = HomeToolAdapter(parallel, _executor(parallel), _scope(tmp_path))
    adapter_s = HomeToolAdapter(serialized, _executor(serialized), _scope(tmp_path))
    assert adapter_p.is_concurrency_safe is True
    assert adapter_s.is_concurrency_safe is False
    assert await adapter_p.check_read_only({}) is True
    assert await adapter_s.check_read_only({}) is False


def _sha_b64(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_result_to_chunk_carries_images_and_attachments() -> None:
    image_bytes = base64.b64decode("AAAA")
    attach_bytes = base64.b64decode(base64.b64encode(b"hello"))
    result = ToolExecutionResult(
        status=ToolExecutionStatus.SUCCESS,
        text="shot",
        images=(
            ResultImage(
                data_base64="AAAA", media_type="image/png",
                content_sha256=_sha_b64(image_bytes),
            ),
        ),
        attachments=(
            ResultAttachment(
                filename="log.txt", media_type="text/plain",
                data_base64=base64.b64encode(b"hello").decode(),
                content_sha256=_sha_b64(attach_bytes),
            ),
        ),
        data={"status": "success", "backend_attempted": True},
        backend_attempted=True,
    )
    chunk = result_to_chunk(result)
    kinds = [type(b).__name__ for b in chunk.content]
    assert "TextBlock" in kinds and "DataBlock" in kinds
    data = next(b for b in chunk.content if isinstance(b, DataBlock))
    assert data.source.data == "AAAA"
    hm = chunk.metadata["hm"]
    assert hm["backend_attempted"] is True
    assert hm["data"]["backend_attempted"] is True
    assert hm["attachments"][0]["filename"] == "log.txt"


def test_outcome_unknown_never_maps_to_interrupted() -> None:
    from agentscope.message import ToolResultState

    result = ToolExecutionResult(
        status=ToolExecutionStatus.OUTCOME_UNKNOWN,
        text="lost contact after backend start",
        data={"status": "outcome_unknown"},
        error=ToolExecutionError(
            code="outcome_unknown",
            message="lost contact after backend start",
        ),
        outcome_certainty=OutcomeCertainty.UNKNOWN,
        backend_attempted=True,
    )
    chunk = result_to_chunk(result)
    assert chunk.state == ToolResultState.ERROR
    assert chunk.state != ToolResultState.INTERRUPTED
    assert chunk.metadata["hm"]["status"] == "outcome_unknown"
