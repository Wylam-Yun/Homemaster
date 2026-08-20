from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from homemaster.channels.contracts import DeliveryReceipt, DeliveryStatus
from homemaster.gateway.confirmation import (
    ApprovalDecision,
    ApprovalResolveStatus,
    FeishuApprovalRoute,
    FeishuGatewayConfirmationHandler,
)
from homemaster.tools import ToolExecutionContext
from homemaster.tools.contracts import PermissionSubject


class _EventSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.events = []
        self.fail = fail

    async def aemit(self, event) -> None:
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.events.append(event)


class _BlockingEventSink:
    def __init__(self, *, block_on_call: int) -> None:
        self.block_on_call = block_on_call
        self.calls = 0
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()

    async def aemit(self, event) -> None:
        self.calls += 1
        if self.calls != self.block_on_call:
            return
        self.blocked.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            await self.release.wait()


class _Deadline:
    def __init__(self, remaining: float) -> None:
        self.remaining = remaining

    def remaining_s(self) -> float:
        return self.remaining


class _Transport:
    def __init__(self) -> None:
        self.requests = []
        self.updates = []
        self.message_id = "om-approval"
        self.send_status = DeliveryStatus.CONFIRMED_SUCCESS
        self.send_gate: asyncio.Event | None = None
        self.platform_ids: tuple[str, ...] | None = None
        self.update_status = DeliveryStatus.CONFIRMED_SUCCESS

    async def notify(self, request):
        self.requests.append(request)
        if self.send_gate is not None:
            await self.send_gate.wait()
        if self.send_status is DeliveryStatus.CONFIRMED_SUCCESS:
            return DeliveryReceipt(
                status=self.send_status,
                operation="feishu.approval.send",
                platform_ids=(self.message_id,) if self.platform_ids is None else self.platform_ids,
                sent_count=1,
            )
        return DeliveryReceipt(
            status=self.send_status,
            operation="feishu.approval.send",
            failed_count=1,
        )

    async def update(self, message_id: str, outcome: str, actor: str):
        self.updates.append((message_id, outcome, actor))
        return DeliveryReceipt(
            status=self.update_status,
            operation="feishu.approval.update",
            platform_ids=(message_id,),
            sent_count=1 if self.update_status is DeliveryStatus.CONFIRMED_SUCCESS else 0,
            failed_count=1 if self.update_status is DeliveryStatus.CONFIRMED_FAILURE else 0,
        )


def _route(
    transport: _Transport,
    *,
    session_id: str = "session-1",
    generation: int = 7,
) -> FeishuApprovalRoute:
    return FeishuApprovalRoute(
        session_id=session_id,
        generation=generation,
        expected_open_chat_id="oc-chat",
        requester_open_id="ou-requester",
        notify=transport.notify,
        update=transport.update,
    )


def _context(
    tmp_path: Path,
    sink: _EventSink,
    *,
    session_id: str = "session-1",
    generation: int = 7,
    deadline: _Deadline | None = None,
) -> ToolExecutionContext:
    metadata = {
        "session_id": session_id,
        "run_id": "run-1",
        "turn_index": 2,
        "tool_call_id": "call-1",
        "gateway_generation": generation,
        "permission_subject": PermissionSubject(
            "feishu-owner",
            "feishu",
            tenant_id="local",
            capabilities=(),
        ),
        "run_context": SimpleNamespace(event_sink=sink),
    }
    if deadline is not None:
        metadata["deadline"] = deadline
    return ToolExecutionContext(tmp_path, metadata=metadata)


async def _start_confirmation(handler, transport, context):
    task = asyncio.create_task(
        handler.confirm(
            SimpleNamespace(name="write_file"),
            {"path": "exact.txt", "content": "exact value"},
            context,
            SimpleNamespace(reason="confirmation required"),
        )
    )
    for _ in range(200):
        if transport.requests and handler.pending_count:
            # The notifier records before its receipt is consumed. Let the handler
            # complete the deliberate SENDING -> WAITING transition before click.
            for _settle in range(3):
                await asyncio.sleep(0)
            return task, transport.requests[0]
        await asyncio.sleep(0)
    raise AssertionError("confirmation was not sent")


