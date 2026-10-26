"""Session runtime operations: status, compaction, plan/act mode, archival."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict

from fastapi import FastAPI

from homemaster.web._shared import (
    _application_services,
    _ensure_session_loaded,
    _error,
    _session_ids,
    _session_ui_mode,
    _status_to_dict,
)
from homemaster.web.context import WebAppContext
from homemaster.web.schemas import SetSessionModeRequest

logger = logging.getLogger(__name__)


def register(app: FastAPI, ctx: WebAppContext) -> None:
    application = ctx.application
    run_registry = ctx.run_registry

    @app.get("/api/sessions/{session_id}/status")
    async def session_status(session_id: str) -> object:
        if session_id not in _session_ids(application.session_manager):
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        status_fn = getattr(application, "status", None)
        if not callable(status_fn):
            return _error(
                503,
                "status_unavailable",
                "Session status is unavailable.",
                retryable=True,
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
            status = status_fn(session_id)
        except KeyError:
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        result = _status_to_dict(status)
        result["ui_mode"] = _session_ui_mode(application, session_id)
        try:
            rt = application.session_manager.get(session_id)
            est = getattr(rt.agent_state, "estimated_context_tokens", 0) or 0
            if est <= 0 and rt.session.messages:
                est = sum(len(str(getattr(m, "content", ""))) for m in rt.session.messages) // 3
            result["context_tokens"] = int(est)
        except Exception:
            result["context_tokens"] = 0
        return result

    @app.post("/api/sessions/{session_id}/compact")
    async def compact_session(session_id: str) -> object:
        if session_id not in _session_ids(application.session_manager):
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        compact_fn = getattr(application, "compact", None)
        if not callable(compact_fn):
            return _error(
                503,
                "compact_unavailable",
                "Session compaction is unavailable.",
                retryable=True,
            )
        if await run_registry.is_busy(session_id):
            return _error(
                409,
                "session_busy",
                "This session already has an active run.",
                retryable=True,
            )
        loaded = await _ensure_session_loaded(application.session_manager, session_id)
        if not loaded:
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        status_fn = getattr(application, "status", None)
        if callable(status_fn):
            try:
                if status_fn(session_id).active:
                    return _error(
                        409,
                        "session_busy",
                        "This session already has an active run.",
                        retryable=True,
                    )
            except KeyError:
                pass
        try:
            result = await compact_fn(session_id)
        except KeyError:
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        except Exception:
            logger.exception("web_compact_failed", extra={"session_id": session_id})
            return _error(
                503,
                "compact_failed",
                "The session could not be compacted.",
                retryable=True,
            )
        payload = asdict(result) if hasattr(result, "__dataclass_fields__") else dict(result)
        try:
            rt = application.session_manager.get(session_id)
            last_comp = getattr(rt.agent_state, "last_compaction", None)
            if last_comp is not None and payload.get("triggered"):
                payload["before_tokens"] = getattr(last_comp, "before_tokens", None)
                payload["after_tokens"] = getattr(last_comp, "after_tokens", None)
            est = getattr(rt.agent_state, "estimated_context_tokens", None)
            if isinstance(est, int) and est > 0:
                payload["context_tokens"] = est
        except Exception:
            pass
        return payload

    @app.post("/api/sessions/{session_id}/archive", status_code=202)
    async def archive_session(session_id: str) -> object:
        if session_id not in _session_ids(application.session_manager):
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        handler = getattr(application, "session_end_handler", None)
        if not callable(handler):
            return _error(
                503,
                "archive_unavailable",
                "Session archival requires memory.mode='full'.",
                retryable=False,
            )
        try:
            receipt = handler(session_id, "archived")
        except Exception:
            logger.exception("web_archive_failed", extra={"session_id": session_id})
            return _error(
                503,
                "archive_failed",
                "The session could not be queued for archival.",
                retryable=True,
            )
        if receipt is None:
            return _error(
                503,
                "archive_not_admitted",
                "Session archival was not admitted; the memory backend or work queue is not ready.",
                retryable=True,
            )
        return {
            "session_id": session_id,
            "job_id": receipt.job_id,
            "status": receipt.status,
        }

    @app.get("/api/sessions/{session_id}/archive-status")
    async def archive_status(session_id: str) -> object:
        if session_id not in _session_ids(application.session_manager):
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        controller = _application_services(application).get("session_finalization")
        status_for = getattr(controller, "status_for", None)
        if not callable(status_for):
            return _error(
                503,
                "archive_unavailable",
                "Session archival requires memory.mode='full'.",
                retryable=False,
            )
        view = await asyncio.to_thread(status_for, session_id)
        if view is None:
            return {"session_id": session_id, "state": "none"}
        return {"session_id": session_id, **view}

    @app.get("/api/archive-status")
    async def archive_statuses() -> object:
        controller = _application_services(application).get("session_finalization")
        statuses = getattr(controller, "statuses", None)
        if not callable(statuses):
            return {"sessions": {}}
        return {"sessions": await asyncio.to_thread(statuses)}

    @app.post("/api/sessions/{session_id}/mode")
    async def set_session_mode(session_id: str, body: SetSessionModeRequest) -> object:
        if session_id not in _session_ids(application.session_manager):
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        plan_mode = _application_services(application).get("plan_mode")
        set_enabled = getattr(plan_mode, "set", None)
        if not callable(set_enabled):
            return _error(
                503,
                "plan_mode_unavailable",
                "Plan mode control is unavailable.",
                retryable=True,
            )
        # The registered plan-mode listener broadcasts session.mode_changed;
        # publishing here too would emit the event twice per transition.
        set_enabled(session_id, body.ui_mode == "plan")
        return {"session_id": session_id, "ui_mode": body.ui_mode}


__all__ = ["register"]
