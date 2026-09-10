"""Application-owned Web approvals for structured household decisions.

The browser never decides by itself: ``POST /api/approvals/{id}`` submits
per-item choices into the durable store, and the commit wakes exactly the
waiting call. Futures carry the wakeup only; the executor re-reads the
committed decision from the store.
"""

from __future__ import annotations

import asyncio
import inspect
import math
from dataclasses import dataclass
from typing import Any

from homemaster.events.runtime_events import RuntimeEvent
from homemaster.permissions.models import (
    ApprovalCancelled,
    ApprovalResolution,
    ApprovalSubmission,
)

PROTOCOL_VERSION = 2


@dataclass
class _PendingApproval:
    future: asyncio.Future[ApprovalResolution]
    session_id: str
    request_id: str
    context: Any


class WebConfirmationHandler:
    """Suspend a gated physical call until its structured decision commits."""

    def __init__(self, *, store: Any | None = None, timeout_s: float | None = 300.0) -> None:
        if timeout_s is not None and (
            isinstance(timeout_s, bool) or not math.isfinite(timeout_s) or timeout_s <= 0
        ):
            raise ValueError("timeout_s must be a finite positive number or None")
        self._store = store
        self._timeout_s = timeout_s
        self._lock = asyncio.Lock()
        self._pending: dict[str, _PendingApproval] = {}
        self._closed = False

    def bind_store(self, store: Any) -> None:
        if store is None:
            raise TypeError("store must not be None")
        if self._store is not None and self._store is not store:
            raise ValueError("WebConfirmationHandler is already bound to a store")
        self._store = store

    @property
    def store(self) -> Any | None:
        return self._store

    def _require_store(self) -> Any:
        if self._store is None:
            raise RuntimeError("WebConfirmationHandler has no PermissionStore")
        return self._store

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    async def confirm(
        self,
        request: Any,
        missing_item_ids: Any,
        context: Any,
    ) -> ApprovalResolution:
        """Publish one structured request and await its committed decision."""
        store = self._require_store()
        if self._closed:
            raise ApprovalCancelled("confirmation handler is closed")
        approval_id = request.approval_id
        wanted = set(missing_item_ids)
        items = [
            {
                "item_id": item.item_id,
                "display_name": item.display_name,
                "location": item.location,
                "action_label": item.action_label,
            }
            for item in request.requirements
            if item.item_id in wanted
        ]
        future = asyncio.get_running_loop().create_future()
        session_id = str(context.metadata.get("session_id", ""))
        async with self._lock:
            if self._closed:
                raise ApprovalCancelled("confirmation handler is closed")
            self._pending[approval_id] = _PendingApproval(
                future, session_id, request.request_id, context
            )
        await _emit_confirmation_event(
            context,
            event_type="permission.confirmation_requested",
            payload={
                "protocol_version": PROTOCOL_VERSION,
                "approval_id": approval_id,
                "request_id": request.request_id,
                "revision": request.revision,
                "intent_summary": request.intent_summary,
                "items": items,
                "expires_at": request.deadline_at,
                "request_status": "awaiting_approval",
                "subject_id": _subject_id(context),
            },
        )
        try:
            if self._timeout_s is None:
                resolution = await future
            else:
                resolution = await asyncio.wait_for(
                    asyncio.shield(future),
                    timeout=self._timeout_s,
                )
        except TimeoutError:
            async with self._lock:
                self._pending.pop(approval_id, None)
            try:
                store.cancel(request.request_id, "browser wait timed out")
            except Exception:
                pass
            await _emit_confirmation_event(
                context,
                event_type="permission.confirmation_completed",
                payload={
                    "protocol_version": PROTOCOL_VERSION,
                    "approval_id": approval_id,
                    "request_id": request.request_id,
                    "outcome": "expired",
                    "request_status": "cancelled",
                    "subject_id": _subject_id(context),
                },
            )
            raise ApprovalCancelled("browser did not decide in time") from None
        except asyncio.CancelledError:
            async with self._lock:
                self._pending.pop(approval_id, None)
            raise
        except ApprovalCancelled:
            async with self._lock:
                self._pending.pop(approval_id, None)
            await _emit_confirmation_event(
                context,
                event_type="permission.confirmation_completed",
                payload={
                    "protocol_version": PROTOCOL_VERSION,
                    "approval_id": approval_id,
                    "request_id": request.request_id,
                    "outcome": "cancelled",
                    "request_status": "cancelled",
                    "subject_id": _subject_id(context),
                },
            )
            raise
        async with self._lock:
            self._pending.pop(approval_id, None)
        await _emit_confirmation_event(
            context,
            event_type="permission.confirmation_completed",
            payload={
                "protocol_version": PROTOCOL_VERSION,
                "approval_id": approval_id,
                "request_id": resolution.request_id,
                "revision": request.revision,
                "request_status": resolution.request_status,
                "approved": resolution.request_status == "ready",
                "items": [
                    {"item_id": item.item_id, "choice": item.choice}
                    for item in resolution.items
                ],
                "subject_id": _subject_id(context),
            },
        )
        if resolution.persisted_grant_ids:
            await _emit_confirmation_event(
                context,
                event_type="permission.grant_changed",
                payload={
                    "request_id": resolution.request_id,
                    "grant_ids": list(resolution.persisted_grant_ids),
                    "subject_id": _subject_id(context),
                },
            )
        return resolution

    async def resolve(
        self,
        approval_id: str,
        submission: ApprovalSubmission,
        actor: str,
    ) -> ApprovalResolution:
        """Submit one browser decision; the commit wakes the waiting call."""
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise ValueError("approval_id must be a non-empty string")
        if not isinstance(submission, ApprovalSubmission):
            raise TypeError("submission must be ApprovalSubmission")
        resolution = self._require_store().submit(approval_id, submission, actor)
        async with self._lock:
            pending = self._pending.pop(approval_id, None)
        if pending is not None and not pending.future.done():
            pending.future.set_result(resolution)
        return resolution

    async def cancel_approval(
        self, approval_id: str, submission_id: str, request_revision: int
    ) -> str:
        """Atomically cancel one pending approval without recording choices."""
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise ValueError("approval_id must be a non-empty string")
        store = self._require_store()
        stored = store.get_request(approval_id)
        if stored.revision != request_revision:
            from homemaster.permissions.models import ApprovalConflict

            raise ApprovalConflict("request revision changed; reload before cancelling")
        status = store.cancel(stored.request_id, "browser card closed")
        async with self._lock:
            pending = self._pending.pop(approval_id, None)
        if pending is not None and not pending.future.done():
            pending.future.set_exception(
                ApprovalCancelled(f"approval {approval_id} was cancelled")
            )
        del submission_id
        return status

    async def deny_session(self, session_id: str) -> int:
        """Cancel pending approvals for exactly one disconnected session."""
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        store = self._store
        async with self._lock:
            selected = tuple(
                (approval_id, pending)
                for approval_id, pending in self._pending.items()
                if pending.session_id == session_id
            )
            for approval_id, _pending in selected:
                self._pending.pop(approval_id, None)
        for _approval_id, pending in selected:
            if store is not None:
                try:
                    store.cancel(pending.request_id, "browser disconnected")
                except Exception:
                    pass
            if not pending.future.done():
                pending.future.set_exception(
                    ApprovalCancelled("browser disconnected")
                )
        return len(selected)

    async def aclose(self) -> None:
        """Cancel and release every pending approval exactly once."""
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            pending = tuple(self._pending.values())
            self._pending.clear()
        store = self._store
        for item in pending:
            if store is not None:
                try:
                    store.cancel(item.request_id, "confirmation handler closed")
                except Exception:
                    pass
            if not item.future.done():
                item.future.set_exception(ApprovalCancelled("handler closed"))


def _subject_id(context: Any) -> str:
    subject = context.metadata.get("permission_subject")
    return str(getattr(subject, "subject_id", ""))


async def _emit_confirmation_event(
    context: Any,
    *,
    event_type: str,
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
        name=None,
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
    "ApprovalCancelled",
    "WebConfirmationHandler",
]
