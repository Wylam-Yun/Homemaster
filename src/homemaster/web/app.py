"""FastAPI adapter for the HomeMaster browser console."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from homemaster.application import RunRequest
from homemaster.artifacts.tool_output_store import ArtifactStoreError
from homemaster.tools.contracts import PermissionSubject
from homemaster.web.confirmations import UnknownApprovalError, WebConfirmationHandler
from homemaster.web.event_hub import WebEventHub
from homemaster.web.event_projection import WebEventProjection
from homemaster.web.run_registry import SessionBusyError, WebRunRegistry
from homemaster.web.schemas import (
    ApprovalDecisionRequest,
    CreateSessionRequest,
    SendMessageRequest,
    WebEvent,
)
from homemaster.web.static import mount_web_static

_DEFAULT_SUBJECT = RunRequest(text="Web permission subject").permission_subject
_WEB_PERMISSION_SUBJECT = PermissionSubject(
    subject_id="web-local-operator",
    channel="web",
    roles=_DEFAULT_SUBJECT.roles,
    tenant_id=_DEFAULT_SUBJECT.tenant_id,
    capabilities=tuple(
        capability
        for capability in _DEFAULT_SUBJECT.capabilities
        if capability != "tool.auto"
    ),
)


def create_web_app(
    *,
    application: Any,
    confirmation_handler: WebConfirmationHandler,
) -> FastAPI:
    """Build a Web adapter around one long-lived ApplicationRuntime."""

    run_registry = WebRunRegistry()
    hub = WebEventHub(
        application.event_bus,
        run_registry,
        WebEventProjection(include_thinking=True),
    )
    close_lock = asyncio.Lock()
    closed = False

    async def close_resources() -> None:
        nonlocal closed
        async with close_lock:
            if closed:
                return
            await confirmation_handler.aclose()
            await run_registry.aclose()
            await hub.aclose()
            await application.aclose()
            closed = True

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        await hub.start()
        try:
            yield
        finally:
            await close_resources()

    app = FastAPI(title="HomeMaster Web Console", lifespan=lifespan)
    app.state.application = application
    app.state.confirmation_handler = confirmation_handler
    app.state.run_registry = run_registry
    app.state.event_hub = hub
    app.state.aclose = close_resources

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: object, exc: RequestValidationError) -> JSONResponse:
        del request, exc
        return _error(
            422,
            "invalid_request",
            "Request validation failed.",
            retryable=False,
        )

    @app.post("/api/sessions", status_code=201)
    async def create_session(body: CreateSessionRequest | None = None) -> object:
        requested_id = body.session_id if body is not None else None
        if requested_id is None:
            runtime = await application.session_manager.open_or_resume()
        else:
            if requested_id not in _session_ids(application.session_manager):
                return _error(
                    404,
                    "session_not_found",
                    "The requested session does not exist.",
                    retryable=False,
                )
            runtime = await application.session_manager.open_or_resume(
                requested_id,
                resume=True,
            )
        return {"session_id": runtime.session.session_id}

    @app.get("/api/sessions")
    async def list_sessions() -> object:
        return {
            "sessions": [
                {"session_id": session_id}
                for session_id in sorted(_session_ids(application.session_manager))
            ]
        }

    @app.get("/api/sessions/{session_id}/history")
    async def session_history(session_id: str) -> object:
        if session_id not in _session_ids(application.session_manager):
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        try:
            runtime = application.session_manager.get(session_id)
        except KeyError:
            runtime = await application.session_manager.resume(session_id)
        return {
            "session_id": session_id,
            "messages": [_history_message(message) for message in runtime.session.messages],
        }

    @app.post("/api/sessions/{session_id}/messages", status_code=202)
    async def send_message(session_id: str, body: SendMessageRequest) -> object:
        if session_id not in _session_ids(application.session_manager):
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        if await run_registry.is_accepted(session_id, body.request_id):
            return {
                "accepted": True,
                "session_id": session_id,
                "request_id": body.request_id,
            }
        if not await hub.has_subscriber(session_id):
            return _error(
                409,
                "event_stream_not_ready",
                "Subscribe to the session event stream before sending a message.",
                retryable=True,
            )

        start_gate = asyncio.Event()

        async def run_owned() -> object:
            await start_gate.wait()
            return await _run_and_report_prestart_failure(
                application=application,
                request=RunRequest(
                    text=body.text,
                    session_id=session_id,
                    resume=True,
                    permission_subject=_WEB_PERMISSION_SUBJECT,
                    metadata={"web_request_id": body.request_id},
                ),
                request_id=body.request_id,
                run_registry=run_registry,
                hub=hub,
            )

        try:
            acceptance = await run_registry.accept(
                session_id,
                body.request_id,
                run_owned,
            )
        except SessionBusyError:
            return _error(
                409,
                "session_busy",
                "This session already has an active run.",
                retryable=True,
            )
        if acceptance.created:
            await hub.publish(
                WebEvent(
                    type="request.accepted",
                    session_id=session_id,
                    run_id="",
                    request_id=body.request_id,
                    payload={},
                )
            )
            start_gate.set()
        return {
            "accepted": True,
            "session_id": session_id,
            "request_id": body.request_id,
        }

    @app.post("/api/sessions/{session_id}/cancel")
    async def cancel(session_id: str) -> object:
        if session_id not in _session_ids(application.session_manager):
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        return {
            "cancelled": bool(application.cancel(session_id)),
            "session_id": session_id,
        }

    @app.post("/api/approvals/{approval_id}")
    async def resolve_approval(approval_id: str, body: ApprovalDecisionRequest) -> object:
        try:
            approved = await confirmation_handler.resolve(approval_id, body.outcome)
        except UnknownApprovalError:
            return _error(
                404,
                "approval_not_found",
                "The approval is unknown, expired, or already resolved.",
                retryable=False,
            )
        return {"approval_id": approval_id, "approved": approved}

    @app.get("/api/artifacts/{artifact_handle}")
    async def download_artifact(
        artifact_handle: str,
        session_id: str,
        run_id: str,
    ) -> object:
        publisher = getattr(application, "artifact_publisher", None)
        store = getattr(publisher, "store", None)
        try:
            if store is None or session_id not in _session_ids(application.session_manager):
                raise ArtifactStoreError("artifact store unavailable")
            content = store.read(
                artifact_handle,
                tenant_id="local",
                session_id=session_id,
                run_id=run_id,
            )
        except (ArtifactStoreError, ValueError):
            return _error(
                404,
                "artifact_not_found",
                "The artifact is unavailable or expired.",
                retryable=False,
            )
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"X-Content-SHA256": hashlib.sha256(content).hexdigest()},
        )

    @app.websocket("/api/events")
    async def events(websocket: WebSocket, session_id: str) -> None:
        if session_id not in _session_ids(application.session_manager):
            await websocket.close(code=4404)
            return
        queue = await hub.subscribe(session_id)
        await websocket.accept()
        try:
            await _stream_events(websocket, queue)
        except WebSocketDisconnect:
            pass
        finally:
            await hub.unsubscribe(session_id, queue)
            await _deny_approvals_without_subscriber(
                session_id,
                hub=hub,
                confirmation_handler=confirmation_handler,
            )

    mount_web_static(app)
    return app


async def _stream_events(
    websocket: WebSocket,
    queue: asyncio.Queue[WebEvent],
) -> None:
    """Forward events while independently observing an idle client disconnect."""

    event_task = asyncio.create_task(queue.get())
    receive_task = asyncio.create_task(websocket.receive())
    try:
        while True:
            done, _pending = await asyncio.wait(
                (event_task, receive_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive_task in done:
                message = receive_task.result()
                if message.get("type") == "websocket.disconnect":
                    return
                receive_task = asyncio.create_task(websocket.receive())
            if event_task in done:
                event = event_task.result()
                await websocket.send_json(event.to_dict())
                event_task = asyncio.create_task(queue.get())
    finally:
        for task in (event_task, receive_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(event_task, receive_task, return_exceptions=True)


async def _run_and_report_prestart_failure(
    *,
    application: Any,
    request: RunRequest,
    request_id: str,
    run_registry: WebRunRegistry,
    hub: WebEventHub,
) -> object | None:
    """Turn adapter failures before ``turn_started`` into one safe terminal event."""

    try:
        return await application.run(request)
    except Exception:
        session_id = request.session_id or ""
        if await run_registry.fail_before_start(session_id, request_id):
            await hub.publish(
                WebEvent(
                    type="run.failed",
                    session_id=session_id,
                    run_id="",
                    request_id=request_id,
                    payload={
                        "code": "adapter_failed",
                        "message": "The run could not be started.",
                        "retryable": True,
                    },
                )
            )
        return None


async def _deny_approvals_without_subscriber(
    session_id: str,
    *,
    hub: WebEventHub,
    confirmation_handler: WebConfirmationHandler,
) -> None:
    """Fail closed only when the last browser for this session has disconnected."""

    if not await hub.has_subscriber(session_id):
        await confirmation_handler.deny_session(session_id, outcome="disconnected")


def _session_ids(manager: Any) -> set[str]:
    persisted = set(manager.list_session_ids())
    active = {runtime.session.session_id for runtime in manager.sessions}
    return persisted | active


def _history_message(message: Any) -> dict[str, object]:
    projected: dict[str, object] = {
        "role": str(getattr(message, "role", "unknown")),
        "text": str(getattr(message, "text", "") or ""),
    }
    thinking = getattr(message, "reasoning_content", None)
    if isinstance(thinking, str) and thinking:
        projected["thinking"] = thinking
    tool_call_id = getattr(message, "tool_call_id", None)
    if isinstance(tool_call_id, str) and tool_call_id:
        projected["tool_call_id"] = tool_call_id
    name = getattr(message, "name", None)
    if isinstance(name, str) and name:
        projected["name"] = name
    return projected


def _error(status_code: int, code: str, message: str, *, retryable: bool) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "retryable": retryable},
    )


__all__ = ["create_web_app"]
