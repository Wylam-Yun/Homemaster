"""Core domain models for the memory-grounded task brain (Phase A)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class TaskIntent(StrEnum):
    """Supported Phase A user intents."""

    CHECK_OBJECT_PRESENCE = "check_object_presence"
    FETCH_OBJECT = "fetch_object"


class ObservationSource(StrEnum):
    """Observation backends normalized by adapters."""

    MOCK_WORLD = "mock_world"
    AI2_THOR = "ai2_thor"
    BEHAVIOR = "behavior"
    HABITAT = "habitat"
    VLM = "vlm"
    REAL_ROBOT = "real_robot"


class ConfidenceLevel(StrEnum):
    """Discrete confidence bands for Phase A."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EvidenceSource(StrEnum):
    """Evidence provenance used by memory records."""

    DIRECT_OBSERVATION = "direct_observation"
    USER_PROVIDED = "user_provided"
    INFERRED_EXPERIENCE = "inferred_experience"


class BeliefState(StrEnum):
    """Long-term memory belief state."""

    CONFIRMED = "confirmed"
    STALE = "stale"
    CONTRADICTED = "contradicted"
    UNKNOWN = "unknown"


class TargetObject(BaseModel):
    """Structured target object extracted from instruction."""

    category: str
    aliases: list[str] = Field(default_factory=list)
    attributes: list[str] = Field(default_factory=list)


class TaskRequest(BaseModel):
    """Top-level user request representation."""

    source: str
    user_id: str
    utterance: str
    request_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ParsedTask(BaseModel):
    """Parser output consumed by retrieval and planning."""

    intent: TaskIntent
    target_object: TargetObject
    quantity: int = Field(default=1, ge=1)
    explicit_location_hint: str | None = None
    delivery_target: str | None = None
    requires_navigation: bool = True
    requires_manipulation: bool = False


class Predicate(BaseModel):
    """Verification predicate represented as a name + ordered arguments."""

    name: str
    args: list[str] = Field(default_factory=list)

    @classmethod
    def from_list(cls, items: list[str]) -> Predicate:
        """Build a predicate from list form: [name, arg1, arg2, ...]."""
        if not items:
            raise ValueError("Predicate list must contain at least a predicate name.")
        return cls(name=items[0], args=items[1:])

    def to_list(self) -> list[str]:
        """Convert predicate back to list form."""
        return [self.name, *self.args]


class Anchor(BaseModel):
    """Structured anchor for long-term memory and task evidence."""

    room_id: str
    anchor_id: str
    anchor_type: str
    viewpoint_id: str | None = None
    display_text: str | None = None


class RelativeRelation(BaseModel):
    """Structured relation relative to another world entity."""

    type: str
    target: str
    text: str | None = None


class ObservedAnchor(BaseModel):
    """Anchor observed in the current scene snapshot."""

    room_id: str
    anchor_id: str
    anchor_type: str
    viewpoint_id: str | None = None
    display_text: str | None = None


class SceneRelation(BaseModel):
    """Relation between two observed objects."""

    relation_type: str
    subject_object_id: str
    target_object_id: str
    text: str | None = None


class ObservedObject(BaseModel):
    """Normalized object view in one observation."""

    observation_object_id: str
    category: str
    aliases: list[str] = Field(default_factory=list)
    attributes: list[str] = Field(default_factory=list)
    detector_id: str | None = None
    memory_id: str | None = None
    confidence_level: ConfidenceLevel = ConfidenceLevel.MEDIUM
    state_summary: str | None = None
    spatial_relation: str | None = None

    @model_validator(mode="after")
    def validate_id_boundaries(self) -> ObservedObject:
        """Detector ID and memory ID must never be mixed as the same ID."""
        if self.detector_id and self.memory_id and self.detector_id == self.memory_id:
            raise ValueError("detector_id and memory_id must remain distinct identifiers.")
        return self


class Observation(BaseModel):
    """Adapter output consumed by planner and verification."""

    observation_id: str
    source: ObservationSource
    viewpoint_id: str
    room_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    visible_objects: list[ObservedObject] = Field(default_factory=list)
    visible_anchors: list[ObservedAnchor] = Field(default_factory=list)
    scene_relations: list[SceneRelation] = Field(default_factory=list)
    raw_ref: str | None = None


class TaskNegativeEvidence(BaseModel):
    """Task-scoped negative evidence used for candidate exclusion."""

    task_request_id: str | None = None
    location_key: str
    status: str = "searched_not_found"
    reason: str = "searched_not_found"
    object_category: str | None = None
    anchor: Anchor | None = None
    evidence: dict[str, Any] | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ObjectMemory(BaseModel):
    """Long-term object memory entry."""

    memory_id: str
    object_category: str
    aliases: list[str] = Field(default_factory=list)
    anchor: Anchor
    relative_relation: RelativeRelation | None = None
    last_observed_state: str | None = None
    evidence_source: EvidenceSource = EvidenceSource.INFERRED_EXPERIENCE
    confidence_level: ConfidenceLevel = ConfidenceLevel.LOW
    last_confirmed_at: datetime | None = None
    description: str | None = None
    belief_state: BeliefState = BeliefState.UNKNOWN


