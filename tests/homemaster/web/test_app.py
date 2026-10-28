from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sse_support import asgi_client

from homemaster.agent.messages import UserMessage
from homemaster.application import RunRequest, SessionManager
from homemaster.artifacts import ToolOutputStore
from homemaster.events.bus import EventBus
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.web.app import (
    _run_and_report_prestart_failure,
    create_web_app,
)
from homemaster.web.confirmations import WebConfirmationHandler
from homemaster.web.memory_views import (
    ManagedMemory,
    MemoryGroup,
    MemoryNotFoundError,
    MemorySearchHit,
    MemorySearchResult,
    MemorySnapshot,
    MemoryStats,
)
from homemaster.web.sse import _sse_frames


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

    async def history(self, memory_id: str, *, tenant_id: str) -> tuple[ManagedMemory, ...]:
        self.history_calls.append((memory_id, tenant_id))
        if memory_id == "missing":
            raise MemoryNotFoundError(memory_id)
        return (_managed_memory(memory_id),)

    async def create(
        self,
        content: str,
        memory_type: str = "fact",
        *,
        tenant_id: str,
    ) -> ManagedMemory:
        del content, memory_type, tenant_id
        return _managed_memory("memory-created")

    async def update(
        self,
        memory_id: str,
        content: str,
        *,
        tenant_id: str,
        fields: dict[str, object] | None = None,
    ) -> ManagedMemory:
        del content, tenant_id, fields
        if memory_id == "missing":
            raise MemoryNotFoundError(memory_id)
        return _managed_memory(memory_id)

    async def delete(
        self,
        memory_id: str,
        *,
        tenant_id: str,
    ) -> dict[str, object]:
        del tenant_id
        if memory_id == "missing":
            raise MemoryNotFoundError(memory_id)
        return {"memory_id": memory_id, "deleted": True, "status": "archived"}

    async def detail(self, memory_id: str, *, tenant_id: str) -> ManagedMemory:
        del tenant_id
        if memory_id == "missing":
            raise MemoryNotFoundError(memory_id)
        return _managed_memory(memory_id)

    async def search(
        self,
        query: str,
        *,
        tenant_id: str,
        top_k: int = 10,
    ) -> MemorySearchResult:
        del tenant_id, top_k
        return MemorySearchResult(
            query=query,
            hits=(
                MemorySearchHit(
                    memory_id="memory-01",
                    content="visible memory",
                    memory_type="fact",
                    memory_type_label="事实",
                    score=0.5,
                    session_id="session-01",
                ),
            ),
            search_record_id="sr-1",
        )


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
        # The recording stub never stamps: seq/ts keep their pre-publish
        # defaults, proving stamping is the hub's job, not construction's.
        "seq": 0,
        "ts": 0.0,
        "payload": {
            "code": "adapter_failed",
            "message": "The run could not be started.",
            "retryable": True,
        },
    }


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


def test_web_memory_routes_return_503_without_memory_service() -> None:
    """W1 files tier: serve.py only installs MemoryManagementService when the
    composed bundle has a MindMemOS backend, so /api/memories* must fail with
    the typed memory_unavailable error, not an opaque 500."""

    application = _FakeApplication()
    app = create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
        memory_management_service=None,
    )

    with TestClient(app) as client:
        snapshot = client.get("/api/memories")
        history = client.get("/api/memories/memory-01/history")
        detail = client.get("/api/memories/memory-01")
        search = client.get("/api/memories/search", params={"q": "x"})
        graph = client.get("/api/memories/graph")
    for response in (snapshot, history, detail, search, graph):
        assert response.status_code == 503
        assert response.json()["code"] == "memory_unavailable"
        assert response.json()["retryable"] is True


def test_web_lifespan_starts_application_before_serving() -> None:
    application = _FakeApplication()
    app = create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
    )

    with TestClient(app):
        assert application.started is True


