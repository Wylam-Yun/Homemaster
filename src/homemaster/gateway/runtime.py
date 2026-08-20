"""Application-owned Gateway task lifecycle and generation fencing."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from homemaster.agent.compact import repair_tool_pairs
from homemaster.agent.messages import Message
from homemaster.artifacts import ToolOutputArtifactResolver
from homemaster.channels.bridge import ChannelBridge
from homemaster.channels.bus import BoundedPriorityBus, BusClosedError
from homemaster.channels.contracts import (
    ChannelEventKind,
    DeliveryReceipt,
    DeliveryStatus,
    InboundMessage,
    OutboundArtifactRef,
    OutboundMessage,
)
from homemaster.channels.impl.base import BaseChannel, ChannelDeliveryError
from homemaster.channels.impl.feishu import FeishuApiService, FeishuChannel
from homemaster.channels.router import AttachmentPolicy, ChannelRouter
from homemaster.config.config import GatewayConfig
from homemaster.events.public_projection import (
    PublicEventProjection,
    public_gateway_stream,
)
from homemaster.gateway.confirmation import FeishuApprovalRoute

_GATEWAY_AUDIT_LOGGER = logging.getLogger("homemaster.feishu.audit")


def _audit_gateway_generation(
    action: str, session_id: str, generation: int, started: float
) -> None:
    _GATEWAY_AUDIT_LOGGER.info(
        json.dumps(
            {
                "action": action,
                "target": session_id,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
                "return_code": generation,
                "certainty": "confirmed_success",
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )
    )


class GatewayApplication(Protocol):
    session_manager: object

    async def run(self, request): ...

    def cancel(self, session_id: str) -> bool: ...


@dataclass(frozen=True)
class GatewayAssembly:
    runtime: GatewayRuntime
    bus: BoundedPriorityBus
    channel: BaseChannel


class GatewayRuntime:
    def __init__(
        self,
        *,
        bridge: ChannelBridge,
        bus: BoundedPriorityBus,
        public_projection: PublicEventProjection | None = None,
        shutdown_deadline_s: float = 5.0,
        group_operations: Any | None = None,
        confirmation_handler: Any | None = None,
    ) -> None:
        self.bridge = bridge
        self.bus = bus
        self._public_projection = public_projection or bridge.public_projection
        self._generations: dict[str, int] = {}
        self._active: dict[str, asyncio.Task[None]] = {}
        self._identities: dict[str, object] = {}
        self._delivery_contexts: dict[str, object] = {}
        self._known_sessions: set[str] = set()
        self._lock = asyncio.Lock()
        self._closed = False
        self._service_tasks: tuple[asyncio.Task[None], ...] = ()
        self._channel: BaseChannel | None = None
        self._channel_stop_task: asyncio.Task[None] | None = None
        self._close_lock = asyncio.Lock()
        self._close_complete = False
        self._drained = True
        if shutdown_deadline_s <= 0:
            raise ValueError("shutdown_deadline_s must be positive")
        self._shutdown_deadline_s = shutdown_deadline_s
        self._group_operations = group_operations
        self._confirmation_handler = confirmation_handler

    async def serve(self, channel: BaseChannel) -> None:
        if self._service_tasks:
            raise RuntimeError("gateway service is already running")
        if self._closed:
            raise RuntimeError("gateway is closed")
        tasks = (
            asyncio.create_task(channel.start(), name=f"channel:{channel.name}"),
            asyncio.create_task(self._ingress_loop(), name="gateway:ingress"),
            asyncio.create_task(self._egress_loop(channel), name="gateway:egress"),
            asyncio.create_task(self._public_event_loop(), name="gateway:public-events"),
        )
        self._service_tasks = tasks
        self._channel = channel
        primary_error: BaseException | None = None
        try:
            done, _pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                if task.cancelled() and self._closed:
                    continue
                task.result()
        except BaseException as exc:
            primary_error = exc
        drained = await self.aclose(deadline_s=self._shutdown_deadline_s)
        if primary_error is not None:
            raise primary_error
        if not drained:
            raise TimeoutError("gateway shutdown deadline expired before outbound drain")

    async def submit(self, message: InboundMessage) -> str:
        started = time.monotonic()
        if self._closed:
            raise RuntimeError("gateway is closed")
        route = self.bridge.router.route(message)
        async with self._lock:
            previous = self._active.get(route.session_id)
            if previous is not None and not previous.done():
                await self._cancel_locked(route.session_id, previous)
            generation = self._generations.get(route.session_id, 0) + 1
            self._generations[route.session_id] = generation
            self._identities[route.session_id] = message.identity
            if message.delivery_context is not None:
                self._delivery_contexts[route.session_id] = message.delivery_context
            else:
                self._delivery_contexts.pop(route.session_id, None)
            if self._group_operations is not None and message.identity.channel == "feishu":
                self._group_operations.bind(
                    route.session_id,
                    message.identity,
                    generation=generation,
                )
            resume = await self._prepare_resume(route.session_id)
            task = asyncio.create_task(
                self._run(message, route.session_id, generation, resume),
                name=f"gateway:{route.session_id}:{generation}",
            )
            self._active[route.session_id] = task
            if message.identity.channel == "feishu":
                _audit_gateway_generation(
                    "gateway.generation.submit", route.session_id, generation, started
                )
        return route.session_id

    async def cancel(self, session_id: str, *, reason: str = "cancelled") -> bool:
        started = time.monotonic()
        async with self._lock:
            task = self._active.get(session_id)
            if task is None or task.done():
                return False
            identity = getattr(task, "_homemaster_identity", None)
            generation = self._generations.get(session_id, 0) + 1
            self._generations[session_id] = generation
            audit_identity = self._identities.get(session_id)
            if getattr(audit_identity, "channel", None) == "feishu":
                _audit_gateway_generation(
                    "gateway.generation.cancel", session_id, generation, started
                )
            if self._group_operations is not None:
                self._group_operations.clear(session_id, generation=generation)
            self.bridge.application.cancel(session_id)
            await self._cancel_locked(session_id, task)
            if identity is not None:
                await self.bus.publish_outbound(
                    OutboundMessage(
                        identity=identity,
                        session_id=session_id,
                        generation=generation,
                        kind=ChannelEventKind.CANCEL,
                        content=self._public_projection.project_content(reason),
                        correlation_id=f"cancel-{uuid.uuid4().hex[:12]}",
                        delivery_context=self._delivery_contexts.get(session_id),
                    )
                )
            return True

    async def aclose(self, *, deadline_s: float = 5.0) -> bool:
        if deadline_s <= 0:
            raise ValueError("deadline_s must be positive")
        async with self._close_lock:
            if self._close_complete:
                return self._drained
            deadline = asyncio.get_running_loop().time() + deadline_s
            self._closed = True
            tasks = self._service_tasks
            for task in tasks:
                if task.get_name() in {"gateway:ingress", "gateway:public-events"}:
                    task.cancel()

            active_tasks: list[asyncio.Task[None]] = []
            for session_id, task in tuple(self._active.items()):
                if task.done():
                    continue
                self._generations[session_id] = self._generations.get(session_id, 0) + 1
                identity = self._identities.get(session_id)
                if getattr(identity, "channel", None) == "feishu":
                    _audit_gateway_generation(
                        "gateway.generation.close",
                        session_id,
                        self._generations[session_id],
                        time.monotonic(),
                    )
                self.bridge.application.cancel(session_id)
                task.cancel()
                active_tasks.append(task)
            active_complete = await _wait_until(active_tasks, deadline)

            confirmation_complete = self._confirmation_handler is None
            if self._confirmation_handler is not None:
                close_confirmation = asyncio.create_task(
                    self._confirmation_handler.aclose(deadline=deadline),
                    name="gateway:confirmation-close",
                )
                confirmation_complete = await _wait_until((close_confirmation,), deadline)
                if confirmation_complete:
                    confirmation_complete = close_confirmation.result() is not False

            discard = asyncio.create_task(self._discard_inbound(), name="gateway:discard-inbound")
            try:
                remaining = _remaining(deadline)
                stop_reserve = min(0.25, remaining / 2)
                drain_budget = max(0.0, remaining - stop_reserve)
                self._drained = (
                    await self.bus.aclose(deadline_s=drain_budget) if drain_budget > 0 else False
                )
            finally:
                discard.cancel()
                await _wait_until((discard,), deadline)

            channel_error: BaseException | None = None
            channel_complete = self._channel is None
            service_complete = not tasks
            if self._channel is not None:
                if self._channel_stop_task is None:
                    self._channel_stop_task = asyncio.create_task(
                        self._channel.stop(), name="gateway:channel-stop"
                    )
                channel_complete = await _wait_until((self._channel_stop_task,), deadline)
                if channel_complete:
                    try:
                        self._channel_stop_task.result()
                    except BaseException as exc:
                        channel_error = exc
            if channel_complete:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                service_complete = await _wait_until(tasks, deadline)

            self._close_complete = (
                active_complete
                and confirmation_complete
                and self._drained
                and channel_complete
                and service_complete
            )
            if self._close_complete:
                self._service_tasks = ()
                self._channel = None
                self._channel_stop_task = None
            if channel_error is not None:
                raise channel_error
            return self._close_complete

    async def _ingress_loop(self) -> None:
        while True:
            try:
                await self.submit(await self.bus.receive_inbound())
            except BusClosedError:
                return
            except RuntimeError:
                if self._closed:
                    return
                raise

    async def _egress_loop(self, channel: BaseChannel) -> None:
        while True:
            try:
                message = await self.bus.receive_outbound()
            except BusClosedError:
                return
            if not self._is_current_outbound(message):
                continue
            receipt = await channel.send(message)
            if not isinstance(receipt, DeliveryReceipt):
                raise TypeError("BaseChannel.send() must return DeliveryReceipt")
            if receipt.status is DeliveryStatus.CONFIRMED_SUCCESS:
                continue
            if message.kind is ChannelEventKind.PROGRESS:
                continue
            raise ChannelDeliveryError(receipt)

    async def _public_event_loop(self) -> None:
        event_bus = getattr(self.bridge.application, "event_bus", None)
        if event_bus is None:
            await asyncio.Future()
            return
        async for event in public_gateway_stream(event_bus, self._public_projection):
            if event.event_type in {
                "permission.confirmation_completed",
                "permission.confirmation_requested",
            }:
                continue
            if event.event_type in {
                "runtime.budget_exhausted",
                "runtime.cancelled",
                "runtime.turn_completed",
                "runtime.turn_failed",
                "transport.request_failed",
            }:
                continue
            if (
                event.event_type == "assistant.reply"
                and event.metadata.get("finish_reason") != "tool_calls"
            ):
                continue
            identity = self._identities.get(event.session_id)
            generation = event.gateway_generation
            if (
                identity is None
                or generation is None
                or self._generations.get(event.session_id) != generation
            ):
                continue
            try:
                if event.artifacts:
                    for index, raw in enumerate(event.artifacts):
                        artifact = OutboundArtifactRef(**raw)
                        await self.bus.publish_outbound(
                            OutboundMessage(
                                identity=identity,
                                session_id=event.session_id,
                                generation=generation,
                                kind=ChannelEventKind.MEDIA,
                                content=artifact.filename,
                                correlation_id=f"{event.correlation_id}-{index}",
                                attachments=(artifact,),
                                delivery_context=self._delivery_contexts.get(event.session_id),
                                metadata=event.metadata,
                            )
                        )
                else:
                    if not event.content.strip():
                        continue
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            identity=identity,
                            session_id=event.session_id,
                            generation=generation,
                            kind=ChannelEventKind.PROGRESS,
                            content=event.content,
                            correlation_id=event.correlation_id,
                            delivery_context=self._delivery_contexts.get(event.session_id),
                            metadata=event.metadata,
                        )
                    )
            except BusClosedError:
                return

    async def _discard_inbound(self) -> None:
        while True:
            try:
                await self.bus.receive_inbound()
            except BusClosedError:
                return

    def _is_current_outbound(self, message: OutboundMessage) -> bool:
        return (
            self._generations.get(message.session_id) == message.generation
            and self._identities.get(message.session_id) == message.identity
        )

    async def _run(
        self,
        message: InboundMessage,
        session_id: str,
        generation: int,
        resume: bool,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            task._homemaster_identity = message.identity  # type: ignore[attr-defined]
        confirmation_bound = False
        try:
            delivery = message.delivery_context
            if (
                self._confirmation_handler is not None
                and message.identity.channel == "feishu"
                and delivery is not None
                and delivery.source_chat_id is not None
            ):

                async def notify(request):
                    channel = self._channel
                    send = getattr(channel, "send_exec_approval", None)
                    if not callable(send):
                        return DeliveryReceipt(
                            status=DeliveryStatus.CONFIRMED_FAILURE,
                            operation="feishu.approval.send",
                            api_message="Feishu approval transport is unavailable",
                            failed_count=1,
                        )
                    return await send(delivery=delivery, request=request)

                async def update(message_id: str, outcome: str, actor: str):
                    channel = self._channel
                    patch = getattr(channel, "update_exec_approval", None)
                    if not callable(patch):
                        return DeliveryReceipt(
                            status=DeliveryStatus.CONFIRMED_FAILURE,
                            operation="feishu.approval.update",
                            api_message="Feishu approval transport is unavailable",
                            failed_count=1,
                        )
                    return await patch(message_id, outcome, actor)

                self._confirmation_handler.bind_session(
                    FeishuApprovalRoute(
                        session_id=session_id,
                        generation=generation,
                        expected_open_chat_id=delivery.source_chat_id,
                        requester_open_id=message.identity.sender_id,
                        notify=notify,
                        update=update,
                    )
                )
                confirmation_bound = True
            await self.bridge.handle(
                message,
                generation=generation,
                resume=resume,
                is_current=lambda: self._generations.get(session_id) == generation,
            )
            self._known_sessions.add(session_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._generations.get(session_id) == generation:
                await self.bus.publish_outbound(
                    OutboundMessage(
                        identity=message.identity,
                        session_id=session_id,
                        generation=generation,
                        kind=ChannelEventKind.ERROR,
                        content=type(exc).__name__,
                        correlation_id=message.correlation_id or f"error-{uuid.uuid4().hex[:12]}",
                        delivery_context=message.delivery_context,
                    )
                )
        finally:
            if confirmation_bound:
                await self._confirmation_handler.unbind_session(session_id, generation)
            if self._active.get(session_id) is task:
                self._active.pop(session_id, None)

    async def _cancel_locked(self, session_id: str, task: asyncio.Task[None]) -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        if self._active.get(session_id) is task:
            self._active.pop(session_id, None)

    async def _prepare_resume(self, session_id: str) -> bool:
        if session_id in self._known_sessions:
            return True
        manager = getattr(self.bridge.application, "session_manager", None)
        if manager is None:
            return False
        try:
            runtime = manager.get(session_id)
        except KeyError:
            if session_id not in manager.list_session_ids():
                return False
            runtime = await manager.resume(session_id)
        runtime.session.replace_messages(repair_recovered_messages(runtime.session.messages))
        self._known_sessions.add(session_id)
        return True


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - asyncio.get_running_loop().time())


async def _wait_until(tasks: Sequence[asyncio.Task[Any]], deadline: float) -> bool:
    pending = tuple(task for task in tasks if not task.done())
    if not pending:
        return True
    remaining = _remaining(deadline)
    if remaining <= 0:
        return False
    _done, still_pending = await asyncio.wait(pending, timeout=remaining)
    return not still_pending


def repair_recovered_messages(messages: Sequence[Message]) -> list[Message]:
    """Drop orphan tool calls/results before a persisted session is resumed."""

    repaired = repair_tool_pairs(list(messages))
    call_ids = {
        call.id
        for message in repaired
        if getattr(message, "role", None) == "assistant"
        for call in getattr(message, "tool_calls", ())
    }
    return [
        message
        for message in repaired
        if getattr(message, "role", None) != "tool"
        or getattr(message, "tool_call_id", None) in call_ids
    ]


def build_gateway_assembly(
    application: GatewayApplication,
    config: GatewayConfig,
    *,
    api_service: FeishuApiService | None = None,
    group_operations: Any | None = None,
    profile: str = "home",
    confirmation_handler: Any | None = None,
) -> GatewayAssembly:
    """Wire remote ingress around the exact application-factory runtime instance."""

    bus = BoundedPriorityBus(
        capacity=config.bus_capacity,
        per_tenant_capacity=config.per_tenant_capacity,
        per_session_capacity=config.per_session_capacity,
    )
    attachment_root = config.feishu.attachment_root.expanduser()
    attachment_root.mkdir(parents=True, exist_ok=True)
    projection = PublicEventProjection()
    bridge = ChannelBridge(
        application=application,
        bus=bus,
        router=ChannelRouter(),
        attachment_policy=AttachmentPolicy((attachment_root,)),
        profile=profile,
        public_projection=projection,
    )
    runtime = GatewayRuntime(
        bridge=bridge,
        bus=bus,
        public_projection=projection,
        shutdown_deadline_s=config.shutdown_deadline_s,
        group_operations=group_operations,
        confirmation_handler=confirmation_handler,
    )
    publisher = getattr(application, "artifact_publisher", None)
    artifact_resolver = (
        ToolOutputArtifactResolver(publisher.store) if publisher is not None else None
    )
    return GatewayAssembly(
        runtime=runtime,
        bus=bus,
        channel=FeishuChannel(
            config.feishu,
            bus,
            api_service=api_service or FeishuApiService.from_config(config.feishu),
            artifact_resolver=artifact_resolver,
            approval_handler=confirmation_handler,
        ),
    )


__all__ = [
    "GatewayAssembly",
    "GatewayRuntime",
    "build_gateway_assembly",
    "repair_recovered_messages",
]
