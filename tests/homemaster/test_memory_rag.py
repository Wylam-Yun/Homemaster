from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from homemaster.contracts import MemoryRetrievalQuery, TaskCard
from homemaster.llm_client import LLMProviderResponseError
from homemaster.memory_rag import (
    MemoryRagBoundaryError,
    MemoryRagError,
    build_memory_retrieval_query_prompt,
    run_memory_rag,
)
from homemaster.runtime import ProviderConfig


def _task_card(
    *,
    task_type: str = "fetch_object",
    target: str = "水杯",
    location_hint: str | None = "厨房",
) -> TaskCard:
    return TaskCard(
        task_type=task_type,
        target=target,
        delivery_target="user" if task_type == "fetch_object" else None,
        location_hint=location_hint,
        success_criteria=["找到目标物并完成验证"],
        needs_clarification=False,
        clarification_question=None,
        confidence=0.9,
    )


@dataclass
class StaticQueryProvider:
    query: MemoryRetrievalQuery
    raw_response: str = "{}"
    prompt: str | None = None

    def generate_query(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
    ) -> tuple[MemoryRetrievalQuery, str, dict[str, Any]]:
        self.prompt = prompt
        return self.query, self.raw_response, {"provider_name": "Mimo", "model": "mimo-v2-pro"}


@dataclass
class SequencedQueryProvider:
    responses: list[object]
    prompts: list[str] | None = None

    def generate_query(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
    ) -> tuple[MemoryRetrievalQuery, str, dict[str, Any]]:
        if self.prompts is None:
            self.prompts = []
        self.prompts.append(prompt)
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, MemoryRetrievalQuery):
            return item, item.model_dump_json(), {"provider_name": "Mimo", "model": "mimo-v2-pro"}
        raise TypeError(f"unsupported test response: {item!r}")


@dataclass
class KeywordEmbedder:
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


def _provider() -> ProviderConfig:
    return ProviderConfig(
        name="Mimo",
        base_url="https://mimo.example/anthropic",
        model="mimo-v2-pro",
        api_keys=("secret-one",),
        protocol="anthropic",
        embedding_url=None,
    )


def test_build_memory_retrieval_query_prompt_contains_task_card_and_boundaries() -> None:
    prompt = build_memory_retrieval_query_prompt(
        _task_card(),
        negative_evidence={"excluded_memory_ids": ["mem-cup-1"]},
    )

    assert "MemoryRetrievalQuery" in prompt
    assert '"target": "水杯"' in prompt
    assert '"location_hint": "厨房"' in prompt
    assert '"source_filter": ["object_memory"]' in prompt
    assert "不要编造 memory_id" in prompt
    assert "mem-cup-1" in prompt


def test_memory_rag_fuses_bm25_dense_and_metadata_scores(tmp_path: Path) -> None:
    query_provider = StaticQueryProvider(
        MemoryRetrievalQuery(
            query_text="厨房 水杯 杯子 cup kitchen",
            target_category="cup",
            target_aliases=["水杯", "杯子", "cup"],
            location_terms=["厨房", "kitchen"],
            top_k=2,
        ),
        raw_response=json.dumps({"query_text": "厨房 水杯 杯子 cup kitchen"}),
    )

    result = run_memory_rag(
        _task_card(),
        memory_path=Path("data/scenarios/fetch_cup_retry/memory.json"),
        case_name="cup_object_memory_rag",
        query_provider=query_provider,
        embedding_provider=KeywordEmbedder(),
        llm_provider=_provider(),
        case_root=tmp_path / "cases",
        results_dir=tmp_path / "results",
    )

    assert result.passed is True
    assert result.memory_result.hits[0].memory_id == "mem-cup-1"
    assert result.memory_result.hits[0].bm25_score > 0
    assert result.memory_result.hits[0].dense_score > 0
    assert result.memory_result.hits[0].metadata_score > 0
    assert result.memory_result.hits[0].ranking_stage == "bm25_dense_fusion"
    assert result.memory_result.hits[0].rerank_score is None
    assert (result.case_dir / "result.md").is_file()
    report = (result.case_dir / "result.md").read_text(encoding="utf-8")
    assert "## Mimo Query Prompt" in report
    assert "## BM25 Hits" in report
    assert "## BGE-M3 Dense Hits" in report
    assert "## Fused MemoryRetrievalResult" in report


def test_negative_evidence_excludes_even_high_scoring_hit(tmp_path: Path) -> None:
    result = run_memory_rag(
        _task_card(),
        memory_path=Path("data/scenarios/object_not_found/memory.json"),
        case_name="negative_evidence_excludes_location",
        query_provider=StaticQueryProvider(
            MemoryRetrievalQuery(
                query_text="厨房 水杯 cup",
                target_category="cup",
                target_aliases=["水杯", "cup"],
                location_terms=["厨房"],
                excluded_memory_ids=["mem-cup-1"],
            )
        ),
        embedding_provider=KeywordEmbedder(),
        llm_provider=_provider(),
        negative_evidence={"excluded_memory_ids": ["mem-cup-1"]},
        case_root=tmp_path / "cases",
        results_dir=tmp_path / "results",
    )

    assert [hit.memory_id for hit in result.memory_result.excluded] == ["mem-cup-1"]
    assert "mem-cup-1" not in [hit.memory_id for hit in result.memory_result.hits]