@pytest.mark.asyncio
async def test_idle_disconnect_stops_event_stream_without_an_event() -> None:
    request = SimpleNamespace(is_disconnected=_already_disconnected)

    frames = [
        frame async for frame in _sse_frames(request, asyncio.Queue(), disconnect_poll_s=0.01)
    ]

    assert frames == []


async def _already_disconnected() -> bool:
    return True


@pytest.mark.asyncio
async def test_http_command_and_sse_event_flow_share_one_request_id() -> None:
    application = _FakeApplication()
    app = create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
    )

    async with asgi_client(app) as client:
        created = await client.post("/api/sessions")
        assert created.status_code == 201
        session_id = created.json()["session_id"]

        async with client.stream(session_id) as events:
            accepted = await client.post(
                f"/api/sessions/{session_id}/messages",
                json={"request_id": "request-01", "text": "hello"},
            )
            assert accepted.status_code == 202
            assert accepted.json() == {
                "accepted": True,
                "session_id": session_id,
                "request_id": "request-01",
            }

            frames = [await events.receive_json() for _ in range(7)]

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
        assert frames[-1]["payload"] == {
            "status": "replied",
            "final_reply": "must not duplicate",
        }
        assert frames[3]["payload"] == {"text": "answer"}
        assert frames[5]["payload"] == {"text": "full answer"}

        duplicate = await client.post(
            f"/api/sessions/{session_id}/messages",
            json={"request_id": "request-01", "text": "ignored duplicate"},
        )
        assert duplicate.status_code == 202
        assert len(application.run_requests) == 1

        other = await client.post("/api/sessions")
        other_session = other.json()["session_id"]
        not_subscribed = await client.post(
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
        (entry,) = listed.json()["sessions"]
        assert entry["session_id"] == session_id
        assert entry["title"] == f"会话 {session_id[:8]}"
        assert entry["message_count"] == 0
        assert isinstance(entry["updated_at"], str)

        runtime = application.session_manager.get(session_id)
        runtime.session.append(UserMessage.from_text("帮我查一下机票\n第二行"))
        relisted = client.get("/api/sessions").json()["sessions"]
        assert relisted[0]["title"] == "帮我查一下机票 第二行"
        assert relisted[0]["message_count"] == 1

        history = client.get(f"/api/sessions/{session_id}/history")
        assert history.status_code == 200
        assert history.json() == {
            "session_id": session_id,
            "messages": [{"role": "user", "text": "帮我查一下机票\n第二行"}],
        }

        cancelled = client.post(f"/api/sessions/{session_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json() == {"cancelled": False, "session_id": session_id}

        missing_approval = client.post(
            "/api/approvals/approval-missing",
            json={"outcome": "approve"},
        )
        assert missing_approval.status_code == 422
        assert missing_approval.json()["code"] == "approval_protocol_outdated"

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
        for method in (client.put, client.patch, client.delete):
            assert method("/api/memories").status_code == 405
        created = client.post("/api/memories", json={"content": "new fact", "memory_type": "fact"})
        assert created.status_code == 201
        assert created.json()["memory"]["memory_id"] == "memory-created"
        updated = client.patch("/api/memories/memory-01", json={"content": "updated fact"})
        assert updated.status_code == 200
        deleted = client.delete("/api/memories/memory-01")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True

        detail = client.get("/api/memories/memory-01")
        assert detail.status_code == 200
        assert detail.json()["memory_id"] == "memory-01"
        missing_detail = client.get("/api/memories/missing")
        assert missing_detail.status_code == 404
        assert missing_detail.json()["code"] == "memory_not_found"

        found = client.get("/api/memories/search", params={"q": "visible"})
        assert found.status_code == 200
        assert found.json()["query"] == "visible"
        assert found.json()["hits"][0]["memory_id"] == "memory-01"
        assert found.json()["search_record_id"] == "sr-1"
        empty_query = client.get("/api/memories/search", params={"q": "  "})
        assert empty_query.status_code == 422


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


def test_implicit_feedback_routes_enqueue_and_report_status() -> None:
    """POST admits one implicit-feedback outbox job; GET reflects the
    work-store row; both fail closed when memory services are absent."""
    window = {
        "window_start": "2026-10-25T12:00:00+00:00",
        "window_end": "2026-10-28T12:00:00+00:00",
    }
    done_job = SimpleNamespace(
        job_id="job-1",
        kind="implicit_feedback",
        status="completed",
        last_error=None,
        result={
            "actions": [
                {"action_id": "r:0", "action": "update", "memory_id": "m1"},
                {"action_id": "r:1", "action": "noop", "memory_id": None},
            ],
            "raw_verified": True,
        },
        payload={"window": window},
    )
    accepted_job = SimpleNamespace(
        **{**vars(done_job), "status": "accepted", "result": None}
    )
    other_kind_job = SimpleNamespace(**{**vars(done_job), "kind": "turn_add"})
    enqueued: list[bool] = []

    def _enqueue() -> object:
        enqueued.append(True)
        return accepted_job

    store = SimpleNamespace(
        job=lambda job_id: {"job-1": done_job, "job-2": other_kind_job}.get(job_id)
    )
    application = _FakeApplication()
    application.settings = SimpleNamespace(
        application_services={
            "feedback_scheduler": SimpleNamespace(enqueue_manual=_enqueue),
            "memory_work_store": store,
        }
    )
    app = create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
        memory_management_service=_FakeMemoryManagementService(),
    )

    with TestClient(app) as client:
        accepted = client.post("/api/memories/implicit-feedback")
        assert accepted.status_code == 201
        assert accepted.json() == {
            "job_id": "job-1",
            "status": "accepted",
            "window": window,
        }

        status = client.get("/api/memories/implicit-feedback/job-1")
        assert status.status_code == 200
        assert status.json() == {
            "job_id": "job-1",
            "status": "completed",
            "error": None,
            "actions_count": 1,
            "raw_verified": True,
        }

        missing = client.get("/api/memories/implicit-feedback/nope")
        assert missing.status_code == 404
        assert missing.json()["code"] == "feedback_job_not_found"

        # A job id that belongs to a different outbox kind is not a
        # feedback job — don't leak it through this surface.
        wrong_kind = client.get("/api/memories/implicit-feedback/job-2")
        assert wrong_kind.status_code == 404

    assert enqueued == [True]

    files_app = create_web_app(
        application=_FakeApplication(),
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
    )
    with TestClient(files_app) as client:
        for response in (
            client.post("/api/memories/implicit-feedback"),
            client.get("/api/memories/implicit-feedback/job-1"),
        ):
            assert response.status_code == 503
            assert response.json()["code"] == "memory_unavailable"


def _coro(value):
    """Wrap a plain value in a coroutine for sync-shaped fakes of async APIs."""

    async def _inner():
        return value

    return _inner()


def test_feedback_runs_list_and_detail_routes() -> None:
    """Run list reflects implicit_feedback outbox rows; detail joins the
    inspector's scanned sessions and per-action diffs; both fail closed."""
    window = {
        "window_start": "2026-10-25T12:00:00+00:00",
        "window_end": "2026-10-28T12:00:00+00:00",
    }
    manual_job = SimpleNamespace(
        job_id="job-1",
        kind="implicit_feedback",
        operation_key="implicitcheck:scope:manual:1",
        status="completed",
        last_error=None,
        created_at=1.0,
        updated_at=5.0,
        payload={"window": window},
        result={"actions": [{"action_id": "r:0", "action": "update", "memory_id": "m1"}]},
    )
    scheduled_job = SimpleNamespace(
        **{
            **vars(manual_job),
            "job_id": "job-2",
            "operation_key": "implicitcheck:scope:100",
            "result": None,
        }
    )
    other_kind = SimpleNamespace(
        **{**vars(manual_job), "job_id": "job-3", "kind": "turn_add"}
    )
    store = SimpleNamespace(
        list_jobs=lambda kind, limit=50: [manual_job, scheduled_job],
        job=lambda job_id: {
            "job-1": manual_job, "job-3": other_kind
        }.get(job_id),
    )
    inspector = SimpleNamespace(
        scanned_sessions=lambda window: _coro(
            [
                SimpleNamespace(
                    session_id="s-1",
                    records_count=2,
                    excerpts=(
                        SimpleNamespace(
                            add_record_id="r1",
                            submitted_at="2026-10-27T10:00:00+00:00",
                            preview="其实我搬苏州了",
                        ),
                    ),
                )
            ]
        ),
        action_diffs=lambda actions: _coro(
            [
                SimpleNamespace(
                    action_id="r:0",
                    action="update",
                    memory_id="m1",
                    memory_session_id="s-1",
                    before=SimpleNamespace(content="住杭州", status="archived"),
                    after=SimpleNamespace(content="住苏州", status="active"),
                    missing=False,
                )
            ]
        ),
    )
    application = _FakeApplication()
    application.settings = SimpleNamespace(
        application_services={
            "memory_work_store": store,
            "feedback_run_inspector": inspector,
        }
    )
    app = create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
        memory_management_service=_FakeMemoryManagementService(),
    )

    with TestClient(app) as client:
        listed = client.get("/api/memories/feedback-runs")
        assert listed.status_code == 200
        assert listed.json() == {
            "runs": [
                {
                    "job_id": "job-1",
                    "trigger": "manual",
                    "status": "completed",
                    "window": window,
                    "created_at": 1.0,
                    "updated_at": 5.0,
                    "actions_count": 1,
                    "error": None,
                },
                {
                    "job_id": "job-2",
                    "trigger": "scheduled",
                    "status": "completed",
                    "window": window,
                    "created_at": 1.0,
                    "updated_at": 5.0,
                    "actions_count": 0,
                    "error": None,
                },
            ]
        }

        detail = client.get("/api/memories/feedback-runs/job-1")
        assert detail.status_code == 200
        body = detail.json()
        assert body["sessions"] == [
            {
                "session_id": "s-1",
                "records_count": 2,
                "excerpts": [
                    {
                        "add_record_id": "r1",
                        "submitted_at": "2026-10-27T10:00:00+00:00",
                        "preview": "其实我搬苏州了",
                    }
                ],
            }
        ]
        assert body["actions"] == [
            {
                "action_id": "r:0",
                "action": "update",
                "memory_id": "m1",
                "memory_session_id": "s-1",
                "before": {"content": "住杭州", "status": "archived"},
                "after": {"content": "住苏州", "status": "active"},
                "missing": False,
            }
        ]

        assert client.get("/api/memories/feedback-runs/nope").status_code == 404
        assert client.get("/api/memories/feedback-runs/job-3").status_code == 404

    files_app = create_web_app(
        application=_FakeApplication(),
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
    )
    with TestClient(files_app) as client:
        for response in (
            client.get("/api/memories/feedback-runs"),
            client.get("/api/memories/feedback-runs/job-1"),
        ):
            assert response.status_code == 503
            assert response.json()["code"] == "memory_unavailable"