@pytest.mark.asyncio
async def test_approve_resolves_exact_pending_call_and_audits(tmp_path: Path) -> None:
    sink = _EventSink()
    transport = _Transport()
    handler = FeishuGatewayConfirmationHandler(timeout_s=1)
    handler.bind_session(_route(transport))
    task, request = await _start_confirmation(handler, transport, _context(tmp_path, sink))

    status = await handler.resolve(
        request.approval_id,
        ApprovalDecision.APPROVE,
        operator_open_id="ou-requester",
        open_chat_id="oc-chat",
        open_message_id="om-approval",
    )

    assert status is ApprovalResolveStatus.RESOLVED
    assert await task is True
    assert handler.pending_count == 0
    assert transport.updates == [("om-approval", "approved", "ou-requester")]
    assert request.arguments == {"path": "exact.txt", "content": "exact value"}
    assert [event.type for event in sink.events] == [
        "permission.confirmation_requested",
        "permission.confirmation_completed",
    ]
    assert sink.events[0].payload == {
        "approval_id": request.approval_id,
        "arguments": {"path": "exact.txt", "content": "exact value"},
        "cwd": str(tmp_path.resolve()),
        "reason": "confirmation required",
        "subject_id": "feishu-owner",
    }
    assert sink.events[1].payload == {
        "approval_id": request.approval_id,
        "approved": True,
        "outcome": "approved",
        "subject_id": "feishu-owner",
    }


@pytest.mark.asyncio
async def test_deny_returns_false(tmp_path: Path) -> None:
    transport = _Transport()
    handler = FeishuGatewayConfirmationHandler(timeout_s=1)
    handler.bind_session(_route(transport))
    task, request = await _start_confirmation(
        handler, transport, _context(tmp_path, _EventSink())
    )

    assert (
        await handler.resolve(
            request.approval_id,
            ApprovalDecision.DENY,
            operator_open_id="ou-requester",
            open_chat_id="oc-chat",
            open_message_id="om-approval",
        )
        is ApprovalResolveStatus.RESOLVED
    )
    assert await task is False
    assert transport.updates == [("om-approval", "denied", "ou-requester")]


@pytest.mark.asyncio
async def test_unknown_duplicate_and_wrong_identity_resolve_nothing(tmp_path: Path) -> None:
    transport = _Transport()
    handler = FeishuGatewayConfirmationHandler(timeout_s=1)
    handler.bind_session(_route(transport))
    task, request = await _start_confirmation(
        handler, transport, _context(tmp_path, _EventSink())
    )
    common = {
        "approval_id": request.approval_id,
        "decision": ApprovalDecision.APPROVE,
        "operator_open_id": "ou-requester",
        "open_chat_id": "oc-chat",
        "open_message_id": "om-approval",
    }

    assert (
        await handler.resolve(**{**common, "operator_open_id": "ou-other"})
        is ApprovalResolveStatus.UNAUTHORIZED
    )
    assert (
        await handler.resolve(**{**common, "open_chat_id": "oc-other"})
        is ApprovalResolveStatus.UNAUTHORIZED
    )
    assert (
        await handler.resolve(**{**common, "open_message_id": "om-other"})
        is ApprovalResolveStatus.UNAUTHORIZED
    )
    assert task.done() is False
    assert transport.updates == []
    assert await handler.resolve(**common) is ApprovalResolveStatus.RESOLVED
    assert await task is True
    assert await handler.resolve(**common) is ApprovalResolveStatus.UNKNOWN
    assert (
        await handler.resolve(
            "missing",
            ApprovalDecision.APPROVE,
            operator_open_id="ou-requester",
            open_chat_id="oc-chat",
            open_message_id="om-approval",
        )
        is ApprovalResolveStatus.UNKNOWN
    )


@pytest.mark.asyncio
async def test_notify_failure_denies_without_waiting(tmp_path: Path) -> None:
    transport = _Transport()
    transport.send_status = DeliveryStatus.CONFIRMED_FAILURE
    handler = FeishuGatewayConfirmationHandler(timeout_s=1)
    handler.bind_session(_route(transport))

    approved = await handler.confirm(
        SimpleNamespace(name="write_file"),
        {},
        _context(tmp_path, _EventSink()),
        SimpleNamespace(reason="confirmation required"),
    )

    assert approved is False
    assert handler.pending_count == 0
    assert transport.updates == []


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_ids", [(), ("om-one", "om-two")])
async def test_confirmed_send_requires_exactly_one_message_id(
    tmp_path: Path,
    platform_ids: tuple[str, ...],
) -> None:
    transport = _Transport()
    transport.platform_ids = platform_ids
    handler = FeishuGatewayConfirmationHandler(timeout_s=1)
    handler.bind_session(_route(transport))

    approved = await handler.confirm(
        SimpleNamespace(name="write_file"),
        {},
        _context(tmp_path, _EventSink()),
        SimpleNamespace(reason="confirmation required"),
    )

    assert approved is False
    assert handler.pending_count == 0
    assert transport.updates == []


