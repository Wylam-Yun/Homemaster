"""Structured web confirmation tests against a real store."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from perm_harness import EventSink, FakeClock, make_context, make_request, wait_pending

from homemaster.permissions.models import (
    ApprovalCancelled,
    ApprovalSubmission,
    ItemDecision,
)
from homemaster.permissions.store import PermissionStore
from homemaster.web.confirmations import WebConfirmationHandler


def _open(tmp_path: Path, clock: FakeClock) -> PermissionStore:
    return PermissionStore.open(tmp_path / "confirm.sqlite3", clock=clock)


def _submission(request, choices: dict[str, str], submission_id: str = "sub-1"):
    return ApprovalSubmission(
        submission_id=submission_id,
        request_revision=request.revision,
        decisions=tuple(
            ItemDecision(item_id=item_id, choice=choice)  # type: ignore[arg-type]
            for item_id, choice in choices.items()
        ),
    )


@pytest.mark.asyncio
async def test_confirm_resolve_ready_with_events(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    sink = EventSink()
    handler = WebConfirmationHandler(store=store, timeout_s=5)
    try:
        request = make_request(clock, "web-ok")
        store.create_request(request)
        store.mark_awaiting_approval(request.request_id)
        item_id = request.requirements[0].item_id
        task = asyncio.create_task(
            handler.confirm(request, [item_id], make_context(tmp_path, sink))
        )
        await wait_pending(handler)
        resolution = await handler.resolve(
            request.approval_id,
            _submission(request, {item_id: "allow_always"}, "sub-web-ok"),
            "web-operator",
        )
        assert resolution.request_status == "ready"
        assert await task == resolution
        assert handler.pending_count == 0
        requested = [e for e in sink.events
                     if e.type == "permission.confirmation_requested"]
        completed = [e for e in sink.events
                     if e.type == "permission.confirmation_completed"]
        changed = [e for e in sink.events if e.type == "permission.grant_changed"]
        assert len(requested) == 1
        assert requested[0].payload["protocol_version"] == 2
        assert requested[0].payload["items"][0]["item_id"] == item_id
        assert "arguments" not in requested[0].payload
        assert len(completed) == 1
        assert completed[0].payload["request_status"] == "ready"
        assert completed[0].payload["approved"] is True
        assert len(changed) == 1
        assert changed[0].payload["grant_ids"] == list(resolution.persisted_grant_ids)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_reject_all_blocks_without_grant_event(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    sink = EventSink()
    handler = WebConfirmationHandler(store=store, timeout_s=5)
    try:
        request = make_request(clock, "web-no", combo=True)
        store.create_request(request)
        task = asyncio.create_task(
            handler.confirm(
                request,
                [item.item_id for item in request.requirements],
                make_context(tmp_path, sink),
            )
        )
        await wait_pending(handler)
        resolution = await handler.resolve(
            request.approval_id,
            _submission(
                request,
                {item.item_id: "reject" for item in request.requirements},
                "sub-web-no",
            ),
            "web-operator",
        )
        assert resolution.request_status == "blocked"
        assert await task == resolution
        assert [e.type for e in sink.events].count("permission.grant_changed") == 0
    finally:
        store.close()


@pytest.mark.asyncio
async def test_timeout_cancels_request(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    sink = EventSink()
    handler = WebConfirmationHandler(store=store, timeout_s=0.05)
    try:
        request = make_request(clock, "web-timeout")
        store.create_request(request)
        store.mark_awaiting_approval(request.request_id)
        with pytest.raises(ApprovalCancelled):
            await handler.confirm(
                request,
                [item.item_id for item in request.requirements],
                make_context(tmp_path, sink),
            )
        assert handler.pending_count == 0
        assert store.get_request(request.approval_id).status == "cancelled"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_deny_session_cancels_only_matching_session(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    sink = EventSink()
    handler = WebConfirmationHandler(store=store, timeout_s=5)
    try:
        first = make_request(clock, "web-d1")
        second = make_request(clock, "web-d2")
        for request in (first, second):
            store.create_request(request)
            store.mark_awaiting_approval(request.request_id)
        tasks = [
            asyncio.create_task(
                handler.confirm(
                    request,
                    [item.item_id for item in request.requirements],
                    make_context(tmp_path, sink, session_id=session),
                )
            )
            for request, session in ((first, "gone"), (second, "stays"))
        ]
        await wait_pending(handler, 2)
        assert await handler.deny_session("gone") == 1
        with pytest.raises(ApprovalCancelled):
            await tasks[0]
        assert handler.pending_count == 1
        assert store.get_request(first.approval_id).status == "cancelled"
        assert store.get_request(second.approval_id).status == "awaiting_approval"
        for task in tasks[1:]:
            task.cancel()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_aclose_cancels_and_blocks_new_confirms(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    handler = WebConfirmationHandler(store=store, timeout_s=5)
    try:
        request = make_request(clock, "web-close")
        store.create_request(request)
        task = asyncio.create_task(
            handler.confirm(
                request,
                [item.item_id for item in request.requirements],
                make_context(tmp_path, EventSink()),
            )
        )
        await wait_pending(handler)
        await handler.aclose()
        with pytest.raises(ApprovalCancelled):
            await task
        with pytest.raises(ApprovalCancelled):
            await handler.confirm(
                request,
                [item.item_id for item in request.requirements],
                make_context(tmp_path, EventSink()),
            )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_resolve_unknown_approval_raises_key_error(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    handler = WebConfirmationHandler(store=store, timeout_s=5)
    try:
        request = make_request(clock, "web-unknown")
        with pytest.raises(KeyError):
            await handler.resolve(
                "approval-nope",
                _submission(request, {"item-x": "reject"}, "sub-nope"),
                "web-operator",
            )
    finally:
        store.close()


@pytest.mark.asyncio
async def test_cancel_approval_pending_and_resolved(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    handler = WebConfirmationHandler(store=store, timeout_s=5)
    try:
        request = make_request(clock, "web-cancel")
        store.create_request(request)
        store.mark_awaiting_approval(request.request_id)
        task = asyncio.create_task(
            handler.confirm(
                request,
                [item.item_id for item in request.requirements],
                make_context(tmp_path, EventSink()),
            )
        )
        await wait_pending(handler)
        status = await handler.cancel_approval(
            request.approval_id, "cancel-1", request.revision
        )
        assert status == "cancelled"
        with pytest.raises(ApprovalCancelled):
            await task
        ready = make_request(clock, "web-cancel-2")
        store.create_request(ready)
        item_id = ready.requirements[0].item_id
        await handler.resolve(
            ready.approval_id,
            _submission(ready, {item_id: "allow_once"}, "sub-ready"),
            "web-operator",
        )
        assert await handler.cancel_approval(
            ready.approval_id, "cancel-2", ready.revision
        ) == "ready"
    finally:
        store.close()


def test_bind_store_rules(tmp_path: Path) -> None:
    clock = FakeClock()
    first = PermissionStore.open(tmp_path / "a.sqlite3", clock=clock)
    second = PermissionStore.open(tmp_path / "b.sqlite3", clock=clock)
    try:
        handler = WebConfirmationHandler()
        with pytest.raises(RuntimeError):
            handler._require_store()
        with pytest.raises(TypeError):
            handler.bind_store(None)  # type: ignore[arg-type]
        handler.bind_store(first)
        assert handler.store is first
        handler.bind_store(first)
        with pytest.raises(ValueError):
            handler.bind_store(second)
    finally:
        first.close()
        second.close()


@pytest.mark.asyncio
async def test_confirm_without_store_raises(tmp_path: Path) -> None:
    handler = WebConfirmationHandler()
    with pytest.raises(RuntimeError):
        await handler.confirm(
            make_request(FakeClock(), "web-nostore"),
            ["item-x"],
            make_context(tmp_path, EventSink()),
        )
