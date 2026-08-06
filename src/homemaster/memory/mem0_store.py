"""Application-owned asynchronous boundary around mem0 and embedded Qdrant."""

from __future__ import annotations

import asyncio
import gc
import hashlib
import os
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from homemaster.config import HomeMasterConfig
from homemaster.memory.bm25_preflight import verify_bm25_offline
from homemaster.memory.models import MEMORY_RECORD_ADAPTER, MemoryRecord
from homemaster.memory.outbound_policy import (
    MemoryOutboundPolicyError,
    validate_embedding_endpoint,
    validate_embedding_text,
)
from homemaster.memory.serialization import (
    SerializedMemory,
    normalize_text,
    normalize_url,
    serialize_record,
)
from homemaster.memory.vendor_integrity import verify_vendored_mem0

_INTERNAL_USER_ID = "homemaster"
_T = TypeVar("_T")


class Mem0StoreError(RuntimeError):
    def __init__(self, code: str, message: str, **details: object) -> None:
        self.code = code
        self.details = details
        super().__init__(message)


@dataclass(frozen=True)
class StoredMemory:
    memory_id: str
    memory_type: Literal["fact", "procedure"]
    record: MemoryRecord
    created_at: str | None
    updated_at: str | None
    score: float | None = None
    match_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemorySearchDiagnostic:
    """Secret-safe description of a candidate that failed record validation."""

    code: Literal["memory_record_corrupt"]
    memory_id_hash: str
    match_sources: tuple[str, ...]


@dataclass(frozen=True)
class MemorySearchResult:
    records: tuple[StoredMemory, ...]
    diagnostics: tuple[MemorySearchDiagnostic, ...]


