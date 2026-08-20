"""Application-owned Feishu approval Futures for Gateway tool confirmations."""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from homemaster.channels.contracts import DeliveryReceipt, DeliveryStatus
from homemaster.events.logger import get_logger
from homemaster.events.runtime_events import RuntimeEvent


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
    approval_id: str
    tool_name: str
    arguments: Mapping[str, object]
    cwd: str
    reason: str
    subject_id: str


@dataclass(frozen=True)
class FeishuApprovalRoute:
    session_id: str
    generation: int
    expected_open_chat_id: str
    requester_open_id: str
    notify: Callable[[ApprovalRequest], Awaitable[DeliveryReceipt]]
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


class _PendingState(StrEnum):
    SENDING = "sending"
    WAITING = "waiting"
    TERMINAL = "terminal"


@dataclass
class _PendingApproval:
    request: ApprovalRequest
    route: FeishuApprovalRoute
    future: asyncio.Future[tuple[str, str]]
    state: _PendingState = _PendingState.SENDING
    message_id: str | None = None
    notify_task: asyncio.Task[DeliveryReceipt] | None = None
    reconcile_task: asyncio.Task[None] | None = None
    outcome: str | None = None
    actor: str = "system"


class FeishuGatewayConfirmationHandler:
    """Suspend an exact Gateway tool call until its Feishu card is resolved."""

    def __init__(self, *, timeout_s: float = 300.0, update_timeout_s: float = 5.0) -> None:
        for label, value in (("timeout_s", timeout_s), ("update_timeout_s", update_timeout_s)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{label} must be a number")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{label} must be finite and positive")
        self._timeout_s = float(timeout_s)
        self._update_timeout_s = float(update_timeout_s)
        self._routes: dict[str, FeishuApprovalRoute] = {}
        self._pending: dict[str, _PendingApproval] = {}
        self._lock = asyncio.Lock()
        self._closed = False
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._close_task: asyncio.Task[None] | None = None

    @property
    def pending_count(self) -> int:
        return len(self._pending)

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
            selected = tuple(
                pending
                for pending in self._pending.values()
                if pending.route.session_id == session_id
                and pending.route.generation == generation
            )
        for pending in selected:
            await self._finish(pending, "session_replaced", "system")

    async def confirm(
        self,
        tool: Any,
        arguments: dict[str, Any],
        context: Any,
        decision: Any,
    ) -> bool:
        session_id = str(context.metadata.get("session_id", ""))
        generation = context.metadata.get("gateway_generation")
        route = self._routes.get(session_id)
        if (
            self._closed
            or route is None
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or route.generation != generation
        ):
            return False

        approval_id = uuid.uuid4().hex
        subject_id = _subject_id(context)
        request = ApprovalRequest(
            approval_id=approval_id,
            tool_name=str(tool.name),
            arguments=_copy_mapping(arguments),
            cwd=str(context.cwd),
            reason=str(decision.reason),
            subject_id=subject_id,
        )
        pending = _PendingApproval(
            request=request,
            route=route,
            future=asyncio.get_running_loop().create_future(),
        )
        deadline = _absolute_deadline(context, self._timeout_s)
        async with self._lock:
            current = self._routes.get(session_id)
            if self._closed or current is not route or current.generation != generation:
                return False
            self._pending[approval_id] = pending

        await self._emit_bounded(
            _emit_confirmation_event(
                context,
                event_type="permission.confirmation_requested",
                tool_name=request.tool_name,
                payload={
                    "approval_id": approval_id,
                    "arguments": dict(request.arguments),
                    "cwd": request.cwd,
                    "reason": request.reason,
                    "subject_id": subject_id,
                },
            ),
            deadline,
            "permission.confirmation_requested",
        )

        outcome = "send_failed"
        actor = "system"
        try:
            if deadline <= asyncio.get_running_loop().time():
                await self._finish(pending, "expired", "system")
            else:
                pending.notify_task = asyncio.create_task(
                    route.notify(request),
                    name=f"feishu-approval-send:{approval_id}",
                )
                receipt = await self._await_notification(pending, deadline)
                if receipt is not None:
                    message_id = _confirmed_message_id(receipt)
                    if message_id is None:
                        await self._finish(pending, "send_failed", "system")
                    else:
                        terminal_outcome: str | None = None
                        terminal_actor = "system"
                        async with self._lock:
                            pending.message_id = message_id
                            if pending.state is _PendingState.SENDING:
                                pending.state = _PendingState.WAITING
                            else:
                                terminal_outcome = pending.outcome
                                terminal_actor = pending.actor
                        if terminal_outcome is not None:
                            await self._update_card(
                                pending,
                                terminal_outcome,
                                terminal_actor,
                            )
                        elif deadline <= asyncio.get_running_loop().time():
                            await self._finish(pending, "expired", "system")
                        else:
                            try:
                                async with asyncio.timeout_at(deadline):
                                    await asyncio.shield(pending.future)
                            except TimeoutError:
                                await self._finish(pending, "expired", "system")
            outcome, actor = await pending.future
        except asyncio.CancelledError:
            await self._finish(pending, "cancelled", "system")
            outcome = "cancelled"
            await self._emit_completed(context, request, outcome, deadline)
            raise
        finally:
            async with self._lock:
                if self._pending.get(approval_id) is pending:
                    self._pending.pop(approval_id, None)

        await self._emit_completed(context, request, outcome, deadline)
        return outcome == "approved"

    async def resolve(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        *,
        operator_open_id: str,
        open_chat_id: str,
        open_message_id: str,
    ) -> ApprovalResolveStatus:
        if not isinstance(decision, ApprovalDecision):
            return ApprovalResolveStatus.UNKNOWN
        async with self._lock:
            pending = self._pending.get(approval_id)
            if pending is None:
                return ApprovalResolveStatus.UNKNOWN
            if pending.state is not _PendingState.WAITING or pending.message_id is None:
                return ApprovalResolveStatus.STALE
            current = self._routes.get(pending.route.session_id)
            if current is not pending.route or current.generation != pending.route.generation:
                return ApprovalResolveStatus.STALE
            if (
                operator_open_id != pending.route.requester_open_id
                or open_chat_id != pending.route.expected_open_chat_id
                or open_message_id != pending.message_id
            ):
                return ApprovalResolveStatus.UNAUTHORIZED
        outcome = "approved" if decision is ApprovalDecision.APPROVE else "denied"
        completed = await self._finish(pending, outcome, operator_open_id)
        return ApprovalResolveStatus.RESOLVED if completed else ApprovalResolveStatus.UNKNOWN

    async def aclose(self, *, deadline: float | None = None) -> bool:
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                self._close_impl(), name="feishu-confirmation:close"
            )
        close_task = self._close_task
        if deadline is None:
            await asyncio.shield(close_task)
            return True
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        done, _ = await asyncio.wait((close_task,), timeout=remaining)
        if close_task not in done:
            return False
        close_task.result()
        return True

    async def _close_impl(self) -> None:
        async with self._lock:
            if not self._closed:
                self._closed = True
                self._routes.clear()
                selected = tuple(self._pending.values())
            else:
                selected = ()
        for pending in selected:
            await self._finish(pending, "closed", "system")
        while self._background_tasks:
            await asyncio.gather(*tuple(self._background_tasks), return_exceptions=True)

    async def _await_notification(
        self,
        pending: _PendingApproval,
        deadline: float,
    ) -> DeliveryReceipt | None:
        notify_task = pending.notify_task
        assert notify_task is not None
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            await self._finish(pending, "expired", "system")
            return None
        done, _ = await asyncio.wait(
            (notify_task, pending.future),
            timeout=remaining,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if pending.future in done:
            if not notify_task.done():
                self._start_reconciliation(pending)
            return None
        if notify_task not in done:
            await self._finish(pending, "expired", "system")
            return None
        try:
            return notify_task.result()
        except asyncio.CancelledError:
            if pending.future.done():
                return None
            raise
        except Exception:
            await self._finish(pending, "send_failed", "system")
            return None

    async def _finish(
        self,
        pending: _PendingApproval,
        outcome: str,
        actor: str,
        *,
        update: bool = True,
        update_deadline: float | None = None,
    ) -> bool:
        async with self._lock:
            if pending.state is _PendingState.TERMINAL:
                return False
            pending.state = _PendingState.TERMINAL
            pending.outcome = outcome
            pending.actor = actor
            if self._pending.get(pending.request.approval_id) is pending:
                self._pending.pop(pending.request.approval_id, None)
            notify_task = pending.notify_task
            if not pending.future.done():
                pending.future.set_result((outcome, actor))
        if notify_task is not None and not notify_task.done():
            self._start_reconciliation(pending)
        if update and pending.message_id is not None:
            await self._update_card(pending, outcome, actor, deadline=update_deadline)
        return True

    def _start_reconciliation(self, pending: _PendingApproval) -> None:
        if pending.reconcile_task is not None:
            return
        task = asyncio.create_task(
            self._reconcile_late_notification(pending),
            name=f"feishu-approval-reconcile:{pending.request.approval_id}",
        )
        pending.reconcile_task = task
        self._track_background(task)

    async def _reconcile_late_notification(self, pending: _PendingApproval) -> None:
        notify_task = pending.notify_task
        if notify_task is None:
            return
        try:
            receipt = await asyncio.shield(notify_task)
        except (asyncio.CancelledError, Exception):
            return
        message_id = _confirmed_message_id(receipt)
        if message_id is None:
            return
        async with self._lock:
            pending.message_id = message_id
            outcome = pending.outcome or "closed"
            actor = pending.actor
        await self._update_card(pending, outcome, actor)

    def _track_background(self, task: asyncio.Task[Any]) -> None:
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _emit_bounded(
        self,
        emission: Awaitable[None],
        deadline: float,
        event_type: str,
    ) -> None:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            if inspect.iscoroutine(emission):
                emission.close()
            return
        task = asyncio.create_task(emission, name=f"confirmation-audit:{event_type}")
        self._track_background(task)
        done, _ = await asyncio.wait((task,), timeout=remaining)
        if task in done:
            task.result()
            return
        task.cancel()
        get_logger().warning(
            json.dumps(
                {
                    "event": "permission.confirmation_audit_failed",
                    "event_type": event_type,
                    "exception_type": "TimeoutError",
                },
                sort_keys=True,
            )
        )

    async def _emit_completed(
        self,
        context: Any,
        request: ApprovalRequest,
        outcome: str,
        deadline: float,
    ) -> None:
        await self._emit_bounded(
            _completed_emission(context, request, outcome),
            deadline,
            "permission.confirmation_completed",
        )

    async def _update_card(
        self,
        pending: _PendingApproval,
        outcome: str,
        actor: str,
        *,
        deadline: float | None = None,
    ) -> None:
        message_id = pending.message_id
        if message_id is None:
            return
        timeout = self._update_timeout_s
        if deadline is not None:
            timeout = min(timeout, max(0.0, deadline - asyncio.get_running_loop().time()))
        if timeout <= 0:
            return
        try:
            async with asyncio.timeout(timeout):
                receipt = await pending.route.update(message_id, outcome, actor)
            if not isinstance(receipt, DeliveryReceipt) or (
                receipt.status is not DeliveryStatus.CONFIRMED_SUCCESS
            ):
                raise RuntimeError("approval card update was not confirmed")
        except Exception as exc:
            get_logger().warning(
                json.dumps(
                    {
                        "event": "permission.confirmation_card_update_failed",
                        "approval_id": pending.request.approval_id,
                        "exception_type": type(exc).__name__,
                    },
                    sort_keys=True,
                )
            )


def _confirmed_message_id(receipt: object) -> str | None:
    if not isinstance(receipt, DeliveryReceipt):
        return None
    if receipt.status is not DeliveryStatus.CONFIRMED_SUCCESS:
        return None
    if len(receipt.platform_ids) != 1:
        return None
    message_id = receipt.platform_ids[0]
    return message_id if isinstance(message_id, str) and message_id.strip() else None


def _absolute_deadline(context: Any, timeout_s: float) -> float:
    loop = asyncio.get_running_loop()
    budget = timeout_s
    deadline = context.metadata.get("deadline")
    remaining_fn = getattr(deadline, "remaining_s", None)
    if callable(remaining_fn):
        remaining = remaining_fn()
        if remaining is not None:
            if isinstance(remaining, bool) or not isinstance(remaining, (int, float)):
                return loop.time()
            if not math.isfinite(remaining):
                return loop.time()
            budget = min(budget, float(remaining))
    return loop.time() + max(0.0, budget)


def _copy_mapping(arguments: Mapping[str, Any]) -> dict[str, object]:
    return {str(key): _copy_json(value) for key, value in arguments.items()}


def _copy_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _copy_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_json(item) for item in value]
    return value


def _subject_id(context: Any) -> str:
    subject = context.metadata.get("permission_subject")
    return str(getattr(subject, "subject_id", ""))


async def _completed_emission(
    context: Any, request: ApprovalRequest, outcome: str
) -> None:
    await _emit_confirmation_event(
        context,
        event_type="permission.confirmation_completed",
        tool_name=request.tool_name,
        payload={
            "approval_id": request.approval_id,
            "approved": outcome == "approved",
            "outcome": outcome,
            "subject_id": request.subject_id,
        },
    )


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
        gateway_generation=context.metadata.get("gateway_generation"),
    )
    try:
        emit = getattr(sink, "aemit", None)
        if callable(emit):
            await emit(event)
            return
        value = sink.emit(event)
        if inspect.isawaitable(value):
            await value
    except Exception as exc:
        get_logger().warning(
            json.dumps(
                {
                    "event": "permission.confirmation_audit_failed",
                    "event_type": event_type,
                    "exception_type": type(exc).__name__,
                },
                sort_keys=True,
            )
        )


__all__ = [
    "ApprovalDecision",
    "ApprovalRequest",
    "ApprovalResolveStatus",
    "FeishuApprovalRoute",
    "FeishuGatewayConfirmationHandler",
]