@pytest.mark.asyncio
async def test_callback_while_card_is_sending_is_stale(tmp_path: Path) -> None:
    transport = _Transport()
    transport.send_gate = asyncio.Event()
    handler = FeishuGatewayConfirmationHandler(timeout_s=1)
    handler.bind_session(_route(transport))
    task = asyncio.create_task(
        handler.confirm(
            SimpleNamespace(name="write_file"),
            {},
            _context(tmp_path, _EventSink()),
            SimpleNamespace(reason="confirmation required"),
        )
    )
    for _ in range(200):
        if transport.requests:
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("confirmation send did not start")
    request = transport.requests[0]

    assert (
        await handler.resolve(
            request.approval_id,
            ApprovalDecision.APPROVE,
            operator_open_id="ou-requester",
            open_chat_id="oc-chat",
            open_message_id="om-approval",
        )
        is ApprovalResolveStatus.STALE
    )
    assert task.done() is False

    transport.send_gate.set()
    for _ in range(200):
        if handler.pending_count:
            await asyncio.sleep(0)
            status = await handler.resolve(
                request.approval_id,
                ApprovalDecision.DENY,
                operator_open_id="ou-requester",
                open_chat_id="oc-chat",
                open_message_id="om-approval",
            )
            if status is ApprovalResolveStatus.RESOLVED:
                break
        await asyncio.sleep(0)
    else:
        raise AssertionError("confirmation did not enter waiting state")
    assert await task is False


@pytest.mark.asyncio
async def test_send_and_wait_share_run_deadline(tmp_path: Path) -> None:
    transport = _Transport()
    transport.send_gate = asyncio.Event()
    handler = FeishuGatewayConfirmationHandler(timeout_s=1)
    handler.bind_session(_route(transport))

    started = asyncio.get_running_loop().time()
    approved = await handler.confirm(
        SimpleNamespace(name="write_file"),
        {},
        _context(tmp_path, _EventSink(), deadline=_Deadline(0.02)),
        SimpleNamespace(reason="confirmation required"),
    )

    assert approved is False
    assert asyncio.get_running_loop().time() - started < 0.2
    assert handler.pending_count == 0


@pytest.mark.asyncio
async def test_late_send_is_reconciled_and_repeated_close_waits_same_work(
    tmp_path: Path,
) -> None:
    transport = _Transport()
    transport.send_gate = asyncio.Event()
    handler = FeishuGatewayConfirmationHandler(timeout_s=0.01)
    handler.bind_session(_route(transport))

    approved = await handler.confirm(
        SimpleNamespace(name="write_file"),
        {},
        _context(tmp_path, _EventSink()),
        SimpleNamespace(reason="confirmation required"),
    )

    assert approved is False
    assert handler.pending_count == 0
    loop = asyncio.get_running_loop()
    assert await handler.aclose(deadline=loop.time() + 0.01) is False
    assert transport.updates == []

    transport.send_gate.set()
    assert await handler.aclose(deadline=loop.time() + 1) is True
    assert transport.updates == [("om-approval", "expired", "system")]


@pytest.mark.asyncio
async def test_blocking_requested_audit_cannot_exceed_confirmation_deadline(
    tmp_path: Path,
) -> None:
    sink = _BlockingEventSink(block_on_call=1)
    transport = _Transport()
    handler = FeishuGatewayConfirmationHandler(timeout_s=1)
    handler.bind_session(_route(transport))

    started = asyncio.get_running_loop().time()
    approved = await handler.confirm(
        SimpleNamespace(name="write_file"),
        {},
        _context(tmp_path, sink, deadline=_Deadline(0.02)),
        SimpleNamespace(reason="confirmation required"),
    )

    assert approved is False
    assert asyncio.get_running_loop().time() - started < 0.2
    assert handler.pending_count == 0
    assert transport.requests == []
    sink.release.set()
    assert await handler.aclose(deadline=asyncio.get_running_loop().time() + 1)


@pytest.mark.asyncio
async def test_blocking_completed_audit_does_not_reverse_approval(
    tmp_path: Path,
) -> None:
    sink = _BlockingEventSink(block_on_call=2)
    transport = _Transport()
    handler = FeishuGatewayConfirmationHandler(timeout_s=1)
    handler.bind_session(_route(transport))
    task, request = await _start_confirmation(
        handler,
        transport,
        _context(tmp_path, sink, deadline=_Deadline(0.05)),
    )

    assert (
        await handler.resolve(
            request.approval_id,
            ApprovalDecision.APPROVE,
            operator_open_id="ou-requester",
            open_chat_id="oc-chat",
            open_message_id="om-approval",
        )
        is ApprovalResolveStatus.RESOLVED
    )
    started = asyncio.get_running_loop().time()
    assert await task is True
    assert asyncio.get_running_loop().time() - started < 0.2
    assert handler.pending_count == 0
    sink.release.set()
    assert await handler.aclose(deadline=asyncio.get_running_loop().time() + 1)


