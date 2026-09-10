"""Feishu boundary tests: no cards, no starts, ownership kept."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from homemaster.gateway.confirmation import (
    ApprovalDecision,
    ApprovalResolveStatus,
    FeishuApprovalRoute,
    FeishuGatewayConfirmationHandler,
)
from homemaster.tools import ToolExecutionContext
from homemaster.tools.contracts import PermissionSubject


async def _notify(request):
    raise AssertionError("no Feishu card may be sent in V3.4")


async def _update(message_id: str, outcome: str, actor: str):
    raise AssertionError("no Feishu card may be updated in V3.4")


def _route(
    *,
    session_id: str = "session-1",
    generation: int = 7,
) -> FeishuApprovalRoute:
    return FeishuApprovalRoute(
        session_id=session_id,
        generation=generation,
        expected_open_chat_id="oc-chat",
        requester_open_id="ou-requester",
        notify=_notify,
        update=_update,
    )


def _context(
    tmp_path: Path,
    *,
    session_id: str = "session-1",
    generation: int = 7,
) -> ToolExecutionContext:
    return ToolExecutionContext(
        tmp_path,
        metadata={
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
        },
    )


def _request() -> SimpleNamespace:
    return SimpleNamespace(approval_id="approval-1", request_id="request-1")


@pytest.mark.asyncio
async def test_confirm_reports_unavailable_without_sending(tmp_path: Path) -> None:
    handler = FeishuGatewayConfirmationHandler()
    handler.bind_session(_route())
    assert await handler.confirm(_request(), ["item-1"], _context(tmp_path)) is None
    assert handler.pending_count == 0


@pytest.mark.asyncio
async def test_confirm_without_route_is_unavailable(tmp_path: Path) -> None:
    handler = FeishuGatewayConfirmationHandler()
    assert await handler.confirm(_request(), ["item-1"], _context(tmp_path)) is None


@pytest.mark.asyncio
async def test_confirm_with_stale_generation_is_unavailable(tmp_path: Path) -> None:
    handler = FeishuGatewayConfirmationHandler()
    handler.bind_session(_route(generation=7))
    assert await handler.confirm(_request(), ["item-1"], _context(tmp_path, generation=6)) is None


def test_bind_rejects_stale_generation() -> None:
    handler = FeishuGatewayConfirmationHandler()
    handler.bind_session(_route(generation=7))
    with pytest.raises(ValueError):
        handler.bind_session(_route(generation=6))


@pytest.mark.asyncio
async def test_unbind_removes_only_matching_generation() -> None:
    handler = FeishuGatewayConfirmationHandler()
    handler.bind_session(_route(generation=7))
    await handler.unbind_session("session-1", 6)
    assert handler._routes["session-1"].generation == 7
    await handler.unbind_session("session-1", 7)
    assert "session-1" not in handler._routes


@pytest.mark.asyncio
async def test_resolve_answers_unknown(tmp_path: Path) -> None:
    handler = FeishuGatewayConfirmationHandler()
    status = await handler.resolve(
        "approval-1",
        ApprovalDecision.APPROVE,
        operator_open_id="ou-requester",
        open_chat_id="oc-chat",
        open_message_id="om-1",
    )
    assert status is ApprovalResolveStatus.UNKNOWN


@pytest.mark.asyncio
async def test_close_clears_routes_and_blocks_rebind(tmp_path: Path) -> None:
    handler = FeishuGatewayConfirmationHandler()
    handler.bind_session(_route())
    assert await handler.aclose() is True
    assert await handler.confirm(_request(), ["item-1"], _context(tmp_path)) is None
    with pytest.raises(RuntimeError):
        handler.bind_session(_route())
