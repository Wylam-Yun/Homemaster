from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from homemaster.contracts import ExecutionState, FailureRecord
from homemaster.memory_commit import build_evidence_bundle, build_memory_commit_plan
from homemaster.runtime import ProviderConfig
from homemaster.stage_06 import (
    build_stage_06_case_inputs,
    run_live_stage_06_summary_memory_case,
)
from homemaster.summary import TaskSummaryGenerationError, generate_task_summary


@pytest.mark.live_api
@pytest.mark.parametrize(
    "case_name",
    [
        "check_medicine_summary_memory_live",
        "fetch_cup_success_fact_memory_live",
        "object_not_found_summary_memory_live",
    ],
)
def test_stage_06_live_summary_memory_cases(case_name: str, tmp_path: Path) -> None:
    if os.getenv("HOMEMASTER_RUN_LIVE_LLM") != "1":
        pytest.skip("set HOMEMASTER_RUN_LIVE_LLM=1 to run real Mimo Stage 06 cases")

    result = run_live_stage_06_summary_memory_case(
        case_name,
        memory_root=tmp_path / "memory",
    )

    assert result.passed is True
    assert result.task_summary.evidence_refs
    assert result.memory_commit_plan.task_record is not None
    assert (result.case_dir / "result.md").is_file()
    assert all(result.checks.values())


def test_stage_06_model_output_invalid_task_record_only(tmp_path: Path) -> None:
    inputs = build_stage_06_case_inputs("fetch_cup_success_fact_memory_live")
    failure = FailureRecord(
        failure_id="failure-model-1",
        failure_type="model_output_invalid",
        failed_reason="Mimo 三次非 JSON",
        created_at="2026-04-26T00:00:00Z",
    )
    bundle = build_evidence_bundle(
        task_id="task-model-failed",
        failure_records=[failure],
        created_at="2026-04-26T00:00:00Z",
    )
    provider = ProviderConfig(
        name="Mimo",
        base_url="https://mimo.example/anthropic",
        model="mimo-v2-pro",
        api_keys=("secret-one",),
        protocol="anthropic",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={"content": [{"type": "text", "text": "不是 JSON"}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as client:
        with pytest.raises(TaskSummaryGenerationError) as exc_info:
            generate_task_summary(
                task_card=inputs.planning_context.task_card,
                execution_state=ExecutionState(
                    task_status="failed",
                    failure_record_ids=[failure.failure_id],
                ),
                evidence_bundle=bundle,
                provider=provider,
                client=client,
            )

    assert len(exc_info.value.attempts) == 3
    assert "上一次输出没有通过 TaskSummary 校验" in exc_info.value.attempts[1]["prompt"]

    commit = build_memory_commit_plan(
        task_id="task-model-failed",
        task_card=inputs.planning_context.task_card,
        planning_context=inputs.planning_context,
        orchestration_plan=inputs.orchestration_plan,
        execution_state=ExecutionState(
            task_status="failed",
            failure_record_ids=[failure.failure_id],
        ),
        evidence_bundle=bundle,
        task_summary=None,
        completed_at="2026-04-26T00:01:00Z",
    )

    assert commit.object_memory_updates == []
    assert commit.fact_memory_writes == []
    assert commit.task_record is not None
    assert commit.task_record.failure_record_ids == [failure.failure_id]


def test_stage_06_case_inputs_are_serializable() -> None:
    inputs = build_stage_06_case_inputs("fetch_cup_success_fact_memory_live")

    encoded = json.dumps(
        {
            "planning_context": inputs.planning_context.model_dump(mode="json"),
            "orchestration_plan": inputs.orchestration_plan.model_dump(mode="json"),
            "execution_state": inputs.execution_state.model_dump(mode="json"),
        },
        ensure_ascii=False,
    )

    assert "水杯" in encoded
