"""P0-3 batch: session PATCH/DELETE, /api/skills, /api/health, /api/doctor,
and the WebEvent seq/ts envelope."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sse_support import asgi_client
from test_app import _FakeApplication

import homemaster
from homemaster.agent.messages import UserMessage
from homemaster.application import SessionManager
from homemaster.events.bus import EventBus
from homemaster.skills.registry import SkillLoadIssue, SkillProvenance, SkillRegistry
from homemaster.skills.types import SkillDefinition
from homemaster.web.app import create_web_app
from homemaster.web.confirmations import WebConfirmationHandler
from homemaster.web.event_hub import WebEventHub
from homemaster.web.event_projection import WebEventProjection
from homemaster.web.run_registry import WebRunRegistry
from homemaster.web.schemas import WebEvent


class _P03Application:
    """Minimal fake application surface the new endpoints consume."""

    def __init__(self, session_root=None, services: dict | None = None) -> None:
        self.event_bus = EventBus()
        self.session_manager = (
            SessionManager(session_root=session_root)
            if session_root is not None
            else SessionManager()
        )
        self.settings = SimpleNamespace(application_services=services or {})
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def run(self, request):  # pragma: no cover - unused here
        raise AssertionError("runs are not exercised by this suite")

    def cancel(self, session_id: str) -> bool:
        return False

    async def aclose(self) -> None:
        self.closed = True
        await self.event_bus.aclose()


def _app(application, **kwargs):
    return create_web_app(
        application=application,
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# PATCH /api/sessions/{id}


def test_patch_session_title_updates_summaries() -> None:
    application = _P03Application()
    app = _app(application)

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        renamed = client.patch(f"/api/sessions/{session_id}", json={"title": " renamed title "})
        assert renamed.status_code == 200
        assert renamed.json() == {
            "session_id": session_id,
            "title": "renamed title",
        }

        (entry,) = client.get("/api/sessions").json()["sessions"]
        assert entry["title"] == "renamed title"

        # Renaming must not clobber history or the derived title of others.
        runtime = application.session_manager.get(session_id)
        runtime.session.append(UserMessage.from_text("first user text"))
        entry = client.get("/api/sessions").json()["sessions"][0]
        assert entry["title"] == "renamed title"


def test_patch_session_title_validation_and_missing_session(tmp_path) -> None:
    application = _P03Application()
    app = _app(application)

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]

        assert client.patch(f"/api/sessions/{session_id}", json={"title": ""}).status_code == 422
        assert client.patch(f"/api/sessions/{session_id}", json={"title": "   "}).status_code == 422
        missing = client.patch("/api/sessions/session-missing", json={"title": "x"})
        assert missing.status_code == 404
        assert missing.json()["code"] == "session_not_found"


def test_rename_persists_across_manager_restarts(tmp_path) -> None:
    manager = SessionManager(session_root=tmp_path)

    async def _seed() -> None:
        runtime = await manager.open_or_resume("sess-01")
        runtime.session.append(UserMessage.from_text("original first text"))
        await manager.save("sess-01")

    asyncio.run(_seed())

    assert manager.rename_session("sess-01", "重启后保留的标题") == "重启后保留的标题"
    assert (
        json.loads((tmp_path / "sess-01" / "meta.json").read_text(encoding="utf-8"))[
            "title_override"
        ]
        == "重启后保留的标题"
    )

    fresh = SessionManager(session_root=tmp_path)
    (summary,) = fresh.session_summaries()
    assert summary.session_id == "sess-01"
    assert summary.title == "重启后保留的标题"


def test_rename_unknown_session_raises_key_error(tmp_path) -> None:
    manager = SessionManager(session_root=tmp_path)
    with pytest.raises(KeyError):
        manager.rename_session("ghost", "x")
    with pytest.raises(ValueError):
        manager.rename_session("ghost", "")


# ---------------------------------------------------------------------------
# DELETE /api/sessions/{id}


def test_delete_session_removes_listing_history_and_files(tmp_path) -> None:
    application = _P03Application(session_root=tmp_path)
    app = _app(application)

    async def _seed(session_id: str) -> None:
        runtime = await application.session_manager.open_or_resume(session_id)
        runtime.session.append(UserMessage.from_text("persist me"))
        await application.session_manager.save(session_id)

    session_id = "sess-delete-01"
    asyncio.run(_seed(session_id))
    assert (tmp_path / session_id).is_dir()

    with TestClient(app) as client:
        removed = client.delete(f"/api/sessions/{session_id}")
        assert removed.status_code == 204
        assert removed.content == b""

        listed = client.get("/api/sessions").json()["sessions"]
        assert all(entry["session_id"] != session_id for entry in listed)
        history = client.get(f"/api/sessions/{session_id}/history")
        assert history.status_code == 404
        assert history.json()["code"] == "session_not_found"

    # The hard delete removed the persisted files too.
    assert not (tmp_path / session_id).exists()
    assert session_id not in application.session_manager.list_session_ids()

    again = client.delete(f"/api/sessions/{session_id}")
    assert again.status_code == 404


def test_delete_in_memory_only_session() -> None:
    application = _P03Application()
    app = _app(application)

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        assert client.delete(f"/api/sessions/{session_id}").status_code == 204
        assert client.get("/api/sessions").json()["sessions"] == []


def test_delete_session_during_active_run_is_conflict(tmp_path) -> None:
    application = _P03Application(session_root=tmp_path)
    app = _app(application)

    async def _seed(session_id: str) -> None:
        runtime = await application.session_manager.open_or_resume(session_id)
        runtime.session.append(UserMessage.from_text("keep me"))
        await application.session_manager.save(session_id)

    session_id = "sess-busy-01"
    asyncio.run(_seed(session_id))

    with TestClient(app) as client:
        runtime = application.session_manager.get(session_id)
        # Bound-run sentinel: the delete path only cares that a task is bound.
        runtime.active_task = object()  # type: ignore[assignment]
        try:
            busy = client.delete(f"/api/sessions/{session_id}")
            assert busy.status_code == 409
            assert busy.json()["code"] == "session_busy"
            # Conflict means nothing was deleted.
            assert (tmp_path / session_id).is_dir()
            assert client.get("/api/sessions").json()["sessions"]
        finally:
            runtime.active_task = None


# ---------------------------------------------------------------------------
# GET /api/skills


def _skill_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(
        SkillDefinition(
            name="clean",
            description="clean a room",
            content="Clean ${ARGUMENTS}.",
            source="builtin",
            command_name="clean",
            user_invocable=True,
        ),
        provenance=SkillProvenance(source="builtin", path=Path("/b"), root=Path("/b")),
    )
    registry.register(
        SkillDefinition(
            name="research",
            description="user skill",
            content="research",
            source="user",
            command_name="research",
            user_invocable=False,
        ),
        provenance=SkillProvenance(source="user", path=Path("/u"), root=Path("/u")),
    )
    registry.register(
        SkillDefinition(
            name="deploy",
            description="bundled skill",
            content="deploy",
            source="bundled",
            command_name="deploy",
        ),
        provenance=SkillProvenance(source="bundled", path=Path("/d"), root=Path("/d")),
    )
    return registry


def test_list_skills_returns_readable_sources_and_enabled_flag() -> None:
    application = _P03Application(services={"skill_registry": _skill_registry()})
    app = _app(application)

    with TestClient(app) as client:
        response = client.get("/api/skills")

    assert response.status_code == 200
    skills = {skill["name"]: skill for skill in response.json()["skills"]}
    assert set(skills) == {"clean", "research", "deploy"}
    assert skills["clean"] == {
        "name": "clean",
        "command_name": "clean",
        "description": "clean a room",
        "source": "builtin",
        "user_invocable": True,
        "enabled": True,
        "deletable": False,
    }
    assert skills["research"]["source"] == "user"
    assert skills["research"]["user_invocable"] is False
    # Only the user skill root is deletable; bundled/builtin stay protected.
    assert skills["research"]["deletable"] is True
    # Bundled content surfaces under the builtin label for the UI.
    assert skills["deploy"]["source"] == "builtin"
    assert skills["deploy"]["deletable"] is False


def test_list_skills_reports_unavailable_registry() -> None:
    application = _P03Application()
    app = _app(application)

    with TestClient(app) as client:
        response = client.get("/api/skills")

    assert response.status_code == 503
    assert response.json()["code"] == "skill_registry_unavailable"


def test_list_skills_refreshes_registry_and_surfaces_load_issues() -> None:
    """GET /api/skills must re-scan sources, not serve a stale snapshot."""

    refreshed = _skill_registry()
    refreshed.record_issue(
        SkillLoadIssue(
            source="user",
            code="invalid_skill",
            detail="ValueError",
            path="/u/bad/SKILL.md",
        )
    )
    registry = SkillRegistry()
    calls = {"count": 0}

    def _refresher() -> SkillRegistry:
        calls["count"] += 1
        return refreshed

    registry.set_refresher(_refresher)
    application = _P03Application(services={"skill_registry": registry})
    app = _app(application)

    with TestClient(app) as client:
        response = client.get("/api/skills")

    assert response.status_code == 200
    assert calls["count"] == 1
    body = response.json()
    assert {skill["name"] for skill in body["skills"]} == {"clean", "research", "deploy"}
    assert body["issues"] == [
        {"source": "user", "code": "invalid_skill", "detail": "ValueError", "path": "/u/bad/SKILL.md"}
    ]


def test_list_skills_reports_refresh_failure() -> None:
    registry = SkillRegistry()

    def _failing() -> SkillRegistry:
        raise RuntimeError("disk gone")

    registry.set_refresher(_failing)
    application = _P03Application(services={"skill_registry": registry})
    app = _app(application)

    with TestClient(app) as client:
        response = client.get("/api/skills")

    assert response.status_code == 503
    assert response.json()["code"] == "skill_registry_unavailable"


# ---------------------------------------------------------------------------
# GET /api/health + GET /api/doctor


def test_health_reports_ok_and_package_version() -> None:
    app = _app(_P03Application())

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": homemaster.__version__}


def test_doctor_returns_structured_report(tmp_path) -> None:
    config_path = tmp_path / "homemaster.yaml"
    config_path.write_text(
        """
        providers:
          default: Mimo
          items:
            - name: Mimo
              kind: chat
              api_format: anthropic
              base_url: https://mimo.example/anthropic
              model: mimo-v2.5
              api_keys: [doctor-secret]
        memory:
          mode: files
        """,
        encoding="utf-8",
    )
    from homemaster.config import load_config

    config = load_config(config_path)
    app = _app(_P03Application(), config=config)

    with TestClient(app) as client:
        response = client.get("/api/doctor")

    assert response.status_code == 200
    payload = response.json()
    assert payload["live"] is False
    assert isinstance(payload["checks"], list) and payload["checks"]
    assert all({"name", "status", "message"} <= set(check) for check in payload["checks"])
    assert "doctor-secret" not in json.dumps(payload)


def test_doctor_endpoint_does_not_block_the_event_loop(tmp_path) -> None:
    config_path = tmp_path / "homemaster.yaml"
    config_path.write_text("memory:\n  mode: files\n", encoding="utf-8")
    from homemaster.config import load_config

    config = load_config(config_path)
    app = _app(_P03Application(), config=config)

    async def exercise() -> None:
        import httpx

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            doctor = asyncio.create_task(client.get("/api/doctor?live=false"))
            await asyncio.sleep(0)
            health = await asyncio.wait_for(client.get("/api/health"), timeout=2)
            report = await asyncio.wait_for(doctor, timeout=10)
        return health, report

    health, report = asyncio.run(exercise())
    assert health.status_code == 200
    assert report.status_code == 200
    assert report.json()["live"] is False


# ---------------------------------------------------------------------------
# WebEvent seq/ts envelope


@pytest.mark.asyncio
async def test_publish_stamps_monotonic_seq_and_ts_on_direct_events() -> None:
    hub = WebEventHub(EventBus(), WebRunRegistry(), WebEventProjection())
    first = await hub.subscribe("s1")
    other = await hub.subscribe("s2")

    # Direct publishes that bypass the pump — the path request.accepted,
    # question.*, session.mode_changed and fallback terminals all take.
    await hub.publish(
        WebEvent(
            type="session.mode_changed",
            session_id="s1",
            run_id="",
            request_id="",
            payload={"ui_mode": "plan"},
        )
    )
    await hub.publish(
        WebEvent(
            type="question.answered",
            session_id="s1",
            run_id="run-01",
            request_id="req-1",
            payload={"question_id": "q-1"},
        )
    )
    await hub.publish(
        WebEvent(
            type="answer.delta",
            session_id="s2",
            run_id="run-02",
            request_id="req-9",
            payload={"text": "x"},
        )
    )

    e1, e2 = first.get_nowait(), first.get_nowait()
    assert (e1.seq, e2.seq) == (1, 2)
    assert e1.ts > 0
    assert e2.ts >= e1.ts
    wire = e2.to_dict()
    assert wire["seq"] == 2 and isinstance(wire["ts"], float)

    # The sequence is per-session: the other session starts at 1.
    assert other.get_nowait().seq == 1


@pytest.mark.asyncio
async def test_stream_frames_carry_seq_and_ts_across_pump_and_direct_paths() -> None:
    """request.accepted is a direct publish; run.* frames come via the pump —
    every wire frame must share one per-session sequence, and the SSE ``id:``
    field mirrors that seq for the Last-Event-ID channel."""

    application = _FakeApplication()
    app = _app(application)

    async with asgi_client(app) as client:
        session_id = (await client.post("/api/sessions")).json()["session_id"]
        async with client.stream(session_id) as events:
            accepted = await client.post(
                f"/api/sessions/{session_id}/messages",
                json={"request_id": "request-01", "text": "hello"},
            )
            assert accepted.status_code == 202
            frames = [await events.receive_json() for _ in range(7)]
            assert events.last_id == str(frames[-1]["seq"])

    assert [frame["type"] for frame in frames] == [
        "request.accepted",
        "run.started",
        "thinking.delta",
        "answer.delta",
        "thinking.snapshot",
        "answer.snapshot",
        "run.completed",
    ]
    seqs = [frame["seq"] for frame in frames]
    assert seqs == sorted(seqs) == list(range(1, len(frames) + 1))
    assert all(isinstance(frame["ts"], (int, float)) and frame["ts"] > 0 for frame in frames)


# ---------------------------------------------------------------------------
# provider transport deprecation


def test_transport_field_warns_once_when_config_sets_it(tmp_path) -> None:
    from homemaster.config import config as config_module
    from homemaster.config import load_config

    config_module._warned_transport_providers.discard("legacy_probe")
    config_path = tmp_path / "homemaster.yaml"
    config_path.write_text(
        """
        providers:
          default: legacy_probe
          items:
            - name: legacy_probe
              api_format: anthropic
              transport: anthropic_sdk
              base_url: https://legacy.example
              model: mimo-v2.5
        """,
        encoding="utf-8",
    )

    with pytest.warns(DeprecationWarning, match="transport is deprecated"):
        config = load_config(config_path)
    # The field still parses — extra=forbid compatibility is preserved.
    assert config.providers.items[0].transport == "anthropic_sdk"

    # Second load for the same provider stays silent (warn once).
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_config(config_path)
    assert not any(isinstance(w.message, DeprecationWarning) for w in caught)


def test_transport_absent_means_no_deprecation_warning(tmp_path) -> None:
    import warnings

    from homemaster.config import load_config

    config_path = tmp_path / "homemaster.yaml"
    config_path.write_text(
        """
        providers:
          default: clean_probe
          items:
            - name: clean_probe
              api_format: anthropic
              base_url: https://clean.example
              model: mimo-v2.5
        """,
        encoding="utf-8",
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        load_config(config_path)
    assert not any("transport" in str(w.message) for w in caught)
