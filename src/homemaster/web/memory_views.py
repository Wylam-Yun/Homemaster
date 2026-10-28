"""Read-only memory projections for the Web management surface (V3.8).

The service talks to the standalone MindMemOS server exclusively through
``homemaster.memory.mindmemos_client.MindMemOSClient``. The client is bound to
a fixed ``MemoryScope(tenant_id=...)`` at construction, so every method keeps
its ``tenant_id`` keyword only for call-site compatibility: the value is
ignored and tenant isolation is enforced by the scope-bound client/server
(api key + user_id), never re-passed per request.

V3.8 removed the flat-update fallback: a failed native ``update_memory`` call
surfaces as ``MemoryUnavailableError`` and the stored memory is left intact —
the service never deletes + recreates behind the caller's back.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from homemaster.agent.messages import UserMessage
from homemaster.memory.mindmemos_client import (
    MindMemOSClient,
    MindMemOSClientError,
)
from homemaster.memory.models import MEMORY_RECORD_ADAPTER

if TYPE_CHECKING:
    from homemaster.application.session import SessionManager


StructureStatus = Literal["plain", "valid", "invalid"]
_MEMORY_TYPE_LABELS = {
    "fact": "事实",
    "procedure": "操作流程",
    "trajectory": "ALFWorld 轨迹",
    "alfworld_experience": "ALFWorld 派生经验",
}

# An async edge reader: either a plain callable ``(limit) -> awaitable rows``
# or an object exposing ``graph_edges(limit)`` (``QdrantNeo4jVerifier``).
GraphEdgesReader = Callable[[int], Awaitable[list[Mapping[str, Any]]]]


class MemoryNotFoundError(LookupError):
    """Raised when an exact memory is not visible in the current tenant."""


class MemoryUnavailableError(RuntimeError):
    """Raised when a memory backend needed for a read is not running."""


@dataclass(frozen=True)
class MemoryStats:
    active_count: int
    archived_count: int
    total_count: int
    session_group_count: int


@dataclass(frozen=True)
class ManagedMemory:
    memory_id: str
    content: str
    memory_type: str
    memory_type_label: str
    status: str
    session_id: str | None
    created_at: datetime | None
    updated_at: datetime | None
    archived_at: datetime | None
    archive_reason: str | None
    record: dict[str, object] | None
    structure_status: StructureStatus
    has_history: bool
    domain: str | None = None
    outcome: str | None = None
    classification: str | None = None
    goal_type: str | None = None
    episode_id: str | None = None
    taskset_id: str | None = None
    subtask_index: int | None = None
    is_executable: bool | None = None
    source_trajectory_id: str | None = None
    derived_memory_id: str | None = None
    compile_status: str | None = None


@dataclass(frozen=True)
class MemoryGroup:
    session_id: str | None
    title: str
    active_count: int
    archived_count: int
    memories: tuple[ManagedMemory, ...]


@dataclass(frozen=True)
class MemorySnapshot:
    stats: MemoryStats
    groups: tuple[MemoryGroup, ...]


@dataclass(frozen=True)
class MemorySearchHit:
    """One search hit projected for the browser."""

    memory_id: str
    content: str
    memory_type: str
    memory_type_label: str
    score: float | None
    session_id: str | None


@dataclass(frozen=True)
class MemorySearchResult:
    query: str
    hits: tuple[MemorySearchHit, ...]
    search_record_id: str | None


@dataclass(frozen=True)
class MemoryGraphNode:
    memory_id: str
    preview: str
    memory_type: str
    status: str
    session_id: str | None
    updated_at: datetime | None


@dataclass(frozen=True)
class MemoryGraphEdge:
    src: str
    rel: str
    dst: str
    dst_label: str
    entity_name: str | None


@dataclass(frozen=True)
class MemoryGraphEntity:
    entity_id: str
    name: str


@dataclass(frozen=True)
class MemoryGraphSession:
    session_id: str
    title: str


@dataclass(frozen=True)
class MemoryGraph:
    nodes: tuple[MemoryGraphNode, ...]
    entities: tuple[MemoryGraphEntity, ...]
    edges: tuple[MemoryGraphEdge, ...]
    sessions: tuple[MemoryGraphSession, ...]
    edges_truncated: bool = False


# Nodes carry the server payload fields (mem_type/session_id/update_at); the
# Neo4j edge reader only contributes relationships. The reader fetches one row
# past this cap so truncation is detectable instead of silent (the +1 probe
# lives inside ``QdrantNeo4jVerifier.graph_edges``).
_GRAPH_EDGES_LIMIT = 2000
_SCROLL_PAGE_LIMIT = 500
_SCROLL_MAX_PAGES = 100
_PREVIEW_LIMIT = 160
_MUTATION_OK_STATUSES = frozenset({"completed", "accepted", "processing", "no_action"})


class MemoryManagementService:
    """Compose project-scoped MindMemOS rows into browser-safe read DTOs."""

    def __init__(
        self,
        client: MindMemOSClient,
        sessions: SessionManager,
        graph_reader: GraphEdgesReader | object | None = None,
    ) -> None:
        self._client = client
        self._sessions = sessions
        self._graph_reader = graph_reader

    async def snapshot(self, *, tenant_id: str) -> MemorySnapshot:
        del tenant_id  # scope-bound client (see module docstring)
        items = tuple(_project_memory(row) for row in await self._scroll_all())
        return self._group_snapshot(items)

    async def detail(
        self,
        memory_id: str,
        *,
        tenant_id: str,
    ) -> ManagedMemory:
        del tenant_id
        normalized_id = memory_id.strip()
        if not normalized_id:
            raise MemoryNotFoundError(memory_id)
        raw = await self._get_payload(normalized_id)
        if raw is None:
            raise MemoryNotFoundError(normalized_id)
        return _project_memory(raw)

    async def history(
        self,
        memory_id: str,
        *,
        tenant_id: str,
    ) -> tuple[ManagedMemory, ...]:
        del tenant_id
        normalized_id = memory_id.strip()
        if not normalized_id:
            raise MemoryNotFoundError(memory_id)
        payload = await self._get_payload_full(normalized_id)
        if payload is None:
            raise MemoryNotFoundError(normalized_id)
        # The get response carries version history when the server has it:
        # either a top-level ``history``/``versions`` list or one embedded in
        # the returned memory item. A single-version memory returns just itself.
        versions = _history_items(payload)
        if versions:
            projected = (_project_memory(row) for row in versions)
            return tuple(sorted(projected, key=_memory_sort_key))
        items = _payload_memories(payload)
        if not items:
            raise MemoryNotFoundError(normalized_id)
        return (_project_memory(items[0]),)

    async def search(
        self,
        query: str,
        *,
        tenant_id: str,
        top_k: int = 10,
    ) -> MemorySearchResult:
        del tenant_id
        cleaned = query.strip()
        if not cleaned:
            raise ValueError("query must not be empty")
        try:
            receipt = await self._client.search(cleaned, top_k=top_k)
        except MindMemOSClientError as exc:
            raise MemoryUnavailableError(str(exc)) from exc
        hits = tuple(
            MemorySearchHit(
                memory_id=str(hit.id),
                content=str(hit.memory),
                memory_type=str(hit.memory_type),
                memory_type_label=_MEMORY_TYPE_LABELS.get(
                    str(hit.memory_type), str(hit.memory_type)
                ),
                score=hit.score,
                session_id=_metadata_session_id(hit.metadata),
            )
            for hit in receipt.hits
        )
        return MemorySearchResult(
            query=cleaned,
            hits=hits,
            search_record_id=receipt.search_record_id,
        )

    async def create(
        self,
        content: str,
        memory_type: str = "fact",
        *,
        tenant_id: str,
    ) -> ManagedMemory:
        del tenant_id
        cleaned = content.strip()
        if not cleaned:
            raise ValueError("content must not be empty")
        if memory_type not in {"fact", "procedure"}:
            raise ValueError("memory_type must be fact or procedure")
        try:
            receipt = await self._client.create_memory(
                operation_id=f"webcreate:{uuid4().hex}",
                content=cleaned,
                mem_type=memory_type,
            )
        except MindMemOSClientError as exc:
            raise MemoryUnavailableError(str(exc)) from exc
        memory_id = receipt.memory_id
        if not memory_id:
            raise MemoryUnavailableError("memory backend did not return a memory_id")
        raw = await self._get_payload(memory_id)
        if raw is None:
            raise MemoryUnavailableError("created memory could not be read back")
        return _project_memory(raw)

    async def update(
        self,
        memory_id: str,
        content: str,
        *,
        tenant_id: str,
        fields: Mapping[str, Any] | None = None,
    ) -> ManagedMemory:
        del tenant_id
        normalized_id = memory_id.strip()
        cleaned = content.strip()
        if not normalized_id:
            raise MemoryNotFoundError(memory_id)
        if not cleaned and not fields:
            raise ValueError("content must not be empty")
        current = await self._get_payload(normalized_id)
        if current is None:
            raise MemoryNotFoundError(normalized_id)
        if _is_structured(current) and not fields:
            # Structured records carry record_json, entity linkage and graph
            # relations that a content-only patch would leave stale; per the
            # shared-update-surface rule the text path fails closed instead of
            # corrupting the structural copy. Supplying ``fields`` performs a
            # native structured update (new version + lineage, never
            # delete+recreate).
            raise ValueError(
                "memory contains a structured record and cannot be edited as plain text"
            )
        try:
            receipt = await self._client.update_memory(
                memory_id=normalized_id,
                operation_id=f"webupdate:{uuid4().hex}",
                content=cleaned or None,
                fields=dict(fields) if fields else None,
            )
        except MindMemOSClientError as exc:
            if _is_not_found_error(exc):
                raise MemoryNotFoundError(normalized_id) from exc
            # V3.8: no flat-update fallback — surface the failure, keep the
            # stored memory untouched.
            raise MemoryUnavailableError(str(exc)) from exc
        _raise_for_rejected_mutation(receipt.status, normalized_id)
        updated_id = str(receipt.memory_id or normalized_id)
        raw = await self._get_payload(updated_id)
        if raw is None and updated_id != normalized_id:
            raw = await self._get_payload(normalized_id)
        if raw is None:
            raise MemoryUnavailableError("updated memory could not be read back")
        return _project_memory(raw)

    async def delete(
        self,
        memory_id: str,
        *,
        tenant_id: str,
    ) -> dict[str, object]:
        del tenant_id
        normalized_id = memory_id.strip()
        if not normalized_id:
            raise MemoryNotFoundError(memory_id)
        current = await self._get_payload(normalized_id)
        if current is None:
            raise MemoryNotFoundError(normalized_id)
        try:
            receipt = await self._client.delete_memory(
                memory_id=normalized_id,
                operation_id=f"webdelete:{uuid4().hex}",
            )
        except MindMemOSClientError as exc:
            if _is_not_found_error(exc):
                raise MemoryNotFoundError(normalized_id) from exc
            raise MemoryUnavailableError(str(exc)) from exc
        _raise_for_rejected_mutation(receipt.status, normalized_id)
        return {
            "memory_id": normalized_id,
            "deleted": True,
            "status": "archived",
        }

    async def graph(self, *, tenant_id: str) -> MemoryGraph:
        del tenant_id
        reader = self._graph_reader
        if reader is None:
            raise MemoryUnavailableError("memory graph backend is not configured")
        edge_fn = getattr(reader, "graph_edges", None)
        if not callable(edge_fn):
            if not callable(reader):
                raise MemoryUnavailableError("memory graph backend is not configured")
            edge_fn = reader
        items = await self._scroll_all()
        nodes = tuple(
            MemoryGraphNode(
                memory_id=projected.memory_id,
                preview=projected.content[:_PREVIEW_LIMIT],
                memory_type=projected.memory_type,
                status=projected.status,
                session_id=projected.session_id,
                updated_at=projected.updated_at,
            )
            for projected in (_project_memory(row) for row in items)
        )
        try:
            edge_rows = await edge_fn(_GRAPH_EDGES_LIMIT)
        except RuntimeError as exc:
            # The verifier raises plain RuntimeError for an unconfigured
            # project scope or missing Neo4j driver — a backend-unavailable
            # state, not a read failure.
            raise MemoryUnavailableError(str(exc)) from exc
        edges_truncated = len(edge_rows) > _GRAPH_EDGES_LIMIT
        edges = tuple(
            MemoryGraphEdge(
                src=str(row.get("src") or ""),
                rel=str(row.get("rel") or ""),
                dst=str(row.get("dst") or ""),
                dst_label=str(row.get("dst_label") or ""),
                entity_name=row.get("entity_name")
                if isinstance(row.get("entity_name"), str)
                else None,
            )
            for row in edge_rows[:_GRAPH_EDGES_LIMIT]
            if row.get("src") and row.get("dst")
        )
        entity_map: dict[str, str] = {}
        for edge in edges:
            if edge.dst_label == "Entity" and edge.entity_name:
                entity_map.setdefault(edge.dst, edge.entity_name)
        entities = tuple(
            MemoryGraphEntity(entity_id=entity_id, name=name)
            for entity_id, name in sorted(entity_map.items())
        )
        sessions = tuple(
            MemoryGraphSession(session_id=sid, title=self._title_for_session(sid))
            for sid in sorted({n.session_id for n in nodes if n.session_id is not None})
        )
        return MemoryGraph(
            nodes=nodes,
            entities=entities,
            edges=edges,
            sessions=sessions,
            edges_truncated=edges_truncated,
        )

    async def _scroll_all(
        self, *, filters: dict[str, Any] | None = None
    ) -> list[Mapping[str, Any]]:
        """Page through ``scroll_memories`` until the server stops cursoring."""

        rows: list[Mapping[str, Any]] = []
        cursor: str | None = None
        for _page in range(_SCROLL_MAX_PAGES):
            try:
                payload = await self._client.scroll_memories(
                    filters=filters,
                    limit=_SCROLL_PAGE_LIMIT,
                    cursor=cursor,
                )
            except MindMemOSClientError as exc:
                raise MemoryUnavailableError(str(exc)) from exc
            batch = _payload_memories(payload)
            rows.extend(batch)
            cursor = _next_cursor(payload)
            if not cursor or not batch:
                break
        return rows

    async def _get_payload_full(self, memory_id: str) -> dict[str, Any] | None:
        """Raw ``get_memory`` payload for one id; ``None`` when absent."""

        try:
            payload = await self._client.get_memory(
                filters={"memory_id": memory_id},
            )
        except MindMemOSClientError as exc:
            if _is_not_found_error(exc):
                return None
            raise MemoryUnavailableError(str(exc)) from exc
        if not isinstance(payload, dict) or not payload:
            return None
        return payload

    async def _get_payload(self, memory_id: str) -> Mapping[str, Any] | None:
        """First memory dict for one id; ``None`` when the server has none."""

        payload = await self._get_payload_full(memory_id)
        if payload is None:
            return None
        items = _payload_memories(payload)
        return items[0] if items else None

    def _group_snapshot(self, items: tuple[ManagedMemory, ...]) -> MemorySnapshot:
        grouped: dict[str | None, list[ManagedMemory]] = defaultdict(list)
        for item in items:
            grouped[item.session_id].append(item)

        groups: list[MemoryGroup] = []
        for session_id, rows in grouped.items():
            memories = tuple(sorted(rows, key=_memory_sort_key))
            groups.append(
                MemoryGroup(
                    session_id=session_id,
                    title=self._title_for_session(session_id),
                    active_count=sum(item.status == "active" for item in memories),
                    archived_count=sum(item.status == "archived" for item in memories),
                    memories=memories,
                )
            )
        groups.sort(key=_group_sort_key)

        active_count = sum(item.status == "active" for item in items)
        archived_count = sum(item.status == "archived" for item in items)
        return MemorySnapshot(
            stats=MemoryStats(
                active_count=active_count,
                archived_count=archived_count,
                total_count=active_count + archived_count,
                session_group_count=len({item.session_id for item in items if item.session_id}),
            ),
            groups=tuple(groups),
        )

    def _title_for_session(self, session_id: str | None) -> str:
        if session_id is None:
            return "未关联会话"
        if session_id == "web-memory-management":
            return "手动添加的记忆"
        fallback = f"会话 {session_id[:8]}"
        try:
            messages = self._sessions.read_session_messages(session_id)
        except (KeyError, OSError, TypeError, ValueError):
            return fallback
        for message in messages:
            if not isinstance(message, UserMessage):
                continue
            text = "\n".join(block.text for block in message.content if block.text).strip()
            if text:
                return text
        return fallback


def _is_not_found_error(exc: MindMemOSClientError) -> bool:
    text = str(exc).lower()
    return "not_found" in text or "not found" in text


def _raise_for_rejected_mutation(status: str | None, memory_id: str) -> None:
    """Map a mutation receipt status onto the typed error surface."""

    normalized = (status or "").strip().lower()
    if normalized in _MUTATION_OK_STATUSES or not normalized:
        return
    if "not_found" in normalized or "not found" in normalized:
        raise MemoryNotFoundError(memory_id)
    raise ValueError(f"memory mutation was rejected: {status}")


def _payload_memories(payload: Any) -> list[Mapping[str, Any]]:
    """Extract the memory item list from a get/list/scroll ``data`` payload.

    The server returns ``{"memories": [...]}`` (``MemoryListData``); tolerate
    the obvious aliases plus a single ``memory``/``item`` object so the
    projection does not depend on one exact upstream envelope revision.
    """

    if not isinstance(payload, Mapping):
        return []
    for key in ("memories", "items", "results", "rows"):
        value = payload.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return [item for item in value if isinstance(item, Mapping)]
    for key in ("memory", "item"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            return [value]
    return []


def _next_cursor(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    for key in ("next_cursor", "cursor", "next"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _history_items(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Pull the version list out of a get payload when the server has one."""

    for container in (payload, *_payload_memories(payload)):
        for key in ("history", "versions"):
            value = container.get(key) if isinstance(container, Mapping) else None
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                rows = [item for item in value if isinstance(item, Mapping)]
                if rows:
                    return rows
    return []


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            # Accepts both ISO "T" and the server's "%Y-%m-%d %H:%M:%S" shape.
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _metadata_session_id(metadata: Any) -> str | None:
    if isinstance(metadata, Mapping):
        value = metadata.get("session_id")
        if value:
            return str(value)
    return None


