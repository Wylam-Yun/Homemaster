"""Shared in-memory fakes for unit tests (V3.8)."""

from __future__ import annotations

from typing import Any

from mindmemos.infra.db.collections.operations import operation_point_id
from mindmemos.infra.db.models import OperationPoint, QdrantRecord
from mindmemos.services.operations import OperationLedger


class InMemoryOperationRepo:
    """In-memory ``OperationRepository`` stand-in (upsert/get/patch/list)."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    async def get(self, project_id: str, operation_id: str) -> QdrantRecord | None:
        key = operation_point_id(project_id, operation_id)
        row = self._store.get(key)
        return QdrantRecord(point_id=key, payload=dict(row)) if row is not None else None

    async def upsert(self, point: OperationPoint) -> None:
        self._store[point.operation_point_id] = dict(point.payload)

    async def patch(self, project_id: str, operation_id: str, fields: dict[str, Any]) -> None:
        key = operation_point_id(project_id, operation_id)
        self._store.setdefault(key, {"operation_id": operation_id, "project_id": project_id})
        self._store[key].update(fields)


def in_memory_operation_ledger() -> OperationLedger:
    """A real ``OperationLedger`` over in-memory storage for service-level tests."""

    return OperationLedger(repo=InMemoryOperationRepo())  # type: ignore[arg-type]
