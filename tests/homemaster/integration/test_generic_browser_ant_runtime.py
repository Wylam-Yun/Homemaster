from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

from homemaster.adapters.profiles import build_universal_tool_registry
from homemaster.agent.context import ContextAssembler
from homemaster.agent.messages import ToolCall
from homemaster.application.factory import create_application
from homemaster.application.session import SessionManager
from homemaster.artifacts import ArtifactPublisher, ToolOutputStore
from homemaster.browser.factory import PlaywrightBrowserSessionFactory
from homemaster.browser.policy import BrowserPolicy
from homemaster.channels.bridge import ChannelBridge
from homemaster.channels.bus import BoundedPriorityBus
from homemaster.channels.contracts import (
    ChannelDeliveryContext,
    ChannelEventKind,
    ChannelIdentity,
    InboundMessage,
)
from homemaster.channels.router import AttachmentPolicy, ChannelRouter
from homemaster.config import ContextPolicyConfig, HomeMasterConfig, ProviderProfileConfig
from homemaster.events.bus import EventBus
from homemaster.events.sinks import JsonlTraceSink
from homemaster.gateway.auth import AuthenticatedPrincipal
from homemaster.gateway.browser import BrowserGatewayApplication
from homemaster.gateway.runtime import GatewayRuntime
from homemaster.prompts.loader import PromptId, load_prompt
from homemaster.providers.attempts import ProviderAttemptRecord
from homemaster.providers.transports.types import TransportDelta

ANT_ORIGIN = os.environ.get("HOMEMASTER_ANT_ORIGIN")
pytestmark = pytest.mark.skipif(not ANT_ORIGIN, reason="HOMEMASTER_ANT_ORIGIN is not set")

VALUES = {
    "TenantId": "tenant-phase1",
    "ItemCode": "read",
    "SpecCode": "ext.read.type1",
    "ExtensionName": "read-ext",
}


class _RecordingFactory(PlaywrightBrowserSessionFactory):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.sessions: list[Any] = []

    async def create(self, *, run_id: str):
        session = await super().create(run_id=run_id)
        self.sessions.append(session)
        return session


