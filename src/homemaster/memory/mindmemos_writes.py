"""Write paths for the embedded MindMemOS runtime."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Mapping
from typing import Any

from homemaster.memory._mindmemos_shared import (
    _SCHEMA_EPISODE_MAX_BYTES,
    _TYPED_RECORD,
    NAMESPACE_URL,
    RecordedAddResult,
    _record_from_raw_memory,
    _typed_record_from_metadata,
    serialize_record,
    uuid4,
    uuid5,
)
from homemaster.memory.models import MemoryRecord


class MindMemOSWriteMixin:
    """MindMemOS write paths (schema add, flat add, vanilla add, enrichment)."""

    async def add(
        self,
        messages: list[Any],
        context: Any,
        *,
        force_generation: bool = True,
        metadata: dict[str, Any] | None = None,
        event_timestamp_ms: int | None = None,
    ) -> Any:
        """Synchronously extract and persist memories from MindMemOS messages."""

        from mindmemos.pipelines.memory_db import suppress_recording_errors, utcnow
        from mindmemos.typing import AddPipelineInput

        if self._add_pipeline is None or self._recorder is None:
            raise RuntimeError("embedded MindMemOS is not started")
        payload_kwargs: dict[str, Any] = {
            "messages": messages,
            "mode": "sync",
            "force_generation": force_generation,
            "metadata": metadata or {},
        }
        if event_timestamp_ms is not None:
            payload_kwargs["event_timestamp_ms"] = event_timestamp_ms
        payload = AddPipelineInput(**payload_kwargs)
        add_record_id = str(uuid4())
        submitted_at = utcnow()
        await suppress_recording_errors(
            self._recorder.record_add_input(
                payload,
                ctx=context,
                request_submitted_at=submitted_at,
                add_record_id=add_record_id,
                status="processing",
            ),
            operation="homemaster.mindmemos.add",
        )
        typed_record = _typed_record_from_metadata(metadata)
        token = _TYPED_RECORD.set(typed_record)
        try:
            return await self._add_pipeline.add_sync(
                payload,
                context,
                add_record_id=add_record_id,
            )
        except Exception as exc:
            await suppress_recording_errors(
                self._recorder.mark_add_failed(context, add_record_id, str(exc)),
                operation="homemaster.mindmemos.add",
            )
            raise
        finally:
            _TYPED_RECORD.reset(token)

    async def add_record(
        self,
        record: MemoryRecord,
        *,
        provenance_seq: int,
        context: Any,
    ) -> dict[str, object]:
        """Persist one validated HomeMaster record and verify its raw terminal state."""

        from mindmemos.typing import TextMessage

        serialized = serialize_record(record, provenance_seq=provenance_seq)
        result = await self.add(
            [TextMessage(text=serialized.text)],
            context,
            force_generation=True,
            metadata={
                **serialized.metadata,
                "homemaster_memory_type": record.memory_type,
            },
        )
        candidate_ids: list[str] = []
        for event in result.memories:
            candidate_ids.extend(
                item
                for item in getattr(event, "related_memory_ids", [])
                if isinstance(item, str) and item
            )
            event_id = getattr(event, "memory_id", None)
            if isinstance(event_id, str) and event_id:
                candidate_ids.append(event_id)
        expected_type = "experience" if record.memory_type == "procedure" else record.memory_type
        for memory_id in dict.fromkeys(candidate_ids):
            raw = await self.get_raw(memory_id, context)
            if getattr(raw, "mem_type", None) != expected_type:
                continue
            parsed = _record_from_raw_memory(raw)
            if parsed == record:
                created_at = getattr(raw, "created_at", None)
                updated_at = getattr(raw, "update_at", None)
                return {
                    "memory_id": raw.memory_id,
                    "memory_type": record.memory_type,
                    "record": record.model_dump(mode="json"),
                    "created_at": (
                        created_at.isoformat() if hasattr(created_at, "isoformat") else None
                    ),
                    "updated_at": (
                        updated_at.isoformat() if hasattr(updated_at, "isoformat") else None
                    ),
                    "score": None,
                    "match_sources": [],
                    "verified_terminal_state": True,
                }
        raise RuntimeError("MindMemOS Add returned no verified raw memory")

    async def add_flat(
        self,
        content: str,
        memory_type: str,
        *,
        provenance_seq: int,
        evidence_kind: str,
        context: Any,
        metadata_extra: Mapping[str, Any] | None = None,
        source_type: str = "message",
        source_metadata: Mapping[str, Any] | None = None,
        lineage_source_memory_id: str | None = None,
    ) -> dict[str, object]:
        """Persist exact caller-authored content without extraction or entity modeling."""

        from datetime import UTC, datetime

        from mindmemos.components.id import generate_memory_id, generate_source_id
        from mindmemos.pipelines.memory_db import suppress_recording_errors, utcnow
        from mindmemos.pipelines.utils.dto_factory import build_source_write
        from mindmemos.typing import (
            AddPipelineInput,
            AddPipelineSyncResult,
            GraphNodeRef,
            GraphRelationship,
            MemoryAddEventItem,
            MemoryDbWritePlan,
            MemoryWrite,
            SourceRef,
            TextMessage,
            VectorWrite,
        )

        if (
            self._writer is None
            or self._reader is None
            or self._recorder is None
            or self._flat_text_preprocessor is None
            or self._flat_sparse_encoder is None
            or self._qdrant is None
            or self._neo4j is None
        ):
            raise RuntimeError("embedded MindMemOS is not started")
        if memory_type not in {"fact", "procedure"}:
            raise ValueError("memory_type must be fact or procedure")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must not be empty")
        if evidence_kind not in {"user_statement", "environment_observation"}:
            raise ValueError("unsupported evidence kind")

        native_type = "experience" if memory_type == "procedure" else "fact"
        preprocessed = self._flat_text_preprocessor.preprocess_text(
            content,
            segment_id="homemaster-direct-flat-add",
            include_entities=False,
        )
        sparse = self._flat_sparse_encoder.encode_document(list(preprocessed.tokens))
        now = datetime.now(UTC)
        memory_id = generate_memory_id(
            context.project_id,
            context.request_id,
            f"{preprocessed.content_hash}:{native_type}",
        )
        source_ref = generate_source_id(
            SourceRef(
                source_type=source_type,
                message_id=(
                    f"homemaster-direct-flat-{context.request_id}-evidence-{provenance_seq}"
                ),
                is_parsed=True,
                content_hash=preprocessed.content_hash,
                metadata={
                    "producer": "homemaster_explicit_add",
                    "provenance_seq": provenance_seq,
                    "evidence_kind": evidence_kind,
                    **dict(source_metadata or {}),
                },
            ),
            context,
        )
        source = build_source_write(source_ref, context, now)
        add_record_id = str(uuid4())
        metadata = {
            "homemaster_add_mode": "direct_flat",
            "homemaster_memory_type": memory_type,
            "provenance_seq": provenance_seq,
            "evidence_kind": evidence_kind,
            "content_hash": preprocessed.content_hash,
            "bm25_text": preprocessed.bm25_text,
            "tokens": list(preprocessed.tokens),
            "lang": preprocessed.lang,
            "source_id": source.source_id,
            "source_type": source.source_type,
            "source_session_id": context.session_id,
            "add_record_id": add_record_id,
            "entity_count": 0,
            "entities": [],
            "extractor": "homemaster_direct_flat_v1",
            "vector_pending": True,
            "entity_enrichment_pending": True,
            **dict(metadata_extra or {}),
        }
        memory = MemoryWrite(
            memory_id=memory_id,
            account_id=context.account_id,
            project_id=context.project_id,
            api_key_uuid=context.api_key_uuid,
            user_id=context.user_id,
            app_id=context.app_id,
            session_id=context.session_id,
            agent_id=context.agent_id,
            request_id=context.request_id,
            content=content,
            mem_type=native_type,
            mem_extract_type="homemaster_direct_flat",
            mem_extract_version="homemaster_direct_flat_v1",
            metadata=metadata,
            validate_from=now,
            created_at=now,
            root_id=[memory_id],
        )
        relationship = GraphRelationship(
            source=GraphNodeRef(
                kind="Memory", project_id=context.project_id, node_id=memory_id
            ),
            target=GraphNodeRef(
                kind="Source", project_id=context.project_id, node_id=source.source_id
            ),
            rel_type="EXTRACTED_FROM",
            project_id=context.project_id,
            metadata={
                "source_type": source_type,
                "producer": "homemaster_explicit_add",
            },
        )
        relationships = [relationship]
        if lineage_source_memory_id:
            relationships.append(
                GraphRelationship(
                    source=GraphNodeRef(
                        kind="Memory", project_id=context.project_id, node_id=memory_id
                    ),
                    target=GraphNodeRef(
                        kind="Memory",
                        project_id=context.project_id,
                        node_id=lineage_source_memory_id,
                    ),
                    rel_type="DERIVED_FROM",
                    project_id=context.project_id,
                    metadata={"producer": "homemaster_alfworld_compiler"},
                )
            )
        plan = MemoryDbWritePlan(
            memories=[memory],
            sources=[source],
            vectors=[
                VectorWrite(
                    memory_id=memory_id,
                    bm25_indices=list(sparse.indices),
                    bm25_values=list(sparse.values),
                )
            ],
            relationships=relationships,
        )
        payload = AddPipelineInput(
            messages=[TextMessage(text=content)],
            mode="sync",
            force_generation=False,
            metadata=metadata,
        )
        await suppress_recording_errors(
            self._recorder.record_add_input(
                payload,
                ctx=context,
                request_submitted_at=utcnow(),
                add_record_id=add_record_id,
                status="processing",
            ),
            operation="homemaster.mindmemos.direct_flat_add",
        )
        try:
            write_result = await self._writer.write(context, plan, consistency="strong")
            if (
                memory_id not in write_result.memory_ids
                or bool(write_result.graph_pending)
                or bool(write_result.errors)
            ):
                raise RuntimeError(
                    "direct flat Add database write was incomplete: "
                    f"graph_pending={write_result.graph_pending}, errors={write_result.errors}"
                )
            result = AddPipelineSyncResult(
                status="ok",
                memories=[
                    MemoryAddEventItem(
                        operation="add",
                        content=content,
                        memory_id=memory_id,
                        mem_type=native_type,
                        graph_edge_count=1,
                    )
                ],
            )
            await suppress_recording_errors(
                self._recorder.mark_add_completed(context, add_record_id, result),
                operation="homemaster.mindmemos.direct_flat_add",
            )
            raw = await self.get_raw(memory_id, context)
            if not (
                raw is not None
                and getattr(raw, "status", None) == "active"
                and getattr(raw, "content", None) == content
                and getattr(raw, "mem_type", None) == native_type
                and getattr(raw, "mem_extract_type", None) == "homemaster_direct_flat"
                and (getattr(raw, "metadata", {}) or {}).get("vector_pending") is True
            ):
                raise RuntimeError("direct flat Add raw terminal state could not be verified")
            stored_points = await self._qdrant.get_memories(
                context.project_id,
                [memory_id],
                with_vectors=True,
            )
            semantic_name = self.mindmemos_config.database.qdrant.semantic_vector_name
            bm25_name = self.mindmemos_config.database.qdrant.bm25_vector_name
            stored_vectors = stored_points[0].vectors if len(stored_points) == 1 else None
            dense = stored_vectors.get(semantic_name) if stored_vectors else None
            stored_sparse = stored_vectors.get(bm25_name) if stored_vectors else None
            if (
                not isinstance(dense, list)
                or any(dense)
                or list(getattr(stored_sparse, "indices", ())) != list(sparse.indices)
                or list(getattr(stored_sparse, "values", ())) != list(sparse.values)
            ):
                raise RuntimeError("direct flat Add vector terminal state could not be verified")
            graph_rows = await self._neo4j.run_read(
                """
                MATCH (m:Memory {project_id: $project_id, memory_id: $memory_id})
                      -[:EXTRACTED_FROM]->
                      (s:Source {project_id: $project_id, source_id: $source_id})
                RETURN m.memory_id AS memory_id, s.source_id AS source_id
                """,
                project_id=context.project_id,
                memory_id=memory_id,
                source_id=source.source_id,
            )
            if graph_rows != [{"memory_id": memory_id, "source_id": source.source_id}]:
                raise RuntimeError("direct flat Add graph terminal state could not be verified")
            if lineage_source_memory_id:
                lineage_rows = await self._neo4j.run_read(
                    """
                    MATCH (derived:Memory {project_id: $project_id, memory_id: $memory_id})
                          -[:DERIVED_FROM]->
                          (source:Memory {project_id: $project_id, memory_id: $source_memory_id})
                    RETURN derived.memory_id AS derived_id, source.memory_id AS source_id
                    """,
                    project_id=context.project_id,
                    memory_id=memory_id,
                    source_memory_id=lineage_source_memory_id,
                )
                if lineage_rows != [
                    {"derived_id": memory_id, "source_id": lineage_source_memory_id}
                ]:
                    raise RuntimeError(
                        "derived memory lineage terminal state could not be verified"
                    )
        except Exception as exc:
            await suppress_recording_errors(
                self._recorder.mark_add_failed(context, add_record_id, str(exc)),
                operation="homemaster.mindmemos.direct_flat_add",
            )
            raise

        created_at = getattr(raw, "created_at", None)
        updated_at = getattr(raw, "update_at", None)
        return {
            "memory_id": memory_id,
            "memory_type": memory_type,
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else None,
            "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else None,
            "score": None,
            "match_sources": [],
            "verified_terminal_state": True,
        }

    async def add_derived_experience(
        self,
        content: str,
        *,
        source_memory_id: str,
        metadata: Mapping[str, Any],
        context: Any,
    ) -> dict[str, object]:
        """Persist derived experience content with a verified source lineage edge."""

        return await self.add_flat(
            content,
            "procedure",
            provenance_seq=0,
            evidence_kind="environment_observation",
            context=context,
            metadata_extra={
                "homemaster_memory_type": "alfworld_experience",
                **dict(metadata),
            },
            source_type="alfworld_experience",
            source_metadata={"source_memory_id": source_memory_id},
            lineage_source_memory_id=source_memory_id,
        )

    async def add_trajectory_memory(
        self,
        record: Any,
        *,
        context: Any,
    ) -> dict[str, object]:
        """Persist one validated ALFWorld trajectory as an immutable source memory."""

        from homemaster.alfworld.trajectory_memory import (
            AlfworldTrajectoryRecord,
        )

        if not isinstance(record, AlfworldTrajectoryRecord):
            raise TypeError("record must be an AlfworldTrajectoryRecord")
        content = json.dumps(
            record.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        result = await self.add_flat(
            content,
            "fact",
            provenance_seq=record.final_environment_state.step_index or 0,
            evidence_kind="environment_observation",
            context=context,
            metadata_extra={
                "homemaster_memory_type": "trajectory",
                "record_kind": record.record_kind,
                "trajectory_id": record.trajectory_id,
                "outcome": record.outcome,
                "classification": record.classification,
                "failure_reason": record.failure_reason,
                "source_trace_sha256": record.source_trace_sha256,
                "source_trace_path": record.source_trace_path,
                "record_json": content,
            },
            source_type="alfworld_trajectory",
            source_metadata={
                "trajectory_id": record.trajectory_id,
                "source_trace_sha256": record.source_trace_sha256,
                "episode_id": record.episode_id,
            },
        )
        raw = await self.get_raw(str(result["memory_id"]), context)
        metadata = dict(getattr(raw, "metadata", {}) or {}) if raw is not None else {}
        if (
            raw is None
            or getattr(raw, "status", None) != "active"
            or metadata.get("homemaster_memory_type") != "trajectory"
            or metadata.get("trajectory_id") != record.trajectory_id
            or metadata.get("source_trace_sha256") != record.source_trace_sha256
            or metadata.get("record_json") != content
        ):
            raise RuntimeError("trajectory memory metadata terminal state could not be verified")
        return {**result, "trajectory_id": record.trajectory_id, "readback_verified": True}

    async def enrich_flat_memory(
        self,
        *,
        memory_id: str,
        content: str,
        context: Any,
    ) -> dict[str, object]:
        """Complete dense and Entity enrichment for an already stored flat memory."""
        if (
            self._reader is None
            or self._writer is None
            or self._flat_embed_client is None
            or self._flat_text_preprocessor is None
            or self._flat_entity_extractor is None
            or self._flat_memory_vectorizer is None
            or self._qdrant is None
            or self._neo4j is None
        ):
            raise RuntimeError("embedded MindMemOS is not started")
        current = await self._reader.get_memory(context, memory_id)
        if current is None or getattr(current, "status", None) != "active":
            raise RuntimeError(f"stored memory is not active: {memory_id}")
        metadata = dict(getattr(current, "metadata", {}) or {})
        semantic_name = self.mindmemos_config.database.qdrant.semantic_vector_name
        if metadata.get("vector_pending"):
            response = await self._flat_embed_client.embed(task="memory.add.embed", text=content)
            vectors = list(response.embeddings)
            if not vectors or not vectors[0]:
                raise RuntimeError("enrichment embedding returned no vector")
            await self._qdrant.patch_memory(
                context.project_id,
                memory_id,
                {"metadata": metadata},
                dense_vector=list(vectors[0]),
            )
            verified = await self._qdrant.get_memories(
                context.project_id, [memory_id], with_vectors=True
            )
            dense = (
                verified[0].vectors.get(semantic_name)
                if verified and verified[0].vectors
                else None
            )
            expected_dense = list(vectors[0])
            if (
                not isinstance(dense, list)
                or len(dense) != len(expected_dense)
                or not any(dense)
                or not all(
                    math.isclose(
                        float(stored),
                        float(expected),
                        rel_tol=1e-6,
                        abs_tol=1e-7,
                    )
                    for stored, expected in zip(dense, expected_dense, strict=True)
                )
            ):
                raise RuntimeError("enrichment vector readback failed")
            metadata["vector_pending"] = False
            await self._qdrant.patch_memory(
                context.project_id,
                memory_id,
                {"metadata": metadata},
            )

        entity_ids: list[str] = []
        if metadata.get("entity_enrichment_pending"):
            from datetime import UTC, datetime

            from mindmemos.components.extractor.vanilla._entity import (
                deduplicate_entities,
                resolve_candidate_entities,
            )
            from mindmemos.components.id import generate_entity_id
            from mindmemos.components.memory_modeling.vanilla import build_mentions_edge
            from mindmemos.pipelines.utils import build_entity_write
            from mindmemos.typing import ExtractionEnvelope, MemoryDbWritePlan, TurnMessageRef

            preprocessed = self._flat_text_preprocessor.preprocess_text(
                content,
                segment_id=f"homemaster-flat-enrichment:{memory_id}",
                include_entities=True,
            )
            message = TurnMessageRef(
                text=content,
                role="user",
                raw_role="user",
                timestamp=None,
                message_index=0,
                is_extractable=True,
            )
            extracted = await self._flat_entity_extractor.extract_from_envelope(
                ExtractionEnvelope(
                    extractable_messages=[message],
                    boundary="complete",
                    chunk_index=0,
                ),
                [preprocessed],
                context,
            )
            resolved = []
            for candidate in extracted.memories:
                resolved.extend(
                    resolve_candidate_entities(
                        candidate,
                        extracted.entities,
                        preprocessed.entities,
                    )
                )
            entities = deduplicate_entities(resolved)
            entity_writes = []
            relationships = []
            for entity in entities:
                entity_id = generate_entity_id(context.project_id, entity)
                entity_write = build_entity_write(entity, entity_id, context, datetime.now(UTC))
                entity_write.metadata = {
                    **dict(entity_write.metadata or {}),
                    "search_fields": [content.strip()],
                }
                entity_writes.append(entity_write)
                relationships.append(build_mentions_edge(memory_id, entity_id, entity, context))
                entity_ids.append(entity_id)
            entity_vectors, vector_pending = await self._flat_memory_vectorizer.vectorize_entities(
                entity_writes,
                consistency="strong",
            )
            if vector_pending:
                raise RuntimeError("entity enrichment vectorization was incomplete")
            if entity_writes:
                write_result = await self._writer.write(
                    context,
                    MemoryDbWritePlan(
                        entities=entity_writes,
                        entity_vectors=entity_vectors,
                        relationships=relationships,
                    ),
                    consistency="strong",
                )
                if write_result.graph_pending or write_result.errors:
                    raise RuntimeError("entity enrichment database write was incomplete")
                for entity_id in entity_ids:
                    entity_record = await self._qdrant.get_entity(
                        context.project_id,
                        entity_id,
                        with_vectors=True,
                    )
                    stored_entity_vectors = entity_record.vectors if entity_record else None
                    entity_dense = (
                        stored_entity_vectors.get(semantic_name)
                        if stored_entity_vectors
                        else None
                    )
                    if (
                        entity_record is None
                        or not isinstance(entity_dense, list)
                        or not any(entity_dense)
                    ):
                        raise RuntimeError(f"entity enrichment readback failed: {entity_id}")
                    graph_rows = await self._neo4j.run_read(
                        """
                        MATCH (m:Memory {project_id: $project_id, memory_id: $memory_id})
                              -[:MENTIONS]->
                              (e:Entity {project_id: $project_id, entity_id: $entity_id})
                        RETURN e.entity_id AS entity_id
                        """,
                        project_id=context.project_id,
                        memory_id=memory_id,
                        entity_id=entity_id,
                    )
                    if graph_rows != [{"entity_id": entity_id}]:
                        raise RuntimeError(
                            f"entity enrichment graph readback failed: {entity_id}"
                        )
            metadata["entity_enrichment_pending"] = False
            metadata["entity_count"] = len(entity_ids)
            metadata["entities"] = [
                entity.canonical_name or entity.name for entity in entities
            ]
        await self._qdrant.patch_memory(
            context.project_id,
            memory_id,
            {"metadata": metadata},
        )
        final = await self._reader.get_memory(context, memory_id)
        final_metadata = dict(getattr(final, "metadata", {}) or {})
        if final_metadata.get("vector_pending") or final_metadata.get(
            "entity_enrichment_pending"
        ):
            raise RuntimeError("enrichment completion metadata readback failed")
        return {"memory_id": memory_id, "entity_ids": entity_ids}

    async def add_vanilla(
        self,
        messages: list[Any],
        context: Any,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Extract free-form experience memories with native Vanilla Add."""

        from mindmemos.pipelines.memory_db import suppress_recording_errors, utcnow
        from mindmemos.typing import AddPipelineInput

        if self._vanilla_add_pipeline is None or self._recorder is None:
            raise RuntimeError("embedded MindMemOS is not started")
        payload = AddPipelineInput(
            messages=messages,
            mode="sync",
            force_generation=True,
            metadata=metadata or {},
        )
        add_record_id = str(uuid4())
        await suppress_recording_errors(
            self._recorder.record_add_input(
                payload,
                ctx=context,
                request_submitted_at=utcnow(),
                add_record_id=add_record_id,
                status="processing",
            ),
            operation="homemaster.mindmemos.vanilla_add",
        )
        try:
            result = await self._vanilla_add_pipeline.add_sync(
                payload,
                context,
                add_record_id=add_record_id,
            )
            return RecordedAddResult(add_record_id=add_record_id, result=result)
        except Exception as exc:
            await suppress_recording_errors(
                self._recorder.mark_add_failed(context, add_record_id, str(exc)),
                operation="homemaster.mindmemos.vanilla_add",
            )
            raise

    async def add_schema_episode(
        self,
        episode: dict[str, Any],
        context: Any,
        *,
        metadata: dict[str, Any],
    ) -> RecordedAddResult:
        """Persist one canonical HomeMaster episode through fixed schema extractors."""

        from mindmemos.pipelines.memory_db import utcnow
        from mindmemos.typing import (
            AddPipelineInput,
            AddPipelineSyncResult,
            TextMessage,
        )

        if self._add_pipeline is None or self._recorder is None or self._reader is None:
            raise RuntimeError("embedded MindMemOS is not started")
        canonical = json.dumps(
            episode,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        encoded = canonical.encode("utf-8")
        if len(encoded) > _SCHEMA_EPISODE_MAX_BYTES:
            raise ValueError(
                f"schema episode exceeds {_SCHEMA_EPISODE_MAX_BYTES} UTF-8 bytes"
            )
        required_metadata = {
            "ingress": "homemaster_schema_episode_v1",
            "domain_schema_version": episode.get("schema_version"),
            **dict(metadata),
        }
        if required_metadata.get("ingress") != "homemaster_schema_episode_v1":
            raise ValueError("schema episode ingress metadata cannot be overridden")
        stable_material = "\0".join(
            [
                str(context.account_id),
                str(context.project_id),
                str(context.request_id),
            ]
        )
        # Qdrant local/server point IDs must be UUID-compatible. The UUID5
        # input remains the stable idempotency material; the ingress metadata
        # carries the semantic schema-episode identity.
        add_record_id = str(
            uuid5(NAMESPACE_URL, f"homemaster-schema-episode:{stable_material}")
        )
        required_metadata.setdefault("schema_episode_id", add_record_id)
        payload = AddPipelineInput(
            messages=[TextMessage(text=canonical)],
            mode="sync",
            force_generation=True,
            metadata=required_metadata,
        )
        lock = self._schema_episode_locks.setdefault(add_record_id, asyncio.Lock())
        async with lock:
            records = await self._reader.get_add_records_by_ids(context, [add_record_id])
            if records:
                stored = records[0].payload
                if stored.get("status") == "ok" and stored.get("schema_episode"):
                    result = AddPipelineSyncResult.model_validate(
                        {
                            "status": stored["status"],
                            "memories": stored.get("memories") or [],
                            "schema_episode": stored["schema_episode"],
                        }
                    )
                    return RecordedAddResult(add_record_id=add_record_id, result=result)
                await self._recorder.mark_add_processing(context, add_record_id)
            else:
                await self._recorder.record_add_input(
                    payload,
                    ctx=context,
                    request_submitted_at=utcnow(),
                    add_record_id=add_record_id,
                    status="processing",
                )
            try:
                result = await self._add_pipeline.add_sync(
                    payload,
                    context,
                    add_record_id=add_record_id,
                )
            except Exception as exc:
                await self._recorder.mark_add_failed(context, add_record_id, str(exc))
                raise
            if result.status != "ok" or result.schema_episode is None:
                error = "schema episode add returned no successful aggregate receipt"
                await self._recorder.mark_add_failed(context, add_record_id, error)
                raise RuntimeError(error)
            return RecordedAddResult(add_record_id=add_record_id, result=result)
