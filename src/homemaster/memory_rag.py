"""Stage 03 object_memory-only RAG retrieval."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from homemaster.contracts import (
    MemoryRetrievalHit,
    MemoryRetrievalQuery,
    MemoryRetrievalResult,
    TaskCard,
)
from homemaster.embedding_client import BGEEmbeddingClient, EmbeddingClientError
from homemaster.llm_client import LLMClientError, RawJsonLLMClient
from homemaster.memory_index import (
    JsonEmbeddingCache,
    MemoryBM25Index,
    MemoryDocument,
    build_memory_documents,
    cosine_similarity,
)
from homemaster.memory_tokenizer import (
    JiebaMemoryTokenizer,
    build_domain_terms_from_object_memory,
)
from homemaster.runtime import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_PROVIDER_NAME,
    LLM_CASE_ROOT,
    REPO_ROOT,
    TEST_RESULTS_ROOT,
    ProviderConfig,
    ensure_stage_directories,
    load_provider_config,
)
from homemaster.trace import append_jsonl_event, write_json

STAGE_03_RESULTS_DIR = TEST_RESULTS_ROOT / "stage_03"
STAGE_03_CASE_ROOT = LLM_CASE_ROOT / "stage_03"
DEFAULT_EMBEDDING_PROVIDER_NAME = "MemoryEmbedding"
EMBEDDING_CACHE_DIR = REPO_ROOT / ".cache" / "homemaster" / "embeddings"


class MemoryRagError(RuntimeError):
    def __init__(self, *, error_type: str, message: str) -> None:
        self.error_type = error_type
        self.message = message
        super().__init__(message)


class MemoryRagBoundaryError(MemoryRagError):
    """Raised when an LLM query violates Stage 03 boundaries."""


class MemoryQueryProvider(Protocol):
    def generate_query(self, prompt: str) -> tuple[MemoryRetrievalQuery, str, dict[str, Any]]:
        """Generate a validated memory retrieval query from a prompt."""


class MemoryEmbeddingProvider(Protocol):
    provider_name: str
    model: str

    def public_summary(self) -> dict[str, Any]:
        """Return non-secret provider metadata."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per text."""


@dataclass(frozen=True)
class MemoryRagResult:
    passed: bool
    case_name: str
    task_card: TaskCard
    retrieval_query: MemoryRetrievalQuery
    memory_result: MemoryRetrievalResult
    checks: dict[str, bool]
    prompt: str
    raw_response: str
    provider: dict[str, Any]
    embedding_provider: dict[str, Any]
    case_dir: Path
    results_dir: Path
    elapsed_ms: float


class MimoMemoryQueryProvider:
    """Mimo-backed MemoryRetrievalQuery generator."""

    def __init__(
        self,
        provider: ProviderConfig,
        *,
        client: httpx.Client | None = None,
        max_tokens: int = 2048,
    ) -> None:
        self._provider = provider
        self._client = client
        self._max_tokens = max_tokens

    def generate_query(self, prompt: str) -> tuple[MemoryRetrievalQuery, str, dict[str, Any]]:
        llm_client = RawJsonLLMClient(self._provider, client=self._client)
        try:
            response = llm_client.complete_json(
                prompt,
                max_tokens=self._max_tokens,
                temperature=0.0,
            )
        except LLMClientError:
            raise
        finally:
            llm_client.close()
        try:
            query = MemoryRetrievalQuery.model_validate(response.json_payload)
        except ValidationError as exc:
            raise MemoryRagBoundaryError(
                error_type="query_schema_error",
                message=str(exc),
            ) from exc
        return query, response.content, response.public_summary()


class EmbeddingClientAdapter:
    """Adapter from BGEEmbeddingClient to the simple provider protocol used by RAG."""

    def __init__(self, client: BGEEmbeddingClient) -> None:
        self._client = client
        summary = client.public_summary()
        self.provider_name = str(summary["provider_name"])
        self.model = str(summary["model"])

    def public_summary(self) -> dict[str, Any]:
        return self._client.public_summary()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed_texts(texts).embeddings


