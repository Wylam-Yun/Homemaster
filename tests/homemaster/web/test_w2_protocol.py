"""W2 server-authoritative protocol surface tests.

Covers provider/meta discovery, session status + compaction, Plan/Act mode
switching, pending-approval recovery after disconnect, ask_user question
lifecycle, skill resolution, and the enriched ``run.completed`` payload.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from perm_harness import (
    EventSink,
    FakeClock,
    make_context,
    make_request,
    wait_pending,
)

from homemaster.application import RunRequest, SessionManager
from homemaster.application.runtime import CompactionResult, SessionStatus
from homemaster.config.config import HomeMasterConfig, ProviderProfileConfig
from homemaster.events.bus import EventBus
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.permissions.store import PermissionStore
from homemaster.skills.types import SkillDefinition
from homemaster.tools.contracts import ToolExecutionContext
from homemaster.tools.runtime_services import HomePlanModeService
from homemaster.tools.service_tools import build_service_tools
from homemaster.web.app import create_web_app
from homemaster.web.confirmations import WebConfirmationHandler
from homemaster.web.pending_questions import (
    PendingQuestionRegistry,
    QuestionCancelledError,
)


class _FakeSkillRegistry:
    """In-memory stand-in honouring the SkillRegistry lookup contract."""

    def __init__(self) -> None:
        self.refreshes = 0
        self.skills: dict[str, SkillDefinition] = {}

    def refresh(self) -> None:
        self.refreshes += 1

    def get(self, name: str) -> SkillDefinition | None:
        return self.skills.get(name)


def _providers() -> list[ProviderProfileConfig]:
    return [
        ProviderProfileConfig(
            name="main",
            api_format="anthropic",
            base_url="https://api.anthropic.com",
            model="claude-main",
            api_keys=("sk-live-secret",),
        ),
        ProviderProfileConfig(
            name="backup",
            api_format="openai",
            base_url="https://api.openai.com/v1",
            model="gpt-backup",
            api_keys=(),
        ),
    ]


def _config() -> HomeMasterConfig:
    return HomeMasterConfig(
        providers={"default": "main", "items": _providers()},
    )


class _W2Application:
    """Fake application exposing the W2 surface the adapter consumes."""

    def __init__(self, *, ask_gate: asyncio.Event | None = None) -> None:
        self.event_bus = EventBus()
        self.session_manager = SessionManager()
        self.settings = SimpleNamespace(
            application_services={
                "plan_mode": HomePlanModeService(),
                "skill_registry": _FakeSkillRegistry(),
            }
        )
        self.run_requests: list[RunRequest] = []
        self.compact_calls: list[str] = []
        self.started = False
        self.closed = False
        self.run_tasks: dict[str, set[asyncio.Task[object]]] = {}
        # Tests inject ``app.state.pending_questions`` so a leaking run can
        # deterministically wait until the question is registered.
        self.question_registry: PendingQuestionRegistry | None = None

    async def start(self) -> None:
        self.started = True

    def status(self, session_id: str) -> SessionStatus:
        runtime = self.session_manager.get(session_id)
        return SessionStatus(
            session_id=session_id,
            generation=runtime.generation,
            revision=runtime.revision,
            status="idle",
            active=runtime.active_task is not None,
            cancellation_requested=False,
            task_status=None,
            environment_ref=None,
        )

    async def compact(self, session_id: str) -> CompactionResult:
        self.session_manager.get(session_id)
        self.compact_calls.append(session_id)
        return CompactionResult(
            session_id=session_id,
            generation=3,
            revision=7,
            triggered=True,
            kind="manual",
        )

    def cancel(self, session_id: str) -> bool:
        # Mirror the real SessionManager contract: unknown sessions raise
        # KeyError, an idle session returns False, an active run is cancelled.
        self.session_manager.get(session_id)
        tasks = self.run_tasks.get(session_id)
        if not tasks:
            return False
        for task in tuple(tasks):
            task.cancel()
        return True

    async def run(self, request: RunRequest) -> object:
        self.run_requests.append(request)
        session_id = request.session_id or ""
        task = asyncio.current_task()
        if task is not None:
            self.run_tasks.setdefault(session_id, set()).add(task)
        try:
            return await self._run(request, session_id)
        finally:
            if task is not None:
                tasks = self.run_tasks.get(session_id)
                if tasks is not None:
                    tasks.discard(task)
                    if not tasks:
                        self.run_tasks.pop(session_id, None)

    async def _run(self, request: RunRequest, session_id: str) -> object:
        await self._emit("runtime.turn_started", session_id, {})
        prompt = request.dependencies.get("ask_user_prompt")
        if request.text.startswith("hang:"):
            # A bound run that stays in-flight until cancelled — the web layer
            # must terminalize it even though no terminal event is emitted.
            await asyncio.Event().wait()
            return SimpleNamespace(status="unreachable", run_id="run-01")
        if request.text.startswith("boom:"):
            # A bound run that dies mid-flight with a propagated exception
            # (e.g. AutomaticRecallRunDeadlineExceeded): fail_before_start
            # refuses once run_id is bound, so the adapter's finish marker
            # must produce the honest terminal instead of wedging busy.
            raise RuntimeError("propagated mid-run failure")
        if request.text.startswith("real-ask:") and callable(prompt):
            # Drive the real ask_user_question service tool with the exact
            # contract the runtime uses: services -> metadata -> prompt().
            await asyncio.sleep(0.05)
            result = await _ASK_USER_EXECUTOR.execute(
                {"question": request.text.removeprefix("real-ask:")},
                ToolExecutionContext(
                    session_id=session_id,
                    run_id="run-01",
                    turn_index=0,
                    tool_call_id="call-ask-01",
                    internal_tool_id="homemaster.ask_user_question.v1",
                    permission_subject=request.permission_subject,
                    backend=None,
                    deadline=None,
                    cancellation=None,
                    domain_observer=None,
                    working_directory=Path.cwd(),
                    services={"ask_user_prompt": prompt},
                ),
            )
            answer = result.text
            await self._emit(
                "assistant.reply", session_id, {"reply": f"answer:{answer}"}
            )
            await self._emit(
                "runtime.turn_completed",
                session_id,
                {"final_reply": f"final:{answer}"},
            )
            return SimpleNamespace(status="replied", run_id="run-01")
        if request.text.startswith("ask:") and callable(prompt):
            # Give the hub pump a beat so the run is bound before asking.
            await asyncio.sleep(0.05)
            answer = await prompt(request.text.removeprefix("ask:"))
            await self._emit(
                "assistant.reply", session_id, {"reply": f"answer:{answer}"}
            )
            await self._emit(
                "runtime.turn_completed",
                session_id,
                {"final_reply": f"final:{answer}"},
            )
            return SimpleNamespace(status="replied", run_id="run-01")
        if request.text.startswith("leak:") and callable(prompt):
            # Simulate a run whose tool call outlives the terminal event.
            question_task = asyncio.get_running_loop().create_task(
                prompt(request.text.removeprefix("leak:"))
            )
            question_task.add_done_callback(
                lambda task: None if task.cancelled() else task.exception()
            )
            if self.question_registry is not None:
                for _ in range(200):
                    if self.question_registry.pending_count >= 1:
                        break
                    await asyncio.sleep(0.01)
        await self._emit(
            "runtime.turn_completed",
            session_id,
            {"final_reply": "done"},
        )
        return SimpleNamespace(status="replied", run_id="run-01")

    async def _emit(self, event_type: str, session_id: str, payload: dict) -> None:
        await self.event_bus.aemit(
            RuntimeEvent(
                type=event_type,
                session_id=session_id,
                run_id="run-01",
                turn_index=0,
                payload=payload,
            )
        )

    async def aclose(self) -> None:
        self.closed = True
        await self.event_bus.aclose()


_ASK_USER_EXECUTOR = next(
    tool.executor
    for tool in build_service_tools()
    if tool.definition.internal_id == "homemaster.ask_user_question.v1"
)


def _app(
    application: _W2Application,
    *,
    config: HomeMasterConfig | None = None,
    store: PermissionStore | None = None,
    handler: WebConfirmationHandler | None = None,
) -> object:
    return create_web_app(
        application=application,
        confirmation_handler=handler or WebConfirmationHandler(timeout_s=None),
        permission_store=store,
        config=config,
    )


def test_meta_reports_version_memory_mode_and_environment() -> None:
    app = _app(_W2Application(), config=_config())

    with TestClient(app) as client:
        response = client.get("/api/meta")

    assert response.status_code == 200
    assert response.json() == {
        "version": importlib.metadata.version("homemaster"),
        "memory_mode": "files",
        "environment": "home",
    }


def test_providers_lists_profiles_without_leaking_keys() -> None:
    app = _app(_W2Application(), config=_config())

    with TestClient(app) as client:
        response = client.get("/api/providers")

    assert response.status_code == 200
    assert response.json() == {
        "providers": [
            {
                "name": "main",
                "kind": "chat",
                "model": "claude-main",
                "api_key_configured": True,
            },
            {
                "name": "backup",
                "kind": "chat",
                "model": "gpt-backup",
                "api_key_configured": False,
            },
        ]
    }
    assert "sk-live-secret" not in response.text


def test_send_message_maps_provider_and_model_to_run_request() -> None:
    application = _W2Application()
    app = _app(application, config=_config())

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        with client.websocket_connect(
            f"/api/events?session_id={session_id}"
        ) as websocket:
            accepted = client.post(
                f"/api/sessions/{session_id}/messages",
                json={
                    "request_id": "req-provider",
                    "text": "hello",
                    "provider_name": "main",
                    "model": "claude-main",
                },
            )
            assert accepted.status_code == 202
            while websocket.receive_json()["type"] != "run.completed":
                pass

    (request,) = application.run_requests
    assert request.provider_name == "main"
    assert request.model_override == "claude-main"


def test_send_message_rejects_unknown_ambiguous_or_conflicting_model() -> None:
    ambiguous = HomeMasterConfig(
        providers={
            "default": "main",
            "items": [
                *_providers(),
                ProviderProfileConfig(
                    name="third",
                    api_format="openai",
                    base_url="https://api.openai.com/v1",
                    model="claude-main",
                ),
            ],
        }
    )
    application = _W2Application()
    app = _app(application, config=ambiguous)

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        with client.websocket_connect(f"/api/events?session_id={session_id}"):
            unknown = client.post(
                f"/api/sessions/{session_id}/messages",
                json={"request_id": "req-u", "text": "x", "model": "ghost-model"},
            )
            assert unknown.status_code == 422
            assert unknown.json()["code"] == "unknown_model"
            assert unknown.json()["retryable"] is False

            ambiguous_model = client.post(
                f"/api/sessions/{session_id}/messages",
                json={
                    "request_id": "req-a",
                    "text": "x",
                    "model": "claude-main",
                },
            )
            assert ambiguous_model.status_code == 422
            assert ambiguous_model.json()["code"] == "unknown_model"

            conflict = client.post(
                f"/api/sessions/{session_id}/messages",
                json={
                    "request_id": "req-c",
                    "text": "x",
                    "provider_name": "backup",
                    "model": "main",
                },
            )
            assert conflict.status_code == 422
            assert conflict.json()["code"] == "unknown_model"

            unknown_provider = client.post(
                f"/api/sessions/{session_id}/messages",
                json={
                    "request_id": "req-p",
                    "text": "x",
                    "provider_name": "ghost",
                },
            )
            assert unknown_provider.status_code == 422
            assert unknown_provider.json()["code"] == "unknown_provider"

    assert application.run_requests == []


def test_status_reports_runtime_fields_and_ui_mode() -> None:
    application = _W2Application()
    plan_mode = application.settings.application_services["plan_mode"]
    app = _app(application, config=_config())

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]

        status = client.get(f"/api/sessions/{session_id}/status")
        assert status.status_code == 200
        body = status.json()
        assert body["session_id"] == session_id
        assert body["status"] == "idle"
        assert body["active"] is False
        assert body["ui_mode"] == "act"

        plan_mode.set(session_id, True)
        assert client.get(f"/api/sessions/{session_id}/status").json()["ui_mode"] == "plan"

        missing = client.get("/api/sessions/session-ghost/status")
        assert missing.status_code == 404
        assert missing.json()["code"] == "session_not_found"


def test_mode_endpoint_switches_plan_mode_and_broadcasts() -> None:
    application = _W2Application()
    plan_mode = application.settings.application_services["plan_mode"]
    app = _app(application, config=_config())

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        with client.websocket_connect(
            f"/api/events?session_id={session_id}"
        ) as websocket:
            switched = client.post(
                f"/api/sessions/{session_id}/mode", json={"ui_mode": "plan"}
            )
            assert switched.status_code == 200
            assert switched.json() == {
                "session_id": session_id,
                "ui_mode": "plan",
            }
            assert plan_mode.enabled(session_id) is True
            event = websocket.receive_json()
            assert event["type"] == "session.mode_changed"
            assert event["session_id"] == session_id
            assert event["payload"] == {"ui_mode": "plan"}

            back = client.post(
                f"/api/sessions/{session_id}/mode", json={"ui_mode": "act"}
            )
            assert back.json()["ui_mode"] == "act"
            assert plan_mode.enabled(session_id) is False
            event = websocket.receive_json()
            assert event["payload"] == {"ui_mode": "act"}

        invalid = client.post(
            f"/api/sessions/{session_id}/mode", json={"ui_mode": "turbo"}
        )
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "invalid_request"


def test_service_driven_mode_flip_and_rest_emit_one_event_per_transition() -> None:
    """Tool-driven ``plan_mode.set`` broadcasts like the REST /mode path."""

    application = _W2Application()
    plan_mode = application.settings.application_services["plan_mode"]
    app = _app(application, config=_config())

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        with client.websocket_connect(
            f"/api/events?session_id={session_id}"
        ) as websocket:
            # enter/exit_plan_mode call plan_mode.set directly; the registered
            # listener bridges onto the hub loop from this foreign thread.
            plan_mode.set(session_id, True)
            event = websocket.receive_json()
            assert event["type"] == "session.mode_changed"
            assert event["session_id"] == session_id
            assert event["payload"] == {"ui_mode": "plan"}

            # A no-op set must emit nothing — a duplicate frame here would
            # surface as the next received event and fail the checks below.
            plan_mode.set(session_id, True)

            # The REST path rides the same listener now: still exactly one.
            switched = client.post(
                f"/api/sessions/{session_id}/mode", json={"ui_mode": "act"}
            )
            assert switched.status_code == 200
            event = websocket.receive_json()
            assert event["type"] == "session.mode_changed"
            assert event["session_id"] == session_id
            assert event["payload"] == {"ui_mode": "act"}

            # A duplicate REST write also emits nothing; the following real
            # transition would be masked by any leftover duplicate frame.
            client.post(f"/api/sessions/{session_id}/mode", json={"ui_mode": "act"})
            plan_mode.set(session_id, True)
            event = websocket.receive_json()
            assert event["type"] == "session.mode_changed"
            assert event["payload"] == {"ui_mode": "plan"}
            assert plan_mode.enabled(session_id) is True


def test_compact_returns_result_and_conflicts_while_busy() -> None:
    application = _W2Application()
    app = _app(application, config=_config())

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        with client.websocket_connect(
            f"/api/events?session_id={session_id}"
        ) as websocket:
            compacted = client.post(f"/api/sessions/{session_id}/compact")
            assert compacted.status_code == 200
            assert compacted.json() == {
                "session_id": session_id,
                "generation": 3,
                "revision": 7,
                "triggered": True,
                "kind": "manual",
            }
            assert application.compact_calls == [session_id]

            # A run suspended on a pending question keeps the session busy.
            client.post(
                f"/api/sessions/{session_id}/messages",
                json={"request_id": "req-q1", "text": "ask:阻塞中"},
            )
            while websocket.receive_json()["type"] != "question.asked":
                pass
            busy = client.post(f"/api/sessions/{session_id}/compact")
            assert busy.status_code == 409
            assert busy.json()["code"] == "session_busy"

            questions = client.get(f"/api/sessions/{session_id}/questions").json()[
                "questions"
            ]
            qid = questions[0]["question_id"]
            client.post(
                f"/api/sessions/{session_id}/questions/{qid}/answer",
                json={"text": "好"},
            )
            while websocket.receive_json()["type"] != "run.completed":
                pass

        missing = client.post("/api/sessions/session-ghost/compact")
        assert missing.status_code == 404


def test_question_lifecycle_ask_answer_reconnect_recovery_and_cleanup() -> None:
    application = _W2Application()
    app = _app(application, config=_config())

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        with client.websocket_connect(
            f"/api/events?session_id={session_id}"
        ) as websocket:
            accepted = client.post(
                f"/api/sessions/{session_id}/messages",
                json={"request_id": "req-ask", "text": "ask:哪个杯子？"},
            )
            assert accepted.status_code == 202
            assert websocket.receive_json()["type"] == "request.accepted"
            assert websocket.receive_json()["type"] == "run.started"
            asked = websocket.receive_json()
            assert asked["type"] == "question.asked"
            question_id = asked["payload"]["question_id"]
            assert asked["payload"]["question"] == "哪个杯子？"
            assert asked["payload"]["tool_call_id"] == ""
            assert asked["request_id"] == "req-ask"

            # Reconnect-style recovery: the same pending question is listable.
            listed = client.get(f"/api/sessions/{session_id}/questions")
            (entry,) = listed.json()["questions"]
            assert entry["question_id"] == question_id
            assert entry["question"] == "哪个杯子？"
            assert entry["run_id"] == "run-01"
            assert entry["request_id"] == "req-ask"

            foreign = client.post(
                f"/api/sessions/{session_id}-other/questions/{question_id}/answer",
                json={"text": "x"},
            )
            assert foreign.status_code == 404
            assert foreign.json()["code"] == "session_not_found"

            other_session = client.post("/api/sessions").json()["session_id"]
            wrong_session = client.post(
                f"/api/sessions/{other_session}/questions/{question_id}/answer",
                json={"text": "x"},
            )
            assert wrong_session.status_code == 404
            assert wrong_session.json()["code"] == "question_not_found"

            answered = client.post(
                f"/api/sessions/{session_id}/questions/{question_id}/answer",
                json={"text": "蓝色"},
            )
            assert answered.status_code == 200
            assert answered.json() == {"accepted": True}
            resolved = websocket.receive_json()
            assert resolved["type"] == "question.answered"
            assert resolved["payload"] == {"question_id": question_id}

            snapshot = websocket.receive_json()
            assert snapshot["type"] == "answer.snapshot"
            assert snapshot["payload"] == {"text": "answer:蓝色"}
            completed = websocket.receive_json()
            assert completed["type"] == "run.completed"
            assert completed["payload"] == {
                "status": "replied",
                "final_reply": "final:蓝色",
            }

            assert (
                client.get(f"/api/sessions/{session_id}/questions").json()["questions"]
                == []
            )
            again = client.post(
                f"/api/sessions/{session_id}/questions/{question_id}/answer",
                json={"text": "再答"},
            )
            assert again.status_code == 404
            assert again.json()["code"] == "question_not_found"


def test_questions_are_cancelled_when_their_run_ends() -> None:
    application = _W2Application()
    app = _app(application, config=_config())
    application.question_registry = app.state.pending_questions

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        with client.websocket_connect(
            f"/api/events?session_id={session_id}"
        ) as websocket:
            client.post(
                f"/api/sessions/{session_id}/messages",
                json={"request_id": "req-leak", "text": "leak:悬空问题"},
            )
            seen: set[str] = set()
            asked_id: str | None = None
            while not {"question.asked", "question.cancelled", "run.completed"} <= seen:
                frame = websocket.receive_json()
                seen.add(frame["type"])
                if frame["type"] == "question.asked":
                    asked_id = frame["payload"]["question_id"]
                if frame["type"] == "question.cancelled":
                    assert frame["payload"] == {"question_id": asked_id}
            assert asked_id is not None
            assert (
                client.get(f"/api/sessions/{session_id}/questions").json()["questions"]
                == []
            )


@pytest.mark.asyncio
async def test_pending_approvals_survive_websocket_disconnect(tmp_path: Path) -> None:
    clock = FakeClock()
    store = PermissionStore.open(tmp_path / "perm.sqlite3", clock=clock)
    sink = EventSink()
    handler = WebConfirmationHandler(store=store, timeout_s=None)
    application = _W2Application()
    app = _app(application, config=_config(), store=store, handler=handler)
    try:
        request = make_request(clock, "w2-durable")
        store.create_request(request)
        store.mark_awaiting_approval(request.request_id)
        await application.session_manager.open_or_resume(request.session_id)
        item_ids = [item.item_id for item in request.requirements]
        waiter = asyncio.create_task(
            handler.confirm(
                request,
                item_ids,
                make_context(tmp_path, sink, session_id=request.session_id),
            )
        )
        await wait_pending(handler)

        with TestClient(app) as client:
            # A subscriber connects and disconnects while the approval pends.
            with client.websocket_connect(
                f"/api/events?session_id={request.session_id}"
            ):
                pass

            # W2 durability: disconnect is NOT a decision — the approval stays
            # pending and remains listable for reconnect recovery.
            assert handler.pending_count == 1
            listed = client.get(f"/api/sessions/{request.session_id}/approvals")
            assert listed.status_code == 200
            (entry,) = listed.json()["approvals"]
            assert entry["approval_id"] == request.approval_id
            assert entry["request_status"] == "awaiting_approval"
            assert entry["intent_summary"] == request.intent_summary

            # ...and a reconnected client can still submit the real decision.
            item_id = item_ids[0]
            submitted = client.post(
                f"/api/approvals/{request.approval_id}",
                json={
                    "protocol_version": 2,
                    "submission_id": "sub-durable",
                    "request_revision": request.revision,
                    "decisions": [
                        {"item_id": item_id, "choice": "allow_once"}
                    ],
                },
            )
            assert submitted.status_code == 200
            assert submitted.json()["request_status"] == "ready"

        assert (await waiter).request_id == request.request_id
        assert handler.pending_count == 0
    finally:
        store.close()


def test_session_approvals_lists_only_pending_for_that_session(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store = PermissionStore.open(tmp_path / "perm.sqlite3", clock=clock)
    application = _W2Application()
    handler = WebConfirmationHandler(store=store, timeout_s=None)
    app = _app(application, config=_config(), store=store, handler=handler)

    async def exercise() -> None:
        import httpx

        handler = app.state.confirmation_handler
        request = make_request(clock, "w2-list")
        store.create_request(request)
        await application.session_manager.open_or_resume(request.session_id)
        waiter = asyncio.create_task(
            handler.confirm(
                request,
                [item.item_id for item in request.requirements],
                make_context(tmp_path, EventSink(), session_id=request.session_id),
            )
        )
        await wait_pending(handler)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            listed = await client.get(f"/api/sessions/{request.session_id}/approvals")
            assert listed.status_code == 200
            (entry,) = listed.json()["approvals"]
            assert entry["approval_id"] == request.approval_id

            other = await application.session_manager.open_or_resume("session-empty")
            empty = await client.get(
                f"/api/sessions/{other.session.session_id}/approvals"
            )
            assert empty.json()["approvals"] == []
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

    asyncio.run(exercise())


def test_skills_resolve_returns_skill_metadata_or_plain() -> None:
    application = _W2Application()
    registry = application.settings.application_services["skill_registry"]
    registry.skills["clean"] = SkillDefinition(
        name="clean",
        description="clean a room",
        content="Clean ${ARGUMENTS} now.",
        source="test",
    )
    registry.skills["hidden"] = SkillDefinition(
        name="hidden",
        description="internal",
        content="secret",
        source="test",
        user_invocable=False,
    )
    app = _app(application, config=_config())

    with TestClient(app) as client:
        plain = client.post("/api/skills/resolve", json={"text": "just text"})
        assert plain.json() == {"kind": "plain"}

        unknown = client.post("/api/skills/resolve", json={"text": "/ghost"})
        assert unknown.json() == {"kind": "plain"}

        resolved = client.post("/api/skills/resolve", json={"text": "/clean 卧室"})
        assert resolved.status_code == 200
        body = resolved.json()
        assert body["kind"] == "skill"
        assert body["name"] == "clean"
        assert body["arguments"] == "卧室"
        assert body["prompt"] == "Clean 卧室 now."
        assert body["model_override"] is None

        forbidden = client.post("/api/skills/resolve", json={"text": "/hidden"})
        assert forbidden.status_code == 422
        assert forbidden.json()["code"] == "skill_not_invocable"

        assert registry.refreshes >= 3


def test_skills_resolve_reports_unavailable_registry() -> None:
    application = _W2Application()
    application.settings.application_services.pop("skill_registry")
    app = _app(application, config=_config())

    with TestClient(app) as client:
        response = client.post("/api/skills/resolve", json={"text": "/clean"})

    assert response.status_code == 503
    assert response.json()["code"] == "skill_registry_unavailable"


def test_real_ask_user_tool_roundtrips_through_pending_questions() -> None:
    """The real ask_user_question executor drives the web prompt seam."""

    application = _W2Application()
    app = _app(application, config=_config())

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        with client.websocket_connect(
            f"/api/events?session_id={session_id}"
        ) as websocket:
            client.post(
                f"/api/sessions/{session_id}/messages",
                json={"request_id": "req-real", "text": "real-ask:哪层？"},
            )
            while True:
                frame = websocket.receive_json()
                if frame["type"] == "question.asked":
                    break
            question_id = frame["payload"]["question_id"]
            assert frame["payload"]["question"] == "哪层？"

            answered = client.post(
                f"/api/sessions/{session_id}/questions/{question_id}/answer",
                json={"text": "二楼"},
            )
            assert answered.json() == {"accepted": True}

            types: list[str] = []
            final: dict[str, str] = {}
            while True:
                frame = websocket.receive_json()
                types.append(frame["type"])
                if frame["type"] == "run.completed":
                    final = frame["payload"]
                    break
            assert types == ["question.answered", "answer.snapshot", "run.completed"]
            # The tool result text flows back through the run into the final.
            assert final == {"status": "replied", "final_reply": "final:二楼"}


def test_mode_endpoint_reports_unavailable_plan_mode_service() -> None:
    application = _W2Application()
    application.settings.application_services.pop("plan_mode")
    app = _app(application, config=_config())

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        response = client.post(
            f"/api/sessions/{session_id}/mode", json={"ui_mode": "plan"}
        )
        assert response.status_code == 503
        assert response.json()["code"] == "plan_mode_unavailable"
        # With no plan_mode service the session simply stays in act mode.
        assert (
            client.get(f"/api/sessions/{session_id}/status").json()["ui_mode"]
            == "act"
        )


@pytest.mark.asyncio
async def test_pending_question_registry_unit_semantics() -> None:
    registry = PendingQuestionRegistry()
    record = await registry.ask(
        session_id="s1",
        run_id="r1",
        request_id="req-1",
        question="哪个?",
        tool_call_id="call-1",
    )
    assert record.question_id.startswith("question-")
    assert [r.question_id for r in await registry.pending_for_session("s1")] == [
        record.question_id
    ]
    assert await registry.pending_for_session("s2") == []

    waiter = asyncio.create_task(record.wait())
    resolved = await registry.answer(
        session_id="s1", question_id=record.question_id, text="蓝色"
    )
    assert resolved is record
    assert await waiter == "蓝色"
    assert registry.pending_count == 0

    with pytest.raises(KeyError):
        await registry.answer(
            session_id="s1", question_id=record.question_id, text="x"
        )

    second = await registry.ask(
        session_id="s1", run_id="r2", request_id="req-2",
        question="哪间?", tool_call_id="",
    )
    third = await registry.ask(
        session_id="s2", run_id="r3", request_id="req-3",
        question="何时?", tool_call_id="",
    )
    # Another request on the same session is untouched by this request's end.
    same_session_other_request = await registry.ask(
        session_id="s1", run_id="r2b", request_id="req-2b",
        question="另外?", tool_call_id="",
    )
    dropped = await registry.cancel_request("s1", "req-2")
    assert [r.question_id for r in dropped] == [second.question_id]
    assert await registry.pending_for_session("s1") == [
        same_session_other_request
    ]
    with pytest.raises(QuestionCancelledError):
        await second.wait()
    assert [r.question_id for r in await registry.pending_for_session("s2")] == [
        third.question_id
    ]

    await registry.aclose()
    with pytest.raises(QuestionCancelledError):
        await third.wait()
    with pytest.raises(QuestionCancelledError):
        await registry.ask(
            session_id="s1", run_id="r4", request_id="req-4",
            question="还有?", tool_call_id="",
        )


def test_cancel_bound_run_terminalizes_and_frees_session() -> None:
    """Cancel kills the in-flight run; the web layer still emits the honest
    terminal and releases the session so the next message is not 409."""

    application = _W2Application()
    app = _app(application, config=_config())

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        with client.websocket_connect(
            f"/api/events?session_id={session_id}"
        ) as websocket:
            accepted = client.post(
                f"/api/sessions/{session_id}/messages",
                json={"request_id": "req-hang", "text": "hang:forever"},
            )
            assert accepted.status_code == 202
            while websocket.receive_json()["type"] != "run.started":
                pass

            cancelled = client.post(f"/api/sessions/{session_id}/cancel")
            assert cancelled.status_code == 200
            assert cancelled.json() == {
                "cancelled": True,
                "session_id": session_id,
            }

            # No runtime.cancelled reaches the bus (the run task was killed
            # before it could emit one) — the adapter must synthesize the
            # honest terminal itself, once.
            cancelled_frames = []
            while True:
                frame = websocket.receive_json()
                if frame["type"] == "run.cancelled":
                    cancelled_frames.append(frame)
                    break
            assert cancelled_frames == [
                {
                    "type": "run.cancelled",
                    "session_id": session_id,
                    "run_id": "run-01",
                    "request_id": "req-hang",
                    "payload": {},
                }
            ]

            # The session binding was released — no session_busy on retry.
            again = client.post(
                f"/api/sessions/{session_id}/messages",
                json={"request_id": "req-after", "text": "hello again"},
            )
            assert again.status_code == 202
            while websocket.receive_json()["type"] != "run.completed":
                pass

    assert [request.text for request in application.run_requests] == [
        "hang:forever",
        "hello again",
    ]


def test_mid_run_propagated_failure_terminalizes_and_frees_session() -> None:
    """A bound run whose exception escapes ``application.run`` must end in a
    truthful ``run.failed`` and release the session — not wedge as busy."""

    application = _W2Application()
    app = _app(application, config=_config())

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        with client.websocket_connect(
            f"/api/events?session_id={session_id}"
        ) as websocket:
            accepted = client.post(
                f"/api/sessions/{session_id}/messages",
                json={"request_id": "req-boom", "text": "boom:midrun"},
            )
            assert accepted.status_code == 202

            failed = None
            while failed is None:
                frame = websocket.receive_json()
                if frame["type"] == "run.failed":
                    failed = frame
                else:
                    assert frame["type"] in {"request.accepted", "run.started"}
            assert failed["request_id"] == "req-boom"
            assert failed["run_id"] == "run-01"
            assert failed["payload"]["code"] == "run_failed"
            assert failed["payload"]["retryable"] is False

            again = client.post(
                f"/api/sessions/{session_id}/messages",
                json={"request_id": "req-after", "text": "hello again"},
            )
            assert again.status_code == 202
            while websocket.receive_json()["type"] != "run.completed":
                pass


def test_cancel_on_persisted_but_unloaded_session_is_not_500(tmp_path: Path) -> None:
    """A persisted session not materialized in memory must produce a typed
    response — never an opaque 500 from ``session_manager.get`` KeyError."""

    async def seed() -> str:
        first_manager = SessionManager(session_root=tmp_path / "sessions")
        runtime = await first_manager.open_or_resume()
        session_id = runtime.session.session_id
        await first_manager.save(session_id)
        return session_id

    session_id = asyncio.run(seed())
    application = _W2Application()
    # A second manager on the same root lists the session but has no runtime.
    application.session_manager = SessionManager(session_root=tmp_path / "sessions")
    app = _app(application, config=_config())

    with TestClient(app) as client:
        response = client.post(f"/api/sessions/{session_id}/cancel")
        assert response.status_code == 200
        assert response.json() == {
            "cancelled": False,
            "session_id": session_id,
        }
        # The guard resumed the persisted runtime instead of erroring.
        status = client.get(f"/api/sessions/{session_id}/status")
        assert status.status_code == 200
        assert status.json()["session_id"] == session_id


def test_shutdown_enqueues_finalization_for_active_sessions() -> None:
    """Web/thin-client sessions never open an ApplicationSession, so
    ``session.close()`` never fires for them. On server shutdown the app must
    enqueue finalization for every materialized session instead of silently
    skipping the episode write — the regression that made web-mode sessions
    lose memory finalization entirely."""
    application = _W2Application()
    enqueued: list[tuple[str, str]] = []
    application.session_end_handler = lambda sid, reason: enqueued.append(
        (sid, reason)
    )
    app = _app(application, config=_config())

    with TestClient(app) as client:
        created = client.post("/api/sessions", json={})
        assert created.status_code == 201
        session_id = created.json()["session_id"]
    # Leaving the TestClient context runs lifespan teardown.

    assert (session_id, "server_shutdown") in enqueued


def test_archive_enqueues_finalization_and_returns_receipt() -> None:
    """POST /archive admits the session to the application-owned finalization
    queue — the manual counterpart of the shutdown/`/new` triggers."""
    application = _W2Application()
    enqueued: list[tuple[str, str]] = []

    def handler(sid: str, reason: str):
        enqueued.append((sid, reason))
        return SimpleNamespace(job_id="job-1", status="accepted")

    application.session_end_handler = handler
    app = _app(application, config=_config())

    with TestClient(app) as client:
        session_id = client.post("/api/sessions", json={}).json()["session_id"]
        archived = client.post(f"/api/sessions/{session_id}/archive")
        assert archived.status_code == 202
        assert archived.json() == {
            "session_id": session_id,
            "job_id": "job-1",
            "status": "accepted",
        }
        assert enqueued == [(session_id, "archived")]


def test_archive_reports_unavailable_without_full_tier() -> None:
    """files-mode applications have no session_end_handler — the route must
    say so instead of pretending the session was archived."""
    application = _W2Application()
    application.session_end_handler = None
    app = _app(application, config=_config())

    with TestClient(app) as client:
        session_id = client.post("/api/sessions", json={}).json()["session_id"]
        archived = client.post(f"/api/sessions/{session_id}/archive")
        assert archived.status_code == 503
        assert archived.json()["code"] == "archive_unavailable"


def test_archive_rejects_unknown_session() -> None:
    application = _W2Application()
    app = _app(application, config=_config())

    with TestClient(app) as client:
        response = client.post("/api/sessions/no-such-session/archive")
        assert response.status_code == 404
        assert response.json()["code"] == "session_not_found"


def test_shutdown_finalization_enqueue_failure_does_not_abort_close() -> None:
    """A raising ``session_end_handler`` must not prevent application
    teardown — one bad session cannot veto the rest of the shutdown."""
    application = _W2Application()

    def boom(sid: str, reason: str) -> None:
        raise RuntimeError("queue sealed")

    application.session_end_handler = boom
    app = _app(application, config=_config())

    with TestClient(app) as client:
        created = client.post("/api/sessions", json={})
        assert created.status_code == 201

    assert application.closed


__all__ = []
