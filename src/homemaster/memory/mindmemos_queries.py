"""Query and mutation read paths for the embedded MindMemOS runtime."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from homemaster.memory._mindmemos_shared import (
    _typed_entity_generation,
    _updated_schema_metadata,
    uuid4,
)


class MindMemOSQueryMixin:
    """MindMemOS query paths (search, get, update, lineage, delete)."""

    async def search(
        self,
        query: str,
        context: Any,
        *,
        top_k: int = 10,
        search_pipeline: str = "schema",
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
        score_threshold: float | None = None,
    ) -> Any:
        """Search memories and persist the search operation record."""

        from mindmemos.pipelines.memory_db import suppress_recording_errors, utcnow
        from mindmemos.typing import SearchPipelineInput

        if self._search_pipeline is None or self._recorder is None:
            raise RuntimeError("embedded MindMemOS is not started")
        payload = SearchPipelineInput(
            query=query,
            top_k=top_k,
            search_pipeline=search_pipeline,
            filters=filters,
            rerank=rerank,
            score_threshold=score_threshold,
        )
        submitted_at = utcnow()
        result = None
        try:
            result = await self._search_pipeline.search(payload, context)
            return result
        finally:
            await suppress_recording_errors(
                self._recorder.record_search(
                    payload,
                    result,
                    ctx=context,
                    request_submitted_at=submitted_at,
                    task_completed_at=utcnow(),
                ),
                operation="homemaster.mindmemos.search",
            )

    async def get(
        self,
        context: Any,
        *,
        filters: dict[str, Any] | None = None,
        top_k: int | None = None,
    ) -> Any:
        """List active raw memories through the native get pipeline."""

        from mindmemos.typing import GetPipelineInput

        if self._get_pipeline is None:
            raise RuntimeError("embedded MindMemOS is not started")
        return await self._get_pipeline.get(
            GetPipelineInput(filters=filters, top_k=top_k),
            context,
        )

    async def update(self, memory_id: str, content: str, context: Any) -> Any:
        """Replace the content of one active raw memory."""

        from mindmemos.typing import UpdatePipelineInput

        if self._update_pipeline is None:
            raise RuntimeError("embedded MindMemOS is not started")
        return await self._update_pipeline.update(
            UpdatePipelineInput(memory_id=memory_id, content=content),
            context,
        )

    async def update_versioned(
        self,
        *,
        memory_id: str,
        content: str,
        metadata: dict[str, Any],
        context: Any,
    ) -> Any:
        """Create one deterministic schema version without re-running Schema Add."""

        from datetime import UTC, datetime

        from mindmemos.components.extractor.schema import property_relationships
        from mindmemos.typing import (
            REL_DERIVED_FROM,
            EntityWrite,
            GraphNodeRef,
            GraphRelationship,
            MemoryDbMutationPlan,
            MemoryDbUpdateCommand,
            MemoryWrite,
        )

        if self._reader is None or self._writer is None or self._schema_write_plan_builder is None:
            raise RuntimeError("embedded MindMemOS is not started")
        current = await self._reader.get_memory(context, memory_id)
        if current is None:
            return SimpleNamespace(
                status="error",
                message=f"memory not found: {memory_id}",
                memory_id=None,
            )
        if current.status != "active":
            return SimpleNamespace(
                status="error",
                message=f"memory is not active (status={current.status}): {memory_id}",
                memory_id=None,
            )
        if not current.entity_id or not current.property_name:
            return SimpleNamespace(
                status="error",
                message="structured memory is missing entity linkage",
                memory_id=None,
            )
        entity_state = await self._reader.get_entity_with_memories(context, current.entity_id)
        if entity_state is None:
            return SimpleNamespace(
                status="error",
                message=f"entity not found: {current.entity_id}",
                memory_id=None,
            )

        record_value = metadata.get("record_json")
        try:
            record = json.loads(record_value) if isinstance(record_value, str) else None
            projected = _typed_entity_generation(record, "") if isinstance(record, dict) else None
            entity_description = projected["entities"][0]["description"]
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return SimpleNamespace(
                status="error",
                message=f"invalid structured replacement metadata: {exc}",
                memory_id=None,
            )

        now = datetime.now(UTC)
        new_memory_id = str(uuid4())
        new_memory = MemoryWrite(
            memory_id=new_memory_id,
            account_id=context.account_id,
            project_id=context.project_id,
            api_key_uuid=context.api_key_uuid,
            user_id=context.user_id,
            app_id=context.app_id,
            session_id=context.session_id,
            agent_id=context.agent_id,
            request_id=context.request_id,
            content=content,
            mem_type=current.mem_type,
            mem_extract_type=current.mem_extract_type or "schema",
            mem_extract_version="homemaster_versioned_update_v1",
            metadata=_updated_schema_metadata(current.metadata, metadata),
            validate_from=current.validate_from,
            validate_to=current.validate_to,
            created_at=now,
            parent_ids=[current.memory_id],
            root_id=current.root_id or [current.memory_id],
            property_name=current.property_name,
            entity_id=current.entity_id,
            entity_type=current.entity_type,
        )
        entity_view = entity_state.to_entity_view(project_id=context.project_id)
        entity = EntityWrite(
            entity_id=current.entity_id,
            account_id=context.account_id,
            project_id=context.project_id,
            api_key_uuid=context.api_key_uuid,
            user_id=context.user_id,
            app_id=context.app_id,
            session_id=context.session_id,
            agent_id=context.agent_id,
            request_id=context.request_id,
            entity_name=entity_view.entity_name,
            entity_type=entity_view.entity_type,
            description=str(entity_description),
            created_at=entity_view.created_at or current.created_at or now,
            update_at=now,
            metadata=dict(entity_view.metadata),
        )
        relationships = [
            *property_relationships(context.project_id, current.entity_id, new_memory),
            GraphRelationship(
                source=GraphNodeRef(
                    kind="Memory", project_id=context.project_id, node_id=new_memory_id
                ),
                target=GraphNodeRef(
                    kind="Memory", project_id=context.project_id, node_id=current.memory_id
                ),
                rel_type=REL_DERIVED_FROM,
                project_id=context.project_id,
                metadata={"reason": "direct_structured_update", "created_at": now.isoformat()},
            ),
        ]
        write_plan = await self._schema_write_plan_builder.build(
            memories=[new_memory],
            entities=[entity],
            relationships=relationships,
            project_id=context.project_id,
            entity_context_memories=[new_memory],
        )
        mutation_plan = MemoryDbMutationPlan.from_write_plan(write_plan)
        mutation_plan.memory_updates = [
            MemoryDbUpdateCommand(
                memory_id=current.memory_id,
                status="archived",
                reason="direct_structured_update",
                metadata_patch={"derived_to": new_memory_id},
            )
        ]
        result = await self._writer.apply_mutation_plan(
            context,
            mutation_plan,
            consistency="strong",
        )
        mutation = result.mutations[0] if result.mutations else None
        changed = new_memory_id in result.memory_ids and bool(mutation and mutation.changed)
        return SimpleNamespace(
            status="ok" if changed and not result.errors else "error",
            message="; ".join(result.errors) if result.errors else None,
            memory_id=new_memory_id if changed else None,
        )

    async def list_raw_memories(
        self,
        context: Any,
        *,
        statuses: frozenset[str] = frozenset({"active", "archived"}),
        page_size: int = 50,
    ) -> list[Any]:
        """Return every matching memory from the project-scoped cursor stream."""

        if self._reader is None:
            raise RuntimeError("embedded MindMemOS is not started")
        cursor: Any | None = None
        seen_cursors: set[str] = set()
        seen_ids: set[str] = set()
        rows: list[Any] = []
        while True:
            page, next_cursor = await self._reader.list_memories(
                context,
                limit=page_size,
                cursor=cursor,
            )
            for memory in page:
                if memory.status in statuses and memory.memory_id not in seen_ids:
                    seen_ids.add(memory.memory_id)
                    rows.append(memory)
            if next_cursor is None:
                return rows
            cursor_key = repr(next_cursor)
            if cursor_key in seen_cursors:
                raise RuntimeError("MindMemOS list cursor repeated")
            seen_cursors.add(cursor_key)
            cursor = next_cursor

    async def get_history(self, memory_id: str, context: Any) -> list[Any]:
        """Return every Qdrant version connected to one memory through DERIVED_FROM."""

        if self._reader is None or self._neo4j is None:
            raise RuntimeError("embedded MindMemOS is not started")
        seed = await self._reader.get_memory(context, memory_id)
        if seed is None:
            return []
        rows = await self._neo4j.run_read(
            """
            MATCH (seed:Memory {project_id: $project_id, memory_id: $memory_id})
            OPTIONAL MATCH (seed)-[:DERIVED_FROM*0..]-(version:Memory {project_id: $project_id})
            RETURN DISTINCT coalesce(version.memory_id, seed.memory_id) AS memory_id
            """,
            project_id=context.project_id,
            memory_id=memory_id,
        )
        ids = list(
            dict.fromkeys(
                str(row.get("memory_id"))
                for row in rows
                if isinstance(row.get("memory_id"), str) and row.get("memory_id")
            )
        )
        if memory_id not in ids:
            ids.append(memory_id)
        versions = []
        for version_id in ids:
            raw = await self._reader.get_memory(context, version_id)
            if raw is not None:
                versions.append(raw)
        return sorted(
            versions,
            key=lambda item: str(item.created_at or item.update_at or ""),
            reverse=True,
        )

    async def get_raw(self, memory_id: str, context: Any) -> Any:
        """Read one raw memory by its persistent memory ID."""

        if self._reader is None:
            raise RuntimeError("embedded MindMemOS is not started")
        return await self._reader.get_memory(context, memory_id)

    async def has_memory_lineage(
        self,
        *,
        source_memory_id: str,
        target_memory_id: str,
        relationship: str,
        context: Any,
    ) -> bool:
        if self._neo4j is None:
            raise RuntimeError("embedded MindMemOS is not started")
        rows = await self._neo4j.run_read(
            """
            MATCH (source:Memory {project_id: $project_id, memory_id: $source_memory_id})
                  -[relation]->
                  (target:Memory {project_id: $project_id, memory_id: $target_memory_id})
            WHERE type(relation) = $relationship
               OR relation.relation_type = $relationship
            RETURN count(relation) AS relation_count
            """,
            project_id=context.project_id,
            source_memory_id=source_memory_id,
            target_memory_id=target_memory_id,
            relationship=relationship,
        )
        return bool(rows and int(rows[0].get("relation_count", 0)) > 0)

    async def get_add_records(
        self, add_record_ids: list[str], context: Any
    ) -> list[Any]:
        if self._reader is None:
            raise RuntimeError("embedded MindMemOS is not started")
        return await self._reader.get_add_records_by_ids(context, add_record_ids)

    async def delete(self, memory_id: str, context: Any) -> Any:
        """Archive one raw memory through the native delete pipeline."""

        from mindmemos.typing import DeletePipelineInput

        if self._delete_pipeline is None:
            raise RuntimeError("embedded MindMemOS is not started")
        return await self._delete_pipeline.delete(
            DeletePipelineInput(memory_id=memory_id),
            context,
        )