def build_memory_retrieval_query_prompt(
    task_card: TaskCard,
    *,
    negative_evidence: dict[str, Any] | None = None,
) -> str:
    task_card_json = task_card.model_dump_json(indent=2)
    negative_json = json.dumps(negative_evidence or {}, ensure_ascii=False, indent=2)
    return f"""你是 HomeMaster V1.2 的 memory RAG query 构造组件。

目标：根据 TaskCard 生成一个 MemoryRetrievalQuery JSON。
你只负责构造检索 query，不读取 memory，不返回 memory hit，不选择目标地点。

必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。
不要编造 memory_id、anchor_id、viewpoint_id 或真实位置。

MemoryRetrievalQuery schema:
{{
  "query_text": "非空字符串；包含目标物、别名、位置提示和稳定英文别名",
  "target_category": "字符串或 null",
  "target_aliases": ["目标物别名；可来自 TaskCard 或常识别名"],
  "location_terms": ["位置词；只来自 TaskCard 明说的位置或常识位置别名"],
  "source_filter": ["object_memory"],
  "top_k": 5,
  "excluded_memory_ids": ["只能来自 runtime negative evidence"],
  "excluded_location_keys": ["只能来自 runtime negative evidence"],
  "reason": "字符串或 null"
}}

边界:
- source_filter 必须是 ["object_memory"]。
- top_k 使用 5，除非任务明显需要更多候选；不要超过 10。
- query_text 由你进行语义构造；程序不会替你补写语义别名。
- excluded_memory_ids / excluded_location_keys 只能复制 runtime negative evidence 中已有值。
- 不要编造 memory_id。

TaskCard:
{task_card_json}

Runtime negative evidence:
{negative_json}

只输出 JSON object:
"""


