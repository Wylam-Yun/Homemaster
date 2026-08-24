from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from homemaster.application import RunRequest, SessionManager
from homemaster.artifacts import ToolOutputStore
from homemaster.events.bus import EventBus
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.memory.management import (
    ManagedMemory,
    MemoryGroup,
    MemoryNotFoundError,
    MemorySnapshot,
    MemoryStats,
)
from homemaster.web.app import (
    _deny_approvals_without_subscriber,
    _run_and_report_prestart_failure,
    _stream_events,
    create_web_app,
)
from homemaster.web.confirmations import WebConfirmationHandler


class _FakeApplication:
    def __init__(self) -> None:
        self.event_bus = EventBus()
        self.session_manager = SessionManager()
        self.run_requests: list[RunRequest] = []
        self.started = False
        self.closed = False
        self.close_count = 0

    async def start(self) -> None:
        self.started = True

    async def run(self, request: RunRequest):
        self.run_requests.append(request)
        events = (
            RuntimeEvent(
                type="runtime.turn_started",
                session_id=request.session_id or "",
                run_id="run-01",
                turn_index=0,
                payload={},
            ),
            RuntimeEvent(
                type="transport.delta",
                session_id=request.session_id or "",
                run_id="run-01",
                turn_index=0,
                payload={"reasoning_delta": "think", "text_delta": "answer"},
            ),
            RuntimeEvent(
                type="assistant.thinking",
                session_id=request.session_id or "",
                run_id="run-01",
                turn_index=0,
                payload={"thinking": "full think"},
            ),
            RuntimeEvent(
                type="assistant.reply",
                session_id=request.session_id or "",
                run_id="run-01",
                turn_index=0,
                payload={"reply": "full answer"},
            ),
            RuntimeEvent(
                type="runtime.turn_completed",
                session_id=request.session_id or "",
                run_id="run-01",
                turn_index=0,
                payload={"final_reply": "must not duplicate"},
            ),
        )
        for event in events:
            await self.event_bus.aemit(event)
        return SimpleNamespace(status="replied", run_id="run-01")

    def cancel(self, session_id: str) -> bool:
        return False

    async def aclose(self) -> None:
        self.closed = True
        self.close_count += 1
        await self.event_bus.aclose()


def _managed_memory(memory_id: str, *, status: str = "active") -> ManagedMemory:
    now = datetime(2026, 8, 24, 8, 30, tzinfo=UTC)
    return ManagedMemory(
        memory_id=memory_id,
        content="visible memory",
        memory_type="fact",
        memory_type_label="事实",
        status=status,
        session_id="session-01",
        created_at=now,
        updated_at=now,
        archived_at=now if status == "archived" else None,
        archive_reason="user_request" if status == "archived" else None,
        record=None,
        structure_status="plain",
        has_history=True,
    )


class _FakeMemoryManagementService:
    def __init__(self) -> None:
        self.snapshot_tenants: list[str] = []
        self.history_calls: list[tuple[str, str]] = []

    async def snapshot(self, *, tenant_id: str) -> MemorySnapshot:
        self.snapshot_tenants.append(tenant_id)
        memory = _managed_memory("memory-01")
        return MemorySnapshot(
            stats=MemoryStats(2, 1, 3, 1),
            groups=(
                MemoryGroup(
                    session_id="session-01",
                    title="first request",
                    active_count=1,
                    archived_count=0,
                    memories=(memory,),
                ),
            ),
        )

    async def history(
        self, memory_id: str, *, tenant_id: str
    ) -> tuple[ManagedMemory, ...]:
        self.history_calls.append((memory_id, tenant_id))
        if memory_id == "missing":
            raise MemoryNotFoundError(memory_id)
        return (_managed_memory(memory_id),)


class _FailingApplication:
    async def run(self, request: RunRequest) -> None:
        del request
        raise RuntimeError("provider credential and traceback must stay private")


class _RecordingRegistry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def fail_before_start(self, session_id: str, request_id: str) -> bool:
        self.calls.append((session_id, request_id))
        return True


