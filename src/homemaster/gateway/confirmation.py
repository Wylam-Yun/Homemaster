"""Feishu approval boundary for structured household decisions.

V3.4 issues no interactive Feishu cards: ``confirm()`` validates the
session route ownership and then deterministically reports
``approval_channel_unavailable`` (``None``), so physical calls never
start from Feishu in this version. ``resolve()`` answers ``UNKNOWN``
because no V3.4 card exists for a callback to complete. The Web and CLI
channels carry the complete interactive flow.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from homemaster.channels.contracts import DeliveryReceipt


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    DENY = "deny"


class ApprovalResolveStatus(StrEnum):
    RESOLVED = "resolved"
    UNKNOWN = "unknown"
    UNAUTHORIZED = "unauthorized"
    STALE = "stale"


@dataclass(frozen=True)
class ApprovalRequest:
    """Legacy card payload kept for the channel renderer.

    V3.4 issues no Feishu cards for household approvals, so the gateway
    handler never constructs one; the type remains for the existing
    channel rendering path and its tests.
    """

    approval_id: str
    tool_name: str
    arguments: dict[str, object]
    cwd: str
    reason: str
    subject_id: str


@dataclass(frozen=True)
class FeishuApprovalRoute:
    session_id: str
    generation: int
    expected_open_chat_id: str
    requester_open_id: str
    notify: Callable[[Any], Awaitable[DeliveryReceipt]]
    update: Callable[[str, str, str], Awaitable[DeliveryReceipt]]

    def __post_init__(self) -> None:
        for label, value in (
            ("session_id", self.session_id),
            ("expected_open_chat_id", self.expected_open_chat_id),
            ("requester_open_id", self.requester_open_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be a non-empty string")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise TypeError("generation must be an integer")
        if self.generation < 1:
            raise ValueError("generation must be positive")
        if not callable(self.notify) or not callable(self.update):
            raise TypeError("approval route notify and update must be callable")


class FeishuGatewayConfirmationHandler:
    """Own Feishu session routes; never start physical calls from Feishu."""

    def __init__(self) -> None:
        self._routes: dict[str, FeishuApprovalRoute] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def pending_count(self) -> int:
        return 0

    def bind_session(self, route: FeishuApprovalRoute) -> None:
        if not isinstance(route, FeishuApprovalRoute):
            raise TypeError("route must be FeishuApprovalRoute")
        if self._closed:
            raise RuntimeError("confirmation handler is closed")
        current = self._routes.get(route.session_id)
        if current is not None and route.generation < current.generation:
            raise ValueError("stale Feishu approval route generation")
        self._routes[route.session_id] = route

    async def unbind_session(self, session_id: str, generation: int) -> None:
        async with self._lock:
            route = self._routes.get(session_id)
            if route is not None and route.generation == generation:
                self._routes.pop(session_id, None)

    async def confirm(
        self,
        request: Any,
        missing_item_ids: Any,
        context: Any,
    ) -> None:
        """Validate ownership, then report the channel as unavailable.

        Returning ``None`` tells the executor to answer
        ``approval_channel_unavailable`` without side effects. An old
        Feishu "agree" must never expand into long-term item permissions,
        so no card is sent and nothing is waited on here.
        """
        del request, missing_item_ids
        # The bound session route is still consulted so unbound or stale
        # sessions stay visibly distinct in audits, but the answer is
        # unavailable either way: no card may start a call.
        session_id = str(context.metadata.get("session_id", ""))
        async with self._lock:
            route = self._routes.get(session_id)
        del route
        return None

    async def resolve(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        *,
        operator_open_id: str,
        open_chat_id: str,
        open_message_id: str,
    ) -> ApprovalResolveStatus:
        """No V3.4 card exists, so callbacks complete nothing."""
        del approval_id, decision, operator_open_id, open_chat_id, open_message_id
        return ApprovalResolveStatus.UNKNOWN

    async def aclose(self, *, deadline: float | None = None) -> bool:
        del deadline
        async with self._lock:
            self._closed = True
            self._routes.clear()
        return True


__all__ = [
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalResolveStatus",
    "FeishuApprovalRoute",
    "FeishuGatewayConfirmationHandler",
]
