"""Tests for P4: embedding degradation to BM25-only."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homemaster.contracts import MemoryRetrievalQuery, TaskCard
from homemaster.embedding_client import EmbeddingProviderNetworkError
from homemaster.memory_rag import run_memory_rag
from homemaster.runtime import ProviderConfig

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FailingEmbeddingProvider:
    """Embedding provider that always raises EmbeddingProviderNetworkError."""

    provider_name = "failing-embedding"
    model = "fail-v1"

    def public_summary(self) -> dict[str, Any]:
        return {"provider_name": self.provider_name, "model": self.model}

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingProviderNetworkError(
            error_type="network_error",
            message="connection refused",
        )


@dataclass
class KeywordEmbedder:
    """Minimal embedding provider using keyword matching (same as test_memory_rag)."""

    provider_name: str = "MemoryEmbedding"
    model: str = "BAAI/bge-m3"

    def public_summary(self) -> dict[str, Any]:
        return {"provider_name": self.provider_name, "model": self.model}

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vectors.append(
                [
                    1.0 if any(term in text for term in ("水杯", "杯子", "cup")) else 0.0,
                    1.0 if "厨房" in text or "kitchen" in text else 0.0,
                    1.0 if any(term in text for term in ("药盒", "medicine")) else 0.0,
                ]
            )
        return vectors


@dataclass
class StaticQueryProvider:
    query: MemoryRetrievalQuery
    raw_response: str = "{}"

    def generate_query(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
    ) -> tuple[MemoryRetrievalQuery, str, dict[str, Any]]:
        return self.query, self.raw_response, {"provider_name": "Mimo", "model": "mimo-v2-pro"}


def _provider() -> ProviderConfig:
    return ProviderConfig(
        name="Mimo",
        base_url="https://mimo.example/anthropic",
        model="mimo-v2-pro",
        api_keys=("secret-one",),
        protocol="anthropic",
        embedding_url=None,
    )


def _task_card() -> TaskCard:
    return TaskCard(
        task_type="fetch_object",
        target="水杯",
        delivery_target="user",
        location_hint="厨房",
        success_criteria=["找到目标物并完成验证"],
        needs_clarification=False,
        clarification_question=None,
        confidence=0.9,
    )


def _query_provider() -> StaticQueryProvider:
    return StaticQueryProvider(
        MemoryRetrievalQuery(
            query_text="厨房 水杯 cup",
            target_category="cup",
            target_aliases=["水杯", "cup"],
            location_terms=["厨房"],
        )
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_embedding_failure_degrades_to_bm25_only(tmp_path: Path) -> None:
    """AC1: embedding throws → Stage03 still returns BM25-only results."""
    result = run_memory_rag(
        _task_card(),
        memory_path=Path("data/scenarios/fetch_cup_retry/memory.json"),
        case_name="degrade_test",
        query_provider=_query_provider(),
        embedding_provider=FailingEmbeddingProvider(),
        llm_provider=_provider(),
        case_root=tmp_path / "cases",
        results_dir=tmp_path / "results",
        cache_dir=tmp_path / "cache",
    )
    assert result.passed is True
    assert result.memory_result is not None
    assert len(result.memory_result.hits) > 0
    for hit in result.memory_result.hits:
        assert hit.dense_score == 0.0
        assert hit.ranking_stage == "bm25_only"
    snap = result.memory_result.index_snapshot
    assert snap["retrieval_mode"] == "bm25_only"
    assert snap["degraded"] is True
    assert "degradation_reason" in snap
    assert snap["ranking_stage"] == "bm25_only"
    assert result.embedding_provider["provider_name"] == "degraded"


def test_normal_embedding_returns_hybrid_results(tmp_path: Path) -> None:
    """AC2: normal path still returns hybrid results."""
    result = run_memory_rag(
        _task_card(),
        memory_path=Path("data/scenarios/fetch_cup_retry/memory.json"),
        case_name="hybrid_test",
        query_provider=_query_provider(),
        embedding_provider=KeywordEmbedder(),
        llm_provider=_provider(),
        case_root=tmp_path / "cases",
        results_dir=tmp_path / "results",
        cache_dir=tmp_path / "cache",
    )
    assert result.passed is True
    snap = result.memory_result.index_snapshot
    assert snap["retrieval_mode"] == "hybrid"
    assert snap["degraded"] is False
    assert snap["ranking_stage"] == "bm25_dense_fusion"
    for hit in result.memory_result.hits:
        assert hit.ranking_stage == "bm25_dense_fusion"


def test_degradation_metadata_in_debug_assets(tmp_path: Path) -> None:
    """AC3: debug assets (actual.json) contain retrieval_mode at top level."""
    result = run_memory_rag(
        _task_card(),
        memory_path=Path("data/scenarios/fetch_cup_retry/memory.json"),
        case_name="debug_assets_test",
        query_provider=_query_provider(),
        embedding_provider=FailingEmbeddingProvider(),
        llm_provider=_provider(),
        case_root=tmp_path / "cases",
        results_dir=tmp_path / "results",
        cache_dir=tmp_path / "cache",
    )
    actual_path = result.case_dir / "actual.json"
    actual = json.loads(actual_path.read_text(encoding="utf-8"))
    assert actual["retrieval_mode"] == "bm25_only"
    assert actual["degraded"] is True
    assert "degradation_reason" in actual
