"""Independent raw-storage readback verifier (V3.8).

Workers must prove a mutation really persisted — an SDK success receipt,
HTTP 200, or a queue admission is NOT evidence. This component performs
read-only reads against the raw stores backing the MindMemOS deployment
(Qdrant points, Neo4j nodes) and reports per-id existence.

HomeMaster never *writes* through this path: the verifier is read-only by
construction (no mutating method exists on the object).

Scope isolation: verifiers are bound to a concrete tenant collection/graph
prefix. Test/live-gate instances are built against isolated collections and
databases so they can never read — let alone mutate — real memory data.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

# Memory nodes carry ``project_id``/payload fields in Qdrant; Neo4j Memory
# nodes only hold content/status, so edges are all the graph store can
# authoritatively contribute to the web graph view. This query is the single
# source of truth for that read — callers pass ``$pid`` (tenant project) and
# ``$lim``.
_GRAPH_EDGES_QUERY = """
MATCH (m:Memory {project_id:$pid})-[r]->(n)
WHERE type(r) IN ['MENTIONS','EXTRACTED_FROM','RELATES_TO','DERIVED_FROM']
  AND n.project_id = $pid
RETURN m.memory_id AS src, type(r) AS rel,
       coalesce(n.memory_id, n.entity_id, n.source_id) AS dst,
       CASE WHEN 'Entity' IN labels(n) THEN 'Entity' ELSE labels(n)[0] END
         AS dst_label,
       n.entity_name AS entity_name
LIMIT $lim
"""


class RawMemoryVerifier(Protocol):
    """Read-only proof that given ids exist in raw storage."""

    async def memory_exists(self, memory_id: str) -> bool: ...
    async def add_record_exists(self, add_record_id: str) -> bool: ...
    async def memory_ids_exist(self, memory_ids: list[str]) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for mid in memory_ids:
            results[mid] = await self.memory_exists(mid)
        return results
    async def close(self) -> None: ...


@dataclass(frozen=True)
class NullVerifier:
    """Verifier used when raw readback is unavailable (files-only mode).

    It never claims proof: ``memory_exists`` returns ``None`` semantics via
    a False result, and the worker treats that as ``verified=False`` —
    the job still completes on the SDK receipt but the receipt is annotated
    ``raw_verified: false`` so audits can distinguish proof levels.
    """

    async def memory_exists(self, memory_id: str) -> bool:  # noqa: ARG002
        return False

    async def add_record_exists(self, add_record_id: str) -> bool:  # noqa: ARG002
        return False

    async def memory_ids_exist(self, memory_ids: list[str]) -> dict[str, bool]:
        return {mid: False for mid in memory_ids}

    async def close(self) -> None:
        return None


class QdrantNeo4jVerifier:
    """Read-only verifier over the stores backing a MindMemOS server.

    Upstream storage shape (verified against the live server): memory vectors
    are Qdrant points in ``memory_item_v1`` keyed by ``memory_id``; AddRecords
    are payload-only points in ``add_record_v1`` keyed by ``add_record_id``;
    graph ``Memory`` nodes are keyed by ``memory_id`` + ``project_id``. The
    collection names default to the upstream config values — override them
    only when the deployment renames its collections.
    """

    def __init__(
        self,
        *,
        qdrant_url: str | None = None,
        qdrant_api_key: str | None = None,
        qdrant_collection: str = "memory_item_v1",
        add_record_collection: str = "add_record_v1",
        neo4j_uri: str | None = None,
        neo4j_username: str | None = None,
        neo4j_password: str | None = None,
        neo4j_database: str = "neo4j",
        memory_label: str = "Memory",
        project_id: str | None = None,
    ) -> None:
        self._collection = qdrant_collection
        self._add_record_collection = add_record_collection
        self._memory_label = memory_label
        self._project_id = project_id
        self._qdrant: Any = None
        self._neo4j_driver: Any = None
        self._neo4j_database = neo4j_database
        if qdrant_url:
            from qdrant_client import QdrantClient

            self._qdrant = QdrantClient(url=qdrant_url, api_key=qdrant_api_key or None)
        if neo4j_uri:
            from neo4j import GraphDatabase

            self._neo4j_driver = GraphDatabase.driver(
                neo4j_uri,
                auth=(neo4j_username or "neo4j", neo4j_password or ""),
            )

    async def memory_exists(self, memory_id: str) -> bool:
        found = False
        if self._qdrant is not None:
            found = await asyncio.to_thread(
                self._qdrant_point_exists, self._collection, memory_id
            )
        if not found and self._neo4j_driver is not None:
            found = await asyncio.to_thread(
                self._neo4j_node_exists, self._memory_label, memory_id
            )
        return found

    async def add_record_exists(self, add_record_id: str) -> bool:
        # AddRecords are payload-only Qdrant points — they never reach Neo4j.
        if self._qdrant is None:
            return False
        return await asyncio.to_thread(
            self._qdrant_point_exists, self._add_record_collection, add_record_id
        )

    async def memory_ids_exist(self, memory_ids: list[str]) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for mid in memory_ids:
            results[mid] = await self.memory_exists(mid)
        return results

    async def graph_edges(
        self, limit: int, *, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Read up to ``limit + 1`` memory-graph edges for one tenant project.

        Powers the web memory-graph view (the only read-only graph consumer
        outside the verifier's existence checks). The extra row past ``limit``
        lets the caller detect truncation instead of silently dropping edges.
        ``project_id`` defaults to the constructor-bound scope; raises
        ``RuntimeError`` when Neo4j or the project scope is unconfigured so
        callers can fail closed (the web surface maps it to a 503) rather than
        render an empty graph as if it were real data.
        """

        pid = project_id or self._project_id
        if self._neo4j_driver is None:
            raise RuntimeError("graph edge reads require a neo4j driver")
        if not pid:
            raise RuntimeError("graph edge reads require a project_id")
        return await asyncio.to_thread(self._graph_edges_blocking, pid, int(limit) + 1)

    def _graph_edges_blocking(self, project_id: str, limit: int) -> list[dict[str, Any]]:
        with self._neo4j_driver.session(database=self._neo4j_database) as session:
            result = session.run(_GRAPH_EDGES_QUERY, pid=project_id, lim=limit)
            return [dict(record) for record in result]

    def _qdrant_point_exists(self, collection: str, point_id: str) -> bool:
        try:
            points = self._qdrant.retrieve(
                collection_name=collection, ids=[point_id], with_payload=False
            )
        except Exception:
            return False
        return any(str(p.id) == point_id for p in points)

    def _neo4j_node_exists(self, label: str, node_id: str) -> bool:
        query = f"MATCH (n:`{label}` {{memory_id: $id}}) RETURN count(n) AS c"
        try:
            with self._neo4j_driver.session(database=self._neo4j_database) as session:
                record = session.run(query, id=node_id).single()
        except Exception:
            return False
        return bool(record and record["c"])

    async def close(self) -> None:
        if self._qdrant is not None:
            await asyncio.to_thread(self._qdrant.close)
            self._qdrant = None
        if self._neo4j_driver is not None:
            await asyncio.to_thread(self._neo4j_driver.close)
            self._neo4j_driver = None


__all__ = [
    "NullVerifier",
    "QdrantNeo4jVerifier",
    "RawMemoryVerifier",
]
