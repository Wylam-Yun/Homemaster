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
        actions = result.get("actions") or []
        return {
            "job_id": job.job_id,
            "status": job.status,
            "error": job.last_error,
            "actions_count": sum(
                1
                for action in actions
                if isinstance(action, dict) and action.get("action") not in (None, "noop")
            ),
            "raw_verified": result.get("raw_verified"),
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
