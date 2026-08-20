from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from homemaster.application import RunRequest, SessionManager
from homemaster.artifacts import ToolOutputStore
from homemaster.events.bus import EventBus
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.web.app import (
    _deny_approvals_without_subscriber,
    _run_and_report_prestart_failure,
    create_web_app,
)
from homemaster.web.confirmations import WebConfirmationHandler


class _FakeApplication:
    def __init__(self) -> None:
        self.event_bus = EventBus()
        self.session_manager = SessionManager()
        self.run_requests: list[RunRequest] = []
        self.closed = False

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
        await self.event_bus.aclose()


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