class RobotRuntimeState(BaseModel):
    """Robot status during one task execution."""

    viewpoint_id: str | None = None
    room_id: str | None = None
    holding_object_category: str | None = None
    is_holding_object: bool = False


class VerificationEvidence(BaseModel):
    """Evidence bundle used by verification engine."""

    observation: Observation | None = None
    execution_result: dict[str, Any] | None = None
    robot_runtime_state: RobotRuntimeState | None = None
    task_negative_evidence: list[TaskNegativeEvidence] = Field(default_factory=list)


class SubgoalType(StrEnum):
    """Supported high-level subgoals for deterministic planning."""

    NAVIGATE = "navigate"
    OBSERVE = "observe"
    VERIFY_OBJECT_PRESENCE = "verify_object_presence"
    EMBODIED_MANIPULATION = "embodied_manipulation"
    RETURN_TO_USER = "return_to_user"
    REPORT_FAILURE = "report_failure"
    ASK_CLARIFICATION = "ask_clarification"


class Subgoal(BaseModel):
    """High-level subgoal step emitted by planner."""

    subgoal_id: str
    subgoal_type: SubgoalType
    description: str | None = None
    candidate_id: str | None = None
    target_memory_id: str | None = None
    success_conditions: list[Predicate]

    @model_validator(mode="after")
    def validate_success_conditions(self) -> Subgoal:
        """Every subgoal must have at least one explicit success condition."""
        if not self.success_conditions:
            raise ValueError("subgoal must define at least one success condition.")
        return self


class HighLevelPlan(BaseModel):
    """Planner output with grounding metadata."""

    plan_id: str
    intent: TaskIntent
    subgoals: list[Subgoal] = Field(default_factory=list)
    memory_grounding: list[str] = Field(default_factory=list)
    candidate_grounding: list[str] = Field(default_factory=list)
    notes: str | None = None


class FailureType(StrEnum):
    """Failure taxonomy for failure-aware recovery."""

    NAVIGATION_FAILURE = "navigation_failure"
    OBJECT_PRESENCE_FAILURE = "object_presence_failure"
    MANIPULATION_FAILURE = "manipulation_failure"
    FINAL_TASK_FAILURE = "final_task_failure"


class FailureAnalysis(BaseModel):
    """Structured failure analysis result."""

    failure_type: FailureType
    failed_conditions: list[Predicate] = Field(default_factory=list)
    reason: str | None = None
    selected_candidate_id: str | None = None


class RecoveryAction(StrEnum):
    """Recovery actions supported in Phase A."""

    CONTINUE = "continue"
    RETRY_SAME_SUBGOAL = "retry_same_subgoal"
    SWITCH_CANDIDATE = "switch_candidate"
    RE_OBSERVE = "re_observe"
    REPLAN = "replan"
    ASK_CLARIFICATION = "ask_clarification"
    REPORT_FAILURE = "report_failure"


class RecoveryDecision(BaseModel):
    """Recovery policy decision after a failure analysis."""

    action: RecoveryAction
    reason: str
    next_candidate_id: str | None = None
    allow_revisit: bool = False


class RuntimeState(BaseModel):
    """Single source of truth for current task execution state."""

    current_observation: Observation | None = None
    selected_candidate_id: str | None = None
    selected_object_id: str | None = None
    retry_budget: int = Field(default=0, ge=0)
    recent_failure_analysis: FailureAnalysis | None = None
    task_negative_evidence: list[TaskNegativeEvidence] = Field(default_factory=list)
    candidate_exclusion_state: dict[str, str] = Field(default_factory=dict)
    robot_runtime_state: RobotRuntimeState | None = None


class CapabilitySpec(BaseModel):
    """Stable contract of one adapter-facing capability."""

    name: str = Field(min_length=1)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    failure_modes: list[str] = Field(min_length=1)
    timeout_s: float = Field(gt=0)
    returns_evidence: bool


class TraceEvent(BaseModel):
    """Auditable event written to trace."""

    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    node: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None


__all__ = [
    "Anchor",
    "BeliefState",
    "CapabilitySpec",
    "ConfidenceLevel",
    "EvidenceSource",
    "FailureAnalysis",
    "FailureType",
    "HighLevelPlan",
    "ObjectMemory",
    "Observation",
    "ObservationSource",
    "ObservedAnchor",
    "ObservedObject",
    "ParsedTask",
    "Predicate",
    "RecoveryAction",
    "RecoveryDecision",
    "RelativeRelation",
    "RobotRuntimeState",
    "RuntimeState",
    "SceneRelation",
    "Subgoal",
    "SubgoalType",
    "TargetObject",
    "TaskIntent",
    "TaskNegativeEvidence",
    "TaskRequest",
    "TraceEvent",
    "VerificationEvidence",
]
