from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from homemaster.mcp.client import (
    McpCallError,
    McpCleanupError,
    McpClientManager,
    McpConnection,
    McpFeatureUnavailableError,
    McpServerNotConnectedError,
)
from homemaster.mcp.types import McpStdioServerConfig


@dataclass
class FakeSession:
    name: str
    schema: dict[str, object] = field(default_factory=lambda: {"type": "object"})

    async def initialize(self):
        return None

    async def list_tools(self):
        return [{"name": "echo", "description": "Echo", "inputSchema": self.schema}]

    async def list_resources(self):
        return [{"name": "readme", "uri": "fixture://readme", "description": "Readme"}]

    async def call_tool(self, name, arguments):
        return {"content": [{"type": "text", "text": f"{name}:{arguments['value']}"}]}

    async def read_resource(self, uri):
        return {"contents": [{"uri": uri, "text": "resource-body"}]}


@pytest.mark.asyncio
async def test_partial_connect_and_close_failures_are_isolated_and_redacted() -> None:
    closed: list[str] = []

    async def connector(name, config):
        if name == "bad":
            raise RuntimeError(f"connect rejected {config.env['TOKEN']}")

        async def close():
            closed.append(name)
            if name == "close-bad":
                raise RuntimeError(f"close rejected {config.env['TOKEN']}")

        return McpConnection(FakeSession(name), close)

    manager = McpClientManager(
        {
            name: McpStdioServerConfig(command="fixture", env={"TOKEN": f"secret-{name}"})
            for name in ("good", "bad", "close-bad")
        },
        connector=connector,
    )
    await manager.connect_all()

    statuses = {status.name: status for status in manager.list_statuses()}
    assert statuses["good"].state == "connected"
    assert statuses["bad"].state == "failed"
    assert "secret-bad" not in statuses["bad"].detail

    with pytest.raises(McpCleanupError) as error:
        await manager.aclose()
    assert closed == ["close-bad", "good"]
    assert "secret-close-bad" not in str(error.value)


@pytest.mark.asyncio
async def test_connect_timeout_and_cancellation_do_not_leak_connections() -> None:
    started = asyncio.Event()
    released: list[str] = []

    async def connector(name, config):
        del config
        if name == "slow":
            started.set()
            await asyncio.Event().wait()

        async def close():
            released.append(name)

        return McpConnection(FakeSession(name), close)

    configs = {name: McpStdioServerConfig(command="fixture") for name in ("good", "slow")}
    manager = McpClientManager(configs, connector=connector, connect_timeout_s=0.01)
    await manager.connect_all()
    statuses = {status.name: status for status in manager.list_statuses()}
    assert statuses["slow"].state == "failed"
    await manager.aclose()
    assert released == ["good"]

    started.clear()
    cancelled = McpClientManager(configs, connector=connector, connect_timeout_s=60)
    task = asyncio.create_task(cancelled.connect_all())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await cancelled.aclose()
    assert released.count("good") == 2


@pytest.mark.asyncio
async def test_manager_lists_calls_and_reads_connected_session() -> None:
    audit_events: list[dict[str, object]] = []

    async def connector(name, config):
        del config
        return McpConnection(FakeSession(name), lambda: None)

    manager = McpClientManager(
        {"fixture": McpStdioServerConfig(command="fixture")},
        connector=connector,
        audit_sink=audit_events.append,
    )
    await manager.connect_all()

    assert manager.list_tools()[0].server_name == "fixture"
    assert manager.list_resources()[0].uri == "fixture://readme"
    called = await manager.call_tool("fixture", "echo", {"value": "ok"})
    resource = await manager.read_resource("fixture", "fixture://readme?token=resource-secret")
    assert called.payload["content"][0]["text"] == "echo:ok"
    assert resource.payload["contents"][0]["text"] == "resource-body"
    assert "fixture://readme" not in str(audit_events)
    assert "resource-secret" not in str(audit_events)
    resource_events = [
        event for event in audit_events if str(event["type"]).startswith("mcp.resource")
    ]
    assert resource_events
    assert all(str(event["resource_ref"]).startswith("sha256:") for event in resource_events)

    await manager.aclose()