def _is_structured(raw: Mapping[str, Any]) -> bool:
    """True when the stored memory carries a structured record, not plain text."""

    metadata = raw.get("metadata")
    _, structure_status = _safe_record(metadata)
    if structure_status != "plain":
        return True
    for key in ("structured_record", "record"):
        if isinstance(raw.get(key), Mapping):
            return True
    return False


def _project_memory(raw: Mapping[str, Any]) -> ManagedMemory:
    memory_id = str(_first(raw, "memory_id", "id") or "")
    memory_type = _enum_text(_first(raw, "mem_type", "memory_type") or "unknown")
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), Mapping) else {}
    safe_record, structure_status = _safe_record(metadata)
    if structure_status == "plain":
        # Servers may expose the canonical record directly instead of a
        # metadata-embedded ``record_json`` string.
        structured = _first(raw, "structured_record", "record")
        if isinstance(structured, Mapping):
            safe_record, structure_status = dict(structured), "valid"
    lineage = raw.get("lineage") if isinstance(raw.get("lineage"), Mapping) else {}
    status_raw = raw.get("status")
    if status_raw is None:
        # ``MemorySearchItem`` payloads carry lifecycle only via lineage.role.
        role = lineage.get("role")
        status_raw = "archived" if role == "archived" else "active"
    status = _enum_text(status_raw)
    session_value = raw.get("session_id") or metadata.get("session_id")
    session_id = str(session_value) if session_value else None
    parent_ids = tuple(str(value) for value in (raw.get("parent_ids") or ()))
    root_ids = tuple(str(value) for value in (raw.get("root_id") or ()))
    derived_from = tuple(str(value) for value in (lineage.get("derived_from_memory_ids") or ()))
    derived_to = tuple(str(value) for value in (lineage.get("derived_to_memory_ids") or ()))
    explicit_history = raw.get("has_history")
    fields = _alfworld_projection(metadata, safe_record)
    return ManagedMemory(
        memory_id=memory_id,
        content=str(_first(raw, "content", "memory") or ""),
        memory_type=memory_type,
        memory_type_label=_MEMORY_TYPE_LABELS.get(memory_type, memory_type),
        status=status,
        session_id=session_id,
        created_at=_parse_datetime(raw.get("created_at")),
        updated_at=_parse_datetime(
            _first(raw, "updated_at", "update_at", "last_update_at")
        ),
        archived_at=(
            _parse_datetime(_first(raw, "archived_at", "status_changed_at"))
            if status == "archived"
            else None
        ),
        archive_reason=_archive_reason(metadata),
        record=safe_record,
        structure_status=structure_status,
        has_history=(
            bool(explicit_history)
            or bool(parent_ids)
            or any(root_id != memory_id for root_id in root_ids)
            or bool(derived_from)
            or bool(derived_to)
        ),
        **fields,
    )


