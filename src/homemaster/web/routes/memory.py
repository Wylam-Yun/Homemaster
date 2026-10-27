"""Memory management surface: snapshot, graph, search, detail, history, create, update, delete.

V3.8: the ALFWorld compile job routes (``POST /api/memories/{id}/compile`` and
``GET /api/memory-compilations/{job_id}``) were retired with the compile
branch; all remaining reads/mutations flow through ``MindMemOSClient`` via
``MemoryManagementService``.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from homemaster.web._shared import _WEB_PERMISSION_SUBJECT, _error
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
