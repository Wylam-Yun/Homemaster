"""Bridge authenticated channel input into the application-owned runtime."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Protocol

from homemaster.application.contracts import RunRequest, RunResult, RunStatus
from homemaster.channels.bus import BoundedPriorityBus
from homemaster.channels.contracts import ChannelEventKind, InboundMessage, OutboundMessage
from homemaster.channels.router import AttachmentPolicy, ChannelRoute, ChannelRouter
from homemaster.events.public_projection import PublicEventProjection


class ApplicationRunner(Protocol):
    async def run(self, request: RunRequest) -> RunResult: ...

    def cancel(self, session_id: str) -> bool: ...


class ChannelBridge:
    def __init__(
        self,
        *,
        application: ApplicationRunner,
        bus: BoundedPriorityBus,
        router: ChannelRouter,
        attachment_policy: AttachmentPolicy,
        profile: str = "home",
        public_projection: PublicEventProjection | None = None,
    ) -> None:
        self.application = application
        self.bus = bus
        self.router = router
        self.attachment_policy = attachment_policy
        self.profile = profile
        self.public_projection = public_projection or PublicEventProjection()

    async def handle(
        self,
        message: InboundMessage,
        *,
        generation: int,
        is_current: Callable[[], bool],
        resume: bool = False,
    ) -> RunResult:
        route = self.router.route(message)
        attachments = self.attachment_policy.resolve_all(message.attachments)
        correlation_id = message.correlation_id or f"msg-{uuid.uuid4().hex[:16]}"
        request = RunRequest(
            text=message.content,
            session_id=route.session_id,
            profile=self.profile,
            resume=resume,
            permission_subject=message.principal.to_permission_subject(),
            dependencies={"channel_attachments": attachments},
            metadata={
                "gateway_channel": route.channel,
                "gateway_chat_type": message.identity.chat_type,
                "gateway_correlation_id": correlation_id,
                "gateway_generation": generation,
            },
        )
        result = await self.application.run(request)
        if not is_current():
            return result
        await self.bus.publish_outbound(
            self._outbound(message, route, result, generation, correlation_id)
        )
        return result

    def _outbound(
        self,
        message: InboundMessage,
        route: ChannelRoute,
        result: RunResult,
        generation: int,
        correlation_id: str,
    ) -> OutboundMessage:
        status = RunStatus(result.status) if result.status in set(RunStatus) else result.status
        if status is RunStatus.CANCELLED:
            kind = ChannelEventKind.CANCEL
        elif status is RunStatus.FAILED:
            kind = ChannelEventKind.ERROR
        else:
            kind = ChannelEventKind.FINAL
        content = result.final_reply
        if not content and kind is ChannelEventKind.ERROR:
            content = result.error_code or "gateway_run_failed"
        if not content and kind is ChannelEventKind.CANCEL:
            content = result.error_code or "cancelled"
        content = self.public_projection.sanitize_content(content)
        return OutboundMessage(
            identity=message.identity,
            session_id=route.session_id,
            generation=generation,
            kind=kind,
            content=content,
            correlation_id=correlation_id,
            metadata={"run_id": result.run_id, "status": str(result.status)},
        )


__all__ = ["ApplicationRunner", "ChannelBridge"]