def _dreaming_stubs():
    """Shared fakes for the dreaming observability routes."""
    archive_pending = SimpleNamespace(
        archive_id="archive:s1:10",
        session_id=None,  # bound per test below
        turn_range_start=0,
        turn_range_end=9,
        max_event_seq=10,
        add_operation_ids=("op-add-1", "op-add-missing"),
        status="pending",
        dreaming_job_id="dj-1",
        created_at=2.0,
    )
    dream_job = SimpleNamespace(
        job_id="dj-1",
        status="completed",
        attempts=1,
        last_error=None,
        result={
            "dreaming_record_id": "dr-1",
            "produced_memory_ids": ["m1"],
            "actions": [{"action_id": "a:0", "action": "add", "memory_id": "m1"}],
            "raw_verified": True,
            "missing_ids": [],
        },
    )
    seed_job = SimpleNamespace(job_id="tj-1", turn_id="turn-3")
    return archive_pending, dream_job, seed_job


def test_dreaming_sessions_list_aggregate_states() -> None:
    """Every session appears; aggregate badge follows most-active archive."""
    archive_pending, dream_job, _ = _dreaming_stubs()
    archive_dreamed = SimpleNamespace(
        **{**vars(archive_pending), "archive_id": "archive:s2:5", "status": "dreamed",
           "dreaming_job_id": "dj-2", "max_event_seq": 5, "created_at": 1.0}
    )
    archive_failed = SimpleNamespace(
        **{**vars(archive_pending), "archive_id": "archive:s3:3", "status": "failed",
           "dreaming_job_id": "dj-3", "max_event_seq": 3, "created_at": 0.5}
    )
    store = SimpleNamespace(
        list_archives=lambda limit=500: [archive_pending, archive_dreamed, archive_failed],
    )
    application = _FakeApplication()
    application.settings = SimpleNamespace(
        application_services={"memory_work_store": store}
    )
    app = create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
        memory_management_service=_FakeMemoryManagementService(),
    )

    with TestClient(app) as client:
        s_pending = client.post("/api/sessions").json()["session_id"]
        s_done = client.post("/api/sessions").json()["session_id"]
        s_failed = client.post("/api/sessions").json()["session_id"]
        s_unarchived = client.post("/api/sessions").json()["session_id"]
        archive_pending.session_id = s_pending
        archive_dreamed.session_id = s_done
        archive_failed.session_id = s_failed

        listed = client.get("/api/memories/dreaming-sessions")
        assert listed.status_code == 200
        rows = {row["session_id"]: row for row in listed.json()["sessions"]}
        assert set(rows) == {s_pending, s_done, s_failed, s_unarchived}
        assert rows[s_pending]["archive_state"] == "processing"
        assert rows[s_pending]["archive_count"] == 1
        assert rows[s_pending]["last_archive_at"] == 2.0
        assert rows[s_done]["archive_state"] == "completed"
        assert rows[s_failed]["archive_state"] == "failed"
        assert rows[s_unarchived]["archive_state"] == "unarchived"
        assert rows[s_unarchived]["archive_count"] == 0
        assert rows[s_unarchived]["last_archive_at"] is None

    files_app = create_web_app(
        application=_FakeApplication(),
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
    )
    with TestClient(files_app) as client:
        response = client.get("/api/memories/dreaming-sessions")
        assert response.status_code == 503
        assert response.json()["code"] == "memory_unavailable"


