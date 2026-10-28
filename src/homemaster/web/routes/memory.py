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
from homemaster.web._shared import _WEB_PERMISSION_SUBJECT, _application_services, _error
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
            "actions": [
                {
                    "action_id": diff.action_id,
                    "action": diff.action,
                    "memory_id": diff.memory_id,
                    "memory_session_id": diff.memory_session_id,
                    "before": (
                        {"content": diff.before.content, "status": diff.before.status}
                        if diff.before is not None
                        else None
                    ),
                    "after": (
                        {"content": diff.after.content, "status": diff.after.status}
                        if diff.after is not None
                        else None
                    ),
                    "missing": diff.missing,
                }
                for diff in diffs
            ],
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
