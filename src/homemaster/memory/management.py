"""Read-only memory projections for the Web management surface."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from homemaster.agent.messages import UserMessage
from homemaster.memory.automatic_recall import build_mindmemos_request_context
from homemaster.memory.models import MEMORY_RECORD_ADAPTER

if TYPE_CHECKING:
    from homemaster.application.session import SessionManager
    from homemaster.memory.mindmemos_runtime import EmbeddedMindMemOS


StructureStatus = Literal["plain", "valid", "invalid"]
_MEMORY_TYPE_LABELS = {
    "fact": "事实",
    "procedure": "操作流程",
}


class MemoryNotFoundError(LookupError):
    """Raised when an exact memory is not visible in the current tenant."""


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


class MemoryManagementService:
    """Compose project-scoped MindMemOS rows into browser-safe read DTOs."""

    def __init__(
        self,
        mindmemos: EmbeddedMindMemOS,
        sessions: SessionManager,
    ) -> None:
        self._mindmemos = mindmemos
        self._sessions = sessions

    async def snapshot(self, *, tenant_id: str) -> MemorySnapshot:
        context = _request_context("web-memory", tenant_id)
        raw_rows = await self._mindmemos.list_raw_memories(context)
        items = tuple(_project_memory(row) for row in raw_rows)
        return self._group_snapshot(items)

    async def history(
        self,
        memory_id: str,
        *,
        tenant_id: str,
    ) -> tuple[ManagedMemory, ...]:
        normalized_id = memory_id.strip()
        if not normalized_id:
            raise MemoryNotFoundError(memory_id)
        context = _request_context("web-memory-history", tenant_id)
        versions = await self._mindmemos.get_history(normalized_id, context)
        if not versions:
            raise MemoryNotFoundError(normalized_id)
        projected = (_project_memory(row) for row in versions)
        return tuple(sorted(projected, key=_memory_sort_key))

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


def _request_context(prefix: str, tenant_id: str) -> Any:
    return build_mindmemos_request_context(
        request_id=f"{prefix}-{uuid4().hex}",
        tenant_id=tenant_id,
        session_id="web-memory-management",
    )


def _project_memory(raw: Any) -> ManagedMemory:
    memory_id = str(raw.memory_id)
    memory_type = _enum_text(getattr(raw, "mem_type", "unknown"))
    status = _enum_text(getattr(raw, "status", "unknown"))
    metadata = getattr(raw, "metadata", None)
    safe_record, structure_status = _safe_record(metadata)
    session_value = getattr(raw, "session_id", None)
    session_id = str(session_value) if session_value else None
    parent_ids = tuple(str(value) for value in (getattr(raw, "parent_ids", None) or ()))
    root_ids = tuple(str(value) for value in (getattr(raw, "root_id", None) or ()))
    status_changed_at = getattr(raw, "status_changed_at", None)
    return ManagedMemory(
        memory_id=memory_id,
        content=str(getattr(raw, "content", "") or ""),
        memory_type=memory_type,
        memory_type_label=_MEMORY_TYPE_LABELS.get(memory_type, memory_type),
        status=status,
        session_id=session_id,
        created_at=getattr(raw, "created_at", None),
        updated_at=getattr(raw, "update_at", None),
        archived_at=status_changed_at if status == "archived" else None,
        archive_reason=_archive_reason(metadata),
        record=safe_record,
        structure_status=structure_status,
        has_history=bool(parent_ids) or any(root_id != memory_id for root_id in root_ids),
    )


def _safe_record(metadata: Any) -> tuple[dict[str, object] | None, StructureStatus]:
    found, raw_json = _find_record_json(metadata)
    if not found:
        return None, "plain"
    if not isinstance(raw_json, str):
        return None, "invalid"
    try:
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
    "ManagedMemory",
    "MemoryGroup",
    "MemoryManagementService",
    "MemoryNotFoundError",
    "MemorySnapshot",
    "MemoryStats",
]
