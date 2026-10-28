"""Qdrant adapters for ``skill_traces`` + ``skill_evolution_batches`` (V3.8).

Dedicated lifecycle stores for the HomeMaster V3.8 integration:

- ``skill_traces`` holds sealed skill invocation traces submitted by clients,
  keyed by ``(project_id, cloud_skill_id, invocation_id)`` via deterministic
  ``uuid5`` point ids so resubmission is an idempotent upsert.
- ``skill_evolution_batches`` holds explicit evolution-batch claims that consume
  a fixed set of sealed traces and mint draft skill versions.
"""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qmodels

from ....config import QdrantConfig
from ....logging import get_logger
from ..engine import QdrantEngine
from ..filters import is_empty, match_value
from ..models import QdrantRecord, SkillEvolutionBatchPoint, SkillTracePoint
from ..schema import skill_evolution_batch_collection_spec, skill_trace_collection_spec

logger = get_logger(__name__)

_SKILL_TRACE_NAMESPACE = uuid5(NAMESPACE_URL, "mindmemos://skill_traces/v1")
_SKILL_BATCH_NAMESPACE = uuid5(NAMESPACE_URL, "mindmemos://skill_evolution_batches/v1")


def skill_trace_point_id(project_id: str, cloud_skill_id: str, invocation_id: str) -> str:
    """Deterministic Qdrant point id for one invocation trace."""

    return str(uuid5(_SKILL_TRACE_NAMESPACE, f"{project_id}\x00{cloud_skill_id}\x00{invocation_id}"))


def skill_batch_point_id(project_id: str, cloud_skill_id: str, batch_id: str) -> str:
    """Deterministic Qdrant point id for one evolution batch claim."""

    return str(uuid5(_SKILL_BATCH_NAMESPACE, f"{project_id}\x00{cloud_skill_id}\x00{batch_id}"))


class SkillTraceRepository:
    """Thin Qdrant adapter for sealed skill invocation traces."""

    def __init__(
        self,
        cfg: QdrantConfig,
        *,
        client: AsyncQdrantClient | None = None,
        engine: QdrantEngine | None = None,
    ) -> None:
        if engine is None and client is None:
            raise ValueError("SkillTraceRepository requires either an engine or a client")
        self._cfg = cfg
        self._engine = engine if engine is not None else QdrantEngine(cfg, client=client)

    @property
    def collection(self) -> str:
        """Configured ``skill_traces`` collection name."""

        return self._cfg.skill_trace_collection

    async def ensure_schema(self) -> None:
        """Create the trace collection and its payload indexes."""

        if not self._cfg.auto_create:
            return
        await self._engine.ensure_collection(skill_trace_collection_spec(self._cfg))

    async def upsert(self, point: SkillTracePoint) -> None:
        """Upsert one sealed trace (idempotent on invocation identity)."""

        await self._engine.upsert(
            self.collection,
            [qmodels.PointStruct(id=point.trace_point_id, vector={}, payload=self._engine.safe_payload(point.payload))],
        )

    async def get(self, project_id: str, cloud_skill_id: str, invocation_id: str) -> QdrantRecord | None:
        """Retrieve one trace by ``(project_id, cloud_skill_id, invocation_id)``."""

        if not invocation_id:
            return None
        records = await self._engine.retrieve(
            self.collection,
            [skill_trace_point_id(project_id, cloud_skill_id, invocation_id)],
        )
        return records[0] if records else None

    async def get_many(
        self,
        project_id: str,
        cloud_skill_id: str,
        invocation_ids: list[str],
    ) -> list[QdrantRecord]:
        """Retrieve traces for a batch claim, preserving request order/dedup."""

        ids = [skill_trace_point_id(project_id, cloud_skill_id, iid) for iid in dict.fromkeys(invocation_ids) if iid]
        if not ids:
            return []
        return await self._engine.retrieve(self.collection, ids)

    async def list_for_skill(
        self,
        project_id: str,
        cloud_skill_id: str,
        *,
        limit: int = 100,
    ) -> list[QdrantRecord]:
        """List traces for one skill (newest first)."""

        records, _ = await self._engine.scroll(
            self.collection,
            scroll_filter=self._engine.project_filter(
                project_id, conditions=[match_value("cloud_skill_id", cloud_skill_id)]
            ),
            limit=limit,
        )
        return records

    async def count_unconsumed(self, project_id: str, cloud_skill_id: str) -> int:
        """Count sealed traces that no evolution batch has consumed yet."""

        total = await self._engine.count(
            self.collection,
            count_filter=self._engine.project_filter(
                project_id,
                conditions=[
                    match_value("cloud_skill_id", cloud_skill_id),
                    is_empty("consumed_by_batch_id"),
                ],
            ),
        )
        return int(total)

    async def patch(self, project_id: str, cloud_skill_id: str, invocation_id: str, fields: dict) -> None:
        """Merge ``fields`` (e.g. ``consumed_by_batch_id``) into one trace."""

        await self._engine.set_payload(
            self.collection,
            skill_trace_point_id(project_id, cloud_skill_id, invocation_id),
            self._engine.safe_payload(fields),
        )


class SkillEvolutionBatchRepository:
    """Thin Qdrant adapter for evolution-batch claims."""

    def __init__(
        self,
        cfg: QdrantConfig,
        *,
        client: AsyncQdrantClient | None = None,
        engine: QdrantEngine | None = None,
    ) -> None:
        if engine is None and client is None:
            raise ValueError("SkillEvolutionBatchRepository requires either an engine or a client")
        self._cfg = cfg
        self._engine = engine if engine is not None else QdrantEngine(cfg, client=client)

    @property
    def collection(self) -> str:
        """Configured ``skill_evolution_batches`` collection name."""

        return self._cfg.skill_evolution_batch_collection

    async def ensure_schema(self) -> None:
        """Create the batch collection and its payload indexes."""

        if not self._cfg.auto_create:
            return
        await self._engine.ensure_collection(skill_evolution_batch_collection_spec(self._cfg))

    async def upsert(self, point: SkillEvolutionBatchPoint) -> None:
        """Upsert one batch claim (idempotent on batch identity)."""

        await self._engine.upsert(
            self.collection,
            [qmodels.PointStruct(id=point.batch_point_id, vector={}, payload=self._engine.safe_payload(point.payload))],
        )

    async def get(self, project_id: str, cloud_skill_id: str, batch_id: str) -> QdrantRecord | None:
        """Retrieve one batch claim by ``(project_id, cloud_skill_id, batch_id)``."""

        if not batch_id:
            return None
        records = await self._engine.retrieve(
            self.collection,
            [skill_batch_point_id(project_id, cloud_skill_id, batch_id)],
        )
        return records[0] if records else None

    async def patch(self, project_id: str, cloud_skill_id: str, batch_id: str, fields: dict) -> None:
        """Merge ``fields`` into one batch claim's payload."""

        await self._engine.set_payload(
            self.collection,
            skill_batch_point_id(project_id, cloud_skill_id, batch_id),
            self._engine.safe_payload(fields),
        )