class _RecordingHub:
    def __init__(self) -> None:
        self.events = []

    async def publish(self, event) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_prestart_adapter_failure_is_stable_public_terminal_event() -> None:
    registry = _RecordingRegistry()
    hub = _RecordingHub()
    request = RunRequest(text="hello", session_id="session-01", resume=True)

    result = await _run_and_report_prestart_failure(
        application=_FailingApplication(),
        request=request,
        request_id="request-01",
        run_registry=registry,
        hub=hub,
    )

    assert result is None
    assert registry.calls == [("session-01", "request-01")]
    assert len(hub.events) == 1
    assert hub.events[0].to_dict() == {
        "type": "run.failed",
        "session_id": "session-01",
        "run_id": "",
        "request_id": "request-01",
        "payload": {
            "code": "adapter_failed",
            "message": "The run could not be started.",
            "retryable": True,
        },
    }


@pytest.mark.asyncio
async def test_disconnect_denies_approvals_only_after_last_subscriber_leaves() -> None:
    denied: list[tuple[str, str]] = []
    handler = SimpleNamespace(
        deny_session=lambda session_id, *, outcome: _record_denial(
            denied, session_id, outcome
        )
    )
    subscribed_hub = SimpleNamespace(has_subscriber=lambda session_id: _true())
    empty_hub = SimpleNamespace(has_subscriber=lambda session_id: _false())

    await _deny_approvals_without_subscriber(
        "session-01", hub=subscribed_hub, confirmation_handler=handler
    )
    await _deny_approvals_without_subscriber(
        "session-01", hub=empty_hub, confirmation_handler=handler
    )

    assert denied == [("session-01", "disconnected")]


@pytest.mark.asyncio
async def test_public_web_close_hook_is_idempotent() -> None:
    application = _FakeApplication()
    app = create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
    )

    await app.state.aclose()
    await app.state.aclose()

    assert application.close_count == 1


def test_web_lifespan_starts_application_before_serving() -> None:
    application = _FakeApplication()
    app = create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
    )

    with TestClient(app):
        assert application.started is True


@pytest.mark.asyncio
async def test_idle_websocket_disconnect_stops_event_stream_without_an_event() -> None:
    websocket = SimpleNamespace(
        receive=lambda: _disconnect_message(),
        send_json=lambda _payload: _unexpected_send(),
    )

    await asyncio.wait_for(_stream_events(websocket, asyncio.Queue()), timeout=1)


async def _disconnect_message() -> dict[str, str]:
    return {"type": "websocket.disconnect"}


async def _unexpected_send() -> None:
    raise AssertionError("an idle disconnect must not send an event")


async def _record_denial(items, session_id: str, outcome: str) -> int:
    items.append((session_id, outcome))
    return 1


async def _true() -> bool:
    return True


async def _false() -> bool:
    return False


def test_http_command_and_websocket_event_flow_share_one_request_id() -> None:
    application = _FakeApplication()
    app = create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
    )

    with TestClient(app) as client:
        created = client.post("/api/sessions")
        assert created.status_code == 201
        session_id = created.json()["session_id"]

        with client.websocket_connect(f"/api/events?session_id={session_id}") as websocket:
            accepted = client.post(
                f"/api/sessions/{session_id}/messages",
                json={"request_id": "request-01", "text": "hello"},
            )
            assert accepted.status_code == 202
            assert accepted.json() == {
                "accepted": True,
                "session_id": session_id,
                "request_id": "request-01",
            }

            frames = [websocket.receive_json() for _ in range(7)]

        assert [frame["type"] for frame in frames] == [
            "request.accepted",
            "run.started",
            "thinking.delta",
            "answer.delta",
            "thinking.snapshot",
            "answer.snapshot",
            "run.completed",
        ]
        assert {frame["request_id"] for frame in frames} == {"request-01"}
        assert frames[-1]["payload"] == {}
        assert "must not duplicate" not in str(frames[-1])

        duplicate = client.post(
            f"/api/sessions/{session_id}/messages",
            json={"request_id": "request-01", "text": "ignored duplicate"},
        )
        assert duplicate.status_code == 202
        assert len(application.run_requests) == 1

        other = client.post("/api/sessions")
        other_session = other.json()["session_id"]
        not_subscribed = client.post(
            f"/api/sessions/{other_session}/messages",
            json={"request_id": "request-02", "text": "must reject"},
        )
        assert not_subscribed.status_code == 409
        assert not_subscribed.json()["code"] == "event_stream_not_ready"

    assert application.closed is True