class _DeterministicBrowserTransport:
    def __init__(self, *, origin: str, factory: _RecordingFactory) -> None:
        self.origin = origin
        self.factory = factory
        self.iteration = 0
        self.manifests: list[tuple[str, ...]] = []
        self.tool_calls: list[str] = []
        self.request_image_counts: list[int] = []
        self.automatic_observations: list[dict[str, object]] = []
        self.independent_dom: dict[str, str] = {}
        self.independent_console = ""
        self.independent_command = ""
        self.backfill_confirmed = False
        self.backfill_receipt_sha256 = ""
        self.backfill_preview_sha256 = ""

    async def stream(
        self,
        messages,
        *,
        tools=None,
        attempt_sink=None,
        model_attempt_id="attempt",
        **kwargs,
    ):
        del kwargs
        names = tuple(item["name"] for item in tools or [])
        self.manifests.append(names)
        self.request_image_counts.append(
            sum(block.type == "image" for block in messages[-1].content)
        )
        automatic_observation = getattr(messages[-1], "data", None) or {}
        if "automatic_observation" in automatic_observation:
            self.automatic_observations.append(
                dict(automatic_observation["automatic_observation"])
            )
        if attempt_sink is not None:
            attempt_sink.record_attempt(
                ProviderAttemptRecord(
                    model_attempt_id=model_attempt_id,
                    request_sha256=hashlib.sha256(repr((messages, tools)).encode()).hexdigest(),
                    outbound_images=(),
                    stripped_images=False,
                    response_completed=True,
                    error_type=None,
                    cause_code=None,
                )
            )
        call = await self._next_call(messages)
        if call is None:
            yield TransportDelta(type="text", text_delta="completed", finish_reason="stop")
            return
        self.tool_calls.append(call.name)
        yield TransportDelta(type="tool_call", tool_call_delta=call, finish_reason="tool_calls")

    async def _next_call(self, messages) -> ToolCall | None:
        index = self.iteration
        self.iteration += 1
        if index == 0:
            return self._call(
                "browser_navigate",
                {"url": f"{self.origin}/dashboard/automation"},
            )
        if index in {1, 3, 5, 7}:
            label = tuple(VALUES)[(index - 1) // 2]
            return self._call("browser_inspect", {"name": label})
        if index in {2, 4, 6, 8}:
            label = tuple(VALUES)[(index - 2) // 2]
            snapshot_id, element_id = _single_ref(messages[-1], label)
            return self._call(
                "browser_fill",
                {
                    "snapshot_id": snapshot_id,
                    "element_id": element_id,
                    "value": VALUES[label],
                },
            )
        if index == 9:
            return self._call("browser_inspect", {"name": "确认执行"})
        if index == 10:
            snapshot_id, element_id = _single_ref(messages[-1], "确认执行")
            return self._call(
                "browser_click",
                {"snapshot_id": snapshot_id, "element_id": element_id},
            )
        if index == 11:
            return self._call(
                "browser_wait",
                {
                    "condition": {
                        "kind": "text_present",
                        "value": "执行状态：SUCCESS (exitCode=0)",
                        "timeout_ms": 10_000,
                    }
                },
            )
        if index == 12:
            return self._call("browser_inspect", {"name": "自动化执行回填截图"})
        if index == 13:
            snapshot_id, element_id = _single_ref(messages[-1], "自动化执行回填截图")
            return self._call(
                "browser_backfill",
                {"snapshot_id": snapshot_id, "element_id": element_id},
            )
        if index == 14:
            self.backfill_receipt_sha256 = str(messages[-1].data["sha256"])
            return self._call("browser_inspect", {"name": "确认回填"})
        if index == 15:
            snapshot_id, element_id = _single_ref(messages[-1], "确认回填")
            return self._call(
                "browser_click",
                {"snapshot_id": snapshot_id, "element_id": element_id},
            )
        if index == 16:
            page = self.factory.sessions[0]._page
            self.independent_dom = {
                name: await page.locator(f"#{name}").input_value() for name in VALUES
            }
            self.independent_console = await page.get_by_test_id("execution-console").inner_text()
            self.independent_command = await page.get_by_test_id("command-preview").inner_text()
            self.backfill_confirmed = (
                await page.get_by_text("已确认回填", exact=True).count()
            ) == 1
            preview_src = await page.get_by_alt_text(
                "自动化执行回填截图预览", exact=True
            ).get_attribute("src")
            assert preview_src is not None and preview_src.startswith("data:image/png;base64,")
            self.backfill_preview_sha256 = hashlib.sha256(
                base64.b64decode(preview_src.split(",", 1)[1])
            ).hexdigest()
            return None
        raise AssertionError(f"unexpected deterministic provider iteration {index}")

    def _call(self, name: str, arguments: dict[str, object]) -> ToolCall:
        return ToolCall(id=f"call-{self.iteration}-{name}", name=name, arguments=arguments)


def _single_ref(message, expected_name: str) -> tuple[str, str]:
    elements = [item for item in message.data["elements"] if item["name"] == expected_name]
    assert len(elements) == 1, elements
    return str(message.data["snapshot_id"]), str(elements[0]["element_id"])


@pytest.mark.asyncio
async def test_feishu_gateway_runtime_completes_real_ant_automation(tmp_path: Path) -> None:
    assert ANT_ORIGIN is not None
    registry = build_universal_tool_registry(
        world_path=None,
        memory_path=None,
        runtime_memory_root=tmp_path / "memory",
    )
    profile = ProviderProfileConfig(
        name="deterministic",
        protocol="anthropic",
        base_url="https://example.invalid",
        model="deterministic-browser",
        api_keys=["not-used"],
        context_window_tokens=100_000,
    )
    factory = _RecordingFactory(
        policy=BrowserPolicy(
            allowed_origins=(ANT_ORIGIN,),
            action_timeout_ms=15_000,
            navigation_timeout_ms=30_000,
            wait_timeout_ms=10_000,
        ),
        video_root=tmp_path / "browser-runs",
    )
    transport = _DeterministicBrowserTransport(origin=ANT_ORIGIN, factory=factory)
    bus = EventBus()
    trace = JsonlTraceSink(tmp_path / "runtime-trace")
    unsubscribe = bus.subscribe(trace.emit)

    def context_factory(request, provider):
        del request
        return ContextAssembler(
            provider=profile,
            policy=ContextPolicyConfig(),
            system_prompt=load_prompt(PromptId.BROWSER_GATEWAY),
            summary_client=provider,
        )

    application = create_application(
        config=HomeMasterConfig(),
        registry=registry,
        event_bus=bus,
        session_manager=SessionManager(session_root=tmp_path / "sessions"),
        provider_factory=lambda _request, _run_id: transport,
        context_assembler_factory=context_factory,
        artifact_publisher=ArtifactPublisher(
            ToolOutputStore(
                tmp_path / "gateway-artifacts",
                quota_bytes=100 * 1024 * 1024,
                ttl_seconds=3_600,
            )
        ),
    )
    gateway_bus = BoundedPriorityBus(capacity=128)
    browser_application = BrowserGatewayApplication(application, factory)
    bridge = ChannelBridge(
        application=browser_application,
        bus=gateway_bus,
        router=ChannelRouter(),
        attachment_policy=AttachmentPolicy((tmp_path,)),
        profile="browser",
    )
    gateway = GatewayRuntime(bridge=bridge, bus=gateway_bus)
    identity = ChannelIdentity(
        tenant_id="tenant-ant-demo",
        channel="feishu",
        chat_id="oc-ant-demo",
        sender_id="ou-ant-owner",
    )
    delivery_context = ChannelDeliveryContext(
        receive_id_type="open_id",
        receive_id="ou-ant-owner",
        source_message_id="om-ant-ticket",
    )
    inbound = InboundMessage(
        identity=identity,
        principal=AuthenticatedPrincipal(
            tenant_id=identity.tenant_id,
            principal_id="feishu-ant-owner",
            channel="feishu",
            roles=("local_operator",),
            capabilities=("device.read", "device.control", "network.http"),
        ),
        content="读取 https://docs.example/change/CASE-02 并执行 Ant 自动化变更单。",
        correlation_id="om-ant-ticket",
        delivery_context=delivery_context,
    )
    public_events = asyncio.create_task(gateway._public_event_loop())
    outbound = []
    try:
        session_id = await gateway.submit(inbound)
        while True:
            message = await asyncio.wait_for(gateway_bus.receive_outbound(), timeout=60)
            outbound.append(message)
            final_seen = any(item.kind is ChannelEventKind.FINAL for item in outbound)
            if final_seen:
                break
    finally:
        public_events.cancel()
        with pytest.raises(asyncio.CancelledError):
            await public_events
        await application.aclose()
        trace.close()
        unsubscribe()

    media = [item for item in outbound if item.kind is ChannelEventKind.MEDIA]
    final = [item for item in outbound if item.kind is ChannelEventKind.FINAL]
    assert media == []
    assert len(final) == 1 and final[0].content == "completed"
    assert all(item.identity == identity for item in (*media, *final))
    assert all(item.session_id == session_id for item in (*media, *final))
    assert all(item.generation == 1 for item in (*media, *final))
    assert all(item.delivery_context == delivery_context for item in (*media, *final))
    assert all(len(item.attachments) == 1 for item in media)
    assert transport.independent_dom == VALUES
    assert "执行状态：SUCCESS (exitCode=0)" in transport.independent_console
    assert all(value in transport.independent_command for value in VALUES.values())
    assert transport.request_image_counts == [
        0,
        0,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        0,
        1,
        0,
        1,
    ]
    assert len(transport.automatic_observations) == 7
    assert all(
        observation["status"] == "success"
        for observation in transport.automatic_observations
    )
    assert transport.backfill_confirmed is True
    assert transport.backfill_receipt_sha256 == transport.backfill_preview_sha256
    assert transport.tool_calls == [
        "browser_navigate",
        "browser_inspect",
        "browser_fill",
        "browser_inspect",
        "browser_fill",
        "browser_inspect",
        "browser_fill",
        "browser_inspect",
        "browser_fill",
        "browser_inspect",
        "browser_click",
        "browser_wait",
        "browser_inspect",
        "browser_backfill",
        "browser_inspect",
        "browser_click",
    ]
    assert "observe" not in transport.tool_calls
    assert len(transport.manifests) == len(transport.tool_calls) + 1
    assert all(
        tool_name in manifest
        for tool_name, manifest in zip(
            transport.tool_calls,
            transport.manifests[: len(transport.tool_calls)],
            strict=True,
        )
    )
    assert len(factory.sessions) == 1 and factory.sessions[0].closed
    assert factory.sessions[0].video_path is not None
    assert factory.sessions[0].video_path.stat().st_size > 0
    assert factory.sessions[0].trace_path.stat().st_size > 0
    runtime_trace = tmp_path / "runtime-trace" / "runtime_events.jsonl"
    assert runtime_trace.is_file() and runtime_trace.stat().st_size > 0