def _alfworld_projection(
    metadata: Any, record: dict[str, object] | None
) -> dict[str, object | None]:
    if not isinstance(metadata, Mapping):
        return {}
    kind = metadata.get("homemaster_memory_type")
    if kind not in {"trajectory", "alfworld_experience"}:
        return {}
    payload: dict[str, Any] = {
        "domain": "alfworld",
        "outcome": metadata.get("outcome"),
        "classification": metadata.get("classification"),
        "goal_type": metadata.get("goal_type"),
        "episode_id": metadata.get("episode_id"),
        "taskset_id": metadata.get("taskset_id"),
        "subtask_index": metadata.get("subtask_index"),
        "is_executable": metadata.get("is_executable"),
        "source_trajectory_id": metadata.get("source_trajectory_id"),
        "compile_status": "succeeded" if kind == "alfworld_experience" else "available",
    }
    if kind == "trajectory" and isinstance(record, dict):
        payload.update(
            {
                "goal_type": record.get("goal_type"),
                "episode_id": record.get("episode_id"),
                "taskset_id": record.get("taskset_id"),
                "subtask_index": record.get("subtask_index"),
                "outcome": record.get("outcome"),
                "classification": record.get("classification"),
                "is_executable": False,
            }
        )
    return payload


def _safe_record(metadata: Any) -> tuple[dict[str, object] | None, StructureStatus]:
    found, raw_json = _find_record_json(metadata)
    if not found:
        return None, "plain"
    if not isinstance(raw_json, str):
        return None, "invalid"
    try:
        kind = metadata.get("homemaster_memory_type") if isinstance(metadata, Mapping) else None
        if kind in {"trajectory", "alfworld_experience"}:
            # The typed trajectory/experience record models were retired with
            # the compile branch; keep the stored payload inspectable without
            # re-validating it against deleted schemas.
            parsed = json.loads(raw_json)
            if not isinstance(parsed, dict):
                raise ValueError("record_json must decode to an object")
            return parsed, "valid"
        record = MEMORY_RECORD_ADAPTER.validate_json(raw_json)
    except ValueError:
        return None, "invalid"
    return record.model_dump(mode="json", exclude_none=True), "valid"


