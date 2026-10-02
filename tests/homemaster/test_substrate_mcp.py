"""Phase-2 MCP acceptance (decision ④ §3): MCP tools reach the AS engine
through the HM chain — ``McpClientManager`` → ``build_mcp_registered_tools``
→ ``ToolRegistry`` → ``HomeToolAdapter`` → ``Toolkit.call_tool``. AS's own
``Toolkit(mcps=)`` stays unused because it would bypass the artifact/ACL,
audit and permission funnel.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from agentscope.tool import ToolResponse

from homemaster.artifacts.tool_output_store import ToolOutputStore
from homemaster.mcp.adapter import (
    build_mcp_registered_tools,
    register_mcp_tools_atomically,
)
from homemaster.mcp.client import McpClientManager
from homemaster.mcp.types import McpStdioServerConfig
from homemaster.substrate.toolkit import (
    HomeToolAdapter,
    RunScope,
    _current_tool_call_id,
)
from homemaster.tools.base import ToolRegistry
from homemaster.tools.contracts import PermissionSubject
from homemaster.tools.executor import ToolExecutor


@pytest.mark.asyncio
async def test_mcp_tool_calls_through_home_tool_adapter(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    fixture = (
        Path(__file__).parent / "fixtures" / "fake_mcp_server.py"
    )
    manager = McpClientManager(
        {
            "stdio-fixture": McpStdioServerConfig(
                command=sys.executable,
                args=(str(fixture),),
            )
        },
        connect_timeout_s=10,
        call_timeout_s=10,
    )
    store = ToolOutputStore(
        tmp_path / "artifacts", quota_bytes=1 << 20, ttl_seconds=600
    )
    try:
        await manager.connect_all()
        assert manager.list_statuses()[0].state == "connected"

        registered = build_mcp_registered_tools(manager, store)
        registry = ToolRegistry()
        register_mcp_tools_atomically(registry, registered)
        names = {tool.name for tool in registry.list_tools()}
        assert "mcp__stdio_fixture__nested_query" in names

        executor = ToolExecutor(registry)
        scope = RunScope(
            session_id="mcp-as",
            run_id="run-1",
            permission_subject=PermissionSubject(
                subject_id="test",
                channel="pytest",
                tenant_id="t1",
                capabilities=("mcp.call", "tool.read", "tool.mutate"),
            ),
            working_directory=tmp_path,
        )
        adapter = HomeToolAdapter(
            registry.get("mcp__stdio_fixture__nested_query"), executor, scope
        )

        # Drive the adapter exactly as ``Toolkit.call_tool`` would.
        from agentscope.message import ToolCallBlock

        token = current_tool_call_id.set("mcp-call-1")
        try:
            response: Any = None
            async for chunk in adapter.call(
                mode="safe", filters={"tags": ["one", "two"]}
            ):
                response = chunk
        finally:
            current_tool_call_id.reset(token)

        assert isinstance(response, ToolResponse)
        payload = json.dumps(
            [b.model_dump(mode="json") for b in response.content],
            default=str,
        )
        assert "accepted" in payload
        assert response.metadata.get("hm", {}).get("backend_attempted") is True
    finally:
        await manager.aclose()
