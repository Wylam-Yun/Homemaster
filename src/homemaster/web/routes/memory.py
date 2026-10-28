"""Memory management surface: snapshot, graph, search, detail, history, create, update, delete.

V3.8: the ALFWorld compile job routes (``POST /api/memories/{id}/compile`` and
``GET /api/memory-compilations/{job_id}``) were retired with the compile
branch; all remaining reads/mutations flow through ``MindMemOSClient`` via
``MemoryManagementService``.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI

from homemaster.memory.memory_work_store import JOB_IMPLICIT_FEEDBACK
from homemaster.web._shared import (
    _ARCHIVE_STATE_MAP,
    _WEB_PERMISSION_SUBJECT,
    _application_services,
    _error,
    _session_ids,
)
from homemaster.web.context import WebAppContext
from homemaster.web.memory_views import MemoryNotFoundError, MemoryUnavailableError
from homemaster.web.schemas import (
    CreateMemoryRequest,
    ManagedMemoryResponse,
    MemoryGraphResponse,
    MemoryHistoryResponse,
    MemorySearchResponse,
    MemorySnapshotResponse,
    UpdateMemoryRequest,
)

logger = logging.getLogger(__name__)


def _non_noop_actions_count(result: object) -> int:
    actions = result.get("actions") if isinstance(result, dict) else None
    return sum(
        1
        for action in actions or []
        if isinstance(action, dict) and action.get("action") not in (None, "noop")
    )


def _action_diff_view(diff: object) -> dict[str, object]:
    """Public projection of one ActionDiff — shared by feedback and dreaming
    run details so both pages render the identical before/after wire shape."""
    before = getattr(diff, "before", None)
    after = getattr(diff, "after", None)
    return {
        "action_id": getattr(diff, "action_id", None),
        "action": getattr(diff, "action", None),
        "memory_id": getattr(diff, "memory_id", None),
        "memory_session_id": getattr(diff, "memory_session_id", None),
        "before": (
            {"content": before.content, "status": before.status}
            if before is not None
            else None
        ),
        "after": (
            {"content": after.content, "status": after.status}
            if after is not None
            else None
        ),
        "missing": getattr(diff, "missing", False),
    }


def _aggregate_archive_state(archives: list[object]) -> str:
    """Session-level archive badge: the most active state wins — any in-flight
    archive means ``processing``, else any ``failed``, else all ``dreamed``
    (public ``completed``). No archives means ``unarchived``."""
    if not archives:
        return "unarchived"
    states = [
        _ARCHIVE_STATE_MAP.get(getattr(archive, "status", ""), "unknown")
        for archive in archives
    ]
    if "processing" in states:
        return "processing"
    if "failed" in states:
        return "failed"
    return "completed"


def _turn_user_preview(store: object, session_id: str, turn_id: str) -> str | None:
    """Last ``role == 'user'`` message in one turn, truncated to 200 chars."""
    events_for_turn = getattr(store, "events_for_turn", None)
    if not callable(events_for_turn):
        return None
    try:
        events = events_for_turn(session_id, turn_id)
    except Exception:
        return None
    for event in reversed(events):
        payload = getattr(event, "payload", None) or {}
        if payload.get("role") == "user" and payload.get("content"):
            return str(payload["content"])[:200]
    return None


def register(app: FastAPI, ctx: WebAppContext) -> None:
    application = ctx.application

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

    @app.post("/api/memories", status_code=201)
    async def create_memory(body: CreateMemoryRequest) -> object:
        service = app.state.memory_management_service
        if service is None:
            return _error(
                503,
                "memory_unavailable",
                "Memory service is unavailable.",
                retryable=True,
            )
        try:
            created = await service.create(
                body.content,
                body.memory_type,
                tenant_id=_WEB_PERMISSION_SUBJECT.tenant_id,
            )
        except ValueError as exc:
            return _error(422, "invalid_memory_input", str(exc), retryable=False)
        except Exception:
            logger.exception("web_memory_create_failed")
            return _error(
                503,
                "memory_write_failed",
                "Memory could not be created.",
                retryable=True,
            )
        return {
            "memory": ManagedMemoryResponse.model_validate(created).model_dump(mode="json"),
            "status": "created",
        }

    # ``/api/memories/graph`` and ``/api/memories/search`` must be registered
    # before ``/api/memories/{memory_id}`` so the literal segments win.

    @app.get("/api/memories/graph")
    async def memories_graph() -> object:
        service = app.state.memory_management_service
        if service is None:
            return _error(
                503,
                "memory_unavailable",
                "Memory service is unavailable.",
                retryable=True,
            )
        try:
            graph = await service.graph(tenant_id=_WEB_PERMISSION_SUBJECT.tenant_id)
        except MemoryUnavailableError:
            return _error(
                503,
                "memory_unavailable",
                "Memory graph backend is unavailable.",
                retryable=True,
            )
        except Exception:
            logger.exception("web_memory_graph_failed")
            return _error(
                503,
                "memory_read_failed",
                "Memory data could not be read.",
                retryable=True,
            )
        return MemoryGraphResponse.from_domain(graph).model_dump(mode="json")

    @app.get("/api/memories/search")
    async def memories_search(q: str = "") -> object:
        service = app.state.memory_management_service
        if service is None:
            return _error(
                503,
                "memory_unavailable",
                "Memory service is unavailable.",
                retryable=True,
            )
        if not q.strip():
            return _error(
                422,
                "invalid_memory_input",
                "The search query must not be empty.",
                retryable=False,
            )
        try:
            result = await service.search(
                q,
                tenant_id=_WEB_PERMISSION_SUBJECT.tenant_id,
            )
        except ValueError as exc:
            return _error(422, "invalid_memory_input", str(exc), retryable=False)
        except Exception:
            logger.exception("web_memory_search_failed")
            return _error(
                503,
                "memory_read_failed",
                "Memory data could not be read.",
                retryable=True,
            )
        return MemorySearchResponse.from_domain(result).model_dump(mode="json")

    # ``/api/memories/implicit-feedback`` is a literal segment like ``graph`` /
    # ``search`` — registered before ``/{memory_id}`` for the same reason.

    @app.post("/api/memories/implicit-feedback", status_code=201)
    async def trigger_implicit_feedback() -> object:
        """User-triggered implicit feedback: enqueue one background job.

        The shared worker pool claims and executes it (server analysis +
        per-action raw readback); this route only admits the work.
        """
        service = app.state.memory_management_service
        if service is None:
            return _error(
                503,
                "memory_unavailable",
                "Memory service is unavailable.",
                retryable=True,
            )
        scheduler = _application_services(application).get("feedback_scheduler")
        enqueue = getattr(scheduler, "enqueue_manual", None)
        if not callable(enqueue):
            return _error(
                503,
                "implicit_feedback_unavailable",
                "Implicit feedback scheduling is unavailable.",
                retryable=True,
            )
        try:
            job = await asyncio.to_thread(enqueue)
        except Exception:
            logger.exception("web_implicit_feedback_enqueue_failed")
            return _error(
                503,
                "implicit_feedback_enqueue_failed",
                "The implicit-feedback job could not be queued.",
                retryable=True,
            )
        return {
            "job_id": job.job_id,
            "status": job.status,
            "window": (job.payload or {}).get("window"),
        }

    @app.get("/api/memories/implicit-feedback/{job_id}")
    async def implicit_feedback_status(job_id: str) -> object:
        service = app.state.memory_management_service
        if service is None:
            return _error(
                503,
                "memory_unavailable",
                "Memory service is unavailable.",
                retryable=True,
            )
        store = _application_services(application).get("memory_work_store")
        job_fn = getattr(store, "job", None)
        if not callable(job_fn):
            return _error(
                503,
                "implicit_feedback_unavailable",
                "Implicit feedback status is unavailable.",
                retryable=True,
            )
        job = await asyncio.to_thread(job_fn, job_id)
        if job is None or job.kind != JOB_IMPLICIT_FEEDBACK:
            return _error(
                404,
                "feedback_job_not_found",
                "The implicit-feedback job does not exist.",
                retryable=False,
            )
        result = job.result if isinstance(job.result, dict) else {}
        return {
            "job_id": job.job_id,
            "status": job.status,
            "error": job.last_error,
            "actions_count": _non_noop_actions_count(result),
            "raw_verified": result.get("raw_verified"),
        }

    # ``/api/memories/feedback-runs`` is a literal segment like ``graph`` /
    # ``search`` — registered before ``/{memory_id}`` for the same reason.

    @app.get("/api/memories/feedback-runs")
    async def feedback_runs(limit: int = 50) -> object:
        """List implicit-feedback runs from the durable outbox (read-only)."""
        service = app.state.memory_management_service
        if service is None:
            return _error(
                503,
                "memory_unavailable",
                "Memory service is unavailable.",
                retryable=True,
            )
        store = _application_services(application).get("memory_work_store")
        list_jobs = getattr(store, "list_jobs", None)
        if not callable(list_jobs):
            return _error(
                503,
                "feedback_runs_unavailable",
                "Implicit-feedback runs are unavailable.",
                retryable=True,
            )
        jobs = await asyncio.to_thread(list_jobs, JOB_IMPLICIT_FEEDBACK, limit=limit)
        return {
            "runs": [
                {
                    "job_id": job.job_id,
                    "trigger": "manual" if ":manual:" in job.operation_key else "scheduled",
                    "status": job.status,
                    "window": (job.payload or {}).get("window"),
                    "created_at": job.created_at,
                    "updated_at": job.updated_at,
                    "actions_count": _non_noop_actions_count(job.result),
                    "error": job.last_error,
                }
                for job in jobs
            ]
        }

    @app.get("/api/memories/feedback-runs/{job_id}")
    async def feedback_run_detail(job_id: str) -> object:
        """Rebuild one run's scanned sessions and per-action memory diffs."""
        service = app.state.memory_management_service
        if service is None:
            return _error(
                503,
                "memory_unavailable",
                "Memory service is unavailable.",
                retryable=True,
            )
        services = _application_services(application)
        store = services.get("memory_work_store")
        job_fn = getattr(store, "job", None)
        inspector = services.get("feedback_run_inspector")
        if not callable(job_fn) or inspector is None:
            return _error(
                503,
                "feedback_runs_unavailable",
                "Implicit-feedback run details are unavailable.",
                retryable=True,
            )
        job = await asyncio.to_thread(job_fn, job_id)
        if job is None or job.kind != JOB_IMPLICIT_FEEDBACK:
            return _error(
                404,
                "feedback_job_not_found",
                "The implicit-feedback job does not exist.",
                retryable=False,
            )
        window = (job.payload or {}).get("window") or {}
        sessions = await inspector.scanned_sessions(window)
        result = job.result if isinstance(job.result, dict) else {}
        diffs = await inspector.action_diffs(result.get("actions") or [])
        return {
            "job_id": job.job_id,
            "trigger": "manual" if ":manual:" in job.operation_key else "scheduled",
            "status": job.status,
            "window": window or None,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "error": job.last_error,
            "sessions": [
                {
                    "session_id": session.session_id,
                    "records_count": session.records_count,
                    "excerpts": [
                        {
                            "add_record_id": excerpt.add_record_id,
                            "submitted_at": excerpt.submitted_at,
                            "preview": excerpt.preview,
                        }
                        for excerpt in session.excerpts
                    ],
                }
                for session in sessions
            ],
            "actions": [_action_diff_view(diff) for diff in diffs],
        }

    # ``/api/memories/dreaming-sessions`` — literal segments like
    # ``feedback-runs``: registered before ``/{memory_id}``.

    @app.get("/api/memories/dreaming-sessions")
    async def dreaming_sessions(limit: int = 500) -> object:
        """List every session with its aggregate archive/dreaming state."""
        service = app.state.memory_management_service
        if service is None:
            return _error(
                503,
                "memory_unavailable",
                "Memory service is unavailable.",
                retryable=True,
            )
        store = _application_services(application).get("memory_work_store")
        list_archives = getattr(store, "list_archives", None)
        if not callable(list_archives):
            return _error(
                503,
                "dreaming_unavailable",
                "Dreaming observability is unavailable.",
                retryable=True,
            )
        archives = await asyncio.to_thread(list_archives, limit=limit)
        by_session: dict[str, list[object]] = {}
        for archive in archives:
            by_session.setdefault(archive.session_id, []).append(archive)
        return {
            "sessions": [
                {
                    "session_id": summary.session_id,
                    "title": summary.title,
                    "updated_at": summary.updated_at,
                    "archive_state": _aggregate_archive_state(
                        by_session.get(summary.session_id) or []
                    ),
                    "archive_count": len(by_session.get(summary.session_id) or []),
                    "last_archive_at": max(
                        (a.created_at for a in by_session.get(summary.session_id) or []),
                        default=None,
                    ),
                }
                for summary in application.session_manager.session_summaries()
            ]
        }

    @app.get("/api/memories/dreaming-sessions/{session_id}")
    async def dreaming_session_detail(session_id: str) -> object:
        """Per-archive dreaming detail: seeds, jobs, and before/after diffs."""
        service = app.state.memory_management_service
        if service is None:
            return _error(
                503,
                "memory_unavailable",
                "Memory service is unavailable.",
                retryable=True,
            )
        if session_id not in _session_ids(application.session_manager):
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        services = _application_services(application)
        store = services.get("memory_work_store")
        archives_fn = getattr(store, "archives_for_session", None)
        job_fn = getattr(store, "job", None)
        op_fn = getattr(store, "job_by_operation_key", None)
        turns_fn = getattr(store, "turns_for_session", None)
        if not callable(archives_fn) or not callable(job_fn):
            return _error(
                503,
                "dreaming_unavailable",
                "Dreaming observability is unavailable.",
                retryable=True,
            )
        inspector = services.get("feedback_run_inspector")

        archives = await asyncio.to_thread(archives_fn, session_id)
        session_excerpts: list[dict[str, object]] = []
        if callable(turns_fn):
            turns = await asyncio.to_thread(turns_fn, session_id)
            for turn in turns[-5:]:
                preview = await asyncio.to_thread(
                    _turn_user_preview, store, session_id, turn.turn_id
                )
                session_excerpts.append(
                    {"turn_id": turn.turn_id, "preview": preview}
                )

        archive_views: list[dict[str, object]] = []
        for archive in archives:
            job = (
                await asyncio.to_thread(job_fn, archive.dreaming_job_id)
                if archive.dreaming_job_id
                else None
            )
            result = job.result if job is not None and isinstance(job.result, dict) else {}
            seeds: list[dict[str, object]] = []
            if callable(op_fn):
                for op_id in archive.add_operation_ids:
                    seed_job = await asyncio.to_thread(op_fn, op_id)
                    turn_id = getattr(seed_job, "turn_id", None) if seed_job else None
                    if turn_id is None:
                        seeds.append(
                            {
                                "operation_id": op_id,
                                "turn_id": None,
                                "preview": None,
                                "missing": True,
                            }
                        )
                    else:
                        preview = await asyncio.to_thread(
                            _turn_user_preview, store, session_id, turn_id
                        )
                        seeds.append(
                            {
                                "operation_id": op_id,
                                "turn_id": turn_id,
                                "preview": preview,
                                "missing": False,
                            }
                        )
            if inspector is None:
                actions: object = None
                diffs_unavailable = True
            elif job is None:
                actions = []
                diffs_unavailable = False
            else:
                diffs = await inspector.action_diffs(result.get("actions") or [])
                actions = [_action_diff_view(diff) for diff in diffs]
                diffs_unavailable = False
            archive_views.append(
                {
                    "archive_id": archive.archive_id,
                    "state": _ARCHIVE_STATE_MAP.get(archive.status, archive.status),
                    "turn_range": [archive.turn_range_start, archive.turn_range_end],
                    "max_event_seq": archive.max_event_seq,
                    "created_at": archive.created_at,
                    "job": (
                        {
                            "job_id": job.job_id,
                            "status": job.status,
                            "attempts": job.attempts,
                            "error": job.last_error,
                            "dreaming_record_id": result.get("dreaming_record_id"),
                            "raw_verified": result.get("raw_verified"),
                            "missing_ids": result.get("missing_ids") or [],
                            "extracted_count": (
                                len(result["produced_memory_ids"])
                                if isinstance(result.get("produced_memory_ids"), list)
                                else None
                            ),
                        }
                        if job is not None
                        else None
                    ),
                    "seeds": seeds,
                    "actions": actions,
                    "diffs_unavailable": diffs_unavailable,
                }
            )
        return {
            "session_id": session_id,
            "archive_state": _aggregate_archive_state(archives),
            "session_excerpts": session_excerpts,
            "archives": archive_views,
        }

    @app.get("/api/memories/{memory_id}")
    async def memory_detail(memory_id: str) -> object:
        service = app.state.memory_management_service
        if service is None:
            return _error(
                503,
                "memory_unavailable",
                "Memory service is unavailable.",
                retryable=True,
            )
        try:
            memory = await service.detail(
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
            logger.exception("web_memory_detail_failed", extra={"memory_id": memory_id})
            return _error(
                503,
                "memory_read_failed",
                "Memory data could not be read.",
                retryable=True,
            )
        return ManagedMemoryResponse.model_validate(memory).model_dump(mode="json")

    @app.patch("/api/memories/{memory_id}")
    async def update_memory(memory_id: str, body: UpdateMemoryRequest) -> object:
        service = app.state.memory_management_service
        if service is None:
            return _error(
                503,
                "memory_unavailable",
                "Memory service is unavailable.",
                retryable=True,
            )
        try:
            updated = await service.update(
                memory_id,
                body.content,
                tenant_id=_WEB_PERMISSION_SUBJECT.tenant_id,
                fields=body.fields,
            )
        except MemoryNotFoundError:
            return _error(
                404,
                "memory_not_found",
                "The memory does not exist.",
                retryable=False,
            )
        except ValueError as exc:
            return _error(422, "memory_update_rejected", str(exc), retryable=False)
        except Exception:
            logger.exception("web_memory_update_failed", extra={"memory_id": memory_id})
            return _error(
                503,
                "memory_write_failed",
                "Memory could not be updated.",
                retryable=True,
            )
        return {
            "memory": ManagedMemoryResponse.model_validate(updated).model_dump(mode="json"),
            "status": "updated",
        }

    @app.delete("/api/memories/{memory_id}")
    async def delete_memory(memory_id: str) -> object:
        service = app.state.memory_management_service
        if service is None:
            return _error(
                503,
                "memory_unavailable",
                "Memory service is unavailable.",
                retryable=True,
            )
        try:
            result = await service.delete(
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
        except ValueError as exc:
            return _error(422, "memory_delete_rejected", str(exc), retryable=False)
        except Exception:
            logger.exception("web_memory_delete_failed", extra={"memory_id": memory_id})
            return _error(
                503,
                "memory_write_failed",
                "Memory could not be deleted.",
                retryable=True,
            )
        return result

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


__all__ = ["register"]
