"""FastAPI surface and real DOM pages for the case_02 environment."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from case02_openenv.automation import AutomationEngine
from case02_openenv.config import ServiceConfig
from case02_openenv.episode_store import EpisodeError, EpisodeStore
from case02_openenv.models import (
    ActionEventRequest,
    ActionReservation,
    AutomationJobRequest,
    ConfigCheckRequest,
    DecisionRequest,
    MonitorQueryRequest,
    RunCreateRequest,
    RuntimeEventRequest,
    TerminalRequest,
    ToolPayload,
)
from case02_openenv.presentation import PresentationInput
from case02_openenv.public_views import automation_view, monitor_view, ticket_view
from case02_openenv.recording.display import DisplayManager
from case02_openenv.recording.recorder import DemoRecorder
from case02_openenv.terminal.executor import TerminalExecutor
from case02_openenv.terminal.policy import CommandPolicyError

PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def create_app(
    config: ServiceConfig | None = None,
    *,
    store: EpisodeStore | None = None,
    automation: AutomationEngine | None = None,
    terminal: TerminalExecutor | None = None,
) -> FastAPI:
    config = config or ServiceConfig.from_env()
    store = store or EpisodeStore(data_root=config.data_root, artifact_root=config.artifact_root)
    automation = automation or AutomationEngine(store)
    terminal = terminal or TerminalExecutor(store)
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    app.state.config = config
    app.state.store = store
    app.state.automation = automation
    app.state.terminal = terminal
    app.state.recorders = {}
    app.state.sse_idle_iterations = 300
    app.state.sse_poll_interval_s = 0.1
    templates = Jinja2Templates(directory=str(PACKAGE_ROOT / "templates"))
    app.mount("/static", StaticFiles(directory=str(PACKAGE_ROOT / "static")), name="static")

    @app.exception_handler(EpisodeError)
    async def handle_episode_error(_request: Request, exc: EpisodeError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "error_code": exc.code, "message": str(exc)},
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "schema_version": 1}

    @app.post("/api/runs")
    async def create_run(payload: RunCreateRequest) -> dict[str, Any]:
        state = store.create(payload.run_id, payload.scenario_id)
        return {"success": True, "run_id": state.run_id, "state": _state_payload(state)}

    @app.post("/api/runs/{run_id}/reset")
    async def reset_run(run_id: str) -> dict[str, Any]:
        state = store.reset(run_id)
        return {"success": True, "run_id": run_id, "state": _state_payload(state)}

    @app.get("/api/runs/{run_id}/state")
    async def get_state(run_id: str) -> dict[str, Any]:
        return {"success": True, "state": store.state(run_id).model_dump(mode="json")}

    @app.get("/api/runs/{run_id}/audit")
    async def get_audit(run_id: str) -> dict[str, Any]:
        return {
            "success": True,
            "events": [event.model_dump(mode="json") for event in store.audit(run_id)],
        }

    @app.post("/api/runs/{run_id}/presentation-events")
    async def post_presentation_event(
        run_id: str, payload: PresentationInput
    ) -> dict[str, Any]:
        event = store.record_presentation(run_id, payload)
        return {"success": True, "event": event.model_dump(mode="json")}

    @app.get("/api/runs/{run_id}/presentation")
    async def get_presentation(run_id: str) -> dict[str, Any]:
        generation, _, snapshot = store.presentation_stream_state(run_id)
        snapshot["presentation_generation"] = generation
        return {"success": True, "snapshot": snapshot}

    @app.get("/api/runs/{run_id}/presentation-events")
    async def presentation_events(
        request: Request,
        run_id: str,
        last_event_id: str | None = Header(default=None),
    ) -> StreamingResponse:
        initial_generation, initial_events, initial_snapshot = (
            store.presentation_stream_state(run_id)
        )
        initial_snapshot["presentation_generation"] = initial_generation

        async def stream():
            start = 0
            if last_event_id:
                for index, event in enumerate(initial_events):
                    if event.event_id == last_event_id:
                        start = index + 1
                        break
            snapshot_json = json.dumps(
                initial_snapshot, ensure_ascii=False, separators=(",", ":")
            )
            yield f"event: presentation.snapshot\ndata: {snapshot_json}\n\n"
            generation = initial_generation
            cursor = start
            idle = 0
            while idle < app.state.sse_idle_iterations:
                if await request.is_disconnected():
                    break
                current_generation, current, current_snapshot = (
                    store.presentation_stream_state(run_id)
                )
                current_snapshot["presentation_generation"] = current_generation
                emitted = False
                if current_generation != generation:
                    generation = current_generation
                    cursor = 0
                    idle = 0
                    snapshot_json = json.dumps(
                        current_snapshot,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    yield f"event: presentation.snapshot\ndata: {snapshot_json}\n\n"
                    emitted = True
                while cursor < len(current):
                    event = current[cursor]
                    yield (
                        f"id: {event.event_id}\n"
                        "event: presentation.event\n"
                        f"data: {event.model_dump_json()}\n\n"
                    )
                    cursor += 1
                    idle = 0
                    emitted = True
                if not emitted:
                    yield ": heartbeat\n\n"
                idle += 1
                await asyncio.sleep(app.state.sse_poll_interval_s)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/runs/{run_id}/runtime-events")
    async def runtime_event(run_id: str, payload: RuntimeEventRequest) -> dict[str, Any]:
        store.validate_runtime_node(run_id, payload.node_id)
        event = store.record(
            run_id,
            source="runtime",
            kind="tool_result",
            status=payload.status,
            action_id=payload.action_id,
            arguments={"tool_name": payload.tool_name, **payload.arguments},
            evidence_refs=payload.evidence_refs,
            node_id=payload.node_id,
            mutate_version=False,
        )
        return {"success": True, "event": event.model_dump(mode="json")}

    @app.post("/api/runs/{run_id}/action-events")
    async def action_event(run_id: str, payload: ActionEventRequest) -> dict[str, Any]:
        if payload.operation == "reserve":
            record = store.reserve_action(
                run_id,
                ActionReservation(
                    action_id=payload.action_id,
                    tool_name=payload.tool_name,
                    page_state_version=payload.page_state_version,
                ),
            )
            return {"success": True, "reservation": record.model_dump(mode="json")}
        store.consume_action(
            run_id,
            payload.action_id,
            payload.page_state_version,
            payload.tool_name,
        )
        event = store.record(
            run_id,
            source="browser",
            kind="browser_action",
            status=payload.status,
            action_id=payload.action_id,
            arguments={"tool_name": payload.tool_name, **payload.arguments},
            evidence_refs=payload.evidence_refs,
            node_id=payload.node_id,
            mutate_version=False,
        )
        return {"success": True, "event": event.model_dump(mode="json")}

    @app.post("/api/runs/{run_id}/decisions")
    async def decision(run_id: str, payload: DecisionRequest) -> ToolPayload:
        event = store.decide(run_id, payload)
        return _tool_payload(run_id, payload.action_id, event, {"decision": payload.decision})

    @app.post("/api/runs/{run_id}/ticket/config-check")
    async def config_check(run_id: str, payload: ConfigCheckRequest) -> ToolPayload:
        event, observation = store.check_config(
            run_id,
            action_id=payload.action_id,
            page_state_version=payload.page_state_version,
            check=payload.check,
        )
        return _tool_payload(run_id, payload.action_id, event, observation)

    @app.post("/api/runs/{run_id}/monitor/query")
    async def monitor_query(run_id: str, payload: MonitorQueryRequest) -> ToolPayload:
        event, observation = store.monitor_query(
            run_id,
            action_id=payload.action_id,
            page_state_version=payload.page_state_version,
            query=payload.query,
            region=payload.region,
            cluster=payload.cluster,
        )
        return _tool_payload(run_id, payload.action_id, event, observation)

    @app.post("/api/runs/{run_id}/automation/jobs")
    async def submit_job(run_id: str, payload: AutomationJobRequest) -> ToolPayload:
        job = automation.submit(
            run_id,
            action_id=payload.action_id,
            page_state_version=payload.page_state_version,
            script=payload.script,
            operation=payload.operation,
            parameters=payload.parameters,
        )
        state = store.state(run_id)
        return ToolPayload(
            success=True,
            run_id=run_id,
            action_id=payload.action_id,
            backend_status="accepted",
            page_state_version=state.state_version,
            visible_observation={
                "job_id": job.job_id,
                "operation": job.operation,
                "status": job.status,
                "submitted_payload": job.submitted_payload,
            },
            evidence_refs=[f"job-{job.job_id}-accepted"],
        )

    @app.get("/api/runs/{run_id}/automation/jobs/{job_id}")
    async def get_job(run_id: str, job_id: str) -> dict[str, Any]:
        job = store.job(run_id, job_id)
        return {
            "success": True,
            "job": job.model_dump(mode="json"),
            "state_version": store.state(run_id).state_version,
        }

    @app.post("/api/runs/{run_id}/terminal")
    async def execute_terminal(run_id: str, payload: TerminalRequest) -> ToolPayload:
        try:
            result = await asyncio.to_thread(
                terminal.execute,
                run_id,
                action_id=payload.action_id,
                page_state_version=payload.page_state_version,
                command=payload.command,
            )
        except CommandPolicyError as exc:
            raise EpisodeError("command_not_allowed", str(exc), status_code=422) from exc
        return ToolPayload(
            success=True,
            run_id=run_id,
            action_id=payload.action_id,
            backend_status="succeeded",
            page_state_version=result["page_state_version"],
            visible_observation={
                "command_id": result["command_id"],
                "exit_code": result["exit_code"],
                "stdout": result["stdout"],
                "stderr": result["stderr"],
            },
            evidence_refs=[result["evidence_id"], result["event_id"]],
        )

    @app.post("/api/runs/{run_id}/recording/start")
    async def start_recording(run_id: str) -> dict[str, Any]:
        episode = store.episode(run_id)
        manager = app.state.recorders.get(run_id)
        if manager is None:
            manager = _ServiceRecordingSession(
                run_id=run_id,
                run_root=episode.run_root,
                observer_url=f"http://127.0.0.1:{config.port}/observer/{run_id}",
            )
            app.state.recorders[run_id] = manager
        return manager.start()

    @app.get("/api/runs/{run_id}/recording")
    async def get_recording(run_id: str) -> dict[str, Any]:
        store.state(run_id)
        manager = app.state.recorders.get(run_id)
        return manager.status() if manager else {"success": True, "status": "not_started"}

    @app.post("/api/runs/{run_id}/recording/stop")
    async def stop_recording(run_id: str) -> dict[str, Any]:
        episode = store.episode(run_id)
        manager = app.state.recorders.get(run_id)
        if manager is None:
            return {"success": False, "status": "not_configured"}
        result = manager.stop()
        for relative in (
            "video/demo.mp4",
            "video/poster.png",
            "video/video_manifest.json",
        ):
            if (episode.run_root / relative).is_file():
                episode.registry.register(relative, producer="recorder")
        from case02_openenv.evaluation.scoring import publish_video_verification

        result["summary"] = publish_video_verification(store, run_id, result["manifest"])
        return result

    @app.post("/api/runs/{run_id}/finalize")
    async def finalize(run_id: str) -> dict[str, Any]:
        from case02_openenv.evaluation.scoring import finalize_run

        return finalize_run(store, run_id)

    @app.get("/api/runs/{run_id}/scores")
    async def scores(run_id: str) -> dict[str, Any]:
        episode = store.episode(run_id)
        path = episode.run_root / "scores/summary.json"
        if not path.is_file():
            return {"success": True, "status": "pending"}
        return {"success": True, "status": "final", "summary": json.loads(path.read_text())}

    @app.get("/api/runs/{run_id}/events")
    async def events(
        run_id: str, last_event_id: str | None = Header(default=None)
    ) -> StreamingResponse:
        store.state(run_id)

        async def stream():
            audit = store.audit(run_id)
            start = 0
            if last_event_id:
                for index, event in enumerate(audit):
                    if event.event_id == last_event_id:
                        start = index + 1
                        break
            snapshot = store.state(run_id).model_dump(mode="json")
            yield f"event: snapshot\ndata: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
            cursor = start
            idle = 0
            while idle < app.state.sse_idle_iterations:
                current = store.audit(run_id)
                while cursor < len(current):
                    event = current[cursor]
                    yield f"id: {event.event_id}\nevent: audit\ndata: {event.model_dump_json()}\n\n"
                    cursor += 1
                    idle = 0
                idle += 1
                await asyncio.sleep(app.state.sse_poll_interval_s)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/ticket/{run_id}")
    async def ticket_page(request: Request, run_id: str):
        episode = store.episode(run_id)
        view = ticket_view(store.state(run_id), episode.ticket)
        return templates.TemplateResponse(
            request=request,
            name="ticket.html",
            context={"view": view.model_dump(mode="json"), "view_json": view.model_dump_json()},
        )

    @app.get("/monitor/{run_id}")
    async def monitor_page(request: Request, run_id: str):
        view = monitor_view(store.state(run_id))
        return templates.TemplateResponse(
            request=request,
            name="monitor.html",
            context={"view": view.model_dump(mode="json"), "view_json": view.model_dump_json()},
        )

    @app.get("/automation/{run_id}")
    async def automation_page(request: Request, run_id: str):
        view = automation_view(store.state(run_id))
        return templates.TemplateResponse(
            request=request,
            name="automation.html",
            context={"view": view.model_dump(mode="json"), "view_json": view.model_dump_json()},
        )

    @app.get("/observer/{run_id}")
    async def observer_page(request: Request, run_id: str):
        state = store.state(run_id)
        return templates.TemplateResponse(
            request=request,
            name="observer.html",
            context={"run_id": run_id, "state": state.model_dump(mode="json")},
        )

    return app


def _state_payload(state: Any) -> dict[str, Any]:
    return {"run_id": state.run_id, "phase": state.phase, "state_version": state.state_version}


def _tool_payload(
    run_id: str, action_id: str, event: Any, observation: dict[str, Any]
) -> ToolPayload:
    return ToolPayload(
        success=True,
        run_id=run_id,
        action_id=action_id,
        backend_status="succeeded",
        page_state_version=event.state_version,
        visible_observation=observation,
        evidence_refs=[event.event_id],
    )


class _ServiceRecordingSession:
    def __init__(self, *, run_id: str, run_root: Path, observer_url: str) -> None:
        self.run_id = run_id
        self.run_root = run_root
        self.observer_url = observer_url
        self.display = DisplayManager(run_root)
        self.recorder: DemoRecorder | None = None
        self.display_info: dict[str, Any] | None = None

    def start(self) -> dict[str, Any]:
        if self.recorder is not None:
            return self.status()
        try:
            self.display_info = self.display.start()
            self.display.start_companion_windows(observer_url=self.observer_url)
            self.recorder = DemoRecorder(
                run_id=self.run_id,
                run_root=self.run_root,
                display=self.display.display,
            )
            recorder_info = self.recorder.start()
            return {
                "success": True,
                "status": "recording",
                **(self.display_info or {}),
                "recorder": recorder_info,
            }
        except Exception:
            if self.recorder is not None:
                self.recorder.abort()
            self.display.stop()
            raise

    def status(self) -> dict[str, Any]:
        status = self.recorder.status() if self.recorder else {"status": "not_started"}
        return {
            "success": True,
            **(self.display_info or {}),
            "recorder": status,
        }

    def stop(self) -> dict[str, Any]:
        if self.recorder is None:
            raise RuntimeError("recording session has not started")
        try:
            result = self.recorder.stop()
        finally:
            display_result = self.display.stop()
        return {
            **result,
            "observer_was_alive": display_result["observer_was_alive"],
            "display_return_codes": display_result["return_codes"],
        }