@pytest.mark.asyncio
async def test_timeout_denies_updates_and_removes_pending(tmp_path: Path) -> None:
    transport = _Transport()
    handler = FeishuGatewayConfirmationHandler(timeout_s=0.01)
    handler.bind_session(_route(transport))

    approved = await handler.confirm(
        SimpleNamespace(name="write_file"),
        {},
        _context(tmp_path, _EventSink()),
        SimpleNamespace(reason="confirmation required"),
    )

    assert approved is False
    assert handler.pending_count == 0
    assert transport.updates == [("om-approval", "expired", "system")]


@pytest.mark.asyncio
async def test_cancel_removes_pending_updates_and_propagates(tmp_path: Path) -> None:
    transport = _Transport()
    handler = FeishuGatewayConfirmationHandler(timeout_s=1)
    handler.bind_session(_route(transport))
    task, _request = await _start_confirmation(
        handler, transport, _context(tmp_path, _EventSink())
    )

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert handler.pending_count == 0
    assert transport.updates == [("om-approval", "cancelled", "system")]


@pytest.mark.asyncio
async def test_unbind_denies_only_matching_generation(tmp_path: Path) -> None:
    first_transport = _Transport()
    second_transport = _Transport()
    first_transport.message_id = "om-first"
    second_transport.message_id = "om-second"
    handler = FeishuGatewayConfirmationHandler(timeout_s=1)
    handler.bind_session(_route(first_transport, generation=1))
    first, _ = await _start_confirmation(
        handler,
        first_transport,
        _context(tmp_path, _EventSink(), generation=1),
    )
    handler.bind_session(_route(second_transport, generation=2))
    second, request = await _start_confirmation(
        handler,
        second_transport,
        _context(tmp_path, _EventSink(), generation=2),
    )

    await handler.unbind_session("session-1", 1)
    assert await first is False
    assert second.done() is False
    assert (
        await handler.resolve(
            request.approval_id,
            ApprovalDecision.APPROVE,
            operator_open_id="ou-requester",
            open_chat_id="oc-chat",
            open_message_id="om-second",
        )
        is ApprovalResolveStatus.RESOLVED
    )
    assert await second is True


@pytest.mark.asyncio
async def test_stale_context_cannot_select_new_route(tmp_path: Path) -> None:
    transport = _Transport()
    handler = FeishuGatewayConfirmationHandler(timeout_s=1)
    handler.bind_session(_route(transport, generation=2))

    assert (
        await handler.confirm(
            SimpleNamespace(name="write_file"),
            {},
            _context(tmp_path, _EventSink(), generation=1),
            SimpleNamespace(reason="confirmation required"),
        )
        is False
    )
    assert transport.requests == []


@pytest.mark.asyncio
async def test_close_denies_every_pending_confirmation(tmp_path: Path) -> None:
    handler = FeishuGatewayConfirmationHandler(timeout_s=1)
    transports = [_Transport(), _Transport()]
    tasks = []
    for index, transport in enumerate(transports, start=1):
        transport.message_id = f"om-{index}"
        handler.bind_session(_route(transport, session_id=f"session-{index}"))
        task, _ = await _start_confirmation(
            handler,
            transport,
            _context(tmp_path, _EventSink(), session_id=f"session-{index}"),
        )
        tasks.append(task)

    await handler.aclose()

    assert await asyncio.gather(*tasks) == [False, False]
    assert handler.pending_count == 0
    assert [transport.updates for transport in transports] == [
        [("om-1", "closed", "system")],
        [("om-2", "closed", "system")],
    ]


@pytest.mark.asyncio
async def test_audit_failures_are_isolated_from_locked_decision(tmp_path: Path) -> None:
    transport = _Transport()
    handler = FeishuGatewayConfirmationHandler(timeout_s=1)
    handler.bind_session(_route(transport))
    task, request = await _start_confirmation(
        handler, transport, _context(tmp_path, _EventSink(fail=True))
    )

    assert (
        await handler.resolve(
            request.approval_id,
            ApprovalDecision.APPROVE,
            operator_open_id="ou-requester",
            open_chat_id="oc-chat",
            open_message_id="om-approval",
        )
        is ApprovalResolveStatus.RESOLVED
    )
    assert await task is True
    assert handler.pending_count == 0


@pytest.mark.asyncio
async def test_card_update_failure_does_not_reverse_locked_approval(tmp_path: Path) -> None:
    transport = _Transport()
    transport.update_status = DeliveryStatus.CONFIRMED_FAILURE
    handler = FeishuGatewayConfirmationHandler(timeout_s=1)
    handler.bind_session(_route(transport))
    task, request = await _start_confirmation(
        handler, transport, _context(tmp_path, _EventSink())
    )

    assert (
        await handler.resolve(
            request.approval_id,
            ApprovalDecision.APPROVE,
            operator_open_id="ou-requester",
            open_chat_id="oc-chat",
            open_message_id="om-approval",
        )
        is ApprovalResolveStatus.RESOLVED
    )
    assert await task is True
    assert transport.updates == [("om-approval", "approved", "ou-requester")]