def run_memory_rag(
    task_card: TaskCard,
    *,
    memory_path: Path,
    case_name: str,
    query_provider: MemoryQueryProvider,
    embedding_provider: MemoryEmbeddingProvider,
    llm_provider: ProviderConfig,
    negative_evidence: dict[str, Any] | None = None,
    expected: dict[str, Any] | None = None,
    case_root: Path = STAGE_03_CASE_ROOT,
    results_dir: Path = STAGE_03_RESULTS_DIR,
    cache_dir: Path = EMBEDDING_CACHE_DIR,
) -> MemoryRagResult:
    started = time.perf_counter()
    case_dir = case_root / case_name
    ensure_stage_directories(case_dir=case_dir, results_dir=results_dir)
    memory_payload = json.loads(memory_path.read_text(encoding="utf-8"))
    expected_payload = expected or minimal_stage_03_expectation(case_name=case_name)

    prompt = build_memory_retrieval_query_prompt(task_card, negative_evidence=negative_evidence)
    query, raw_response, query_provider_summary = query_provider.generate_query(prompt)
    _validate_query_boundaries(query, negative_evidence or {})

    domain_terms = build_domain_terms_from_object_memory(memory_payload)
    tokenizer = JiebaMemoryTokenizer(domain_terms=domain_terms)
    documents = build_memory_documents(memory_payload)
    bm25_index = MemoryBM25Index.build(documents, tokenizer)
    bm25_hits = bm25_index.search(query.query_text, top_k=query.top_k)

    cache = JsonEmbeddingCache(cache_dir)
    document_vectors_list = cache.get_or_embed_documents(
        documents,
        provider_name=embedding_provider.provider_name,
        model=embedding_provider.model,
        embed_texts=embedding_provider.embed_texts,
    )
    document_vectors = dict(zip(
        [document.document_id for document in documents],
        document_vectors_list,
        strict=True,
    ))
    query_vector = embedding_provider.embed_texts([query.query_text])[0]
    dense_hits = _dense_hits(documents, document_vectors, query_vector, top_k=query.top_k)
    memory_result = _fuse_hits(
        documents=documents,
        bm25_hits=bm25_hits,
        dense_hits=dense_hits,
        query=query,
        negative_evidence=negative_evidence or {},
        embedding_provider_summary=embedding_provider.public_summary(),
        index_snapshot={
            "document_count": len(documents),
            "domain_terms": domain_terms,
            "tokenized_query": tokenizer.tokenize(query.query_text),
            "ranking_stage": "bm25_dense_fusion",
        },
    )
    checks = validate_memory_rag_expectations(memory_result, expected_payload)
    passed = all(checks.values())
    elapsed_ms = (time.perf_counter() - started) * 1000

    actual = {
        "case_name": case_name,
        "passed": passed,
        "task_card": task_card.model_dump(mode="json"),
        "query_provider": query_provider_summary,
        "embedding_provider": embedding_provider.public_summary(),
        "prompt": prompt,
        "raw_response": raw_response,
        "retrieval_query": query.model_dump(mode="json"),
        "memory_documents": [
            {
                "document_id": document.document_id,
                "text": document.text,
                "metadata": document.metadata,
                "executable": document.executable,
                "invalid_reason": document.invalid_reason,
            }
            for document in documents
        ],
        "tokenized_query": tokenizer.tokenize(query.query_text),
        "bm25_hits": [
            {"document_id": hit.document.document_id, "score": hit.score, "rank": hit.rank}
            for hit in bm25_hits
        ],
        "dense_hits": [
            {
                "document_id": item["document"].document_id,
                "score": item["score"],
                "rank": item["rank"],
            }
            for item in dense_hits
        ],
        "memory_result": memory_result.model_dump(mode="json"),
        "checks": checks,
        "elapsed_ms": elapsed_ms,
    }
    _write_stage_03_assets(
        case_dir=case_dir,
        results_dir=results_dir,
        expected=expected_payload,
        actual=actual,
        status="PASS" if passed else "FAIL",
    )
    if not passed:
        raise MemoryRagError(
            error_type="expectation_failed",
            message=f"Stage 03 case {case_name!r} did not match expected fields.",
        )
    return MemoryRagResult(
        passed=passed,
        case_name=case_name,
        task_card=task_card,
        retrieval_query=query,
        memory_result=memory_result,
        checks=checks,
        prompt=prompt,
        raw_response=raw_response,
        provider=query_provider_summary,
        embedding_provider=embedding_provider.public_summary(),
        case_dir=case_dir,
        results_dir=results_dir,
        elapsed_ms=elapsed_ms,
    )


def run_stage_03_case(
    case_name: str,
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    llm_provider_name: str = DEFAULT_PROVIDER_NAME,
    embedding_provider_name: str = DEFAULT_EMBEDDING_PROVIDER_NAME,
    llm_client: httpx.Client | None = None,
    embedding_client: httpx.Client | None = None,
) -> MemoryRagResult:
    cases = stage_03_case_expectations()
    if case_name not in cases:
        raise MemoryRagError(
            error_type="unknown_case",
            message=f"unknown Stage 03 case: {case_name}",
        )
    expected = cases[case_name]
    task_card = TaskCard.model_validate(expected["task_card"])
    llm_provider = load_provider_config(config_path, provider_name=llm_provider_name)
    embedding_provider_config = load_provider_config(
        config_path,
        provider_name=embedding_provider_name,
    )
    query_provider = MimoMemoryQueryProvider(llm_provider, client=llm_client)
    bge_client = BGEEmbeddingClient(embedding_provider_config, client=embedding_client)
    try:
        return run_memory_rag(
            task_card,
            memory_path=REPO_ROOT / expected["memory_path"],
            case_name=case_name,
            query_provider=query_provider,
            embedding_provider=EmbeddingClientAdapter(bge_client),
            llm_provider=llm_provider,
            negative_evidence=expected.get("negative_evidence"),
            expected=expected,
        )
    except (LLMClientError, EmbeddingClientError) as exc:
        raise MemoryRagError(
            error_type=getattr(exc, "error_type", type(exc).__name__),
            message=str(exc),
        ) from exc
    finally:
        bge_client.close()


