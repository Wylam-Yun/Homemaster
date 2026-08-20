from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from homemaster.tools import ToolExecutionContext
from homemaster.tools.contracts import PermissionSubject
from homemaster.web.confirmations import (
    ApprovalOutcome,
    UnknownApprovalError,
    WebConfirmationHandler,
)


class _EventSink:
    def __init__(self) -> None:
        self.events = []

    async def aemit(self, event) -> None:
        self.events.append(event)


def _context(
    tmp_path: Path,
    sink: _EventSink,
    *,
    session_id: str = "session-01",
) -> ToolExecutionContext:
    return ToolExecutionContext(
        tmp_path,
        metadata={
            "session_id": session_id,
            "run_id": "run-01",
            "turn_index": 2,
            "tool_call_id": "call-01",
            "permission_subject": PermissionSubject(
                "web-operator",
                "web",
                tenant_id="local",
                capabilities=(),
            ),
            "run_context": SimpleNamespace(event_sink=sink),
        },
    )


async def _wait_for_pending(handler: WebConfirmationHandler) -> None:
    for _ in range(100):
        if handler.pending_count:
            return
        await asyncio.sleep(0)
    raise AssertionError("approval was not registered")


@pytest.mark.asyncio
async def test_web_confirmation_waits_for_one_approved_resolution(tmp_path: Path) -> None:
    sink = _EventSink()
    handler = WebConfirmationHandler(timeout_s=1)
    task = asyncio.create_task(
        handler.confirm(
            SimpleNamespace(name="write_file"),
            {"path": "permission-test.txt", "content": "approved"},
            _context(tmp_path, sink),
            SimpleNamespace(reason="confirmation required"),
        )
    )
    await _wait_for_pending(handler)

    requested = sink.events[0]
    approval_id = requested.payload["approval_id"]
    assert requested.type == "permission.confirmation_requested"
    assert requested.session_id == "session-01"
    assert requested.run_id == "run-01"
    assert requested.tool_call_id == "call-01"
    assert requested.payload == {
        "approval_id": approval_id,
        "arguments": {"path": "permission-test.txt", "content": "approved"},
        "cwd": str(tmp_path.resolve()),
        "reason": "confirmation required",
        "subject_id": "web-operator",
    }

    assert await handler.resolve(approval_id, ApprovalOutcome.APPROVE) is True
    assert await task is True
    assert handler.pending_count == 0
    with pytest.raises(UnknownApprovalError):
        await handler.resolve(approval_id, ApprovalOutcome.REJECT)

    completed = sink.events[1]
    assert completed.type == "permission.confirmation_completed"
    assert completed.payload == {
        "approval_id": approval_id,
        "approved": True,
        "outcome": "approved",
        "subject_id": "web-operator",
    }


@pytest.mark.asyncio
async def test_web_confirmation_timeout_and_close_deny_without_leaks(tmp_path: Path) -> None:
    timeout_sink = _EventSink()
    timeout_handler = WebConfirmationHandler(timeout_s=0.01)
    timed_out = await timeout_handler.confirm(
        SimpleNamespace(name="write_file"),
        {},
        _context(tmp_path, timeout_sink),
        SimpleNamespace(reason="confirmation required"),
    )

    assert timed_out is False
    assert timeout_handler.pending_count == 0
    assert timeout_sink.events[-1].payload["outcome"] == "expired"

    close_sink = _EventSink()
    close_handler = WebConfirmationHandler(timeout_s=None)
    pending = asyncio.create_task(
        close_handler.confirm(
            SimpleNamespace(name="write_file"),
            {},
            _context(tmp_path, close_sink),
            SimpleNamespace(reason="confirmation required"),
        )
    )
    await _wait_for_pending(close_handler)
    await close_handler.aclose()

    assert await pending is False
    assert close_handler.pending_count == 0
    assert close_sink.events[-1].payload["outcome"] == "closed"


@pytest.mark.asyncio
async def test_disconnect_denies_only_matching_session_approvals(tmp_path: Path) -> None:
    handler = WebConfirmationHandler(timeout_s=None)
    first_sink = _EventSink()
    second_sink = _EventSink()
    first = asyncio.create_task(
        handler.confirm(
            SimpleNamespace(name="write_file"),
            {},
            _context(tmp_path, first_sink, session_id="session-01"),
            SimpleNamespace(reason="confirmation required"),
        )
    )
    second = asyncio.create_task(
        handler.confirm(
            SimpleNamespace(name="write_file"),
            {},
            _context(tmp_path, second_sink, session_id="session-02"),
            SimpleNamespace(reason="confirmation required"),
        )
    )
    for _ in range(100):
        if handler.pending_count == 2:
            break
        await asyncio.sleep(0)
    assert handler.pending_count == 2

    assert await handler.deny_session("session-01", outcome="disconnected") == 1
    assert await first is False
    assert second.done() is False
    assert handler.pending_count == 1
    assert first_sink.events[-1].payload["outcome"] == "disconnected"

    await handler.aclose()
    assert await second is False
