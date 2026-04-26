from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from homemaster.contracts import (
    ExecutionState,
    FailureRecord,
    RecoveryDecision,
    VerificationResult,
)
from homemaster.recovery import (
    RecoveryDecisionGenerationError,
    build_recovery_prompt,
    generate_recovery_decision,
)
from homemaster.runtime import load_provider_config


def _config_path(tmp_path: Path) -> Path:
    path = tmp_path / "api_config.json"
    path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "name": "Mimo",
                        "base_url": "https://mimo.example/anthropic",
                        "model": "mimo-v2-pro",
                        "api_keys": ["secret-one"],
                        "protocol": "anthropic",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def _failure() -> FailureRecord:
    return FailureRecord(
        failure_id="failure-1",
        subtask_id="find_cup",
        subtask_intent="找到水杯",
        skill="navigation",
        failure_type="verification_failed",
        failed_reason="厨房桌子旁没有看到水杯",
        verification_result=VerificationResult(
            scope="subtask",
            passed=False,
            failed_reason="没有看到水杯",
            confidence=0.8,
        ),
        negative_evidence=[{"memory_id": "mem-cup-1", "reason": "not_visible"}],
        retry_count=1,
    )


def test_recovery_prompt_contains_failure_records_and_negative_evidence() -> None:
    prompt = build_recovery_prompt(
        ExecutionState(
            negative_evidence=[{"memory_id": "mem-cup-1", "reason": "not_visible"}]
        ),
        [_failure()],
    )

    assert "FailureRecord" in prompt
    assert "mem-cup-1" in prompt
    assert "retrieve_again" in prompt
    assert "switch_target" not in prompt
    assert "next_target_id" not in prompt


def test_generate_recovery_decision_accepts_retrieve_again(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "action": "retrieve_again",
                                "reason": "当前记忆位置已经验证失败，需要带负证据重新检索",
                                "failure_record_ids": ["failure-1"],
                                "should_retrieve_again": True,
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
            },
        )

    provider = load_provider_config(_config_path(tmp_path), provider_name="Mimo")
    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as client:
        result = generate_recovery_decision(
            ExecutionState(),
            [_failure()],
            provider,
            client=client,
        )

    assert isinstance(result.decision, RecoveryDecision)
    assert result.decision.action == "retrieve_again"
    assert result.decision.should_retrieve_again is True


def test_generate_recovery_decision_rejects_switch_target(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"action": "switch_target", "next_target_id": "mem-cup-2"},
                            ensure_ascii=False,
                        ),
                    }
                ]
            },
        )

    provider = load_provider_config(_config_path(tmp_path), provider_name="Mimo")
    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as client:
        with pytest.raises(RecoveryDecisionGenerationError) as exc_info:
            generate_recovery_decision(
                ExecutionState(),
                [_failure()],
                provider,
                client=client,
            )

    assert exc_info.value.error_type == "recovery_schema_error"
