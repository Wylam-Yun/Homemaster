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


class ObjectMemorySearchPlan(ContractModel):
    target_category: str
    target_aliases: list[str] = Field(default_factory=list)
    location_hint: str | None = None
    excluded_location_keys: list[str] = Field(default_factory=list)
    ranking_policy: str | None = None
    reason: str | None = None


class ObjectMemoryEvidence(ContractModel):
    hits: list[dict[str, Any]] = Field(default_factory=list)
    excluded: list[dict[str, Any]] = Field(default_factory=list)
    ranking_reasons: list[str] = Field(default_factory=list)
    retrieval_summary: str | None = None


class Candidate(ContractModel):
    candidate_id: str
    memory_id: str | None = None
    room_id: str | None = None
    anchor_id: str | None = None
    viewpoint_id: str | None = None
    display_text: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class CandidatePool(ContractModel):
    candidates: list[Candidate] = Field(default_factory=list)
    generation_summary: dict[str, Any] = Field(default_factory=dict)


class CandidateSelection(ContractModel):
    selected_candidate_id: str | None = None
    ranked_candidate_ids: list[str] = Field(default_factory=list)
    reason: str | None = None
    need_retrieve_again: bool = False


class Subtask(ContractModel):
    id: str
    type: Literal["navigate", "observe_verify", "embodied_operate", "ask_user", "finish"]
    target: str | None = None
    success_criteria: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class OrchestrationPlan(ContractModel):
    goal: str
    selected_candidate_id: str | None = None
    subtasks: list[Subtask] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class StepDecision(ContractModel):
    subtask_id: str | None = None
    module: Literal["navigate", "observe", "verify", "operate", "ask_user", "finish"]
    module_input: dict[str, Any] = Field(default_factory=dict)
    expected_result: str | None = None
    verify_after: bool = True
    reason: str | None = None


class ModuleExecutionResult(ContractModel):
    module: str
    status: Literal["success", "failed", "blocked"]
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


class RecoveryDecision(ContractModel):
    action: Literal[
        "retry_step",
        "reobserve",
        "switch_candidate",
        "retrieve_again",
        "replan",
        "ask_user",
        "finish_failed",
    ]
    reason: str | None = None
    next_candidate_id: str | None = None
    should_retrieve_again: bool = False
    should_replan: bool = False
    ask_user_question: str | None = None


class EmbodiedActionPlan(ContractModel):
    operation_goal: str
    target: str | None = None
    observation: dict[str, Any] | None = None
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    vla_instruction: str | None = None


class TaskSummary(ContractModel):
    result: Literal["success", "failed", "needs_user"]
    confirmed_facts: list[str] = Field(default_factory=list)
    unconfirmed_facts: list[str] = Field(default_factory=list)
    recovery_attempts: list[str] = Field(default_factory=list)
    user_reply: str | None = None


class MemoryCommitPlan(ContractModel):
    object_memory_updates: list[dict[str, Any]] = Field(default_factory=list)
    negative_evidence: list[dict[str, Any]] = Field(default_factory=list)
    task_record_note: str | None = None
    skipped: bool = False
