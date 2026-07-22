from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from homemaster.application.contracts import RunResult, RunStatus
from homemaster.application.session import SessionManager
from homemaster.channels.bridge import ChannelBridge
from homemaster.channels.bus import BoundedPriorityBus
from homemaster.channels.contracts import (
    ChannelEventKind,
    ChannelIdentity,
    InboundMessage,
    OutboundMessage,
)
from homemaster.channels.router import AttachmentPolicy, ChannelRouter
from homemaster.cli.gateway_command import serve_gateway
from homemaster.config import GatewayConfig, HomeMasterConfig
from homemaster.events.bus import EventBus
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.gateway.auth import AuthenticatedPrincipal
from homemaster.gateway.runtime import (
    GatewayRuntime,
    build_gateway_assembly,
    sanitize_recovered_messages,
)


@dataclass
class _FakeApplication:
    requests: list = None

    def __post_init__(self) -> None:
        self.requests = []
        self.event_bus = EventBus()
        self.cancelled: list[str] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, request):
        self.requests.append(request)
        self.entered.set()
        await self.release.wait()
        return RunResult("run-1", request.session_id, RunStatus.REPLIED, "done")

    def cancel(self, session_id: str) -> bool:
        self.cancelled.append(session_id)
        return True


class _RecoveringApplication:
    def __init__(self, manager: SessionManager) -> None:
        self.session_manager = manager
        self.requests = []
        self.generations: list[int] = []
        self.completed = asyncio.Event()

    async def run(self, request):
        self.requests.append(request)
        async with self.session_manager.turn(request.session_id) as (_runtime, generation, _):
            self.generations.append(generation)
        self.completed.set()
        return RunResult("run-recovered", request.session_id, RunStatus.REPLIED, "restored")

    def cancel(self, _session_id: str) -> bool:
        return True


class _FakeChannel:
    name = "fake"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.sent = []
        self.events = []
        self.send_error: Exception | None = None

    async def start(self) -> None:
        self.events.append("start")
        self.started.set()
        await self.stopped.wait()

    async def stop(self) -> None:
        self.events.append("stop")
        self.stopped.set()

    async def send(self, message) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.events.append(f"send:{message.content}")
        self.sent.append(message)


def _inbound(content: str = "hello") -> InboundMessage:
    return InboundMessage(
        identity=ChannelIdentity("tenant-a", "telegram", "chat-a", "sender-a"),
        principal=AuthenticatedPrincipal(
            "tenant-a", "operator-a", "telegram", capabilities=("tool.read",)
        ),
        content=content,
    )


@pytest.mark.asyncio
async def test_bridge_submits_only_application_run_request_with_authoritative_principal(
    tmp_path,
) -> None:
    app = _FakeApplication()
    app.release.set()
    bus = BoundedPriorityBus()
    bridge = ChannelBridge(
        application=app,
        bus=bus,
        router=ChannelRouter(),
        attachment_policy=AttachmentPolicy((tmp_path,)),
    )

    await bridge.handle(_inbound(), generation=1, is_current=lambda: True)
    request = app.requests[0]
    outbound = await bus.receive_outbound()

    assert request.permission_subject.tenant_id == "tenant-a"
    assert request.permission_subject.subject_id == "operator-a"
    assert request.session_id.startswith("gw-")
    assert request.enabled_tool_ids is None
    assert request.metadata["gateway_generation"] == 1
    assert outbound.kind is ChannelEventKind.FINAL
    assert outbound.content == "done"


@pytest.mark.asyncio
async def test_bridge_metadata_cannot_enable_catalog_or_plugin_tools(tmp_path) -> None:
    app = _FakeApplication()
    app.release.set()
    bus = BoundedPriorityBus()
    inbound = _inbound()
    inbound = InboundMessage(
        identity=inbound.identity,
        principal=inbound.principal,
        content=inbound.content,
        metadata={"enabled_tool_ids": ["plugin.audit.query.v1"]},
    )
    bridge = ChannelBridge(
        application=app,
        bus=bus,
        router=ChannelRouter(),
        attachment_policy=AttachmentPolicy((tmp_path,)),
    )

    await bridge.handle(inbound, generation=1, is_current=lambda: True)

    assert app.requests[0].enabled_tool_ids is None
    assert "enabled_tool_ids" not in app.requests[0].metadata


