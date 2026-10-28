"""Project-scoped management reads backed by the memory database."""

from __future__ import annotations

from ...logging import get_logger
from ...mappers import parse_search_dsl
from ...typing import (
    FieldCondition,
    GetPipelineInput,
    GetPipelineResult,
    MemoryLineage,
    MemoryListPipelineInput,
    MemoryListPipelineResult,
    MemoryRequestContext,
    MemoryScrollPipelineInput,
    MemoryScrollPipelineResult,
    MemorySearchItem,
    MemoryView,
    SearchFilter,
)
from ..utils import format_datetime, format_memory_event_time, format_source_timestamp
from .reader import MemoryDbReader

logger = get_logger(__name__)


class MemoryCatalog:
    """Format project-scoped memory reads for management endpoints."""

    def __init__(self, *, reader: MemoryDbReader | None = None) -> None:
        self.reader = reader or MemoryDbReader()

    async def get(self, inp: GetPipelineInput, context: MemoryRequestContext) -> GetPipelineResult:
        """Return active memories matching an unscored filter.

        V3.8 management parity: each returned item carries ``version`` (when
        the payload stores one) and ``lineage`` (DERIVED_FROM ancestors, when
        the graph has them).
        """

        limit_kwargs = {"limit": inp.top_k} if inp.top_k is not None else {}
        memories, _ = await self.reader.list_memories(
            context,
            filters=_active_filter(inp),
            **limit_kwargs,
        )
        lineage = await self._lineage_for(context, [memory.memory_id for memory in memories])
        items = [
            _to_memory_search_item(
                memory,
                lineage=lineage.get(memory.memory_id),
            )
            for memory in memories
        ]
        return GetPipelineResult(
            status="ok",
            memories=items,
            message=None,
        )

    async def _lineage_for(
        self,
        context: MemoryRequestContext,
        memory_ids: list[str],
    ) -> dict[str, list[str]]:
        """Best-effort DERIVED_FROM ancestor lookup; failures degrade to none."""

        try:
            return await self.reader.get_memory_lineage(context, memory_ids)
        except Exception:
            logger.warning("memory get: lineage lookup failed", exc_info=True)
            return {}

    async def list(
        self,
        inp: MemoryListPipelineInput,
        context: MemoryRequestContext,
    ) -> MemoryListPipelineResult:
        """Return a management page from the project-scoped catalog."""

        filters = _active_filter(inp)
        offset = (inp.page - 1) * inp.page_size
        read_limit = offset + inp.page_size + 1
        memories, _ = await self.reader.list_memories(
            context,
            filters=filters,
            limit=read_limit,
        )
        page_memories = memories[offset : offset + inp.page_size]
        total = await self.reader.count_memories(context, filters=filters) if inp.include_total else None
        has_more = (inp.page * inp.page_size < total) if total is not None else len(memories) > offset + inp.page_size
        return MemoryListPipelineResult(
            status="ok",
            memories=[_to_memory_search_item(memory) for memory in page_memories],
            page=inp.page,
            page_size=inp.page_size,
            total=total,
            has_more=has_more,
            message=None,
        )

    async def scroll(
        self,
        inp: MemoryScrollPipelineInput,
        context: MemoryRequestContext,
    ) -> MemoryScrollPipelineResult:
        """Return a cursor page from the project-scoped catalog."""

        memories, next_cursor = await self.reader.list_memories(
            context,
            filters=_active_filter(inp),
            limit=inp.limit,
            cursor=inp.cursor,
        )
        return MemoryScrollPipelineResult(
            status="ok",
            memories=[_to_memory_search_item(memory) for memory in memories],
            next_cursor=str(next_cursor) if next_cursor is not None else None,
            message=None,
        )


def _active_filter(inp: GetPipelineInput) -> SearchFilter:
    base = parse_search_dsl(inp.filters)
    if getattr(inp, "include_inactive", False):
        return base
    return SearchFilter(
        must=[
            *base.must,
            FieldCondition(field="status", op="match", value="active"),
        ],
        should=base.should,
        must_not=base.must_not,
    )


def _to_memory_search_item(
    memory: MemoryView,
    *,
    lineage: list[str] | None = None,
) -> MemorySearchItem:
    updated_at = memory.update_at or memory.created_at
    item_lineage = None
    if lineage or memory.status == "archived":
        item_lineage = MemoryLineage(
            role="archived" if memory.status == "archived" else "current",
            derived_from_memory_ids=list(lineage or []),
        )
    return MemorySearchItem(
        id=memory.memory_id,
        memory=memory.content,
        memory_type=memory.mem_type,
        last_update_at=format_datetime(updated_at),
        event_time=format_memory_event_time(memory),
        source_timestamp=format_source_timestamp(memory),
        lineage=item_lineage,
        metadata=dict(memory.metadata or {}),
        status=memory.status,
        entity_id=memory.entity_id,
        entity_type=memory.entity_type,
        property_name=memory.property_name,
        version=memory.version,
    )
