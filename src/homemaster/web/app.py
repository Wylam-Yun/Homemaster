"""FastAPI adapter for the HomeMaster browser console."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from homemaster.application import RunRequest
from homemaster.artifacts.tool_output_store import ArtifactStoreError
from homemaster.memory.management import MemoryManagementService, MemoryNotFoundError
from homemaster.permissions.models import (
    ApprovalConflict,
    ApprovalExpired,
    ApprovalSubmission,
    ItemDecision,
    PermissionStorageUnavailable,
)
from homemaster.tools.contracts import PermissionSubject
from homemaster.web.confirmations import WebConfirmationHandler
from homemaster.web.event_hub import WebEventHub
from homemaster.web.event_projection import WebEventProjection
from homemaster.web.run_registry import SessionBusyError, WebRunRegistry
from homemaster.web.schemas import (
    ApprovalSubmissionRequest,
    CancelApprovalRequest,
    CreateSessionRequest,
    MemoryHistoryResponse,
    MemorySnapshotResponse,
    RevokeGrantRequest,
    SendMessageRequest,
    WebEvent,
)
from homemaster.web.static import mount_web_static

_DEFAULT_SUBJECT = RunRequest(text="Web permission subject").permission_subject
logger = logging.getLogger(__name__)
_WEB_PERMISSION_SUBJECT = PermissionSubject(
    subject_id="web-local-operator",
    channel="web",
    roles=_DEFAULT_SUBJECT.roles,
    tenant_id=_DEFAULT_SUBJECT.tenant_id,
    capabilities=tuple(
        capability for capability in _DEFAULT_SUBJECT.capabilities if capability != "tool.auto"
    ),
)


def create_web_app(
    *,
    application: Any,
    confirmation_handler: WebConfirmationHandler,
    memory_management_service: MemoryManagementService | None = None,
    alfworld_compile_jobs: Any | None = None,
    permission_store: Any | None = None,
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
        try:
            await application.start()
            await hub.start()
            yield
        finally:
            await close_resources()

    app = FastAPI(title="HomeMaster Web Console", lifespan=lifespan)
    app.state.application = application
    app.state.confirmation_handler = confirmation_handler
    app.state.run_registry = run_registry
    app.state.event_hub = hub
    app.state.memory_management_service = memory_management_service
    app.state.alfworld_compile_jobs = alfworld_compile_jobs
    app.state.aclose = close_resources

    def _web_store() -> Any | None:
        if permission_store is not None:
            return permission_store
        return confirmation_handler.store

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
                {
                    "session_id": summary.session_id,
                    "title": summary.title,
                    "message_count": summary.message_count,
                    "updated_at": summary.updated_at,
                }
                for summary in application.session_manager.session_summaries()
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

    @app.get("/api/memories")
    async def memories() -> object:
        service = app.state.memory_management_service
        if service is None:
            return _error(
                503,
                "memory_unavailable",
                "Memory service is unavailable.",
                retryable=True,
            )
        try:
            snapshot = await service.snapshot(tenant_id=_WEB_PERMISSION_SUBJECT.tenant_id)
        except Exception:
            logger.exception("web_memory_snapshot_failed")
            return _error(
                503,
                "memory_read_failed",
                "Memory data could not be read.",
                retryable=True,
            )
        return MemorySnapshotResponse.from_domain(snapshot).model_dump(mode="json")

    @app.post("/api/memories/{memory_id}/compile", status_code=202)
    async def compile_memory(memory_id: str) -> object:
        service = app.state.alfworld_compile_jobs
        if service is None:
            return _error(
                503, "memory_unavailable", "Memory compiler is unavailable.", retryable=True
            )
        try:
            return service.enqueue(memory_id, session_id="web-memory-management")
        except ValueError:
            return _error(400, "invalid_memory_id", "The memory ID is invalid.", retryable=False)
        except Exception:
            logger.exception("web_memory_compile_admission_failed", extra={"memory_id": memory_id})
            return _error(
                503,
                "compile_admission_failed",
                "The compile job could not be accepted.",
                retryable=True,
            )

    @app.get("/api/memory-compilations/{job_id}")
    async def compile_status(job_id: str) -> object:
        service = app.state.alfworld_compile_jobs
        if service is None:
            return _error(
                503, "memory_unavailable", "Memory compiler is unavailable.", retryable=True
            )
        try:
            job = service.get(job_id)
        except ValueError:
            return _error(
                404, "compile_job_not_found", "The compile job does not exist.", retryable=False
            )
        if job is None:
            return _error(
                404, "compile_job_not_found", "The compile job does not exist.", retryable=False
            )
        return job

    @app.get("/api/memories/{memory_id}/history")
    async def memory_history(memory_id: str) -> object:
        service = app.state.memory_management_service
        if service is None:
            return _error(
                503,
                "memory_unavailable",
                "Memory service is unavailable.",
                retryable=True,
            )
        try:
            versions = await service.history(
                memory_id,
                tenant_id=_WEB_PERMISSION_SUBJECT.tenant_id,
            )
        except MemoryNotFoundError:
            return _error(
                404,
                "memory_not_found",
                "The memory does not exist.",
                retryable=False,
            )
        except Exception:
            logger.exception("web_memory_history_failed", extra={"memory_id": memory_id})
            return _error(
                503,
                "memory_read_failed",
                "Memory data could not be read.",
                retryable=True,
            )
        return MemoryHistoryResponse.from_domain(memory_id, versions).model_dump(mode="json")

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
    async def resolve_approval(approval_id: str, body: dict) -> object:
        try:
            submission_request = ApprovalSubmissionRequest.model_validate(body)
        except ValidationError:
            if isinstance(body, dict) and "outcome" in body:
                return _error(
                    422,
                    "approval_protocol_outdated",
                    "This console speaks approval protocol 2 with per-item"
                    " decisions; refresh the page and decide again.",
                    retryable=False,
                )
            return _error(
                422,
                "invalid_request",
                "An approval submission must carry protocol_version,"
                " submission_id, request_revision and decisions.",
                retryable=False,
            )
        try:
            submission = ApprovalSubmission(
                protocol_version=submission_request.protocol_version,
                submission_id=submission_request.submission_id,
                request_revision=submission_request.request_revision,
                decisions=tuple(
                    ItemDecision(item_id=item.item_id, choice=item.choice)
                    for item in submission_request.decisions
                ),
            )
        except ValidationError:
            return _error(
                422,
                "invalid_request",
                "Each decision needs an item_id and one of allow_once,"
                " allow_always or reject.",
                retryable=False,
            )
        store = _web_store()
        if store is None:
            return _error(
                503,
                "permission_store_unavailable",
                "The permission store is unavailable.",
                retryable=True,
            )
        try:
            resolution = await confirmation_handler.resolve(
                approval_id, submission, _WEB_PERMISSION_SUBJECT.subject_id
            )
        except KeyError:
            return _error(
                404,
                "approval_not_found",
                "The approval is unknown, expired, or already resolved.",
                retryable=False,
            )
        except ApprovalConflict:
            return _error(
                409,
                "approval_conflict",
                "The approval changed; reload it and decide again.",
                retryable=False,
            )
        except ApprovalExpired:
            return _error(
                410,
                "approval_expired",
                "The approval passed its deadline without a decision.",
                retryable=False,
            )
        except ValueError:
            return _error(
                422,
                "invalid_request",
                "The submission must decide exactly the pending items once each.",
                retryable=False,
            )
        except PermissionStorageUnavailable:
            return _error(
                503,
                "permission_store_unavailable",
                "The decision could not be saved; nothing was executed.",
                retryable=True,
            )
        return _resolution_to_dict(resolution)

    @app.get("/api/approvals/{approval_id}")
    async def read_approval(approval_id: str) -> object:
        store = _web_store()
        if store is None:
            return _error(
                503,
                "permission_store_unavailable",
                "The permission store is unavailable.",
                retryable=True,
            )
        try:
            stored = store.get_request(approval_id)
        except KeyError:
            return _error(
                404,
                "approval_not_found",
                "The approval is unknown, expired, or already resolved.",
                retryable=False,
            )
        return _approval_to_dict(stored)

    @app.post("/api/approvals/{approval_id}/cancel")
    async def cancel_approval(approval_id: str, body: CancelApprovalRequest) -> object:
        if _web_store() is None:
            return _error(
                503,
                "permission_store_unavailable",
                "The permission store is unavailable.",
                retryable=True,
            )
        try:
            status = await confirmation_handler.cancel_approval(
                approval_id, body.submission_id, body.request_revision
            )
        except KeyError:
            return _error(
                404,
                "approval_not_found",
                "The approval is unknown, expired, or already resolved.",
                retryable=False,
            )
        except ApprovalConflict:
            return _error(
                409,
                "approval_conflict",
                "The approval changed; reload it and decide again.",
                retryable=False,
            )
        except PermissionStorageUnavailable:
            return _error(
                503,
                "permission_store_unavailable",
                "The cancellation could not be saved.",
                retryable=True,
            )
        return {"approval_id": approval_id, "request_status": status}

    @app.get("/api/permissions/grants")
    async def list_grants(
        resource_kind: str | None = None,
        status: str = "active",
        environment_id: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> object:
        store = _web_store()
        if store is None:
            return _error(
                503,
                "permission_store_unavailable",
                "The permission store is unavailable.",
                retryable=True,
            )
        if resource_kind is not None and resource_kind not in ("object", "area"):
            return _error(
                422,
                "invalid_request",
                "resource_kind must be object or area.",
                retryable=False,
            )
        try:
            if environment_id:
                grants, next_cursor = store.list_grants(
                    environment_id,
                    resource_kind=resource_kind,
                    status=status,
                    cursor=cursor,
                    limit=limit,
                )
            else:
                grants, next_cursor = store.list_all_grants(
                    resource_kind=resource_kind,
                    status=status,
                    cursor=cursor,
                    limit=limit,
                )
        except KeyError:
            return _error(
                404,
                "grant_cursor_not_found",
                "The page cursor is unknown.",
                retryable=False,
            )
        except ValueError:
            return _error(
                422,
                "invalid_request",
                "status must be active or revoked and limit within 1..200.",
                retryable=False,
            )
        return {
            "grants": [
                _grant_to_dict(grant, store.grant_display(grant.source_item_id))
                for grant in grants
            ],
            "next_cursor": next_cursor,
        }

    @app.post("/api/permissions/grants/{grant_id}/revoke")
    async def revoke_grant(grant_id: str, body: RevokeGrantRequest) -> object:
        store = _web_store()
        if store is None:
            return _error(
                503,
                "permission_store_unavailable",
                "The permission store is unavailable.",
                retryable=True,
            )
        try:
            grant = store.revoke(
                grant_id,
                body.submission_id,
                body.expected_revision,
                _WEB_PERMISSION_SUBJECT.subject_id,
            )
        except KeyError:
            return _error(
                404,
                "grant_not_found",
                "The grant is unknown.",
                retryable=False,
            )
        except ApprovalConflict:
            return _error(
                409,
                "grant_conflict",
                "The grant changed; reload the list and revoke again.",
                retryable=False,
            )
        except PermissionStorageUnavailable:
            return _error(
                503,
                "permission_store_unavailable",
                "The revocation could not be saved.",
                retryable=True,
            )
        return _grant_to_dict(grant, store.grant_display(grant.source_item_id))

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
        await confirmation_handler.deny_session(session_id)


def _session_ids(manager: Any) -> set[str]:
    persisted = set(manager.list_session_ids())
    active = {runtime.session.session_id for runtime in manager.sessions}
    return persisted | active


def _message_text(message: Any) -> str:
    """Extract readable text from content blocks (UserMessage has no .text)."""

    content = getattr(message, "content", None)
    if isinstance(content, (list, tuple)):
        joined = "\n".join(str(getattr(block, "text", "") or "") for block in content)
        if joined.strip():
            return joined
    return str(getattr(message, "text", "") or "")


def _history_message(message: Any) -> dict[str, object]:
    projected: dict[str, object] = {
        "role": str(getattr(message, "role", "unknown")),
        "text": _message_text(message),
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


def _resolution_to_dict(resolution: Any) -> dict[str, Any]:
    return {
        "approval_id": resolution.approval_id,
        "request_id": resolution.request_id,
        "request_status": resolution.request_status,
        "execution_started": resolution.execution_started,
        "persisted_grant_ids": list(resolution.persisted_grant_ids),
        "items": [
            {"item_id": item.item_id, "choice": item.choice}
            for item in resolution.items
        ],
    }


def _approval_to_dict(stored: Any) -> dict[str, Any]:
    return {
        "approval_id": stored.approval_id,
        "request_id": stored.request_id,
        "environment_id": stored.environment_id,
        "revision": stored.revision,
        "intent_summary": stored.intent_summary,
        "request_status": stored.status,
        "created_at": stored.created_at,
        "deadline_at": stored.deadline_at,
        "resolved_at": stored.resolved_at,
        "items": [
            {
                "item_id": item.item_id,
                "display_name": item.display_name,
                "location": item.location,
                "action_label": item.action_label,
                "resource_kind": item.key.resource_kind,
                "resource_id": item.key.resource_id,
                "action": item.key.action,
                "decision": item.decision,
                "matched_grant_id": item.matched_grant_id,
                "step_ids": list(item.step_ids),
            }
            for item in stored.items
        ],
    }


def _grant_to_dict(
    grant: Any, display: tuple[str, str, str] | None = None
) -> dict[str, Any]:
    return {
        "grant_id": grant.grant_id,
        "environment_id": grant.environment_id,
        "resource_kind": grant.resource_kind,
        "resource_id": grant.resource_id,
        "action": grant.action,
        "created_at": grant.created_at,
        "created_by": grant.created_by,
        "source_request_id": grant.source_request_id,
        "source_item_id": grant.source_item_id,
        "revoked_at": grant.revoked_at,
        "revoked_by": grant.revoked_by,
        "revision": grant.revision,
        "status": "active" if grant.revoked_at is None else "revoked",
        "display_name": display[0] if display is not None else None,
        "location": display[1] if display is not None else None,
        "action_label": display[2] if display is not None else None,
    }


def _error(status_code: int, code: str, message: str, *, retryable: bool) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "retryable": retryable},
    )


__all__ = ["create_web_app"]