@pytest.mark.asyncio
async def test_bridge_sanitizes_terminal_free_text_before_egress(tmp_path) -> None:
    app = _FakeApplication()
    app.release.set()
    bus = BoundedPriorityBus()
    app.run = AsyncMock(
        return_value=RunResult(
            "run-secret",
            ChannelRouter().route(_inbound()).session_id,
            RunStatus.REPLIED,
            "token=raw-secret /home/operator/private.txt",
        )
    )
    bridge = ChannelBridge(
        application=app,
        bus=bus,
        router=ChannelRouter(),
        attachment_policy=AttachmentPolicy((tmp_path,)),
    )

    await bridge.handle(_inbound(), generation=1, is_current=lambda: True)

    content = (await bus.receive_outbound()).content
    assert "raw-secret" not in content
    assert "/home/operator" not in content


@pytest.mark.asyncio
async def test_gateway_cancel_joins_worker_and_rejects_late_result(tmp_path) -> None:
    app = _FakeApplication()
    bus = BoundedPriorityBus()
    gateway = GatewayRuntime(
        bridge=ChannelBridge(
            application=app,
            bus=bus,
            router=ChannelRouter(),
            attachment_policy=AttachmentPolicy((tmp_path,)),
        ),
        bus=bus,
    )
    await gateway.submit(_inbound())
    await app.entered.wait()
    route = ChannelRouter().route(_inbound())

    assert await gateway.cancel(route.session_id, reason="user_requested")
    app.release.set()
    await asyncio.sleep(0)
    cancel = await bus.receive_outbound()
    assert cancel.kind is ChannelEventKind.CANCEL
    assert bus.outbound_size == 0


def test_restart_sanitizer_removes_unpaired_assistant_tool_tail() -> None:
    messages = [
        UserMessage.from_text("first"),
        AssistantMessage(tool_calls=[ToolCall(id="paired", name="observe")]),
        ToolResultMessage(
            tool_call_id="paired",
            name="observe",
            content=[ContentBlock(text="ok")],
        ),
        UserMessage.from_text("second"),
        AssistantMessage(tool_calls=[ToolCall(id="orphan", name="mutate")]),
    ]

    sanitized = sanitize_recovered_messages(messages)

    assert [message.role for message in sanitized] == ["user", "assistant", "tool", "user"]


def test_gateway_assembly_reuses_supplied_application_runtime(tmp_path) -> None:
    application = _FakeApplication()
    config = GatewayConfig(
        bus_capacity=8,
        per_tenant_capacity=6,
        per_session_capacity=4,
        telegram={"attachment_root": tmp_path},
    )

    assembly = build_gateway_assembly(
        application,
        config,
        sensitive_values=("configured-provider-secret",),
    )

    assert assembly.runtime.bridge.application is application
    assert assembly.channel.bus is assembly.bus
    assert (
        assembly.runtime.bridge.public_projection.sanitize_content("configured-provider-secret")
        == "[REDACTED]"
    )


@pytest.mark.asyncio
async def test_gateway_restart_restores_sanitized_snapshot_with_new_generation(tmp_path) -> None:
    session_id = ChannelRouter().route(_inbound()).session_id
    original = SessionManager(session_root=tmp_path / "sessions")
    persisted = await original.open_or_resume(session_id)
    persisted.session.append(UserMessage.from_text("keep"))
    persisted.session.append(AssistantMessage(tool_calls=[ToolCall(id="orphan", name="mutate")]))
    await original.save(session_id)

    restored_manager = SessionManager(session_root=tmp_path / "sessions")
    application = _RecoveringApplication(restored_manager)
    bus = BoundedPriorityBus()
    gateway = GatewayRuntime(
        bridge=ChannelBridge(
            application=application,
            bus=bus,
            router=ChannelRouter(),
            attachment_policy=AttachmentPolicy((tmp_path,)),
        ),
        bus=bus,
    )

    await gateway.submit(_inbound())
    await asyncio.wait_for(application.completed.wait(), timeout=1)
    restored = restored_manager.get(session_id)

    assert application.requests[0].resume is True
    assert application.generations == [persisted.generation + 1]
    assert [message.role for message in restored.session.messages] == ["user"]
    assert (await bus.receive_outbound()).content == "restored"


