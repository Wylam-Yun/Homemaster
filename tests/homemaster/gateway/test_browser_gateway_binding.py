from __future__ import annotations

from pathlib import Path

import pytest

from homemaster.application.contracts import RunRequest, RunResult, RunStatus
from homemaster.channels.bridge import ChannelBridge
from homemaster.channels.bus import BoundedPriorityBus
from homemaster.channels.contracts import (
    ChannelDeliveryContext,
    ChannelEventKind,
    ChannelIdentity,
    InboundMessage,
)
from homemaster.channels.router import AttachmentPolicy, ChannelRouter
from homemaster.gateway.auth import AuthenticatedPrincipal
from homemaster.gateway.browser import BrowserGatewayApplication


class _Application:
    def __init__(self) -> None:
        self.requests: list[RunRequest] = []

    async def run(self, request: RunRequest) -> RunResult:
        self.requests.append(request)
        return RunResult(
            run_id="browser-run",
            session_id=request.session_id or "missing",
            status=RunStatus.REPLIED,
            final_reply="done",
        )

    def cancel(self, _session_id: str) -> bool:
        return True


@pytest.mark.asyncio
async def test_browser_binding_preserves_gateway_request_and_injects_factory() -> None:
    application = _Application()
    factory = object()
    wrapped = BrowserGatewayApplication(application, factory)
    request = RunRequest(
        text="Read https://example.test/ticket/1 and complete it.",
        session_id="gateway-session",
        dependencies={"channel_attachments": (Path("/tmp/existing.json"),)},
        metadata={"gateway_generation": 7},
    )

    result = await wrapped.run(request)
    enriched = application.requests[0]

    assert result.status is RunStatus.REPLIED
    assert enriched.profile == "browser"
    assert enriched.text == request.text
    assert enriched.dependencies["browser_session_factory"] is factory
    assert (
        enriched.dependencies["channel_attachments"] == request.dependencies["channel_attachments"]
    )
    assert enriched.session_id == request.session_id
    assert enriched.permission_subject is request.permission_subject
    assert enriched.metadata == request.metadata
    assert enriched.run_policy.max_tool_iterations is None


@pytest.mark.asyncio
async def test_feishu_bridge_to_browser_binding_preserves_text_route_and_final(
    tmp_path: Path,
) -> None:
    application = _Application()
    factory = object()
    wrapped = BrowserGatewayApplication(application, factory)
    bus = BoundedPriorityBus()
    bridge = ChannelBridge(
        application=wrapped,
        bus=bus,
        router=ChannelRouter(),
        attachment_policy=AttachmentPolicy((tmp_path,)),
        profile="browser",
    )
    delivery = ChannelDeliveryContext(
        receive_id_type="open_id",
        receive_id="ou-owner",
        source_message_id="om-browser-ticket",
    )
    text = "读取 https://docs.example/change/CASE-02 并执行；保留这段原文。"
    inbound = InboundMessage(
        identity=ChannelIdentity("tenant-a", "feishu", "oc-chat", "ou-owner"),
        principal=AuthenticatedPrincipal("tenant-a", "feishu-owner", "feishu"),
        content=text,
        correlation_id="om-browser-ticket",
        delivery_context=delivery,
    )

    result = await bridge.handle(inbound, generation=7, is_current=lambda: True)
    outbound = await bus.receive_outbound()
    request = application.requests[0]

    assert result.status is RunStatus.REPLIED
    assert request.profile == "browser"
    assert request.text == text
    assert request.dependencies["browser_session_factory"] is factory
    assert request.permission_subject.subject_id == "feishu-owner"
    assert request.metadata["gateway_generation"] == 7
    assert outbound.kind is ChannelEventKind.FINAL
    assert outbound.content == "done"
    assert outbound.delivery_context is delivery