def test_dreaming_session_detail_seeds_and_diffs() -> None:
    """Detail resolves seeds exactly via operation ids and reuses action diffs."""
    archive, dream_job, seed_job = _dreaming_stubs()
    events = [
        SimpleNamespace(payload={"role": "user", "content": "我下周去爬山"}),
        SimpleNamespace(payload={"role": "assistant", "content": "好的"}),
    ]
    store = SimpleNamespace(
        archives_for_session=lambda session_id: [archive],
        job=lambda job_id: {"dj-1": dream_job}.get(job_id),
        job_by_operation_key=lambda key: {"op-add-1": seed_job}.get(key),
        turns_for_session=lambda session_id: [SimpleNamespace(turn_id="turn-3")],
        events_for_turn=lambda session_id, turn_id: events,
    )
    inspector = SimpleNamespace(
        action_diffs=lambda actions: _coro(
            [
                SimpleNamespace(
                    action_id="a:0",
                    action="add",
                    memory_id="m1",
                    memory_session_id="s1",
                    before=None,
                    after=SimpleNamespace(content="喜欢爬山", status="active"),
                    missing=False,
                )
            ]
        ),
    )
    application = _FakeApplication()
    application.settings = SimpleNamespace(
        application_services={
            "memory_work_store": store,
            "feedback_run_inspector": inspector,
        }
    )
    app = create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
        memory_management_service=_FakeMemoryManagementService(),
    )

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        archive.session_id = session_id

        detail = client.get(f"/api/memories/dreaming-sessions/{session_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["archive_state"] == "processing"
        assert body["session_excerpts"] == [
            {"turn_id": "turn-3", "preview": "我下周去爬山"}
        ]
        assert len(body["archives"]) == 1
        view = body["archives"][0]
        assert view["archive_id"] == "archive:s1:10"
        assert view["state"] == "processing"
        assert view["turn_range"] == [0, 9]
        assert view["job"]["dreaming_record_id"] == "dr-1"
        assert view["job"]["raw_verified"] is True
        assert view["job"]["extracted_count"] == 1
        assert view["seeds"] == [
            {
                "operation_id": "op-add-1",
                "turn_id": "turn-3",
                "preview": "我下周去爬山",
                "missing": False,
            },
            {
                "operation_id": "op-add-missing",
                "turn_id": None,
                "preview": None,
                "missing": True,
            },
        ]
        assert view["diffs_unavailable"] is False
        assert view["actions"] == [
            {
                "action_id": "a:0",
                "action": "add",
                "memory_id": "m1",
                "memory_session_id": "s1",
                "before": None,
                "after": {"content": "喜欢爬山", "status": "active"},
                "missing": False,
            }
        ]

        assert client.get("/api/memories/dreaming-sessions/nope").status_code == 404


def test_dreaming_session_detail_unarchived_and_inspector_missing() -> None:
    """Unarchived session still renders (empty archives); absent inspector
    degrades to diffs_unavailable rather than an empty list."""
    store = SimpleNamespace(
        archives_for_session=lambda session_id: [],
        job=lambda job_id: None,
        turns_for_session=lambda session_id: [SimpleNamespace(turn_id="turn-1")],
        events_for_turn=lambda session_id, turn_id: [
            SimpleNamespace(payload={"role": "user", "content": "你好"})
        ],
    )
    application = _FakeApplication()
    application.settings = SimpleNamespace(
        application_services={"memory_work_store": store}
    )
    app = create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
        memory_management_service=_FakeMemoryManagementService(),
    )

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        detail = client.get(f"/api/memories/dreaming-sessions/{session_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["archive_state"] == "unarchived"
        assert body["archives"] == []
        assert body["session_excerpts"] == [{"turn_id": "turn-1", "preview": "你好"}]

    # Inspector absent with a real archive -> diffs_unavailable flag.
    archive, dream_job, _ = _dreaming_stubs()
    store2 = SimpleNamespace(
        archives_for_session=lambda session_id: [archive],
        job=lambda job_id: dream_job,
        job_by_operation_key=lambda key: None,
        turns_for_session=lambda session_id: [],
    )
    application2 = _FakeApplication()
    application2.settings = SimpleNamespace(
        application_services={"memory_work_store": store2}
    )
    app2 = create_web_app(
        application=application2,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
        memory_management_service=_FakeMemoryManagementService(),
    )
    with TestClient(app2) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        archive.session_id = session_id
        detail = client.get(f"/api/memories/dreaming-sessions/{session_id}")
        assert detail.status_code == 200
        view = detail.json()["archives"][0]
        assert view["actions"] is None
        assert view["diffs_unavailable"] is True
        assert view["seeds"][0]["missing"] is True


def test_session_attachment_upload_and_message_delivery(tmp_path: Path) -> None:
    import base64

    application = _FakeApplication()
    application.working_directory = tmp_path
    app = create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
    )

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        encoded = base64.b64encode(b"hello attachment content").decode("ascii")
        uploaded = client.post(
            f"/api/sessions/{session_id}/attachments",
            json={
                "filename": "notes.txt",
                "media_type": "text/plain",
                "data_base64": encoded,
            },
        )
        assert uploaded.status_code == 201
        att = uploaded.json()
        assert att["filename"] == "notes.txt"
        assert att["size"] == len(b"hello attachment content")
        files = list((tmp_path / "var" / "web_attachments").rglob("*notes.txt"))
        assert len(files) == 1
        assert files[0].read_bytes() == b"hello attachment content"


