"""Application-owned Web approval Futures for permission confirmations."""

from __future__ import annotations

import asyncio
import inspect
import math
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from homemaster.events.runtime_events import RuntimeEvent


class ApprovalOutcome(StrEnum):
    """Typed browser choices accepted by the approval endpoint."""

    APPROVE = "approve"
    REJECT = "reject"


class UnknownApprovalError(LookupError):
    """The approval id is unknown, expired, or already consumed."""


@dataclass(frozen=True)
class _PendingApproval:
    future: asyncio.Future[str]
    session_id: str


class WebConfirmationHandler:
    """Suspend a gated tool call until one browser decision arrives."""

    def __init__(self, *, timeout_s: float | None = 300.0) -> None:
        if timeout_s is not None and (
            isinstance(timeout_s, bool) or not math.isfinite(timeout_s) or timeout_s <= 0
        ):
            raise ValueError("timeout_s must be a finite positive number or None")
        self._timeout_s = timeout_s
        self._lock = asyncio.Lock()
        self._pending: dict[str, _PendingApproval] = {}
        self._closed = False

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    async def confirm(
        self,
        tool: Any,
        arguments: dict[str, Any],
        context: Any,
        decision: Any,
    ) -> bool:
        """Publish one request and await its one-shot browser resolution."""

        approval_id = f"approval-{uuid.uuid4().hex}"
        future = asyncio.get_running_loop().create_future()
        session_id = str(context.metadata.get("session_id", ""))
        async with self._lock:
            if self._closed:
                return False
            self._pending[approval_id] = _PendingApproval(future, session_id)
        subject_id = _subject_id(context)
        await _emit_confirmation_event(
            context,
            event_type="permission.confirmation_requested",
            tool_name=str(tool.name),
            payload={
                "approval_id": approval_id,
                "arguments": arguments,
                "cwd": str(context.cwd),
                "reason": str(decision.reason),
                "subject_id": subject_id,
            },
        )

        try:
            if self._timeout_s is None:
                outcome = await future
            else:
                outcome = await asyncio.wait_for(
                    asyncio.shield(future),
                    timeout=self._timeout_s,
                )
        except TimeoutError:
            outcome = "expired"
        finally:
            async with self._lock:
                self._pending.pop(approval_id, None)

        approved = outcome == "approved"
        await _emit_confirmation_event(
            context,
            event_type="permission.confirmation_completed",
            tool_name=str(tool.name),
            payload={
                "approval_id": approval_id,
                "approved": approved,
                "outcome": outcome,
                "subject_id": subject_id,
            },
        )
        return approved

    async def resolve(self, approval_id: str, outcome: ApprovalOutcome) -> bool:
        """Consume one pending approval id and resume its tool call."""

        if not isinstance(approval_id, str) or not approval_id.strip():
            raise ValueError("approval_id must be a non-empty string")
        if not isinstance(outcome, ApprovalOutcome):
            raise TypeError("outcome must be ApprovalOutcome")
        async with self._lock:
            pending = self._pending.pop(approval_id, None)
            if pending is None:
                raise UnknownApprovalError(f"unknown approval id: {approval_id}")
            resolved = "approved" if outcome is ApprovalOutcome.APPROVE else "denied"
            pending.future.set_result(resolved)
            return outcome is ApprovalOutcome.APPROVE

    async def deny_session(self, session_id: str, *, outcome: str) -> int:
        """Deny pending approvals for exactly one disconnected browser session."""

        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if not isinstance(outcome, str) or not outcome.strip():
            raise ValueError("outcome must be a non-empty string")
        async with self._lock:
            selected = tuple(
                (approval_id, pending)
                for approval_id, pending in self._pending.items()
                if pending.session_id == session_id
            )
            for approval_id, _pending in selected:
                self._pending.pop(approval_id, None)
        for _approval_id, pending in selected:
            if not pending.future.done():
                pending.future.set_result(outcome)
        return len(selected)

    async def aclose(self) -> None:
        """Deny and release every pending approval exactly once."""

        async with self._lock:
            if self._closed:
                return
            self._closed = True
            pending = tuple(self._pending.values())
            self._pending.clear()
        for item in pending:
            if not item.future.done():
                item.future.set_result("closed")


def _subject_id(context: Any) -> str:
    subject = context.metadata.get("permission_subject")
    return str(getattr(subject, "subject_id", ""))


async def _emit_confirmation_event(
    context: Any,
    *,
    event_type: str,
    tool_name: str,
    payload: dict[str, Any],
) -> None:
    run_context = context.metadata.get("run_context")
    sink = getattr(run_context, "event_sink", None)
    if sink is None:
        return
    event = RuntimeEvent(
        type=event_type,
        session_id=str(context.metadata.get("session_id", "")),
        run_id=str(context.metadata.get("run_id", "")),
        turn_index=context.metadata.get("turn_index"),
        tool_call_id=str(context.metadata.get("tool_call_id", "")) or None,
        name=tool_name,
        payload=payload,
    )
    emit = getattr(sink, "aemit", None)
    if callable(emit):
        await emit(event)
        return
    value = sink.emit(event)
    if inspect.isawaitable(value):
        await value


__all__ = [
    "ApprovalOutcome",
    "UnknownApprovalError",
    "WebConfirmationHandler",
]
