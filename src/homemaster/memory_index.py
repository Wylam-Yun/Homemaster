"""Memory document, BM25 index, and embedding cache for Stage 03 RAG."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bm25s

from homemaster.memory_tokenizer import (
    ANCHOR_ALIASES,
    OBJECT_ALIASES,
    ROOM_ALIASES,
    MemoryTokenizer,
)
from homemaster.trace import write_json


@dataclass(frozen=True)
class MemoryDocument:
    document_id: str
    source_type: str
    text: str
    metadata: dict[str, Any]
    executable: bool
    invalid_reason: str | None = None


@dataclass(frozen=True)
class BM25Hit:
    document: MemoryDocument
    score: float
    rank: int


def build_memory_documents(memory_payload: dict[str, Any]) -> list[MemoryDocument]:
    raw_items = memory_payload.get("object_memory", [])
    if not isinstance(raw_items, list):
        return []

    documents: list[MemoryDocument] = []
    for index, memory in enumerate(item for item in raw_items if isinstance(item, dict)):
        metadata = _metadata_from_memory(memory)
        memory_id = _as_text(metadata.get("memory_id")) or f"missing-memory-id-{index}"
        document_id = f"object_memory:{memory_id}"
        text = _document_text(metadata)
        metadata["document_text_hash"] = _text_hash(text)
        invalid_fields = [
            field
            for field in ("memory_id", "room_id", "anchor_id", "viewpoint_id")
            if not _as_text(metadata.get(field))
        ]
        documents.append(
            MemoryDocument(
                document_id=document_id,
                source_type="object_memory",
                text=text,
                metadata=metadata,
                executable=not invalid_fields,
                invalid_reason=(
                    "missing required grounding fields: " + ", ".join(invalid_fields)
                    if invalid_fields
                    else None
                ),
            )
        )
    return documents


class MemoryBM25Index:
    def __init__(
        self,
        *,
        documents: list[MemoryDocument],
        tokenizer: MemoryTokenizer,
        retriever: bm25s.BM25,
    ) -> None:
        self.documents = documents
        self.tokenizer = tokenizer
        self._retriever = retriever

    @classmethod
    def build(
        cls,
        documents: list[MemoryDocument],
        tokenizer: MemoryTokenizer,
    ) -> MemoryBM25Index:
        tokenized_documents = [tokenizer.tokenize(document.text) for document in documents]
        retriever = bm25s.BM25()
        retriever.index(tokenized_documents, show_progress=False)
        return cls(documents=documents, tokenizer=tokenizer, retriever=retriever)

    def search(self, query_text: str, *, top_k: int) -> list[BM25Hit]:
        if not self.documents:
            return []
        query_tokens = self.tokenizer.tokenize(query_text)
        results = self._retriever.retrieve(
            [query_tokens],
            k=min(top_k, len(self.documents)),
            show_progress=False,
        )
        hits: list[BM25Hit] = []
        for rank, (doc_index, score) in enumerate(
            zip(results.documents[0], results.scores[0], strict=True),
            start=1,
        ):
            hits.append(
                BM25Hit(
                    document=self.documents[int(doc_index)],
                    score=float(score),
                    rank=rank,
                )
            )
        return hits


class JsonEmbeddingCache:
    """Small file-per-vector JSON cache for document embeddings."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def get_or_embed_documents(
        self,
        documents: Sequence[MemoryDocument],
        *,
        provider_name: str,
        model: str,
        embed_texts: Callable[[list[str]], list[list[float]]],
    ) -> list[list[float]]:
        cached: dict[str, list[float]] = {}
        missing: list[MemoryDocument] = []
        for document in documents:
            cached_vector = self.get_document_embedding(
                document,
                provider_name=provider_name,
                model=model,
            )
            if cached_vector is None:
                missing.append(document)
            else:
                cached[document.document_id] = cached_vector

        if missing:
            vectors = embed_texts([document.text for document in missing])
            for document, vector in zip(missing, vectors, strict=True):
                self.set_document_embedding(
                    document,
                    vector,
                    provider_name=provider_name,
                    model=model,
                )
                cached[document.document_id] = vector
        return [cached[document.document_id] for document in documents]

    def get_document_embedding(
        self,
        document: MemoryDocument,
        *,
        provider_name: str,
        model: str,
    ) -> list[float] | None:
        path = self._path_for_document(document, provider_name=provider_name, model=model)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return None
        vector = payload.get("embedding")
        if not isinstance(vector, list) or not vector:
            return None
        try:
            return [float(value) for value in vector]
        except (TypeError, ValueError):
            return None

    def set_document_embedding(
        self,
        document: MemoryDocument,
        vector: list[float],
        *,
        provider_name: str,
        model: str,
    ) -> None:
        write_json(
            self._path_for_document(document, provider_name=provider_name, model=model),
            {
                "provider_name": provider_name,
                "model": model,
                "document_id": document.document_id,
                "document_text_hash": document.metadata.get("document_text_hash"),
                "embedding_dim": len(vector),
                "embedding": vector,
            },
        )

    def _path_for_document(
        self,
        document: MemoryDocument,
        *,
        provider_name: str,
        model: str,
    ) -> Path:
        key_payload = {
            "provider_name": provider_name,
            "model": model,
            "document_id": document.document_id,
            "document_text_hash": document.metadata.get("document_text_hash"),
        }
        key = _text_hash(json.dumps(key_payload, sort_keys=True, ensure_ascii=False))
        return self.root / f"{key}.json"


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _metadata_from_memory(memory: dict[str, Any]) -> dict[str, Any]:
    anchor = memory.get("anchor") if isinstance(memory.get("anchor"), dict) else {}
    aliases = memory.get("aliases")
    normalized_aliases = (
        [item for item in aliases if isinstance(item, str)] if isinstance(aliases, list) else []
    )
    return {
        "source_type": "object_memory",
        "memory_id": _as_text(memory.get("memory_id")),
        "object_category": _as_text(memory.get("object_category")),
        "aliases": normalized_aliases,
        "room_id": _as_text(anchor.get("room_id")),
        "anchor_id": _as_text(anchor.get("anchor_id")),
        "anchor_type": _as_text(anchor.get("anchor_type")),
        "display_text": _as_text(anchor.get("display_text")),
        "viewpoint_id": _as_text(anchor.get("viewpoint_id")),
        "confidence_level": _as_text(memory.get("confidence_level")),
        "belief_state": _as_text(memory.get("belief_state")),
        "last_confirmed_at": _as_text(memory.get("last_confirmed_at")),
    }


