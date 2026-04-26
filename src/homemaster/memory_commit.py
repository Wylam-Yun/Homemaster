"""Evidence bundling and memory commit planning for Stage 06."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from homemaster.contracts import (
    EvidenceBundle,
    EvidenceRef,
    ExecutionState,
    FactMemoryWrite,
    FailureRecord,
    MemoryCommitPlan,
    ModuleExecutionResult,
    ObjectMemoryUpdate,
    OrchestrationPlan,
    PlanningContext,
    TaskCard,
    TaskRecord,
    TaskSummary,
    VerificationResult,
)

SYSTEM_FAILURE_TYPES = {"model_output_invalid"}


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def stale_after(created_at: str, *, days: int = 7) -> str:
    try:
        base = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        base = datetime.now(tz=UTC)
    return (base + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_evidence_bundle(
    *,
    task_id: str,
    verification_results: list[VerificationResult] | None = None,
    skill_results: list[ModuleExecutionResult] | None = None,
    failure_records: list[FailureRecord] | None = None,
    trace_events: list[dict[str, Any]] | None = None,
    created_at: str | None = None,
) -> EvidenceBundle:
    """Create stable evidence references from Stage 05 outputs."""

    created = created_at or utc_now_iso()
    refs: list[EvidenceRef] = []
    verified_facts: list[str] = []
    failure_facts: list[str] = []
    system_failures: list[str] = []
    negative_evidence: list[dict[str, Any]] = []

    for index, verification in enumerate(verification_results or [], start=1):
        facts = list(verification.verified_facts)
        verified_facts.extend(facts)
        if not verification.passed and verification.failed_reason:
            failure_facts.append(verification.failed_reason)
        refs.append(
            EvidenceRef(
                evidence_id=f"verification:{task_id}:{index}",
                evidence_type="verification_result",
                source_id=f"verification-{index}",
                created_at=created,
                summary=_summary_for_verification(verification),
            )
        )

    for index, result in enumerate(skill_results or [], start=1):
        refs.append(
            EvidenceRef(
                evidence_id=f"skill_result:{task_id}:{index}:{result.skill}",
                evidence_type="skill_result",
                source_id=f"skill-{index}",
                created_at=created,
                summary=f"{result.skill} {result.status}",
            )
        )
        if result.observation:
            refs.append(
                EvidenceRef(
                    evidence_id=f"observation:{task_id}:{index}",
                    evidence_type="observation",
                    source_id=f"skill-{index}",
                    created_at=created,
                    summary=_summary_for_observation(result.observation),
                )
            )

    for failure in failure_records or []:
        if failure.failure_type in SYSTEM_FAILURE_TYPES:
            system_failures.append(failure.failed_reason)
        else:
            failure_facts.append(failure.failed_reason)
        refs.append(
            EvidenceRef(
                evidence_id=f"failure:{failure.failure_id}",
                evidence_type="failure_record",
                source_id=failure.failure_id,
                subtask_id=failure.subtask_id,
                created_at=failure.created_at or created,
                summary=failure.failed_reason,
            )
        )
        for item in failure.negative_evidence:
            enriched = _enrich_negative_evidence(
                item,
                failure_record_id=failure.failure_id,
                created_at=failure.created_at or created,
            )
            negative_evidence.append(enriched)

    for index, event in enumerate(trace_events or [], start=1):
        source_id = str(event.get("event_id") or f"trace-{index}")
        refs.append(
            EvidenceRef(
                evidence_id=f"trace_event:{source_id}",
                evidence_type="trace_event",
                source_id=source_id,
                created_at=created,
                summary=str(event.get("summary") or event.get("event") or "trace event"),
            )
        )

    return EvidenceBundle(
        task_id=task_id,
        evidence_refs=refs,
        verified_facts=verified_facts,
        failure_facts=failure_facts,
        system_failures=system_failures,
        negative_evidence=negative_evidence,
    )


def build_memory_commit_plan(
    *,
    task_id: str,
    task_card: TaskCard,
    planning_context: PlanningContext,
    orchestration_plan: OrchestrationPlan,
    execution_state: ExecutionState,
    evidence_bundle: EvidenceBundle,
    task_summary: TaskSummary | None,
    completed_at: str | None = None,
    started_at: str | None = None,
) -> MemoryCommitPlan:
    """Generate a Stage 06 commit plan using hard evidence only."""

    completed = completed_at or utc_now_iso()
    selected_target = planning_context.selected_target
    failure_refs = [
        ref for ref in evidence_bundle.evidence_refs if ref.evidence_type == "failure_record"
    ]
    verification_refs = [
        ref for ref in evidence_bundle.evidence_refs if ref.evidence_type == "verification_result"
    ]
    selected_ref = _selected_target_evidence_ref(planning_context, created_at=completed)
    evidence_refs = list(evidence_bundle.evidence_refs)
    if selected_ref:
        evidence_refs.append(selected_ref)

    has_system_failure = bool(evidence_bundle.system_failures)
    has_verified_success = (
        execution_state.task_status == "completed"
        or any(ref for ref in verification_refs)
        and bool(evidence_bundle.verified_facts)
    )
    object_updates: list[ObjectMemoryUpdate] = []
    fact_writes: list[FactMemoryWrite] = []
    stale_ids: list[str] = []

    if selected_target and has_verified_success and not has_system_failure:
        refs = _refs_for_memory_write(evidence_refs, fallback=evidence_refs)
        object_updates.append(
            ObjectMemoryUpdate(
                memory_id=selected_target.memory_id,
                update_type="confirm",
                updated_fields={
                    "last_confirmed_at": completed,
                    "confidence_level": "high",
                    "belief_state": "confirmed",
                },
                evidence_refs=refs,
                reason="目标物已由本次任务证据确认",
            )
        )
        stale_ids.append(selected_target.memory_id)
        fact_writes.extend(
            _success_fact_writes(
                task_id=task_id,
                task_card=task_card,
                planning_context=planning_context,
                evidence_refs=refs,
                completed_at=completed,
            )
        )

    if selected_target and not has_verified_success and not has_system_failure:
        if _has_object_not_seen_evidence(evidence_bundle):
            refs = _refs_for_memory_write(failure_refs, fallback=evidence_refs)
            object_updates.append(
                ObjectMemoryUpdate(
                    memory_id=selected_target.memory_id,
                    update_type="mark_stale",
                    updated_fields={"belief_state": "stale"},
                    evidence_refs=refs,
                    reason="本次任务在对应位置未观察到目标物",
                )
            )
            stale_ids.append(selected_target.memory_id)
            fact_writes.append(
                _object_not_seen_fact(
                    task_id=task_id,
                    task_card=task_card,
                    planning_context=planning_context,
                    evidence_refs=refs,
                    completed_at=completed,
                )
            )
        elif _has_operation_failure(evidence_bundle):
            refs = _refs_for_memory_write(failure_refs, fallback=evidence_refs)
            fact_writes.append(
                FactMemoryWrite(
                    fact_id=f"fact:{task_id}:operation-failed",
                    fact_type="operation_failed",
                    polarity="negative",
                    target=task_card.target,
                    location=_location_from_context(planning_context),
                    time_scope="task_run",
                    confidence=0.8,
                    text=f"本次任务中，{task_card.target}相关操作失败，未更新物体位置。",
                    evidence_refs=refs,
                    stale_after=stale_after(completed),
                )
            )

    negative_evidence = _negative_evidence_with_defaults(
        evidence_bundle.negative_evidence,
        created_at=completed,
    )
    result = _task_result(execution_state, has_system_failure)
    summary = task_summary or _default_summary(
        result=result,
        evidence_bundle=evidence_bundle,
        evidence_refs=evidence_refs,
    )
    task_record = TaskRecord(
        task_id=task_id,
        task_card=task_card,
        summary=summary,
        result=result,
        started_at=started_at,
        completed_at=completed,
        evidence_refs=evidence_refs,
        failure_record_ids=list(execution_state.failure_record_ids)
        or [ref.source_id for ref in failure_refs],
    )

    return MemoryCommitPlan(
        commit_id=f"commit:{task_id}",
        object_memory_updates=object_updates,
        fact_memory_writes=fact_writes,
        task_record=task_record,
        negative_evidence=negative_evidence,
        skipped_candidates=_skipped_candidates(evidence_bundle, object_updates, fact_writes),
        index_stale_memory_ids=sorted(set(stale_ids)),
        skipped=False,
    )


def _summary_for_verification(verification: VerificationResult) -> str:
    if verification.passed and verification.verified_facts:
        return "; ".join(verification.verified_facts[:2])
    if verification.failed_reason:
        return verification.failed_reason
    if verification.missing_evidence:
        return "; ".join(verification.missing_evidence[:2])
    return f"verification passed={verification.passed}"


def _summary_for_observation(observation: dict[str, Any]) -> str:
    if observation.get("target_object_visible") is True:
        location = observation.get("target_object_location") or observation.get("current_location")
        return f"observation: target visible at {location or 'unknown location'}"
    if observation.get("target_object_visible") is False:
        return "observation: target not visible"
    if observation.get("delivered_object"):
        return f"observation: delivered {observation.get('delivered_object')}"
    return "observation captured"


def _enrich_negative_evidence(
    item: dict[str, Any],
    *,
    failure_record_id: str,
    created_at: str,
) -> dict[str, Any]:
    enriched = dict(item)
    enriched.setdefault("failure_record_id", failure_record_id)
    enriched.setdefault("created_at", created_at)
    enriched.setdefault("stale_after", stale_after(created_at))
    return enriched


def _negative_evidence_with_defaults(
    items: list[dict[str, Any]],
    *,
    created_at: str,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        enriched = dict(item)
        enriched.setdefault("failure_record_id", f"unknown-failure-{index}")
        enriched.setdefault("created_at", created_at)
        enriched.setdefault("stale_after", stale_after(created_at))
        normalized.append(enriched)
    return normalized


def _selected_target_evidence_ref(
    context: PlanningContext,
    *,
    created_at: str,
) -> EvidenceRef | None:
    target = context.selected_target
    if target is None:
        return None
    return EvidenceRef(
        evidence_id=f"selected_target:{target.memory_id}",
        evidence_type="selected_target",
        source_id=target.memory_id,
        memory_id=target.memory_id,
        location_key=f"{target.room_id}:{target.anchor_id}",
        created_at=created_at,
        summary=f"Stage04 selected reliable target {target.memory_id}",
    )


def _refs_for_memory_write(
    refs: list[EvidenceRef],
    *,
    fallback: list[EvidenceRef],
) -> list[EvidenceRef]:
    return refs or fallback[:1]


def _success_fact_writes(
    *,
    task_id: str,
    task_card: TaskCard,
    planning_context: PlanningContext,
    evidence_refs: list[EvidenceRef],
    completed_at: str,
) -> list[FactMemoryWrite]:
    location = _location_from_context(planning_context)
    writes = [
        FactMemoryWrite(
            fact_id=f"fact:{task_id}:object-seen",
            fact_type="object_seen",
            polarity="positive",
            target=task_card.target,
            location=location,
            time_scope="task_run",
            confidence=0.9,
            text=f"本次任务中，{task_card.target}在{_display_location(location)}被观察到。",
            evidence_refs=evidence_refs,
            stale_after=stale_after(completed_at),
        )
    ]
    if task_card.task_type == "fetch_object" and task_card.delivery_target:
        writes.append(
            FactMemoryWrite(
                fact_id=f"fact:{task_id}:delivery-verified",
                fact_type="delivery_verified",
                polarity="positive",
                target=task_card.target,
                location={},
                time_scope="task_run",
                confidence=0.85,
                text=f"本次任务中，{task_card.target}已交付给{task_card.delivery_target}。",
                evidence_refs=evidence_refs,
                stale_after=stale_after(completed_at),
            )
        )
    return writes


def _object_not_seen_fact(
    *,
    task_id: str,
    task_card: TaskCard,
    planning_context: PlanningContext,
    evidence_refs: list[EvidenceRef],
    completed_at: str,
) -> FactMemoryWrite:
    location = _location_from_context(planning_context)
    return FactMemoryWrite(
        fact_id=f"fact:{task_id}:object-not-seen",
        fact_type="object_not_seen",
        polarity="negative",
        target=task_card.target,
        location=location,
        time_scope="task_run",
        confidence=0.8,
        text=f"本次任务中，在{_display_location(location)}没有观察到{task_card.target}。",
        evidence_refs=evidence_refs,
        stale_after=stale_after(completed_at),
    )


def _location_from_context(context: PlanningContext) -> dict[str, Any]:
    target = context.selected_target
    if target is None:
        return {}
    return {
        "memory_id": target.memory_id,
        "room_id": target.room_id,
        "anchor_id": target.anchor_id,
        "viewpoint_id": target.viewpoint_id,
        "display_text": target.display_text,
    }


def _display_location(location: dict[str, Any]) -> str:
    return str(
        location.get("display_text")
        or location.get("anchor_id")
        or location.get("room_id")
        or "本次观察位置"
    )


def _has_object_not_seen_evidence(bundle: EvidenceBundle) -> bool:
    if bundle.negative_evidence:
        return True
    joined = " ".join(bundle.failure_facts)
    return any(term in joined for term in ("没有观察", "未观察", "未找到", "找不到", "not visible"))


def _has_operation_failure(bundle: EvidenceBundle) -> bool:
    joined = " ".join(bundle.failure_facts)
    return any(term in joined for term in ("拿", "取", "抓", "放", "交付", "operation", "验证"))


def _task_result(
    state: ExecutionState,
    has_system_failure: bool,
) -> Literal["success", "failed", "needs_user"]:
    if state.task_status == "completed" and not has_system_failure:
        return "success"
    if state.task_status == "needs_user_input":
        return "needs_user"
    return "failed"


def _default_summary(
    *,
    result: str,
    evidence_bundle: EvidenceBundle,
    evidence_refs: list[EvidenceRef],
) -> TaskSummary:
    return TaskSummary(
        result=result,  # type: ignore[arg-type]
        confirmed_facts=list(evidence_bundle.verified_facts),
        unconfirmed_facts=list(evidence_bundle.failure_facts),
        recovery_attempts=[],
        user_reply=None,
        failure_summary="; ".join(evidence_bundle.failure_facts) or None,
        evidence_refs=[ref.evidence_id for ref in evidence_refs],
    )


def _skipped_candidates(
    bundle: EvidenceBundle,
    updates: list[ObjectMemoryUpdate],
    facts: list[FactMemoryWrite],
) -> list[dict[str, Any]]:
    if updates or facts:
        return []
    if bundle.system_failures:
        return [
            {
                "reason": "system_failure_not_environment_fact",
                "system_failures": list(bundle.system_failures),
            }
        ]
    if not bundle.evidence_refs:
        return [{"reason": "no_evidence_refs"}]
    return []