class Mem0MemoryStore:
    """Own one installed mem0 Memory instance and serialize every SDK call."""

    def __init__(self, config: HomeMasterConfig) -> None:
        self.config = config
        self._memory: Any | None = None
        self._lock = asyncio.Lock()
        self._unavailable_cause: str | None = None
        self._closing = False
        self._closed = False
        self._inflight: set[asyncio.Task[Any]] = set()

    @property
    def available(self) -> bool:
        return self._memory is not None and self._unavailable_cause is None and not self._closed

    @property
    def unavailable_cause(self) -> str | None:
        return self._unavailable_cause

    def start(self) -> None:
        if self._closing or self._closed:
            raise RuntimeError("mem0 store is closed")
        constructed: Any | None = None
        try:
            verify_bm25_offline(self.config.memory.mem0.fastembed_cache_path)
            provider = self.config.get_provider(
                self.config.memory.embedding_provider_name, kind="embedding"
            )
            validate_embedding_endpoint(provider.base_url, provider.embedding_url)
            os.environ["MEM0_TELEMETRY"] = "False"
            verify_vendored_mem0()
            from mem0 import Memory

            constructed = Memory.from_config(self._mem0_config(provider))
            if type(constructed.vector_store).__name__ != "Qdrant":
                raise RuntimeError("memory store constructed a non-Qdrant backend")
            keyword_probe = constructed.vector_store.keyword_search(
                "homemaster bm25 preflight",
                top_k=1,
                filters={"user_id": _INTERNAL_USER_ID},
            )
            if keyword_probe is None:
                raise RuntimeError("Qdrant BM25 sparse search is unavailable")
            self._memory = constructed
            self._unavailable_cause = None
        except Exception as exc:
            if constructed is not None:
                client = getattr(getattr(constructed, "vector_store", None), "client", None)
                try:
                    constructed.close()
                finally:
                    if client is not None:
                        client.close()
            # Memory.from_config can raise after constructing an internal local
            # Qdrant client but before returning its Memory owner. Collect that
            # partial object while qdrant modules are still importable.
            gc.collect()
            self._memory = None
            self._unavailable_cause = f"{type(exc).__name__}: {exc}"

    async def close(self) -> None:
        if self._closed:
            return
        self._closing = True
        async with self._lock:
            memory = self._memory
            self._memory = None
            self._closed = True
            if memory is None:
                return

            def close_owned_resources() -> None:
                client = getattr(getattr(memory, "vector_store", None), "client", None)
                try:
                    memory.close()
                finally:
                    if client is not None:
                        client.close()
                # Drop the owner while imports are still live so Qdrant's __del__
                # cannot defer a second idempotent close until interpreter shutdown.
                gc.collect()

            await asyncio.to_thread(close_owned_resources)

    async def add(self, record: MemoryRecord, *, provenance_seq: int) -> StoredMemory:
        serialized = serialize_record(record, provenance_seq=provenance_seq)
        self._validate_outbound(serialized.text)
        return await self._owned_mutation(self._add_serialized(serialized, record))

    async def _add_serialized(
        self, serialized: SerializedMemory, record: MemoryRecord
    ) -> StoredMemory:
        async with self._lock:
            existing = await asyncio.to_thread(
                self._find_exact_sync, record.memory_type, serialized.dedupe_key
            )
            if existing:
                current = self._parse(existing[0])
                if current.record == record:
                    return current
                raise Mem0StoreError(
                    "memory_conflict", "memory identity already exists", memory_id=current.memory_id
                )
            response = await asyncio.to_thread(self._add_sync, serialized)
            results = response.get("results") if isinstance(response, dict) else None
            if (
                not isinstance(results, list)
                or len(results) != 1
                or results[0].get("event") != "ADD"
            ):
                raise Mem0StoreError("memory_backend_rejected", "mem0 add returned no ADD receipt")
            memory_id = results[0].get("id")
            if not isinstance(memory_id, str) or not memory_id:
                raise Mem0StoreError("memory_backend_rejected", "mem0 add returned no id")
            return self._verified_sync(memory_id, serialized)

    async def get(self, memory_id: str) -> StoredMemory:
        async with self._lock:
            raw = await asyncio.to_thread(self._get_sync, memory_id)
        if raw is None:
            raise Mem0StoreError("memory_not_found", "memory id was not found")
        return self._parse(raw)

    async def search(
        self,
        query: str,
        *,
        memory_type: Literal["fact", "procedure"] | None = None,
        limit: int = 5,
        subject: dict[str, object] | None = None,
        predicate: str | None = None,
        entry_url: str | None = None,
        name: str | None = None,
    ) -> tuple[StoredMemory, ...]:
        result = await self.search_with_diagnostics(
            query,
            memory_type=memory_type,
            limit=limit,
            subject=subject,
            predicate=predicate,
            entry_url=entry_url,
            name=name,
        )
        return result.records

    async def search_with_diagnostics(
        self,
        query: str,
        *,
        memory_type: Literal["fact", "procedure"] | None = None,
        limit: int = 5,
        subject: dict[str, object] | None = None,
        predicate: str | None = None,
        entry_url: str | None = None,
        name: str | None = None,
    ) -> MemorySearchResult:
        if not query.strip():
            raise Mem0StoreError("memory_invalid_input", "query must not be empty")
        self._validate_outbound(query)
        filters: dict[str, object] = {"user_id": _INTERNAL_USER_ID}
        if memory_type is not None:
            filters["memory_type"] = memory_type
        exact_filters = self._exact_filters(
            memory_type=memory_type,
            subject=subject,
            predicate=predicate,
            entry_url=entry_url,
            name=name,
        )
        async with self._lock:
            exact = (
                await asyncio.to_thread(self._get_all_sync, exact_filters, limit)
                if exact_filters
                else []
            )
            response = await asyncio.to_thread(
                self._require_memory().search,
                query,
                filters=filters,
                top_k=limit,
                threshold=self.config.memory.mem0.search_threshold,
                rerank=False,
            )
        results = response.get("results", []) if isinstance(response, dict) else []
        ranked: dict[str, tuple[StoredMemory, float, int]] = {}
        diagnostics: dict[str, MemorySearchDiagnostic] = {}
        for raw in exact:
            item = self._parse_candidate(raw, source="exact", diagnostics=diagnostics)
            if item is None:
                continue
            ranked[item.memory_id] = (item, 1.0, len(ranked))
        self._merge_ranked(ranked, results, source="hybrid", diagnostics=diagnostics)
        ordered = sorted(
            ranked.values(),
            key=lambda entry: ("exact" not in entry[0].match_sources, -entry[1], entry[2]),
        )
        return MemorySearchResult(
            records=tuple(entry[0] for entry in ordered[:limit]),
            diagnostics=tuple(diagnostics.values()),
        )

    async def update(
        self, memory_id: str, record: MemoryRecord, *, provenance_seq: int
    ) -> StoredMemory:
        serialized = serialize_record(record, provenance_seq=provenance_seq)
        self._validate_outbound(serialized.text)
        return await self._owned_mutation(
            self._update_serialized(memory_id, record, serialized, provenance_seq)
        )

    async def _update_serialized(
        self,
        memory_id: str,
        record: MemoryRecord,
        serialized: SerializedMemory,
        provenance_seq: int,
    ) -> StoredMemory:
        async with self._lock:
            current_raw = await asyncio.to_thread(self._get_sync, memory_id)
            if current_raw is None:
                raise Mem0StoreError("memory_not_found", "memory id was not found")
            current = self._parse(current_raw)
            if current.memory_type != record.memory_type:
                raise Mem0StoreError("memory_conflict", "memory type cannot change")
            current_seq = int(current_raw["metadata"].get("provenance_seq", 0))
            if provenance_seq <= current_seq:
                raise Mem0StoreError("memory_stale_observation", "evidence is not newer")
            conflicts = await asyncio.to_thread(
                self._find_exact_sync, record.memory_type, serialized.dedupe_key
            )
            if any(item.get("id") != memory_id for item in conflicts):
                raise Mem0StoreError(
                    "memory_conflict", "updated identity conflicts with another memory"
                )
            if current.record == record:
                return current
            response = await asyncio.to_thread(
                self._require_memory().update,
                memory_id,
                text=serialized.text,
                metadata=serialized.metadata,
            )
            if not isinstance(response, dict) or "updated successfully" not in str(
                response.get("message", "")
            ):
                raise Mem0StoreError("memory_backend_rejected", "mem0 update was rejected")
            return self._verified_sync(memory_id, serialized)

    async def delete(self, memory_id: str) -> None:
        await self._owned_mutation(self._delete_id(memory_id))

    async def _delete_id(self, memory_id: str) -> None:
        async with self._lock:
            if await asyncio.to_thread(self._get_sync, memory_id) is None:
                raise Mem0StoreError("memory_not_found", "memory id was not found")
            response = await asyncio.to_thread(self._require_memory().delete, memory_id)
            if not isinstance(response, dict) or "deleted successfully" not in str(
                response.get("message", "")
            ):
                raise Mem0StoreError("memory_backend_rejected", "mem0 delete was rejected")
            if self._get_sync(memory_id) is not None or self._raw_point_sync(memory_id) is not None:
                raise Mem0StoreError("memory_outcome_unknown", "deleted memory still exists")

    async def _owned_mutation(self, operation: Awaitable[_T]) -> _T:
        """Keep a started sync-backed mutation alive after caller timeout/cancellation."""

        if self._closing or self._closed:
            if hasattr(operation, "close"):
                operation.close()  # type: ignore[union-attr]
            raise Mem0StoreError("memory_backend_unavailable", "memory backend is closed")
        task = asyncio.create_task(operation)
        self._inflight.add(task)

        def completed(done: asyncio.Task[Any]) -> None:
            self._inflight.discard(done)
            if done.cancelled():
                return
            # Retrieve a background exception when the fenced caller has gone away.
            done.exception()

        task.add_done_callback(completed)
        return await asyncio.shield(task)

    def _add_sync(self, serialized: SerializedMemory) -> dict[str, Any]:
        return self._require_memory().add(
            serialized.text, user_id=_INTERNAL_USER_ID, metadata=serialized.metadata, infer=False
        )

    def _get_sync(self, memory_id: str) -> dict[str, Any] | None:
        return self._require_memory().get(memory_id)

    def _find_exact_sync(self, memory_type: str, dedupe_key: str) -> list[dict[str, Any]]:
        response = self._require_memory().get_all(
            filters={
                "user_id": _INTERNAL_USER_ID,
                "memory_type": memory_type,
                "dedupe_key": dedupe_key,
            },
            top_k=20,
        )
        return list(response.get("results", [])) if isinstance(response, dict) else []

    def _get_all_sync(self, filters: dict[str, object], limit: int) -> list[dict[str, Any]]:
        response = self._require_memory().get_all(filters=filters, top_k=limit)
        return list(response.get("results", [])) if isinstance(response, dict) else []

    @staticmethod
    def _exact_filters(
        *,
        memory_type: str | None,
        subject: dict[str, object] | None,
        predicate: str | None,
        entry_url: str | None,
        name: str | None,
    ) -> dict[str, object]:
        filters: dict[str, object] = {"user_id": _INTERNAL_USER_ID}
        has_exact_hint = False
        if memory_type is not None:
            filters["memory_type"] = memory_type
        if subject:
            if isinstance(subject.get("type"), str):
                filters["subject_type"] = subject["type"]
                has_exact_hint = True
            if isinstance(subject.get("id"), str):
                filters["subject_id"] = subject["id"]
                has_exact_hint = True
            elif isinstance(subject.get("name"), str):
                filters["subject_name_normalized"] = normalize_text(subject["name"])
                has_exact_hint = True
        if predicate:
            filters["predicate"] = predicate
            has_exact_hint = True
        if entry_url:
            filters["entry_url_normalized"] = normalize_url(entry_url)
            has_exact_hint = True
        if name:
            filters["procedure_name_normalized"] = normalize_text(name)
            has_exact_hint = True
        return filters if has_exact_hint else {}

    def _verified_sync(self, memory_id: str, expected: SerializedMemory) -> StoredMemory:
        raw = self._get_sync(memory_id)
        if raw is None:
            raise Mem0StoreError("memory_outcome_unknown", "memory missing after mutation")
        if raw.get("memory") != expected.text or raw.get("metadata") != expected.metadata:
            raise Mem0StoreError("memory_outcome_unknown", "memory terminal state differs")
        point = self._raw_point_sync(memory_id)
        if point is None or point.payload.get("data") != expected.text:
            raise Mem0StoreError("memory_outcome_unknown", "Qdrant raw point differs")
        if any(point.payload.get(key) != value for key, value in expected.metadata.items()):
            raise Mem0StoreError("memory_outcome_unknown", "Qdrant raw metadata differs")
        return self._parse(raw)

    def _raw_point_sync(self, memory_id: str) -> Any | None:
        memory = self._require_memory()
        points = memory.vector_store.client.retrieve(
            collection_name=self.config.memory.mem0.collection_name,
            ids=[memory_id],
            with_payload=True,
            with_vectors=False,
        )
        return points[0] if points else None

    @classmethod
    def _merge_ranked(
        cls,
        ranked: dict[str, tuple[StoredMemory, float, int]],
        raw_items: list[dict[str, Any]],
        *,
        source: str,
        diagnostics: dict[str, MemorySearchDiagnostic],
    ) -> None:
        for position, raw in enumerate(raw_items):
            item = cls._parse_candidate(raw, source=source, diagnostics=diagnostics)
            if item is None:
                continue
            contribution = 1.0 / (60 + position + 1)
            existing = ranked.get(item.memory_id)
            if existing is None:
                ranked[item.memory_id] = (item, contribution, len(ranked))
                continue
            current, score, order = existing
            sources = tuple(dict.fromkeys((*current.match_sources, source)))
            ranked[item.memory_id] = (
                StoredMemory(
                    memory_id=item.memory_id,
                    memory_type=item.memory_type,
                    record=item.record,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                    score=max(value for value in (current.score, item.score) if value is not None)
                    if current.score is not None or item.score is not None
                    else None,
                    match_sources=sources,
                ),
                score + contribution,
                order,
            )

    @classmethod
    def _parse_candidate(
        cls,
        raw: dict[str, Any],
        *,
        source: str,
        diagnostics: dict[str, MemorySearchDiagnostic],
    ) -> StoredMemory | None:
        try:
            return cls._parse(raw, match_sources=(source,))
        except Mem0StoreError as exc:
            if exc.code != "memory_record_corrupt":
                raise
            raw_id = raw.get("id")
            identity = str(raw_id) if raw_id is not None else "missing-id"
            memory_id_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
            existing = diagnostics.get(memory_id_hash)
            sources = tuple(
                dict.fromkeys((*existing.match_sources, source)) if existing else (source,)
            )
            diagnostics[memory_id_hash] = MemorySearchDiagnostic(
                code="memory_record_corrupt",
                memory_id_hash=memory_id_hash,
                match_sources=sources,
            )
            return None

    @staticmethod
    def _point_to_raw(point: Any) -> dict[str, Any]:
        payload = dict(point.payload or {})
        metadata = {
            key: value
            for key, value in payload.items()
            if key not in {"data", "hash", "created_at", "updated_at", "user_id"}
        }
        return {
            "id": str(point.id),
            "memory": payload.get("data", ""),
            "metadata": metadata,
            "created_at": payload.get("created_at"),
            "updated_at": payload.get("updated_at"),
            "score": getattr(point, "score", None),
        }

    @staticmethod
    def _parse(raw: dict[str, Any], *, match_sources: tuple[str, ...] = ()) -> StoredMemory:
        try:
            metadata = raw["metadata"]
            record = MEMORY_RECORD_ADAPTER.validate_json(metadata["record_json"])
            memory_id = raw["id"]
        except Exception as exc:
            raise Mem0StoreError("memory_record_corrupt", "stored record is corrupt") from exc
        return StoredMemory(
            memory_id=memory_id,
            memory_type=record.memory_type,
            record=record,
            created_at=raw.get("created_at"),
            updated_at=raw.get("updated_at"),
            score=raw.get("score"),
            match_sources=match_sources,
        )

    def _require_memory(self) -> Any:
        if not self.available:
            raise Mem0StoreError(
                "memory_backend_unavailable",
                self._unavailable_cause or "memory backend unavailable",
            )
        return self._memory

    @staticmethod
    def _validate_outbound(text: str) -> None:
        try:
            validate_embedding_text(text)
        except MemoryOutboundPolicyError as exc:
            raise Mem0StoreError("memory_outbound_blocked", str(exc)) from exc

    def _mem0_config(self, provider: Any) -> dict[str, Any]:
        memory = self.config.memory
        mem0 = memory.mem0
        return {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "path": str(memory.qdrant_path),
                    "collection_name": mem0.collection_name,
                    "embedding_model_dims": memory.embedding_dimensions,
                    "on_disk": True,
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": provider.model,
                    "api_key": provider.api_keys[0],
                    "openai_base_url": provider.base_url,
                    "embedding_dims": memory.embedding_dimensions,
                },
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": "homemaster-infer-disabled",
                    "api_key": "non-secret-sentinel",
                    "openai_base_url": "http://127.0.0.1:9/v1",
                },
            },
            "history_db_path": str(memory.history_db_path),
        }


__all__ = [
    "MemorySearchDiagnostic",
    "MemorySearchResult",
    "Mem0MemoryStore",
    "Mem0StoreError",
    "StoredMemory",
]