@pytest.mark.asyncio
async def test_gateway_relays_only_projected_public_progress(tmp_path) -> None:
    application = _FakeApplication()
    bus = BoundedPriorityBus()
    gateway = GatewayRuntime(
        bridge=ChannelBridge(
            application=application,
            bus=bus,
            router=ChannelRouter(),
            attachment_policy=AttachmentPolicy((tmp_path,)),
        ),
        bus=bus,
    )
    channel = _FakeChannel()
    service = asyncio.create_task(gateway.serve(channel))
    await channel.started.wait()
    session_id = await gateway.submit(_inbound())
    await application.entered.wait()
    while application.event_bus.subscriber_count == 0:
        await asyncio.sleep(0)

    private = RuntimeEvent(
        type="assistant.reply",
        session_id=session_id,
        run_id="run-public",
        turn_index=1,
        payload={"reply": "public progress", "provider_private": "must-not-leak"},
        gateway_generation=1,
    )
    await application.event_bus.aemit(private)
    progress = RuntimeEvent(
        type="tool.call_started",
        session_id=session_id,
        run_id="run-public",
        turn_index=1,
        tool_call_id="tool-public",
        name="observe",
        payload={},
        gateway_generation=1,
    )
    await application.event_bus.aemit(progress)
    while not channel.sent:
        await asyncio.sleep(0)

    assert application.event_bus.events == [private, progress]
    assert channel.sent[0].kind is ChannelEventKind.PROGRESS
    assert channel.sent[0].content == "tool.call_started"
    assert "provider_private" not in channel.sent[0].metadata

    application.release.set()
    while len(channel.sent) < 2:
        await asyncio.sleep(0)
    assert [message.kind for message in channel.sent] == [
        ChannelEventKind.PROGRESS,
        ChannelEventKind.FINAL,
    ]
    assert channel.sent[1].content == "done"
    await channel.stop()
    await asyncio.wait_for(service, timeout=1)


@pytest.mark.asyncio
async def test_egress_drops_already_queued_stale_generation(tmp_path) -> None:
    application = _FakeApplication()
    bus = BoundedPriorityBus()
    gateway = GatewayRuntime(
        bridge=ChannelBridge(
            application=application,
            bus=bus,
            router=ChannelRouter(),
            attachment_policy=AttachmentPolicy((tmp_path,)),
        ),
        bus=bus,
    )
    channel = _FakeChannel()
    route = ChannelRouter().route(_inbound())
    gateway._generations[route.session_id] = 2
    gateway._identities[route.session_id] = _inbound().identity
    for generation, content in ((1, "stale"), (2, "current")):
        await bus.publish_outbound(
            OutboundMessage(
                identity=_inbound().identity,
                session_id=route.session_id,
                generation=generation,
                kind=ChannelEventKind.FINAL,
                content=content,
                correlation_id=f"message-{generation}",
            )
        )

    egress = asyncio.create_task(gateway._egress_loop(channel))
    while not channel.sent:
        await asyncio.sleep(0)
    egress.cancel()
    with pytest.raises(asyncio.CancelledError):
        await egress

    assert [message.content for message in channel.sent] == ["current"]


