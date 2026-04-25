from __future__ import annotations

import json
from pathlib import Path

import httpx

from homemaster.pipeline import build_stage_01_task_card_prompt, run_stage_01_contract_smoke


def test_stage_01_prompt_contains_task_card_contract() -> None:
    prompt = build_stage_01_task_card_prompt("去桌子那边看看药盒是不是还在。")

    assert "TaskCard schema" in prompt
    assert '"task_type": "check_presence | fetch_object | unknown"' in prompt
    assert '{"utterance": "去桌子那边看看药盒是不是还在。"}' in prompt
    assert "只输出 JSON object" in prompt


def test_stage_01_contract_smoke_writes_debug_assets_with_mock_llm(tmp_path: Path) -> None:
    config_path = tmp_path / "api_config.json"
    case_dir = tmp_path / "case"
    results_dir = tmp_path / "results"
    config_path.write_text(
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

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "task_type": "check_presence",
                                "target": "药盒",
                                "delivery_target": None,
                                "location_hint": "桌子那边",
                                "success_criteria": ["确认药盒是否还在桌子附近"],
                                "needs_clarification": False,
                                "clarification_question": None,
                                "confidence": 0.9,
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as http_client:
        result = run_stage_01_contract_smoke(
            config_path=config_path,
            case_dir=case_dir,
            results_dir=results_dir,
            client=http_client,
        )

    assert result.passed is True
    assert (case_dir / "input.json").is_file()
    assert (case_dir / "expected.json").is_file()
    assert (case_dir / "actual.json").is_file()
    assert (case_dir / "result.md").is_file()
    assert (results_dir / "llm_samples.jsonl").is_file()

    actual_text = (case_dir / "actual.json").read_text(encoding="utf-8")
    result_text = (case_dir / "result.md").read_text(encoding="utf-8")
    assert "secret-one" not in actual_text
    assert "mimo-v2-pro" in actual_text
    assert "## Full Prompt Sent To Mimo" in result_text
    assert "## Mimo Raw Response" in result_text
    assert "去桌子那边看看药盒是不是还在。" in result_text
    assert '"task_type": "check_presence"' in result_text