def test_query_boundary_rejects_model_excluded_ids_not_in_runtime_evidence(
    tmp_path: Path,
) -> None:
    with pytest.raises(MemoryRagBoundaryError) as exc_info:
        run_memory_rag(
            _task_card(),
            memory_path=Path("data/scenarios/object_not_found/memory.json"),
            case_name="bad_query",
            query_provider=StaticQueryProvider(
                MemoryRetrievalQuery(
                    query_text="厨房 水杯 cup",
                    excluded_memory_ids=["mem-cup-1"],
                )
            ),
            embedding_provider=KeywordEmbedder(),
            llm_provider=_provider(),
            case_root=tmp_path / "cases",
            results_dir=tmp_path / "results",
        )

    assert exc_info.value.error_type == "query_boundary_error"


def test_stage_03_retries_query_generation_after_invalid_json(tmp_path: Path) -> None:
    query_provider = SequencedQueryProvider(
        responses=[
            LLMProviderResponseError(
                error_type="response_not_json_object",
                message="model output did not contain a JSON object",
                raw_content="不是 JSON",
            ),
            MemoryRetrievalQuery(
                query_text="厨房 水杯 cup",
                target_category="cup",
                target_aliases=["水杯", "cup"],
                location_terms=["厨房"],
            ),
        ],
    )

    result = run_memory_rag(
        _task_card(),
        memory_path=Path("data/scenarios/fetch_cup_retry/memory.json"),
        case_name="retry_after_invalid_json",
        query_provider=query_provider,
        embedding_provider=KeywordEmbedder(),
        llm_provider=_provider(),
        case_root=tmp_path / "cases",
        results_dir=tmp_path / "results",
    )

    assert result.passed is True
    assert query_provider.prompts is not None
    assert len(query_provider.prompts) == 2
    assert "上一次输出没有通过 MemoryRetrievalQuery 校验" in query_provider.prompts[1]
    actual = json.loads((result.case_dir / "actual.json").read_text(encoding="utf-8"))
    assert actual["query_attempt_count"] == 2
    assert actual["query_attempts"][0]["passed"] is False
    assert actual["query_attempts"][1]["passed"] is True


def test_stage_03_writes_fail_debug_assets_when_query_generation_fails_twice(
    tmp_path: Path,
) -> None:
    query_provider = SequencedQueryProvider(
        responses=[
            LLMProviderResponseError(
                error_type="response_not_json_object",
                message="model output did not contain a JSON object",
                raw_content="第一次不是 JSON",
            ),
            LLMProviderResponseError(
                error_type="response_not_json_object",
                message="model output did not contain a JSON object",
                raw_content="第二次还是不是 JSON",
            ),
            LLMProviderResponseError(
                error_type="response_not_json_object",
                message="model output did not contain a JSON object",
                raw_content="第三次还是不是 JSON",
            ),
        ],
    )
    case_root = tmp_path / "cases"

    with pytest.raises(MemoryRagError) as exc_info:
        run_memory_rag(
            _task_card(),
            memory_path=Path("data/scenarios/fetch_cup_retry/memory.json"),
            case_name="query_generation_fail",
            query_provider=query_provider,
            embedding_provider=KeywordEmbedder(),
            llm_provider=_provider(),
            case_root=case_root,
            results_dir=tmp_path / "results",
        )

    assert exc_info.value.error_type == "response_not_json_object"
    actual_path = case_root / "query_generation_fail" / "actual.json"
    report_path = case_root / "query_generation_fail" / "result.md"
    assert actual_path.is_file()
    assert report_path.is_file()
    actual = json.loads(actual_path.read_text(encoding="utf-8"))
    report = report_path.read_text(encoding="utf-8")
    assert actual["passed"] is False
    assert actual["query_attempt_count"] == 3
    assert "第一次不是 JSON" in report
    assert "第二次还是不是 JSON" in report
    assert "第三次还是不是 JSON" in report
    assert "Status: FAIL" in report


def test_stage_03_retries_after_query_boundary_error(tmp_path: Path) -> None:
    query_provider = SequencedQueryProvider(
        responses=[
            MemoryRetrievalQuery(
                query_text="厨房 水杯 cup",
                target_category="cup",
                target_aliases=["水杯", "cup"],
                location_terms=["厨房"],
                excluded_memory_ids=["mem-cup-1"],
            ),
            MemoryRetrievalQuery(
                query_text="厨房 水杯 cup",
                target_category="cup",
                target_aliases=["水杯", "cup"],
                location_terms=["厨房"],
            ),
        ],
    )

    result = run_memory_rag(
        _task_card(),
        memory_path=Path("data/scenarios/fetch_cup_retry/memory.json"),
        case_name="retry_after_boundary_error",
        query_provider=query_provider,
        embedding_provider=KeywordEmbedder(),
        llm_provider=_provider(),
        case_root=tmp_path / "cases",
        results_dir=tmp_path / "results",
    )

    assert result.passed is True
    assert query_provider.prompts is not None
    assert len(query_provider.prompts) == 2
    actual = json.loads((result.case_dir / "actual.json").read_text(encoding="utf-8"))
    assert actual["query_attempts"][0]["error_type"] == "query_boundary_error"
    assert actual["query_attempts"][1]["passed"] is True


def test_source_filter_rejects_non_object_memory_at_schema_boundary() -> None:
    with pytest.raises(ValidationError):
        MemoryRetrievalQuery(query_text="水杯", source_filter=["episodic_memory"])
