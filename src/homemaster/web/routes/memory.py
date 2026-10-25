"""Read-only memory surface plus the server-owned ALFWorld compile jobs."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from homemaster.web._shared import _WEB_PERMISSION_SUBJECT, _error
from homemaster.web.context import WebAppContext
from homemaster.web.memory_views import MemoryNotFoundError, MemoryUnavailableError
from homemaster.web.schemas import (
    MemoryGraphResponse,
    MemoryHistoryResponse,
    MemorySnapshotResponse,
)

logger = logging.getLogger(__name__)


def register(app: FastAPI, ctx: WebAppContext) -> None:
    del ctx  # Services are optional and stay lazy reads off ``app.state``.

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


__all__ = ["register"]
