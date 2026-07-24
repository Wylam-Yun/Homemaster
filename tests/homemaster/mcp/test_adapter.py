from __future__ import annotations

from pathlib import Path

import pytest

from homemaster.artifacts.tool_output_store import ToolOutputStore
from homemaster.mcp.adapter import build_mcp_registered_tools
from homemaster.mcp.client import McpCallError, McpServerNotConnectedError
from homemaster.mcp.types import McpPayload, McpResourceInfo, McpToolInfo
from homemaster.tools import ToolRegistry
from homemaster.tools.contracts import (
    PermissionSubject,
    ToolExecutionContext,
    ToolExecutionStatus,
)


class FakeManager:
    def __init__(self) -> None:
        self.calls = 0
        self.secret_values = ("server-secret",)

    def list_tools(self):
        return [
            McpToolInfo(
                server_name="demo",
                name="nested-query",
                description="Nested query",
                input_schema={
                    "type": "object",
                    "properties": {
                        "mode": {"enum": ["fast", "safe"]},
                        "filters": {
                            "type": "object",
                            "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
                            "additionalProperties": False,
                        },
                    },
                    "required": ["mode"],
                    "additionalProperties": False,
                },
            )
        ]

    def list_resources(self):
        return [McpResourceInfo("demo", "Readme", "demo://readme", "Readme")]

    async def call_tool(self, server_name, tool_name, arguments):
        del server_name, tool_name, arguments
        self.calls += 1
        return McpPayload(
            payload={
                "content": [
                    {
                        "type": "text",
                        "text": "server-secret " + ("x" * 200),
                    }
                ]
            },
            media_type="application/json",
        )

    async def read_resource(self, server_name, uri):
        del server_name, uri
        return McpPayload(
            payload={
                "contents": [
                    {
                        "uri": "demo://readme?token=resource-secret",
                        "text": "resource",
                    }
                ]
            }
        )


def _context(internal_id: str) -> ToolExecutionContext:
    return ToolExecutionContext(
        session_id="session-a",
        run_id="run-a",
        turn_index=0,
        tool_call_id="call-a",
        internal_tool_id=internal_id,
        permission_subject=PermissionSubject(
            subject_id="principal-a",
            tenant_id="tenant-a",
            channel="application",
        ),
        backend=None,
        deadline=None,
        cancellation=None,
        domain_observer=None,
        working_directory=Path.cwd(),
    )


@pytest.mark.asyncio
async def test_adapter_preserves_schema_and_returns_raw_preview(tmp_path) -> None:
    manager = FakeManager()
    store = ToolOutputStore(tmp_path / "artifacts", quota_bytes=4096, ttl_seconds=60)
    tools = build_mcp_registered_tools(manager, store, preview_chars=40)
    mcp_tool = next(tool for tool in tools if tool.definition.execution_backend.value == "mcp")

    assert (
        mcp_tool.definition.input_schema["properties"]["filters"]["additionalProperties"] is False
    )
    assert mcp_tool.definition.state_effects == ("mcp.remote_state",)
    assert mcp_tool.definition.input_schema["properties"]["mode"]["enum"] == (
        "fast",
        "safe",
    )

    result = await mcp_tool.executor.execute(
        {"mode": "fast", "filters": {"tags": ["one"]}},
        _context(mcp_tool.definition.internal_id),
    )

    assert result.success is True
    assert result.data["truncated"] is True
    assert "server-secret" in result.data["preview"]
    raw = store.read(
        result.data["artifact_handle"],
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
    )
    assert b"server-secret" in raw

    list_resources = next(
        tool for tool in tools if tool.definition.model_alias == "list_mcp_resources"
    )
    listed = await list_resources.executor.execute(
        {},
        _context(list_resources.definition.internal_id),
    )
    assert "demo://readme" not in listed.data["preview"]
    listed_raw = store.read(
        listed.data["artifact_handle"],
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
    )
    assert b"demo://readme" not in listed_raw
    assert b"mcp-resource:" in listed_raw

    read_resource = next(
        tool for tool in tools if tool.definition.model_alias == "read_mcp_resource"
    )
    resource_id = read_resource.definition.input_schema["properties"]["resource_id"]["enum"][0]
    read = await read_resource.executor.execute(
        {"resource_id": resource_id},
        _context(read_resource.definition.internal_id),
    )
    assert "resource" in read.data["preview"]
    assert "demo://readme" not in read.text
    assert read.data["preview"] == read.text
    read_raw = store.read(
        read.data["artifact_handle"],
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
    )
    assert b"demo://readme?token=resource-secret" in read_raw
    assert list_resources.definition.state_effects == ()
    assert read_resource.definition.state_effects == ()


def test_registry_registration_is_atomic_on_name_conflict(tmp_path) -> None:
    manager = FakeManager()
    store = ToolOutputStore(tmp_path / "artifacts", quota_bytes=4096, ttl_seconds=60)
    registry = ToolRegistry()
    tools = build_mcp_registered_tools(manager, store)
    duplicate = [tools[0], *tools]

    from homemaster.mcp.adapter import register_mcp_tools_atomically

    with pytest.raises(ValueError, match="duplicate tool name"):
        register_mcp_tools_atomically(registry, duplicate)
    assert registry.list_tools() == []


@pytest.mark.asyncio
async def test_non_canonical_payload_returns_typed_failure_without_artifact(tmp_path) -> None:
    manager = FakeManager()

    async def invalid_payload(*args, **kwargs):
        del args, kwargs
        return McpPayload(payload={"value": float("nan")})

    manager.call_tool = invalid_payload
    root = tmp_path / "artifacts"
    store = ToolOutputStore(root, quota_bytes=4096, ttl_seconds=60)
    tool = build_mcp_registered_tools(manager, store)[0]

    result = await tool.executor.execute({}, _context(tool.definition.internal_id))

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "mcp_execution_failed"
    assert "canonical JSON" in result.error.message
    assert not any(path.suffix == ".blob" for path in root.rglob("*"))


@pytest.mark.asyncio
async def test_attempted_mcp_call_failure_has_unknown_outcome(tmp_path) -> None:
    manager = FakeManager()

    async def failed_call(*args, **kwargs):
        del args, kwargs
        raise McpCallError("deadline elapsed after dispatch")

    manager.call_tool = failed_call
    store = ToolOutputStore(tmp_path / "artifacts", quota_bytes=4096, ttl_seconds=60)
    tool = build_mcp_registered_tools(manager, store)[0]

    result = await tool.executor.execute({}, _context(tool.definition.internal_id))

    assert result.status is ToolExecutionStatus.OUTCOME_UNKNOWN
    assert result.backend_attempted is True
    assert result.error is not None and result.error.code == "mcp_outcome_unknown"


@pytest.mark.asyncio
async def test_disconnected_mcp_call_is_pre_backend_failure(tmp_path) -> None:
    manager = FakeManager()

    async def disconnected(*args, **kwargs):
        del args, kwargs
        raise McpServerNotConnectedError("not connected")

    manager.call_tool = disconnected
    store = ToolOutputStore(tmp_path / "artifacts", quota_bytes=4096, ttl_seconds=60)
    tool = build_mcp_registered_tools(manager, store)[0]

    result = await tool.executor.execute({}, _context(tool.definition.internal_id))

    assert result.status is ToolExecutionStatus.INVALID
    assert result.backend_attempted is False
