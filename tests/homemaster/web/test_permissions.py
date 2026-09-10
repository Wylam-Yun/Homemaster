"""HTTP protocol tests for structured approvals, grants and revocation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from perm_harness import (
    EventSink,
    FakeClock,
    build_app,
    make_context,
    make_request,
    submission_body,
    wait_pending,
)

from homemaster.permissions.models import PermissionStorageUnavailable
from homemaster.permissions.store import PermissionStore
from homemaster.web.confirmations import ApprovalCancelled, WebConfirmationHandler


def _open(tmp_path: Path, clock: FakeClock) -> PermissionStore:
    return PermissionStore.open(tmp_path / "perm.sqlite3", clock=clock)


@pytest.mark.asyncio
async def test_submit_allow_once_ready_and_wakes_waiter(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    sink = EventSink()
    handler = WebConfirmationHandler(store=store, timeout_s=5)
    client, _, _ = build_app(store, handler)
    try:
        request = make_request(clock, "http-ready")
        store.create_request(request)
        item_id = request.requirements[0].item_id
        waiter = await _spawn_confirm(handler, request, [item_id], tmp_path, sink)
        await wait_pending(handler)
        response = await client.post(
            f"/api/approvals/{request.approval_id}",
            json=submission_body(request, {item_id: "allow_once"}),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["request_status"] == "ready"
        assert body["persisted_grant_ids"] == []
        assert body["items"] == [{"item_id": item_id, "choice": "allow_once"}]
        resolution = await waiter
        assert resolution.request_id == request.request_id
        requested = [e for e in sink.events if e.type == "permission.confirmation_requested"]
        assert len(requested) == 1
        assert "arguments" not in requested[0].payload
        assert "cwd" not in requested[0].payload
        assert requested[0].payload["items"][0]["item_id"] == item_id
    finally:
        await client.aclose()
        store.close()


@pytest.mark.asyncio
async def test_submit_partial_reject_returns_200_blocked(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    client, _, _ = build_app(store)
    try:
        request = make_request(clock, "http-blocked", combo=True)
        store.create_request(request)
        enter, pick = (item.item_id for item in request.requirements)
        response = await client.post(
            f"/api/approvals/{request.approval_id}",
            json=submission_body(request, {enter: "allow_always", pick: "reject"}),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["request_status"] == "blocked"
        assert len(body["persisted_grant_ids"]) == 1
    finally:
        await client.aclose()
        store.close()


@pytest.mark.asyncio
async def test_submit_missing_item_422(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    client, _, _ = build_app(store)
    try:
        request = make_request(clock, "http-missing")
        store.create_request(request)
        response = await client.post(
            f"/api/approvals/{request.approval_id}",
            json=submission_body(request, {"item-ghost": "allow_once"}),
        )
        assert response.status_code == 422
    finally:
        await client.aclose()
        store.close()


@pytest.mark.asyncio
async def test_submit_duplicate_item_422(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    client, _, _ = build_app(store)
    try:
        request = make_request(clock, "http-dup")
        store.create_request(request)
        item_id = request.requirements[0].item_id
        body = submission_body(request, {item_id: "allow_once"})
        body["decisions"].append({"item_id": item_id, "choice": "reject"})
        response = await client.post(
            f"/api/approvals/{request.approval_id}", json=body
        )
        assert response.status_code == 422
    finally:
        await client.aclose()
        store.close()


@pytest.mark.asyncio
async def test_submit_empty_decisions_422(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    client, _, _ = build_app(store)
    try:
        request = make_request(clock, "http-empty")
        store.create_request(request)
        body = submission_body(request, {})
        response = await client.post(
            f"/api/approvals/{request.approval_id}", json=body
        )
        assert response.status_code == 422
    finally:
        await client.aclose()
        store.close()


@pytest.mark.asyncio
async def test_old_outcome_shape_is_rejected_as_outdated(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    client, _, _ = build_app(store)
    try:
        request = make_request(clock, "http-old")
        store.create_request(request)
        response = await client.post(
            f"/api/approvals/{request.approval_id}", json={"outcome": "approve"}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "approval_protocol_outdated"
    finally:
        await client.aclose()
        store.close()


@pytest.mark.asyncio
async def test_unknown_approval_404(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    client, _, _ = build_app(store)
    try:
        response = await client.post(
            "/api/approvals/approval-nope",
            json={
                "protocol_version": 2,
                "submission_id": "sub-x",
                "request_revision": 1,
                "decisions": [{"item_id": "item-x", "choice": "reject"}],
            },
        )
        assert response.status_code == 404
        assert (await client.get("/api/approvals/approval-nope")).status_code == 404
    finally:
        await client.aclose()
        store.close()


@pytest.mark.asyncio
async def test_revision_mismatch_409(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    client, _, _ = build_app(store)
    try:
        request = make_request(clock, "http-rev")
        store.create_request(request)
        item_id = request.requirements[0].item_id
        body = submission_body(request, {item_id: "allow_once"})
        body["request_revision"] = request.revision + 1
        response = await client.post(
            f"/api/approvals/{request.approval_id}", json=body
        )
        assert response.status_code == 409
    finally:
        await client.aclose()
        store.close()


@pytest.mark.asyncio
async def test_expired_410(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    client, _, _ = build_app(store)
    try:
        request = make_request(clock, "http-exp")
        store.create_request(request)
        clock.advance(600)
        item_id = request.requirements[0].item_id
        response = await client.post(
            f"/api/approvals/{request.approval_id}",
            json=submission_body(request, {item_id: "allow_once"}),
        )
        assert response.status_code == 410
    finally:
        await client.aclose()
        store.close()


@pytest.mark.asyncio
async def test_storage_failure_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    client, _, _ = build_app(store)
    try:
        request = make_request(clock, "http-503")
        store.create_request(request)
        item_id = request.requirements[0].item_id

        def broken(approval_id: str, submission, actor: str):
            raise PermissionStorageUnavailable("disk gone")

        monkeypatch.setattr(store, "submit", broken)
        response = await client.post(
            f"/api/approvals/{request.approval_id}",
            json=submission_body(request, {item_id: "allow_once"}),
        )
        assert response.status_code == 503
    finally:
        await client.aclose()
        store.close()


@pytest.mark.asyncio
async def test_idempotent_resubmit_and_conflict(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    client, _, _ = build_app(store)
    try:
        request = make_request(clock, "http-idem")
        store.create_request(request)
        item_id = request.requirements[0].item_id
        body = submission_body(request, {item_id: "allow_always"}, "sub-idem")
        first = await client.post(f"/api/approvals/{request.approval_id}", json=body)
        second = await client.post(f"/api/approvals/{request.approval_id}", json=body)
        assert first.status_code == 200
        assert second.json() == first.json()
        other = submission_body(request, {item_id: "reject"}, "sub-idem")
        conflict = await client.post(
            f"/api/approvals/{request.approval_id}", json=other
        )
        assert conflict.status_code == 409
        conn = sqlite3.connect(str(store.path))
        try:
            assert conn.execute("SELECT COUNT(*) FROM permission_grants").fetchone()[0] == 1
        finally:
            conn.close()
    finally:
        await client.aclose()
        store.close()


@pytest.mark.asyncio
async def test_get_approval_reads_authoritative_state(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    client, _, _ = build_app(store)
    try:
        request = make_request(clock, "http-get")
        store.create_request(request)
        item_id = request.requirements[0].item_id
        await client.post(
            f"/api/approvals/{request.approval_id}",
            json=submission_body(request, {item_id: "allow_always"}),
        )
        response = await client.get(f"/api/approvals/{request.approval_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["request_status"] == "ready"
        assert body["items"][0]["decision"] == "allow_always"
        assert body["items"][0]["matched_grant_id"] is not None
    finally:
        await client.aclose()
        store.close()


@pytest.mark.asyncio
async def test_grants_list_revoke_and_filters(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    client, _, _ = build_app(store)
    try:
        first = make_request(clock, "gl-1")
        store.create_request(first)
        await client.post(
            f"/api/approvals/{first.approval_id}",
            json=submission_body(
                first,
                {item.item_id: "allow_always" for item in first.requirements},
                "sub-gl-1",
            ),
        )
        second = make_request(clock, "gl-2", combo=True)
        store.create_request(second)
        await client.post(
            f"/api/approvals/{second.approval_id}",
            json=submission_body(
                second,
                {item.item_id: "allow_always" for item in second.requirements},
                "sub-gl-2",
            ),
        )
        listed = await client.get("/api/permissions/grants")
        assert listed.status_code == 200
        assert len(listed.json()["grants"]) == 3
        areas = await client.get(
            "/api/permissions/grants", params={"resource_kind": "area"}
        )
        assert [g["resource_id"] for g in areas.json()["grants"]] == ["bedroom"]
        page = await client.get("/api/permissions/grants", params={"limit": 2})
        assert len(page.json()["grants"]) == 2
        cursor = page.json()["next_cursor"]
        assert cursor
        rest = await client.get(
            "/api/permissions/grants", params={"limit": 2, "cursor": cursor}
        )
        assert len(rest.json()["grants"]) == 1
        assert rest.json()["next_cursor"] is None
        target = listed.json()["grants"][0]
        revoked = await client.post(
            f"/api/permissions/grants/{target['grant_id']}/revoke",
            json={"submission_id": "rev-1", "expected_revision": 1},
        )
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "revoked"
        again = await client.post(
            f"/api/permissions/grants/{target['grant_id']}/revoke",
            json={"submission_id": "rev-1", "expected_revision": 1},
        )
        assert again.status_code == 200
        stale = await client.post(
            f"/api/permissions/grants/{target['grant_id']}/revoke",
            json={"submission_id": "rev-2", "expected_revision": 1},
        )
        assert stale.status_code == 409
        missing = await client.post(
            "/api/permissions/grants/grant-nope/revoke",
            json={"submission_id": "rev-3", "expected_revision": 1},
        )
        assert missing.status_code == 404
        revoked_list = await client.get(
            "/api/permissions/grants", params={"status": "revoked"}
        )
        assert len(revoked_list.json()["grants"]) == 1
        bad_status = await client.get(
            "/api/permissions/grants", params={"status": "bogus"}
        )
        assert bad_status.status_code == 422
    finally:
        await client.aclose()
        store.close()


@pytest.mark.asyncio
async def test_cancel_pending_then_resolved_returns_terminal(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    sink = EventSink()
    handler = WebConfirmationHandler(store=store, timeout_s=5)
    client, _, _ = build_app(store, handler)
    try:
        request = make_request(clock, "http-cancel")
        store.create_request(request)
        store.mark_awaiting_approval(request.request_id)
        waiter = await _spawn_confirm(handler, request, [
            item.item_id for item in request.requirements], tmp_path, sink)
        await wait_pending(handler)
        cancelled = await client.post(
            f"/api/approvals/{request.approval_id}/cancel",
            json={"submission_id": "cancel-1", "request_revision": request.revision},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["request_status"] == "cancelled"
        try:
            await waiter
            raise AssertionError("waiter should have been cancelled")
        except ApprovalCancelled:
            pass
        ready_request = make_request(clock, "http-cancel-2")
        store.create_request(ready_request)
        item_id = ready_request.requirements[0].item_id
        await client.post(
            f"/api/approvals/{ready_request.approval_id}",
            json=submission_body(ready_request, {item_id: "allow_once"}),
        )
        again = await client.post(
            f"/api/approvals/{ready_request.approval_id}/cancel",
            json={"submission_id": "cancel-2",
                  "request_revision": ready_request.revision},
        )
        assert again.status_code == 200
        assert again.json()["request_status"] == "ready"
    finally:
        await client.aclose()
        store.close()


@pytest.mark.asyncio
async def test_real_http_submit_matches_raw_sqlite(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    client, _, _ = build_app(store)
    try:
        request = make_request(clock, "http-raw", combo=True)
        store.create_request(request)
        choices = {item.item_id: "allow_always" for item in request.requirements}
        response = await client.post(
            f"/api/approvals/{request.approval_id}",
            json=submission_body(request, choices, "sub-raw"),
        )
        assert response.status_code == 200
        conn = sqlite3.connect(str(store.path))
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            grants = conn.execute(
                "SELECT environment_id, resource_kind, resource_id, action, revoked_at"
                " FROM permission_grants ORDER BY resource_kind, resource_id"
            ).fetchall()
            assert grants == [
                ("home", "area", "bedroom", "enter", None),
                ("home", "object", "cup-b", "pick_up", None),
            ]
            assert conn.execute(
                "SELECT COUNT(*) FROM permission_submissions"
            ).fetchone()[0] == 1
            assert conn.execute(
                "SELECT status FROM permission_requests WHERE request_id = ?",
                (request.request_id,),
            ).fetchone()[0] == "ready"
            decisions = dict(
                conn.execute(
                    "SELECT item_id, decision FROM permission_request_items"
                    " WHERE request_id = ?",
                    (request.request_id,),
                ).fetchall()
            )
            assert decisions == {item.item_id: "allow_always"
                                 for item in request.requirements}
        finally:
            conn.close()
    finally:
        await client.aclose()
        store.close()


@pytest.mark.asyncio
async def test_disconnect_cancels_pending(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    sink = EventSink()
    handler = WebConfirmationHandler(store=store, timeout_s=5)
    try:
        request = make_request(clock, "http-disc")
        store.create_request(request)
        store.mark_awaiting_approval(request.request_id)
        waiter = await _spawn_confirm(
            handler, request,
            [item.item_id for item in request.requirements], tmp_path, sink,
            session_id="session-gone",
        )
        await wait_pending(handler)
        assert await handler.deny_session("session-gone") == 1
        try:
            await waiter
            raise AssertionError("waiter should have been cancelled")
        except ApprovalCancelled:
            pass
        assert store.get_request(request.approval_id).status == "cancelled"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_bool_trap_documented(tmp_path: Path) -> None:
    from homemaster.permissions.models import ApprovalSubmission, ItemDecision

    clock = FakeClock()
    store = _open(tmp_path, clock)
    try:
        request = make_request(clock, "http-trap")
        store.create_request(request)
        item_id = request.requirements[0].item_id
        resolution = store.submit(
            request.approval_id,
            ApprovalSubmission(
                submission_id="sub-trap",
                request_revision=request.revision,
                decisions=(ItemDecision(item_id=item_id, choice="reject"),),
            ),
            "tester",
        )
        assert bool(resolution) is True
        assert resolution.request_status == "blocked"
    finally:
        store.close()


async def _spawn_confirm(handler, request, missing, tmp_path, sink, session_id="session-01"):
    import asyncio

    return asyncio.create_task(
        handler.confirm(request, missing, make_context(tmp_path, sink, session_id))
    )


@pytest.mark.asyncio
async def test_grants_carry_display_snapshot(tmp_path: Path) -> None:
    clock = FakeClock()
    store = _open(tmp_path, clock)
    client, _, _ = build_app(store)
    try:
        request = make_request(clock, "http-display", combo=True)
        store.create_request(request)
        choices = {item.item_id: "allow_always" for item in request.requirements}
        response = await client.post(
            f"/api/approvals/{request.approval_id}",
            json=submission_body(request, choices),
        )
        assert response.status_code == 200
        listed = await client.get(
            "/api/permissions/grants", params={"status": "active"}
        )
        assert listed.status_code == 200
        by_action = {grant["action"]: grant for grant in listed.json()["grants"]}
        assert by_action["enter"]["display_name"] == "卧室"
        assert by_action["enter"]["location"] == "卧室"
        assert by_action["enter"]["action_label"] == "进入"
        assert by_action["pick_up"]["display_name"] == "蓝色杯子"
        assert by_action["pick_up"]["location"] == "卧室书桌"
        assert by_action["pick_up"]["action_label"] == "拿取"
    finally:
        await client.aclose()
        store.close()