def _find_record_json(value: Any) -> tuple[bool, Any]:
    if isinstance(value, Mapping):
        if "record_json" in value:
            return True, value["record_json"]
        for nested in value.values():
            found, record_json = _find_record_json(nested)
            if found:
                return found, record_json
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in value:
            found, record_json = _find_record_json(nested)
            if found:
                return found, record_json
    return False, None


def _archive_reason(metadata: Any) -> str | None:
    if not isinstance(metadata, Mapping):
        return None
    reason = metadata.get("delete_reason")
    return reason.strip() if isinstance(reason, str) and reason.strip() else None


def _enum_text(value: Any) -> str:
    enum_value = getattr(value, "value", value)
    return str(enum_value)


def _timestamp(value: datetime | None) -> float:
    return value.timestamp() if value is not None else float("-inf")


def _memory_sort_key(memory: ManagedMemory) -> tuple[float, str]:
    latest = memory.updated_at or memory.created_at
    return (-_timestamp(latest), memory.memory_id)


def _group_sort_key(group: MemoryGroup) -> tuple[float, str]:
    latest = group.memories[0].updated_at or group.memories[0].created_at
    return (-_timestamp(latest), group.session_id or "\uffff")


__all__ = [
    "GraphEdgesReader",
    "ManagedMemory",
    "MemoryGraph",
    "MemoryGraphEdge",
    "MemoryGraphEntity",
    "MemoryGraphNode",
    "MemoryGraphSession",
    "MemoryGroup",
    "MemoryManagementService",
    "MemoryNotFoundError",
    "MemorySearchHit",
    "MemorySearchResult",
    "MemorySnapshot",
    "MemoryStats",
    "MemoryUnavailableError",
]