def stage_03_case_expectations() -> dict[str, dict[str, Any]]:
    return {
        "mimo_memory_retrieval_query": {
            "case_name": "mimo_memory_retrieval_query",
            "memory_path": "data/scenarios/fetch_cup_retry/memory.json",
            "task_card": _task_card_payload("fetch_object", "水杯", "厨房", "user"),
            "expected_query_keywords": ["水杯", "杯", "cup"],
            "expected_location_keywords": ["厨房", "kitchen"],
            "expected_top_memory_ids": ["mem-cup-1"],
        },
        "medicine_object_memory_rag": {
            "case_name": "medicine_object_memory_rag",
            "memory_path": "data/scenarios/check_medicine_success/memory.json",
            "task_card": _task_card_payload("check_presence", "药盒", "桌子那边", None),
            "expected_query_keywords": ["药盒", "药", "medicine"],
            "expected_top_memory_ids": ["mem-medicine-1", "mem-medicine-2"],
        },
        "cup_object_memory_rag": {
            "case_name": "cup_object_memory_rag",
            "memory_path": "data/scenarios/fetch_cup_retry/memory.json",
            "task_card": _task_card_payload("fetch_object", "水杯", "厨房", "user"),
            "expected_query_keywords": ["水杯", "杯", "cup"],
            "expected_location_keywords": ["厨房", "kitchen"],
            "expected_top_memory_ids": ["mem-cup-1"],
        },
        "negative_evidence_excludes_location": {
            "case_name": "negative_evidence_excludes_location",
            "memory_path": "data/scenarios/object_not_found/memory.json",
            "task_card": _task_card_payload("fetch_object", "水杯", "厨房", "user"),
            "negative_evidence": {"excluded_memory_ids": ["mem-cup-1"]},
            "expected_query_keywords": ["水杯", "杯", "cup"],
            "excluded_memory_ids": ["mem-cup-1"],
        },
        "reranker_not_required_stage_03": {
            "case_name": "reranker_not_required_stage_03",
            "memory_path": "data/scenarios/fetch_cup_retry/memory.json",
            "task_card": _task_card_payload("fetch_object", "水杯", "厨房", "user"),
            "expected_query_keywords": ["水杯", "杯", "cup"],
            "expected_ranking_stage": "bm25_dense_fusion",
            "reranker_empty": True,
        },
    }


def minimal_stage_03_expectation(*, case_name: str) -> dict[str, Any]:
    return {
        "case_name": case_name,
        "required_checks": [
            "query validates as MemoryRetrievalQuery",
            "returns MemoryRetrievalResult",
            "hits include score breakdown",
        ],
    }


def validate_memory_rag_expectations(
    memory_result: MemoryRetrievalResult,
    expected: dict[str, Any],
) -> dict[str, bool]:
    top_hit = memory_result.hits[0] if memory_result.hits else None
    top_memory_id = top_hit.memory_id if top_hit else None
    query_text = (
        memory_result.retrieval_query.query_text if memory_result.retrieval_query else ""
    ).casefold()
    excluded_ids = {hit.memory_id for hit in memory_result.excluded}
    checks: dict[str, bool] = {
        "schema_valid": True,
        "has_score_breakdown": all(
            hit.ranking_stage == "bm25_dense_fusion"
            and hit.ranking_reasons
            and hit.final_score >= 0
            for hit in memory_result.hits
        ),
    }
    if "expected_query_keywords" in expected:
        checks["query_mentions_target"] = _contains_any(
            query_text,
            expected["expected_query_keywords"],
        )
    if "expected_location_keywords" in expected:
        checks["query_mentions_location"] = _contains_any(
            query_text,
            expected["expected_location_keywords"],
        )
    if "expected_top_memory_ids" in expected:
        checks["top_memory_matches"] = top_memory_id in set(expected["expected_top_memory_ids"])
    if "excluded_memory_ids" in expected:
        checks["excluded_memory_matches"] = set(expected["excluded_memory_ids"]).issubset(
            excluded_ids
        )
    if expected.get("reranker_empty"):
        checks["reranker_empty"] = all(
            hit.rerank_score is None and hit.reranker_model is None
            for hit in memory_result.hits + memory_result.excluded
        )
    if "expected_ranking_stage" in expected:
        checks["ranking_stage_matches"] = all(
            hit.ranking_stage == expected["expected_ranking_stage"]
            for hit in memory_result.hits + memory_result.excluded
        )
    return checks