def test_web_route_table_is_stable() -> None:
    """Pin the public API surface so route refactors cannot silently drift.

    Splitting ``create_web_app`` into ``web/routes/`` changed where handlers
    live; this snapshot locks the externally visible (method, path) table so
    future moves still have to update this file deliberately.
    """

    from fastapi.routing import APIRoute

    app = create_web_app(
        application=_FakeApplication(),
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
    )
    actual = sorted(
        (route.path, tuple(sorted(route.methods)))
        for route in app.routes
        if isinstance(route, APIRoute)
    )
    expected = [
        ("/api/approvals/{approval_id}", ("GET",)),
        ("/api/approvals/{approval_id}", ("POST",)),
        ("/api/approvals/{approval_id}/cancel", ("POST",)),
        ("/api/archive-status", ("GET",)),
        ("/api/artifacts/{artifact_handle}", ("GET",)),
        ("/api/doctor", ("GET",)),
        ("/api/health", ("GET",)),
        ("/api/memories", ("GET",)),
        ("/api/memories", ("POST",)),
        ("/api/memories/dreaming-sessions", ("GET",)),
        ("/api/memories/dreaming-sessions/{session_id}", ("GET",)),
        ("/api/memories/feedback-runs", ("GET",)),
        ("/api/memories/feedback-runs/{job_id}", ("GET",)),
        ("/api/memories/graph", ("GET",)),
        ("/api/memories/implicit-feedback", ("POST",)),
        ("/api/memories/implicit-feedback/{job_id}", ("GET",)),
        ("/api/memories/search", ("GET",)),
        ("/api/memories/{memory_id}", ("DELETE",)),
        ("/api/memories/{memory_id}", ("GET",)),
        ("/api/memories/{memory_id}", ("PATCH",)),
        ("/api/memories/{memory_id}/history", ("GET",)),
        ("/api/meta", ("GET",)),
        ("/api/permissions/grants", ("GET",)),
        ("/api/permissions/grants/{grant_id}/revoke", ("POST",)),
        ("/api/provider-templates", ("GET",)),
        ("/api/providers", ("GET",)),
        ("/api/providers", ("POST",)),
        ("/api/providers/test", ("POST",)),
        ("/api/providers/{name}", ("DELETE",)),
        ("/api/providers/{name}", ("PATCH",)),
        ("/api/providers/{name}/models", ("GET",)),
        ("/api/sessions", ("GET",)),
        ("/api/sessions", ("POST",)),
        ("/api/sessions/{session_id}", ("DELETE",)),
        ("/api/sessions/{session_id}", ("PATCH",)),
        ("/api/sessions/{session_id}/approvals", ("GET",)),
        ("/api/sessions/{session_id}/archive", ("POST",)),
        ("/api/sessions/{session_id}/archive-status", ("GET",)),
        ("/api/sessions/{session_id}/attachments", ("POST",)),
        ("/api/sessions/{session_id}/cancel", ("POST",)),
        ("/api/sessions/{session_id}/compact", ("POST",)),
        ("/api/sessions/{session_id}/history", ("GET",)),
        ("/api/sessions/{session_id}/messages", ("POST",)),
        ("/api/sessions/{session_id}/mode", ("POST",)),
        ("/api/sessions/{session_id}/questions", ("GET",)),
        ("/api/sessions/{session_id}/questions/{question_id}/answer", ("POST",)),
        ("/api/sessions/{session_id}/status", ("GET",)),
        ("/api/sessions/{session_id}/stream", ("GET",)),
        ("/api/skill-evolution", ("GET",)),
        ("/api/skill-evolution/{cloud_skill_id}", ("GET",)),
        ("/api/skill-evolution/{cloud_skill_id}/versions/{version_id}/content", ("GET",)),
        ("/api/skills", ("GET",)),
        ("/api/skills/import", ("POST",)),
        ("/api/skills/resolve", ("POST",)),
        ("/api/skills/{name}", ("DELETE",)),
    ]
    assert actual == expected
