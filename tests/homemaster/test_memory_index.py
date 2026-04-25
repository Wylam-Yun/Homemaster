from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from homemaster.memory_index import (
    JsonEmbeddingCache,
    MemoryBM25Index,
    build_memory_documents,
    cosine_similarity,
)
from homemaster.memory_tokenizer import JiebaMemoryTokenizer, build_domain_terms_from_object_memory


def _load_memory(case_name: str) -> dict[str, object]:
    path = Path("data/scenarios") / case_name / "memory.json"
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class CountingEmbedder:
    calls: int = 0

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls += len(texts)
        return [[float(len(text)), 1.0] for text in texts]


def test_object_memory_serializes_to_document_text_and_metadata() -> None:
    documents = build_memory_documents(_load_memory("fetch_cup_retry"))

    first = documents[0]
    assert first.document_id == "object_memory:mem-cup-1"
    assert "水杯" in first.text
    assert "杯子" in first.text
    assert "厨房餐桌" in first.text
    assert "kitchen_table_viewpoint" in first.text
    assert first.metadata["memory_id"] == "mem-cup-1"
    assert first.metadata["anchor_id"] == "anchor_kitchen_table_1"
    assert first.metadata["viewpoint_id"] == "kitchen_table_viewpoint"
    assert first.metadata["document_text_hash"]


def test_bm25_index_ranks_alias_and_location_match_first() -> None:
    memory_payload = _load_memory("fetch_cup_retry")
    tokenizer = JiebaMemoryTokenizer(
        domain_terms=build_domain_terms_from_object_memory(memory_payload)
    )
    documents = build_memory_documents(memory_payload)
    index = MemoryBM25Index.build(documents, tokenizer)

    hits = index.search("厨房 水杯", top_k=2)

    assert hits[0].document.document_id == "object_memory:mem-cup-1"
    assert hits[0].score >= hits[1].score


def test_embedding_cache_reuses_document_vectors_and_invalidates_changed_document(
    tmp_path: Path,
) -> None:
    memory_payload = _load_memory("fetch_cup_retry")
    documents = build_memory_documents(memory_payload)
    cache = JsonEmbeddingCache(tmp_path / "embeddings")
    embedder = CountingEmbedder()

    first_vectors = cache.get_or_embed_documents(
        documents,
        provider_name="MemoryEmbedding",
        model="BAAI/bge-m3",
        embed_texts=embedder.embed_texts,
    )
    second_vectors = cache.get_or_embed_documents(
        documents,
        provider_name="MemoryEmbedding",
        model="BAAI/bge-m3",
        embed_texts=embedder.embed_texts,
    )

    changed_payload = _load_memory("fetch_cup_retry")
    changed_payload["object_memory"][0]["anchor"]["display_text"] = "厨房超长岛台"
    changed_documents = build_memory_documents(changed_payload)
    third_vectors = cache.get_or_embed_documents(
        changed_documents,
        provider_name="MemoryEmbedding",
        model="BAAI/bge-m3",
        embed_texts=embedder.embed_texts,
    )

    assert first_vectors == second_vectors
    assert embedder.calls == 3
    assert third_vectors[1] == second_vectors[1]
    assert third_vectors[0] != second_vectors[0]


def test_documents_missing_required_grounding_fields_are_not_executable() -> None:
    memory_payload = {
        "object_memory": [
            {
                "memory_id": "mem-broken",
                "object_category": "cup",
                "aliases": ["水杯"],
                "anchor": {"room_id": "kitchen", "anchor_id": "anchor-1"},
            }
        ]
    }

    document = build_memory_documents(memory_payload)[0]

    assert document.executable is False
    assert "viewpoint_id" in document.invalid_reason


def test_cosine_similarity_handles_zero_vector() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0
    assert round(cosine_similarity([1.0, 0.0], [1.0, 1.0]), 3) == 0.707
