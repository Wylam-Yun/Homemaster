from __future__ import annotations

import asyncio
import socket
import sys
from pathlib import Path

import pytest

from homemaster.mcp.client import McpClientManager
from homemaster.mcp.types import McpHttpServerConfig, McpStdioServerConfig


@pytest.mark.asyncio
async def test_real_stdio_handshake_list_call_resource_and_close() -> None:
    pytest.importorskip("mcp")
    fixture = Path(__file__).parents[1] / "fixtures" / "fake_mcp_server.py"
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
    try:
        await manager.connect_all()
        status = manager.list_statuses()[0]
        assert status.state == "connected"
        assert [tool.name for tool in status.tools] == ["nested_query"]
        assert status.tools[0].input_schema["properties"]["mode"]["enum"] == [
            "fast",
            "safe",
        ]

        called = await manager.call_tool(
            "stdio-fixture",
            "nested_query",
            {"mode": "safe", "filters": {"tags": ["one", "two"]}},
        )
        resource = await manager.read_resource("stdio-fixture", "fixture://readme")

        assert "accepted" in str(called.payload)
        assert "homemaster-mcp-resource" in str(resource.payload)
    finally:
        await manager.aclose()


@pytest.mark.asyncio
async def test_real_streamable_http_handshake_call_and_shutdown() -> None:
    pytest.importorskip("mcp")
    uvicorn = pytest.importorskip("uvicorn")
    from tests.homemaster.fixtures.fake_mcp_server import server

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    http_server = uvicorn.Server(
        uvicorn.Config(
            server.streamable_http_app(),
            log_level="error",
            lifespan="on",
        )
    )
    serve_task = asyncio.create_task(http_server.serve(sockets=[listener]))
    manager = McpClientManager(
        {
            "http-fixture": McpHttpServerConfig(
                url=f"http://127.0.0.1:{port}/mcp",
                headers={"Authorization": "Bearer loopback-secret"},
            )
        },
        connect_timeout_s=10,
        call_timeout_s=10,
    )
    try:
        for _ in range(200):
            if http_server.started:
                break
            if serve_task.done():
                await serve_task
            await asyncio.sleep(0.01)
        assert http_server.started is True

        await manager.connect_all()
        assert manager.list_statuses()[0].state == "connected"
        called = await manager.call_tool(
            "http-fixture",
            "nested_query",
            {"mode": "fast", "filters": {}},
        )
        assert "accepted" in str(called.payload)
    finally:
        await manager.aclose()
        http_server.should_exit = True
        await asyncio.wait_for(serve_task, timeout=10)
        listener.close()

    assert serve_task.exception() is None