def test_session_cancel_approval_and_validation_endpoints_use_stable_json() -> None:
    application = _FakeApplication()
    app = create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
    )

    with TestClient(app) as client:
        created = client.post("/api/sessions")
        session_id = created.json()["session_id"]

        listed = client.get("/api/sessions")
        assert listed.status_code == 200
        assert listed.json() == {"sessions": [{"session_id": session_id}]}

        history = client.get(f"/api/sessions/{session_id}/history")
        assert history.status_code == 200
        assert history.json() == {"session_id": session_id, "messages": []}

        cancelled = client.post(f"/api/sessions/{session_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json() == {"cancelled": False, "session_id": session_id}

        missing_approval = client.post(
            "/api/approvals/approval-missing",
            json={"outcome": "approve"},
        )
        assert missing_approval.status_code == 404
        assert missing_approval.json()["code"] == "approval_not_found"

        invalid = client.post(
            f"/api/sessions/{session_id}/messages",
            json={"request_id": "", "text": ""},
        )
        assert invalid.status_code == 422
        assert invalid.json() == {
            "code": "invalid_request",
            "message": "Request validation failed.",
            "retryable": False,
        }

        missing_resume = client.post(
            "/api/sessions",
            json={"session_id": "session-missing"},
        )
        assert missing_resume.status_code == 404
        assert missing_resume.json()["code"] == "session_not_found"


def test_artifact_download_enforces_exact_session_and_run_partition(tmp_path) -> None:
    application = _FakeApplication()
    store = ToolOutputStore(tmp_path / "artifacts", quota_bytes=1024, ttl_seconds=60)
    application.artifact_publisher = SimpleNamespace(store=store)
    app = create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
    )

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        stored = store.write(
            tenant_id="local",
            session_id=session_id,
            run_id="run-01",
            content=b"artifact bytes",
            media_type="text/plain",
        )

        downloaded = client.get(
            f"/api/artifacts/{stored.handle}",
            params={"session_id": session_id, "run_id": "run-01"},
        )
        denied = client.get(
            f"/api/artifacts/{stored.handle}",
            params={"session_id": session_id, "run_id": "run-other"},
        )

        assert downloaded.status_code == 200
        assert downloaded.content == b"artifact bytes"
        assert downloaded.headers["content-type"] == "application/octet-stream"
        assert downloaded.headers["x-content-sha256"] == stored.content_sha256
        assert denied.status_code == 404
        assert denied.json()["code"] == "artifact_not_found"


def test_memory_snapshot_and_history_are_get_only() -> None:
    service = _FakeMemoryManagementService()
    app = create_web_app(
        application=_FakeApplication(),
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
        memory_management_service=service,
    )

    with TestClient(app) as client:
        snapshot = client.get("/api/memories")
        history = client.get("/api/memories/memory-01/history")

        assert snapshot.status_code == 200
        assert snapshot.json()["stats"] == {
            "active_count": 2,
            "archived_count": 1,
            "total_count": 3,
            "session_group_count": 1,
        }
        memory = snapshot.json()["groups"][0]["memories"][0]
        assert memory["memory_type_label"] == "事实"
        assert memory["updated_at"] == "2026-08-24T08:30:00Z"
        assert "metadata" not in memory
        assert history.status_code == 200
        assert history.json()["memory_id"] == "memory-01"
        assert service.snapshot_tenants == ["local"]
        assert service.history_calls == [("memory-01", "local")]
        for method in (client.post, client.put, client.patch, client.delete):
            assert method("/api/memories").status_code == 405


def test_memory_routes_return_stable_unavailable_and_not_found_errors() -> None:
    without_service = create_web_app(
        application=_FakeApplication(),
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
    )
    with TestClient(without_service) as client:
        unavailable = client.get("/api/memories")
        assert unavailable.status_code == 503
        assert unavailable.json()["code"] == "memory_unavailable"

    with_service = create_web_app(
        application=_FakeApplication(),
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
        memory_management_service=_FakeMemoryManagementService(),
    )
    with TestClient(with_service) as client:
        missing = client.get("/api/memories/missing/history")
        assert missing.status_code == 404
        assert missing.json() == {
            "code": "memory_not_found",
            "message": "The memory does not exist.",
            "retryable": False,
        }
