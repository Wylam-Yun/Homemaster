"""Session lifecycle routes: create, list, history, rename, delete, cancel."""

from __future__ import annotations

import shutil

from fastapi import FastAPI, Response

from homemaster.application.session import SessionConflictError
from homemaster.web._shared import (
    _ensure_session_loaded,
    _error,
    _history_message,
    _session_attachment_dir,
    _session_ids,
    _web_attachments_root,
)
from homemaster.web.context import WebAppContext
from homemaster.web.schemas import CreateSessionRequest, RenameSessionRequest


def register(app: FastAPI, ctx: WebAppContext) -> None:
    application = ctx.application
    run_registry = ctx.run_registry

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
            try:
                runtime = await application.session_manager.open_or_resume(
                    requested_id,
                    resume=True,
                )
            except (KeyError, FileNotFoundError):
                return _error(
                    404,
                    "session_not_found",
                    "The requested session does not exist.",
                    retryable=False,
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
            try:
                runtime = await application.session_manager.resume(session_id)
            except (KeyError, FileNotFoundError):
                # The session can disappear between the ids probe and the
                # resume load (concurrent DELETE) — report it as gone.
                return _error(
                    404,
                    "session_not_found",
                    "The requested session does not exist.",
                    retryable=False,
                )
        return {
            "session_id": session_id,
            "messages": [_history_message(message) for message in runtime.session.messages],
        }

    @app.patch("/api/sessions/{session_id}")
    async def rename_session(session_id: str, body: RenameSessionRequest) -> object:
        if session_id not in _session_ids(application.session_manager):
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        try:
            title = application.session_manager.rename_session(session_id, body.title)
        except KeyError:
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        except ValueError:
            return _error(
                422,
                "invalid_request",
                "The session title must be a non-empty string.",
                retryable=False,
            )
        return {"session_id": session_id, "title": title}

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str) -> object:
        if session_id not in _session_ids(application.session_manager):
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        if await run_registry.is_busy(session_id):
            return _error(
                409,
                "session_busy",
                "This session already has an active run.",
                retryable=True,
            )
        try:
            # Hard delete — persisted files plus the in-memory runtime. The
            # archive endpoint remains the memory-ingestion path; nothing is
            # recoverable after this returns. The busy check is re-run under
            # the manager lock to close the check-then-act window against a
            # concurrent POST /messages landing run_registry.accept in between.
            await application.session_manager.delete_session(
                session_id, busy_check=run_registry.is_busy_nowait
            )
        except SessionConflictError:
            return _error(
                409,
                "session_busy",
                "This session already has an active run.",
                retryable=True,
            )
        except KeyError:
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        # Hard delete also covers uploaded attachments under
        # var/web_attachments/{session_id} — the user-facing "彻底删除"
        # promise includes user files, not just the session store.
        attachment_dir = _session_attachment_dir(
            _web_attachments_root(application, create=False), session_id, create=False
        )
        shutil.rmtree(attachment_dir, ignore_errors=True)
        return Response(status_code=204)

    @app.post("/api/sessions/{session_id}/cancel")
    async def cancel(session_id: str) -> object:
        if session_id not in _session_ids(application.session_manager):
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        loaded = await _ensure_session_loaded(application.session_manager, session_id)
        if not loaded:
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        try:
            cancelled = bool(application.cancel(session_id))
        except KeyError:
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        return {
            "cancelled": cancelled,
            "session_id": session_id,
        }


__all__ = ["register"]