@pytest.mark.asyncio
async def test_public_event_backlog_keeps_producer_gateway_generation(tmp_path) -> None:
    application = _FakeApplication()
    bus = BoundedPriorityBus()
    gateway = GatewayRuntime(
        bridge=ChannelBridge(
            application=application,
            bus=bus,
            router=ChannelRouter(),
            attachment_policy=AttachmentPolicy((tmp_path,)),
        ),
        bus=bus,
    )
    route = ChannelRouter().route(_inbound())
    gateway._generations[route.session_id] = 2
    gateway._identities[route.session_id] = _inbound().identity
    public_loop = asyncio.create_task(gateway._public_event_loop())
    while application.event_bus.subscriber_count == 0:
        await asyncio.sleep(0)

    for generation, call_id in ((1, "stale"), (2, "current")):
        await application.event_bus.aemit(
            RuntimeEvent(
                type="tool.call_started",
                session_id=route.session_id,
                run_id=f"run-{generation}",
                turn_index=1,
                tool_call_id=call_id,
                name="observe",
                payload={},
                gateway_generation=generation,
            )
        )

    outbound = await bus.receive_outbound()
    assert outbound.correlation_id == "current"
    assert outbound.generation == 2
    assert bus.outbound_size == 0
    public_loop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await public_loop


@pytest.mark.asyncio
async def test_gateway_close_deadline_bounds_cancellation_resistant_worker(tmp_path) -> None:
    application = _FakeApplication()
    cancellation_seen = asyncio.Event()

    async def resistant_run(request):
        application.requests.append(request)
        application.entered.set()
        try:
            await application.release.wait()
        except asyncio.CancelledError:
            cancellation_seen.set()
            await application.release.wait()
        return RunResult("run-resistant", request.session_id, RunStatus.REPLIED, "late")

    application.run = resistant_run
    bus = BoundedPriorityBus()
    gateway = GatewayRuntime(
        bridge=ChannelBridge(
            application=application,
            bus=bus,
            router=ChannelRouter(),
            attachment_policy=AttachmentPolicy((tmp_path,)),
        ),
        bus=bus,
    )
    await gateway.submit(_inbound())
    await application.entered.wait()

    started = time.monotonic()
    assert await gateway.aclose(deadline_s=0.02) is False
    assert time.monotonic() - started < 0.2
    assert cancellation_seen.is_set()

    application.release.set()
    await asyncio.sleep(0)
    assert await gateway.aclose(deadline_s=1) is True


@pytest.mark.asyncio
async def test_service_task_failure_stops_channel_and_propagates(tmp_path) -> None:
    application = _FakeApplication()
    application.release.set()
    bus = BoundedPriorityBus()
    gateway = GatewayRuntime(
        bridge=ChannelBridge(
            application=application,
            bus=bus,
            router=ChannelRouter(),
            attachment_policy=AttachmentPolicy((tmp_path,)),
        ),
        bus=bus,
    )
    channel = _FakeChannel()
    channel.send_error = RuntimeError("send failed")
    service = asyncio.create_task(gateway.serve(channel))
    await channel.started.wait()

    await gateway.submit(_inbound())

    with pytest.raises(RuntimeError, match="send failed"):
        await asyncio.wait_for(service, timeout=1)
    assert channel.stopped.is_set()


@pytest.mark.asyncio
async def test_gateway_close_drains_outbound_before_stopping_channel(tmp_path) -> None:
    application = _FakeApplication()
    bus = BoundedPriorityBus()
    gateway = GatewayRuntime(
        bridge=ChannelBridge(
            application=application,
            bus=bus,
            router=ChannelRouter(),
            attachment_policy=AttachmentPolicy((tmp_path,)),
        ),
        bus=bus,
    )
    channel = _FakeChannel()
    service = asyncio.create_task(gateway.serve(channel))
    await channel.started.wait()
    route = ChannelRouter().route(_inbound())
    gateway._generations[route.session_id] = 1
    gateway._identities[route.session_id] = _inbound().identity
    await bus.publish_outbound(
        OutboundMessage(
            identity=_inbound().identity,
            session_id=route.session_id,
            generation=1,
            kind=ChannelEventKind.FINAL,
            content="drain-me",
            correlation_id="drain-message",
        )
    )

    assert await gateway.aclose(deadline_s=1)
    await asyncio.wait_for(service, timeout=1)

    assert channel.events.index("send:drain-me") < channel.events.index("stop")


@pytest.mark.asyncio
async def test_gateway_cli_lifecycle_fails_before_composition_when_disabled() -> None:
    with pytest.raises(ValueError, match="must both be enabled"):
        await serve_gateway(HomeMasterConfig())