def _document_text(metadata: dict[str, Any]) -> str:
    aliases = "、".join(metadata.get("aliases", []))
    room_aliases = "、".join(ROOM_ALIASES.get(str(metadata.get("room_id") or ""), ()))
    anchor_aliases = "、".join(ANCHOR_ALIASES.get(str(metadata.get("anchor_type") or ""), ()))
    object_aliases = "、".join(OBJECT_ALIASES.get(str(metadata.get("object_category") or ""), ()))
    return (
        "物体记忆。"
        f"目标类别: {metadata.get('object_category') or 'unknown'}。"
        f"目标类别别名: {object_aliases or 'unknown'}。"
        f"别名: {aliases or 'unknown'}。"
        f"历史位置: {metadata.get('display_text') or 'unknown'}。"
        f"房间: {metadata.get('room_id') or 'unknown'}。"
        f"房间别名: {room_aliases or 'unknown'}。"
        f"锚点类型: {metadata.get('anchor_type') or 'unknown'}。"
        f"锚点别名: {anchor_aliases or 'unknown'}。"
        f"可观察视角: {metadata.get('viewpoint_id') or 'unknown'}。"
        f"置信度: {metadata.get('confidence_level') or 'unknown'}。"
        f"记忆状态: {metadata.get('belief_state') or 'unknown'}。"
        f"最近确认时间: {metadata.get('last_confirmed_at') or 'unknown'}。"
    )


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
