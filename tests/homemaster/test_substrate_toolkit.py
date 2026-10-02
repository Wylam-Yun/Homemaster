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
        permission_subject=PermissionSubject(subject_id="test", channel="pytest"),
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
    chunks = [chunk async for chunk in adapter.call(text="hi")]
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
        return ToolExecutionResult(status=ToolExecutionStatus.SUCCESS, text="ok", data={})

    tool = FunctionTool(
        name="rec",
        description="r",
        input_schema={"type": "object"},
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
        name="danger",
        description="d",
        input_schema={"type": "object"},
        execute=_ok,
    )
    settings = PermissionSettingsConfig(mode=PermissionMode.FULL_AUTO, denied_tools=("danger",))
    adapter = HomeToolAdapter(tool, _executor(tool, settings=settings), _scope(tmp_path))
    decision = await adapter.check_permissions({}, context=None)
    assert decision.behavior == PermissionBehavior.DENY
    assert decision.message


@pytest.mark.asyncio
async def test_concurrency_and_read_only_flags(tmp_path) -> None:
    parallel = FunctionTool(
        name="p",
        description="d",
        input_schema={"type": "object"},
        execute=_ok,
        read_only=True,
        concurrency_policy="parallel",
    )
    serialized = FunctionTool(
        name="s",
        description="d",
        input_schema={"type": "object"},
        execute=_ok,
        concurrency_policy="serialized",
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
                data_base64="AAAA",
                media_type="image/png",
                content_sha256=_sha_b64(image_bytes),
            ),
        ),
        attachments=(
            ResultAttachment(
                filename="log.txt",
                media_type="text/plain",
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


def test_result_to_chunk_thaws_frozen_result_data() -> None:
    """Canonical frozen ``result.data`` (MappingProxyType/tuple) must be
    recursively thawed before it enters AS pydantic metadata — otherwise
    ``Msg.model_copy(deep=True)`` in ``_strip_context_images`` crashes with
    ``cannot pickle 'mappingproxy'`` at session-save time (live e2e catch).
    """
    import copy
    from types import MappingProxyType

    result = ToolExecutionResult(
        status=ToolExecutionStatus.SUCCESS,
        text="done",
        data={
            "observation": {"objects": ["lamp", "desk"], "nested": {"won": False}},
            "list_field": [{"a": 1}, {"b": 2}],
        },
        backend_attempted=True,
    )
    # Contract: result.data really is frozen (the thing we must thaw).
    assert isinstance(result.data["observation"], MappingProxyType)

    chunk = result_to_chunk(result)
    payload = chunk.metadata["hm"]["data"]
    # The whole metadata tree must survive Msg deepcopy and contain no
    # frozen containers.
    copied = copy.deepcopy(chunk.metadata)

    def _plain(value: object) -> None:
        assert not isinstance(value, MappingProxyType), value
        assert not isinstance(value, tuple), value
        if isinstance(value, dict):
            for item in value.values():
                _plain(item)
        elif isinstance(value, list):
            for item in value:
                _plain(item)

    _plain(copied)
    assert payload["observation"]["nested"] == {"won": False}
    assert payload["list_field"] == [{"a": 1}, {"b": 2}]


def test_message_to_chunk_and_to_agent_scope_thaw_data() -> None:
    """``message_to_chunk`` and ``to_agent_scope`` take the already-projected
    ``ToolResultMessage.data``; callers may hand it still-frozen mappings, so
    both ingresses must thaw recursively (same snapshot crash class)."""
    import copy
    from types import MappingProxyType

    from homemaster.agent.messages import (
        AssistantMessage,
        ContentBlock,
        ToolCall,
        ToolResultMessage,
    )
    from homemaster.substrate.messages import to_agent_scope
    from homemaster.substrate.toolkit import message_to_chunk

    frozen = MappingProxyType({"nested": MappingProxyType({"won": True})})
    msg = ToolResultMessage(
        tool_call_id="call-1",
        name="thor_action",
        content=[ContentBlock(text='{"ok": true}')],
        data={"backend_attempted": True, "result": frozen},
    )
    copied = copy.deepcopy(message_to_chunk(msg).metadata)
    assert copied["hm"]["data"]["result"]["nested"] == {"won": True}

    as_msgs = to_agent_scope(
        [
            AssistantMessage(
                content=[ContentBlock(text="acting")],
                tool_calls=[ToolCall(id="call-1", name="thor_action", arguments={})],
            ),
            msg,
        ]
    )
    result_block = next(b for b in as_msgs[0].content if type(b).__name__ == "ToolResultBlock")
    copied_msg = copy.deepcopy(result_block.metadata)
    assert copied_msg["hm"]["data"]["result"]["nested"] == {"won": True}


@pytest.mark.asyncio
async def test_on_acting_reset_survives_cross_context_aclose() -> None:
    """Asyncgen finalization can run the ``finally`` in a different Context
    than the one where ``ContextVar.set`` happened (e.g. loop asyncgen
    finalizer, or aclose driven by the consumer task). ``Token.reset``
    raises ``ValueError: token created in a different Context`` — the
    binding must degrade gracefully instead of failing the run
    (live ALFWorld e2e caught this as transport_error)."""
    from types import SimpleNamespace

    async def _handler():
        yield "chunk-1"  # middleware passes through whatever the inner handler yields

    mw = RunScopeMiddleware()
    call = SimpleNamespace(id="call-x")
    agen = mw.on_acting(
        SimpleNamespace(), {"tool_call": call}, _handler
    )

    async def drive_first() -> object:
        return await agen.__anext__()

    async def close() -> None:
        await agen.aclose()

    # First iteration under task context A; close under task context B.
    task_a = asyncio.create_task(drive_first())
    first = await task_a
    assert first is not None
    task_b = asyncio.create_task(close())
    await task_b  # must not raise ValueError


import asyncio  # noqa: E402
