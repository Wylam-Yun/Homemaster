from __future__ import annotations

import json

from homemaster.contracts import (
    FailureRecord,
    ModuleExecutionResult,
    VerificationResult,
)
from homemaster.memory_commit import build_evidence_bundle


def test_evidence_bundle_generates_stable_evidence_refs() -> None:
    verification = VerificationResult(
        scope="subtask",
        passed=True,
        verified_facts=["观察到水杯在厨房餐桌"],
        confidence=0.93,
    )
    skill_result = ModuleExecutionResult(
        skill="navigation",
        status="success",
        observation={
            "target_object_visible": True,
            "target_object_location": "厨房餐桌",
        },
    )
    failure = FailureRecord(
        failure_id="failure-1",
        subtask_id="pick_cup",
        subtask_intent="拿起水杯",
        skill="operation",
        failure_type="verification_failed",
        failed_reason="未确认水杯被拿起",
        negative_evidence=[{"memory_id": "mem-cup-1", "reason": "not_verified"}],
        created_at="2026-04-26T00:00:00Z",
    )

    bundle_one = build_evidence_bundle(
        task_id="task-1",
        verification_results=[verification],
        skill_results=[skill_result],
        failure_records=[failure],
        trace_events=[{"event_id": "trace-1", "summary": "stage05 finished"}],
        created_at="2026-04-26T00:00:00Z",
    )
    bundle_two = build_evidence_bundle(
        task_id="task-1",
        verification_results=[verification],
        skill_results=[skill_result],
        failure_records=[failure],
        trace_events=[{"event_id": "trace-1", "summary": "stage05 finished"}],
        created_at="2026-04-26T00:00:00Z",
    )

    assert bundle_one == bundle_two
    evidence_ids = [ref.evidence_id for ref in bundle_one.evidence_refs]
    assert "verification:task-1:1" in evidence_ids
    assert "skill_result:task-1:1:navigation" in evidence_ids
    assert "failure:failure-1" in evidence_ids
    assert "trace_event:trace-1" in evidence_ids
    assert bundle_one.verified_facts == ["观察到水杯在厨房餐桌"]
    assert bundle_one.failure_facts == ["未确认水杯被拿起"]
    assert bundle_one.negative_evidence[0]["failure_record_id"] == "failure-1"
    assert bundle_one.negative_evidence[0]["stale_after"]


def test_evidence_bundle_summaries_do_not_contain_secret_markers() -> None:
    skill_result = ModuleExecutionResult(
        skill="navigation",
        status="failed",
        observation={
            "Authorization": "Bearer should-not-leak",
            "api_keys": ["sk-should-not-leak"],
            "visible_objects": ["水杯"],
        },
    )

    bundle = build_evidence_bundle(
        task_id="task-secret",
        skill_results=[skill_result],
        created_at="2026-04-26T00:00:00Z",
    )
    encoded = json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False)

    assert "Bearer should-not-leak" not in encoded
    assert "sk-should-not-leak" not in encoded
    assert "Authorization" not in encoded
    assert "api_keys" not in encoded
