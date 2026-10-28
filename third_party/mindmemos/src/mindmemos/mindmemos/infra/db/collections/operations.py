"""Qdrant adapter for the durable ``operations`` ledger (V3.8).

The ledger is keyed by client-supplied ``operation_id`` scoped per project.
Because client ids are arbitrary strings (e.g. ``turnadd:<session>:<turn>``)
while Qdrant point ids must be UUIDs or integers, the point id is a
deterministic ``uuid5`` of ``(project_id, operation_id)`` — the same pair always
maps to the same point, so reservation is an idempotent upsert.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qmodels

from ....config import QdrantConfig
from ....logging import get_logger
from ..engine import QdrantEngine
from ..filters import match_value
from ..models import OperationPoint, QdrantRecord
from ..schema import operation_collection_spec

logger = get_logger(__name__)

_OPERATION_ID_NAMESPACE = uuid5(NAMESPACE_URL, "mindmemos://operations/v1")


def operation_point_id(project_id: str, operation_id: str) -> str:
    """Deterministic Qdrant point id for ``(project_id, operation_id)``."""

    return str(uuid5(_OPERATION_ID_NAMESPACE, f"{project_id}\x00{operation_id}"))


class OperationRepository:
    """Thin Qdrant adapter for the operation ledger (no business logic).

    Like :class:`SkillVersionRepository` this repository reuses a shared
    :class:`QdrantEngine` and never closes it. For standalone use it can be
    built from a borrowed ``AsyncQdrantClient``.
    """

    def __init__(
        self,
        cfg: QdrantConfig,
        *,
        client: AsyncQdrantClient | None = None,
        engine: QdrantEngine | None = None,
    ) -> None:
        if engine is None and client is None:
            raise ValueError("OperationRepository requires either an engine or a client")
        self._cfg = cfg
        self._engine = engine if engine is not None else QdrantEngine(cfg, client=client)

    @property
    def collection(self) -> str:
        """Configured ``operations`` collection name."""

        return self._cfg.operation_collection

    async def ensure_schema(self) -> None:
        """Create the operations collection and its payload indexes."""

        if not self._cfg.auto_create:
            return
        await self._engine.ensure_collection(operation_collection_spec(self._cfg))

    async def upsert(self, point: OperationPoint) -> None:
        """Upsert one operation row (idempotent via deterministic point id)."""

        await self._engine.upsert(
            self.collection,
            [
                qmodels.PointStruct(
                    id=point.operation_point_id, vector={}, payload=self._engine.safe_payload(point.payload)
                )
            ],
        )

    async def get(self, project_id: str, operation_id: str) -> QdrantRecord | None:
        """Retrieve one operation row by ``(project_id, operation_id)``."""

        if not operation_id:
            return None
        records = await self._engine.retrieve(
            self.collection,
            [operation_point_id(project_id, operation_id)],
        )
        record = records[0] if records else None
        if record is None:
            return None
        payload = record.payload or {}
        if str(payload.get("operation_id") or "") != operation_id:
            return None
        return record

    async def patch(self, project_id: str, operation_id: str, fields: dict[str, Any]) -> None:
        """Merge ``fields`` into one operation row's payload."""

        await self._engine.set_payload(
            self.collection,
            operation_point_id(project_id, operation_id),
            self._engine.safe_payload(fields),
        )

    async def list_for_project(
        self,
        project_id: str,
        *,
        kind: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[QdrantRecord]:
        """List operation rows for a project (newest first when indexed)."""

        conditions = []
        if kind:
            conditions.append(match_value("kind", kind))
        if status:
            conditions.append(match_value("status", status))
        records, _ = await self._engine.scroll(
            self.collection,
            scroll_filter=self._engine.project_filter(project_id, conditions=conditions),
            limit=limit,
        )
        return records


def dumps_canonical(value: Any) -> str:
    """Serialize ``value`` to canonical JSON (sorted keys, compact)."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
