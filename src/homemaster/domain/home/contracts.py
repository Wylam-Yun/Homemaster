"""Public contracts for HomeMaster domain objects."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContractModel(BaseModel):
    """Base class for strict domain contracts."""

    model_config = ConfigDict(extra="forbid")


TaskType = Literal["check_presence", "fetch_object", "unknown"]


class TaskCard(ContractModel):
    """Structured description of the user's task."""

    task_type: TaskType
    target: str = Field(min_length=1)
    delivery_target: str | None = None
    location_hint: str | None = None
    success_criteria: list[str] = Field(min_length=1)
    needs_clarification: bool
    clarification_question: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("target")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("target must not be blank")
        return value

    @field_validator("delivery_target", "location_hint", "clarification_question")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("success_criteria")
    @classmethod
    def _strip_success_criteria(cls, value: list[str]) -> list[str]:
        stripped = [item.strip() for item in value if item.strip()]
        if not stripped:
            raise ValueError("success_criteria must contain at least one non-empty item")
        return stripped


class MemoryRetrievalQuery(ContractModel):
    """Query contract for object-memory RAG retrieval."""

    query_text: str = Field(min_length=1)
    target_category: str | None = None
    target_aliases: list[str] = Field(default_factory=list)
    location_terms: list[str] = Field(default_factory=list)
    source_filter: list[Literal["object_memory"]] = Field(
        default_factory=lambda: ["object_memory"]
    )
    top_k: int = Field(default=5, ge=1, le=50)
    excluded_memory_ids: list[str] = Field(default_factory=list)
    excluded_location_keys: list[str] = Field(default_factory=list)
    reason: str | None = None

    @field_validator("query_text")
    @classmethod
    def _strip_query_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query_text must not be blank")
        return value

    @field_validator("target_category", "reason")
    @classmethod
    def _strip_optional_query_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator(
        "target_aliases",
        "location_terms",
        "excluded_memory_ids",
        "excluded_location_keys",
    )
    @classmethod
    def _strip_query_lists(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


class MemoryRetrievalHit(ContractModel):
    """One memory document returned by BM25 + BGE-M3 retrieval."""

    document_id: str
    source_type: str = "object_memory"
    memory_id: str | None = None
    object_category: str | None = None
    aliases: list[str] = Field(default_factory=list)
    room_id: str | None = None
    anchor_id: str | None = None
    anchor_type: str | None = None
    display_text: str | None = None
    viewpoint_id: str | None = None
    confidence_level: str | None = None
    belief_state: str | None = None
    last_confirmed_at: str | None = None
    text_snippet: str | None = None
    bm25_score: float = 0.0
    dense_score: float = 0.0
    metadata_score: float = 0.0
    final_score: float = 0.0
    ranking_reasons: list[str] = Field(default_factory=list)
    canonical_metadata: dict[str, Any] = Field(default_factory=dict)
    executable: bool = False
    invalid_reason: str | None = None
    ranking_stage: str | None = None
    rerank_score: float | None = None
    reranker_model: str | None = None


class MemoryRetrievalResult(ContractModel):
    """Memory evidence returned by object-memory retrieval."""

    hits: list[MemoryRetrievalHit] = Field(default_factory=list)
    excluded: list[MemoryRetrievalHit] = Field(default_factory=list)
    retrieval_query: MemoryRetrievalQuery | None = None
    ranking_reasons: list[str] = Field(default_factory=list)
    retrieval_summary: str | None = None
    embedding_provider: dict[str, Any] = Field(default_factory=dict)
    index_snapshot: dict[str, Any] = Field(default_factory=dict)


class GroundedMemoryTarget(ContractModel):
    """Executable target grounded from a reliable memory hit."""

    memory_id: str
    room_id: str
    anchor_id: str
    viewpoint_id: str
    display_text: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    executable: bool = True
    invalid_reason: str | None = None