@pytest.mark.asyncio
async def test_audit_failures_do_not_leak_connections_or_abort_cleanup() -> None:
    closed: list[str] = []

    async def connector(name, config):
        del config

        async def close():
            closed.append(name)
            if name == "close-bad":
                raise RuntimeError("backend close failed")

        return McpConnection(FakeSession(name), close)

    def failing_audit(event: dict[str, object]) -> None:
        if event["type"] in {
            "mcp.connect.completed",
            "mcp.close.completed",
            "mcp.close.failed",
        }:
            raise RuntimeError(f"audit unavailable for {event['type']}")

    manager = McpClientManager(
        {name: McpStdioServerConfig(command="fixture") for name in ("good", "close-bad")},
        connector=connector,
        audit_sink=failing_audit,
    )

    await manager.connect_all()
    assert all(status.state == "connected" for status in manager.list_statuses())
    with pytest.raises(McpCleanupError, match="backend close failed"):
        await manager.aclose()

    assert closed == ["close-bad", "good"]
    assert [failure.event_type for failure in manager.list_audit_failures()] == [
        "mcp.connect.completed",
        "mcp.connect.completed",
        "mcp.close.failed",
        "mcp.close.completed",
    ]


@pytest.mark.asyncio
async def test_call_timeout_and_cancellation_are_typed_without_disconnecting() -> None:
    started = asyncio.Event()

    class SlowSession(FakeSession):
        async def call_tool(self, name, arguments):
            del name, arguments
            started.set()
            await asyncio.Event().wait()

    async def connector(name, config):
        del config
        return McpConnection(SlowSession(name), lambda: None)

    timed = McpClientManager(
        {"fixture": McpStdioServerConfig(command="fixture")},
        connector=connector,
        call_timeout_s=0.01,
    )
    await timed.connect_all()
    with pytest.raises(McpCallError, match="tool call failed"):
        await timed.call_tool("fixture", "slow", {})
    assert timed.list_statuses()[0].state == "connected"
    await timed.aclose()

    started.clear()
    cancelled = McpClientManager(
        {"fixture": McpStdioServerConfig(command="fixture")},
        connector=connector,
        call_timeout_s=60,
    )
    await cancelled.connect_all()
    task = asyncio.create_task(cancelled.call_tool("fixture", "slow", {}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.list_statuses()[0].state == "connected"
    await cancelled.aclose()


@pytest.mark.asyncio
async def test_transport_disconnect_fences_future_calls_and_closes_once() -> None:
    closed = 0

    class DisconnectedSession(FakeSession):
        async def call_tool(self, name, arguments):
            del name, arguments
            raise ConnectionError("transport dropped server-secret")

    async def connector(name, config):
        del config

        async def close():
            nonlocal closed
            closed += 1

        return McpConnection(DisconnectedSession(name), close)

    manager = McpClientManager(
        {
            "fixture": McpStdioServerConfig(
                command="fixture",
                env={"TOKEN": "server-secret"},
            )
        },
        connector=connector,
    )
    await manager.connect_all()

    with pytest.raises(McpCallError) as error:
        await manager.call_tool("fixture", "disconnect", {})
    assert "server-secret" not in str(error.value)
    status = manager.list_statuses()[0]
    assert status.state == "failed"
    assert status.error_code == "disconnected"
    assert status.tools == ()
    with pytest.raises(McpServerNotConnectedError):
        await manager.call_tool("fixture", "disconnect", {})

    await manager.aclose()
    assert closed == 1


@pytest.mark.asyncio
async def test_optional_sdk_failure_is_reported_as_typed_feature_unavailable() -> None:
    async def connector(name, config):
        del name, config
        raise McpFeatureUnavailableError("optional dependency unavailable")

    manager = McpClientManager(
        {"fixture": McpStdioServerConfig(command="fixture")},
        connector=connector,
    )
    await manager.connect_all()

    status = manager.list_statuses()[0]
    assert status.state == "failed"
    assert status.error_code == "feature_unavailable"
    await manager.aclose()