def _validate_query_boundaries(
    query: MemoryRetrievalQuery,
    negative_evidence: dict[str, Any],
) -> None:
    if query.source_filter != ["object_memory"]:
        raise MemoryRagBoundaryError(
            error_type="query_boundary_error",
            message="source_filter must be ['object_memory']",
        )
    if query.top_k > 50:
        raise MemoryRagBoundaryError(
            error_type="query_boundary_error",
            message="top_k exceeds the schema limit",
        )
    allowed_memory_ids = set(_string_list(negative_evidence.get("excluded_memory_ids")))
    unknown_memory_ids = set(query.excluded_memory_ids) - allowed_memory_ids
    if unknown_memory_ids:
        raise MemoryRagBoundaryError(
            error_type="query_boundary_error",
            message="query excluded_memory_ids must come from runtime negative evidence",
        )
    allowed_location_keys = set(_string_list(negative_evidence.get("excluded_location_keys")))
    unknown_location_keys = set(query.excluded_location_keys) - allowed_location_keys
    if unknown_location_keys:
        raise MemoryRagBoundaryError(
            error_type="query_boundary_error",
            message="query excluded_location_keys must come from runtime negative evidence",
        )


def _dense_hits(
    documents: list[MemoryDocument],
    document_vectors: dict[str, list[float]],
    query_vector: list[float],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    scored = [
        {
            "document": document,
            "score": cosine_similarity(
                document_vectors.get(document.document_id, []),
                query_vector,
            ),
        }
        for document in documents
    ]
    scored.sort(key=lambda item: item["score"], reverse=True)
    return [
        {"document": item["document"], "score": float(item["score"]), "rank": rank}
        for rank, item in enumerate(scored[:top_k], start=1)
    ]


def _fuse_hits(
    *,
    documents: list[MemoryDocument],
    bm25_hits: list[Any],
    dense_hits: list[dict[str, Any]],
    query: MemoryRetrievalQuery,
    negative_evidence: dict[str, Any],
    embedding_provider_summary: dict[str, Any],
    index_snapshot: dict[str, Any],
) -> MemoryRetrievalResult:
    bm25_by_id = {hit.document.document_id: hit for hit in bm25_hits}
    dense_by_id = {hit["document"].document_id: hit for hit in dense_hits}
    excluded_memory_ids = set(query.excluded_memory_ids) | set(
        _string_list(negative_evidence.get("excluded_memory_ids"))
    )
    excluded_location_keys = set(query.excluded_location_keys) | set(
        _string_list(negative_evidence.get("excluded_location_keys"))
    )
    hits: list[MemoryRetrievalHit] = []
    excluded: list[MemoryRetrievalHit] = []

    for document in documents:
        bm25_hit = bm25_by_id.get(document.document_id)
        dense_hit = dense_by_id.get(document.document_id)
        bm25_rank = bm25_hit.rank if bm25_hit else None
        dense_rank = int(dense_hit["rank"]) if dense_hit else None
        bm25_score = float(bm25_hit.score) if bm25_hit else 0.0
        dense_score = float(dense_hit["score"]) if dense_hit else 0.0
        metadata_score, metadata_reasons = _metadata_score(document, query)
        rrf_score = _rrf_score(bm25_rank) + _rrf_score(dense_rank)
        final_score = rrf_score + metadata_score
        ranking_reasons = []
        if bm25_rank is not None:
            ranking_reasons.append(f"bm25_rank={bm25_rank}")
        if dense_rank is not None:
            ranking_reasons.append(f"dense_rank={dense_rank}")
        ranking_reasons.extend(metadata_reasons)

        invalid_reason = document.invalid_reason
        if str(document.metadata.get("memory_id")) in excluded_memory_ids:
            invalid_reason = "excluded by runtime negative evidence memory_id"
        elif any(_location_key_matches(document, key) for key in excluded_location_keys):
            invalid_reason = "excluded by runtime negative evidence location"
        elif document.source_type != "object_memory":
            invalid_reason = "excluded by source_filter"

        hit = _hit_from_document(
            document,
            bm25_score=bm25_score,
            dense_score=dense_score,
            metadata_score=metadata_score,
            final_score=final_score,
            ranking_reasons=ranking_reasons,
            executable=document.executable and invalid_reason is None,
            invalid_reason=invalid_reason,
        )
        if invalid_reason:
            excluded.append(hit)
        else:
            hits.append(hit)

    hits.sort(key=lambda hit: hit.final_score, reverse=True)
    excluded.sort(key=lambda hit: hit.final_score, reverse=True)
    return MemoryRetrievalResult(
        hits=hits[: query.top_k],
        excluded=excluded,
        retrieval_query=query,
        ranking_reasons=["bm25_dense_rrf_fusion", "metadata_guardrail"],
        retrieval_summary=f"returned {len(hits[: query.top_k])} hits and {len(excluded)} excluded",
        embedding_provider=embedding_provider_summary,
        index_snapshot=index_snapshot,
    )


def _hit_from_document(
    document: MemoryDocument,
    *,
    bm25_score: float,
    dense_score: float,
    metadata_score: float,
    final_score: float,
    ranking_reasons: list[str],
    executable: bool,
    invalid_reason: str | None,
) -> MemoryRetrievalHit:
    metadata = document.metadata
    return MemoryRetrievalHit(
        document_id=document.document_id,
        source_type=document.source_type,
        memory_id=_optional_text(metadata.get("memory_id")),
        object_category=_optional_text(metadata.get("object_category")),
        aliases=list(metadata.get("aliases", [])),
        room_id=_optional_text(metadata.get("room_id")),
        anchor_id=_optional_text(metadata.get("anchor_id")),
        anchor_type=_optional_text(metadata.get("anchor_type")),
        display_text=_optional_text(metadata.get("display_text")),
        viewpoint_id=_optional_text(metadata.get("viewpoint_id")),
        confidence_level=_optional_text(metadata.get("confidence_level")),
        belief_state=_optional_text(metadata.get("belief_state")),
        last_confirmed_at=_optional_text(metadata.get("last_confirmed_at")),
        text_snippet=document.text,
        bm25_score=bm25_score,
        dense_score=dense_score,
        metadata_score=metadata_score,
        final_score=final_score,
        ranking_reasons=ranking_reasons,
        canonical_metadata=metadata,
        executable=executable,
        invalid_reason=invalid_reason,
        ranking_stage="bm25_dense_fusion",
        rerank_score=None,
        reranker_model=None,
    )


def _metadata_score(
    document: MemoryDocument,
    query: MemoryRetrievalQuery,
) -> tuple[float, list[str]]:
    metadata = document.metadata
    searchable = " ".join(
        [
            str(metadata.get("object_category") or ""),
            " ".join(metadata.get("aliases", [])),
            str(metadata.get("room_id") or ""),
            str(metadata.get("anchor_type") or ""),
            str(metadata.get("display_text") or ""),
        ]
    ).casefold()
    score = 0.0
    reasons: list[str] = []
    if query.target_category and query.target_category.casefold() in searchable:
        score += 0.2
        reasons.append("metadata_target_category_match")
    if query.target_aliases and any(
        alias.casefold() in searchable for alias in query.target_aliases
    ):
        score += 0.2
        reasons.append("metadata_target_alias_match")
    if query.location_terms and any(term.casefold() in searchable for term in query.location_terms):
        score += 0.15
        reasons.append("metadata_location_match")
    confidence = str(metadata.get("confidence_level") or "").casefold()
    if confidence == "high":
        score += 0.1
        reasons.append("metadata_high_confidence")
    elif confidence == "medium":
        score += 0.05
        reasons.append("metadata_medium_confidence")
    if str(metadata.get("belief_state") or "").casefold() == "stale":
        score -= 0.1
        reasons.append("metadata_stale_penalty")
    return score, reasons


def _write_stage_03_assets(
    *,
    case_dir: Path,
    results_dir: Path,
    expected: dict[str, Any],
    actual: dict[str, Any],
    status: str,
) -> None:
    write_json(
        case_dir / "input.json",
        {"case_name": actual["case_name"], "prompt": actual["prompt"]},
    )
    write_json(case_dir / "expected.json", expected)
    write_json(case_dir / "actual.json", actual)
    _write_stage_03_markdown(
        case_dir / "result.md",
        expected=expected,
        actual=actual,
        status=status,
    )
    append_jsonl_event(
        results_dir / "llm_samples.jsonl",
        event="stage_03_memory_rag",
        payload=actual,
    )
    append_jsonl_event(
        results_dir / "trace" / f"{actual['case_name']}.jsonl",
        event="stage_03_memory_rag",
        payload=actual,
    )


def _write_stage_03_markdown(
    path: Path,
    *,
    expected: dict[str, Any],
    actual: dict[str, Any],
    status: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"""# Stage 03 Memory RAG - {actual["case_name"]}

Status: {status}

Provider: {actual["query_provider"]}

Embedding Provider: {actual["embedding_provider"]}

## Expected Conditions

```json
{json.dumps(expected, ensure_ascii=False, indent=2)}
```

## Mimo Query Prompt

```text
{actual["prompt"]}
```

## Mimo Raw Response

```text
{actual["raw_response"]}
```

## Parsed MemoryRetrievalQuery

```json
{json.dumps(actual["retrieval_query"], ensure_ascii=False, indent=2)}
```

## Memory Documents

```json
{json.dumps(actual["memory_documents"], ensure_ascii=False, indent=2)}
```

## Tokenized Query

```json
{json.dumps(actual["tokenized_query"], ensure_ascii=False, indent=2)}
```

## BM25 Hits

```json
{json.dumps(actual["bm25_hits"], ensure_ascii=False, indent=2)}
```

## BGE-M3 Dense Hits

```json
{json.dumps(actual["dense_hits"], ensure_ascii=False, indent=2)}
```

## Fused MemoryRetrievalResult

```json
{json.dumps(actual["memory_result"], ensure_ascii=False, indent=2)}
```

## Checks

```json
{json.dumps(actual["checks"], ensure_ascii=False, indent=2)}
```
"""
    path.write_text(text, encoding="utf-8")


def _task_card_payload(
    task_type: str,
    target: str,
    location_hint: str | None,
    delivery_target: str | None,
) -> dict[str, Any]:
    return {
        "task_type": task_type,
        "target": target,
        "delivery_target": delivery_target,
        "location_hint": location_hint,
        "success_criteria": ["后续观察可以验证任务是否完成"],
        "needs_clarification": False,
        "clarification_question": None,
        "confidence": 0.9,
    }


def _rrf_score(rank: int | None, *, k: int = 60) -> float:
    return 0.0 if rank is None else 1.0 / (k + rank)


def _location_key_matches(document: MemoryDocument, key: str) -> bool:
    key = key.casefold()
    metadata = document.metadata
    return any(
        key and key in str(metadata.get(field) or "").casefold()
        for field in ("room_id", "anchor_id", "viewpoint_id", "display_text")
    )


def _contains_any(value: str, keywords: list[str]) -> bool:
    return any(str(keyword).casefold() in value for keyword in keywords)


def _string_list(raw: Any) -> list[str]:
    return [item for item in raw if isinstance(item, str)] if isinstance(raw, list) else []


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
