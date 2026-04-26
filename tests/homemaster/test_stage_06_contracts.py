from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from homemaster.contracts import (
    EvidenceBundle,
    EvidenceRef,
    FactMemoryWrite,
    MemoryCommitPlan,
    ObjectMemoryUpdate,
    TaskCard,
    TaskRecord,
    TaskSummary,
)


def _task_card() -> TaskCard:
    return TaskCard(
        task_type="fetch_object",
        target="水杯",
        delivery_target="user",
        location_hint="厨房",
        success_criteria=["水杯交付给用户"],
        needs_clarification=False,
        confidence=0.92,
    )


def _evidence_ref() -> EvidenceRef:
    return EvidenceRef(
        evidence_id="verification:task-1:1",
        evidence_type="verification_result",
        source_id="verify-1",
        subtask_id="find_cup",
        memory_id="mem-cup-1",
        location_key="kitchen:anchor_kitchen_table_1",
        created_at="2026-04-26T00:00:00Z",
        summary="观察到水杯在厨房餐桌",
    )


def test_stage_06_contracts_serialize_and_validate() -> None:
    evidence = _evidence_ref()
    summary = TaskSummary(
        result="success",
        confirmed_facts=["观察到水杯在厨房餐桌", "水杯已交付给用户"],
        unconfirmed_facts=[],
        recovery_attempts=[],
        user_reply="已完成",
        failure_summary=None,
        evidence_refs=[evidence.evidence_id],
    )
    task_record = TaskRecord(
        task_id="task-1",
        task_card=_task_card(),
        summary=summary,
        result="success",
        started_at="2026-04-26T00:00:00Z",
        completed_at="2026-04-26T00:01:00Z",
        evidence_refs=[evidence],
        failure_record_ids=[],
    )
    plan = MemoryCommitPlan(
        commit_id="commit-task-1",
        object_memory_updates=[
            ObjectMemoryUpdate(
                memory_id="mem-cup-1",
                update_type="confirm",
                updated_fields={
                    "last_confirmed_at": "2026-04-26T00:01:00Z",
                    "confidence_level": "high",
                    "belief_state": "confirmed",
                },
                evidence_refs=[evidence],
                reason="目标物已被观察验证",
            )
        ],
        fact_memory_writes=[
            FactMemoryWrite(
                fact_id="fact-task-1-object-seen",
                fact_type="object_seen",
                polarity="positive",
                target="水杯",
                location={"room_id": "kitchen", "anchor_id": "anchor_kitchen_table_1"},
                time_scope="task_run",
                confidence=0.9,
                text="本次任务中，水杯在厨房餐桌被观察到。",
                evidence_refs=[evidence],
                stale_after="2026-05-03T00:00:00Z",
            )
        ],
        task_record=task_record,
        negative_evidence=[],
        skipped_candidates=[],
        index_stale_memory_ids=["mem-cup-1"],
    )
    bundle = EvidenceBundle(
        task_id="task-1",
        evidence_refs=[evidence],
        verified_facts=["观察到水杯在厨房餐桌"],
        failure_facts=[],
        system_failures=[],
        negative_evidence=[],
    )

    assert MemoryCommitPlan.model_validate_json(plan.model_dump_json()) == plan
    assert EvidenceBundle.model_validate_json(bundle.model_dump_json()) == bundle
    assert json.loads(summary.model_dump_json())["evidence_refs"] == [evidence.evidence_id]


def test_memory_updates_and_fact_writes_require_evidence_refs() -> None:
    with pytest.raises(ValidationError):
        ObjectMemoryUpdate(
            memory_id="mem-cup-1",
            update_type="confirm",
            updated_fields={"belief_state": "confirmed"},
            evidence_refs=[],
            reason="missing evidence",
        )

    with pytest.raises(ValidationError):
        FactMemoryWrite(
            fact_id="fact-no-evidence",
            fact_type="object_seen",
            polarity="positive",
            target="水杯",
            text="没有证据的事实不应写入",
            evidence_refs=[],
        )


def test_fact_memory_write_searchable_is_fixed_false() -> None:
    write = FactMemoryWrite(
        fact_id="fact-1",
        fact_type="object_seen",
        polarity="positive",
        target="水杯",
        text="本次任务中观察到水杯。",
        evidence_refs=[_evidence_ref()],
    )

    assert write.searchable is False

    with pytest.raises(ValidationError):
        FactMemoryWrite(
            fact_id="fact-2",
            fact_type="object_seen",
            polarity="positive",
            target="水杯",
            text="不允许进入 Stage03 RAG",
            evidence_refs=[_evidence_ref()],
            searchable=True,
        )
