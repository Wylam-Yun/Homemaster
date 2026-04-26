"""Public contracts for the HomeMaster V1.2 pipeline."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContractModel(BaseModel):
    """Base class for strict stage contracts."""

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


class VLMImageInput(ContractModel):
    enabled: bool = False
    image_ref: str | None = None
    camera: str | None = None
    timestamp: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryRetrievalQuery(ContractModel):
    """Query contract for Stage 03 memory RAG retrieval."""

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
    """Memory evidence returned by Stage 03 retrieval."""

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


class PlanningContext(ContractModel):
    """Minimal context passed from grounding into orchestration."""

    task_card: TaskCard
    retrieval_query: MemoryRetrievalQuery | None = None
    memory_evidence: MemoryRetrievalResult | None = None
    selected_target: GroundedMemoryTarget | None = None
    rejected_hits: list[MemoryRetrievalHit] = Field(default_factory=list)
    runtime_state_summary: dict[str, Any] = Field(default_factory=dict)
    world_summary: dict[str, Any] = Field(default_factory=dict)
    planning_notes: list[str] = Field(default_factory=list)


class Subtask(ContractModel):
    id: str
    intent: str
    target_object: str | None = None
    recipient: str | None = None
    room_hint: str | None = None
    anchor_hint: str | None = None
    success_criteria: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)

    @field_validator("id", "intent")
    @classmethod
    def _strip_required_subtask_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("subtask text fields must not be blank")
        return value

    @field_validator("target_object", "recipient", "room_hint", "anchor_hint")
    @classmethod
    def _strip_optional_subtask_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("success_criteria", "depends_on")
    @classmethod
    def _strip_subtask_lists(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


class OrchestrationPlan(ContractModel):
    goal: str
    subtasks: list[Subtask] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("goal")
    @classmethod
    def _strip_goal(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("goal must not be blank")
        return value


class StepDecision(ContractModel):
    subtask_id: str
    selected_skill: Literal["navigation", "operation"]
    skill_input: dict[str, Any] = Field(default_factory=dict)
    expected_result: str | None = None
    reason: str | None = None

    @field_validator("subtask_id")
    @classmethod
    def _strip_subtask_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("subtask_id must not be blank")
        return value

    @field_validator("expected_result", "reason")
    @classmethod
    def _strip_optional_decision_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class ModuleExecutionResult(ContractModel):
    skill: Literal["navigation", "operation", "verification"]
    status: Literal["success", "failed", "blocked"]
    skill_output: dict[str, Any] = Field(default_factory=dict)
    observation: dict[str, Any] | None = None
    runtime_state_delta: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    image_input: VLMImageInput = Field(default_factory=VLMImageInput)


class VerificationResult(ContractModel):
    scope: Literal["step", "subtask", "task"]
    passed: bool
    verified_facts: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    failed_reason: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SubtaskRuntimeState(ContractModel):
    subtask_id: str
    status: Literal["pending", "running", "verified", "failed", "blocked", "skipped"] = (
        "pending"
    )
    depends_on: list[str] = Field(default_factory=list)
    attempt_count: int = Field(default=0, ge=0)
    last_started_at: str | None = None
    last_completed_at: str | None = None
    last_skill: Literal["navigation", "operation", "verification"] | None = None
    last_observation: dict[str, Any] | None = None
    last_verification_result: VerificationResult | None = None
    failure_record_ids: list[str] = Field(default_factory=list)

    @field_validator("subtask_id")
    @classmethod
    def _strip_runtime_subtask_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("subtask_id must not be blank")
        return value

    @field_validator("depends_on", "failure_record_ids")
    @classmethod
    def _strip_runtime_lists(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


class ExecutionState(ContractModel):
    task_status: Literal["running", "completed", "failed", "needs_user_input"] = "running"
    current_subtask_id: str | None = None
    subtasks: list[SubtaskRuntimeState] = Field(default_factory=list)
    held_object: str | None = None
    target_object_visible: bool = False
    target_object_location: str | None = None
    user_location: str | None = None
    current_location: str | None = None
    last_observation: dict[str, Any] | None = None
    last_skill_call: dict[str, Any] | None = None
    last_skill_result: ModuleExecutionResult | None = None
    last_verification_result: VerificationResult | None = None
    failure_record_ids: list[str] = Field(default_factory=list)
    negative_evidence: list[dict[str, Any]] = Field(default_factory=list)
    retry_counts: dict[str, int] = Field(default_factory=dict)
    completed_subtask_ids: list[str] = Field(default_factory=list)

    @field_validator(
        "current_subtask_id",
        "held_object",
        "target_object_location",
        "user_location",
        "current_location",
    )
    @classmethod
    def _strip_optional_state_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("failure_record_ids", "completed_subtask_ids")
    @classmethod
    def _strip_state_lists(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


FailureType = Literal[
    "model_output_invalid",
    "skill_schema_invalid",
    "skill_failed",
    "precondition_failed",
    "verification_failed",
    "target_not_visible",
    "object_not_found",
    "max_retry_exceeded",
]


class FailureRecord(ContractModel):
    failure_id: str
    subtask_id: str | None = None
    subtask_intent: str | None = None
    skill: Literal["navigation", "operation", "verification"] | None = None
    failure_type: FailureType
    failed_reason: str
    skill_input: dict[str, Any] = Field(default_factory=dict)
    skill_output: dict[str, Any] = Field(default_factory=dict)
    verification_result: VerificationResult | None = None
    observation: dict[str, Any] | None = None
    negative_evidence: list[dict[str, Any]] = Field(default_factory=list)
    retry_count: int = Field(default=0, ge=0)
    created_at: str | None = None
    event_memory_candidate: dict[str, Any] | None = None

    @field_validator("failure_id", "failed_reason")
    @classmethod
    def _strip_required_failure_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("failure text fields must not be blank")
        return value

    @field_validator("subtask_id", "subtask_intent", "created_at")
    @classmethod
    def _strip_optional_failure_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class RecoveryDecision(ContractModel):
    action: Literal[
        "retry_step",
        "reobserve",
        "retrieve_again",
        "replan",
        "ask_user",
        "finish_failed",
    ]
    reason: str | None = None
    failure_record_ids: list[str] = Field(default_factory=list)
    should_retrieve_again: bool = False
    should_replan: bool = False
    ask_user_question: str | None = None
    final_failed_reason: str | None = None

    @field_validator("reason", "ask_user_question", "final_failed_reason")
    @classmethod
    def _strip_optional_recovery_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("failure_record_ids")
    @classmethod
    def _strip_failure_record_ids(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


EvidenceType = Literal[
    "verification_result",
    "failure_record",
    "skill_result",
    "observation",
    "selected_target",
    "trace_event",
]


class EvidenceRef(ContractModel):
    evidence_id: str
    evidence_type: EvidenceType
    source_id: str
    subtask_id: str | None = None
    memory_id: str | None = None
    location_key: str | None = None
    created_at: str
    summary: str

    @field_validator("evidence_id", "source_id", "created_at", "summary")
    @classmethod
    def _strip_required_evidence_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("evidence text fields must not be blank")
        return value

    @field_validator("subtask_id", "memory_id", "location_key")
    @classmethod
    def _strip_optional_evidence_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class EvidenceBundle(ContractModel):
    task_id: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    verified_facts: list[str] = Field(default_factory=list)
    failure_facts: list[str] = Field(default_factory=list)
    system_failures: list[str] = Field(default_factory=list)
    negative_evidence: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("task_id")
    @classmethod
    def _strip_task_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("task_id must not be blank")
        return value

    @field_validator("verified_facts", "failure_facts", "system_failures")
    @classmethod
    def _strip_bundle_lists(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


class ObjectMemoryUpdate(ContractModel):
    memory_id: str
    update_type: Literal["confirm", "mark_stale", "mark_contradicted"]
    updated_fields: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[EvidenceRef] = Field(min_length=1)
    reason: str

    @field_validator("memory_id", "reason")
    @classmethod
    def _strip_required_object_update_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("object memory update text fields must not be blank")
        return value


class FactMemoryWrite(ContractModel):
    fact_id: str
    fact_type: Literal[
        "object_seen",
        "object_not_seen",
        "operation_failed",
        "delivery_verified",
        "verification_failed",
        "user_preference",
        "system_event",
    ]
    polarity: Literal["positive", "negative", "neutral"]
    target: str | None = None
    location: dict[str, Any] = Field(default_factory=dict)
    time_scope: str = "task_run"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    text: str
    evidence_refs: list[EvidenceRef] = Field(min_length=1)
    expires_at: str | None = None
    stale_after: str | None = None
    searchable: bool = False

    @field_validator("fact_id", "time_scope", "text")
    @classmethod
    def _strip_required_fact_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("fact memory text fields must not be blank")
        return value

    @field_validator("target", "expires_at", "stale_after")
    @classmethod
    def _strip_optional_fact_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("searchable")
    @classmethod
    def _searchable_must_be_false(cls, value: bool) -> bool:
        if value:
            raise ValueError("Stage 06 fact/event memory is not searchable yet")
        return False


class TaskSummary(ContractModel):
    result: Literal["success", "failed", "needs_user"]
    confirmed_facts: list[str] = Field(default_factory=list)
    unconfirmed_facts: list[str] = Field(default_factory=list)
    recovery_attempts: list[str] = Field(default_factory=list)
    user_reply: str | None = None
    failure_summary: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator(
        "confirmed_facts",
        "unconfirmed_facts",
        "recovery_attempts",
        "evidence_refs",
    )
    @classmethod
    def _strip_summary_lists(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]

    @field_validator("user_reply", "failure_summary")
    @classmethod
    def _strip_optional_summary_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class TaskRecord(ContractModel):
    task_id: str
    task_card: TaskCard
    summary: TaskSummary
    result: Literal["success", "failed", "needs_user"]
    started_at: str | None = None
    completed_at: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    failure_record_ids: list[str] = Field(default_factory=list)

    @field_validator("task_id")
    @classmethod
    def _strip_task_record_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("task_id must not be blank")
        return value

    @field_validator("started_at", "completed_at")
    @classmethod
    def _strip_optional_task_record_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("failure_record_ids")
    @classmethod
    def _strip_task_record_failure_ids(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


class MemoryCommitPlan(ContractModel):
    commit_id: str | None = None
    object_memory_updates: list[ObjectMemoryUpdate] = Field(default_factory=list)
    fact_memory_writes: list[FactMemoryWrite] = Field(default_factory=list)
    task_record: TaskRecord | None = None
    negative_evidence: list[dict[str, Any]] = Field(default_factory=list)
    skipped_candidates: list[dict[str, Any]] = Field(default_factory=list)
    index_stale_memory_ids: list[str] = Field(default_factory=list)
    skipped: bool = False

    @field_validator("commit_id")
    @classmethod
    def _strip_optional_commit_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("index_stale_memory_ids")
    @classmethod
    def _strip_index_stale_ids(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]
