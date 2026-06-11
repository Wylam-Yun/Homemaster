from __future__ import annotations

import json
from pathlib import Path

from homemaster.benchmarking.alfworld.tracing import AlfworldTraceWriter


def test_trace_writer_writes_jsonl_and_redacts_secret_keys(tmp_path: Path) -> None:
    writer = AlfworldTraceWriter(tmp_path / "episode-1")
    writer.write_event({
        "event": "tool_step",
        "observation": "You see apple 1.",
        "api_key": "secret",
        "nested": {"auth_token": "secret", "safe": "ok"},
    })

    payload = json.loads((tmp_path / "episode-1" / "trace.jsonl").read_text().strip())

    assert payload["event"] == "tool_step"
    assert payload["api_key"] == "[REDACTED]"
    assert payload["nested"]["auth_token"] == "[REDACTED]"
    assert payload["nested"]["safe"] == "ok"


def test_trace_writer_writes_model_trace_and_readable_trajectory(tmp_path: Path) -> None:
    writer = AlfworldTraceWriter(tmp_path / "episode-1")
    writer.write_model_event({
        "event_type": "episode_started",
        "state": {"task": "Your task is to: move apple.", "api_key": "secret"},
    })
    writer.write_model_event({
        "event_type": "assistant_message",
        "elapsed_ms": 12.3,
        "message": {
            "content": [{"text": "I will navigate."}],
            "reasoning_content": "short provider reasoning",
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "robot_navigate",
                    "arguments": {"target_receptacle": "countertop 1"},
                }
            ],
        },
        "usage": {"input_tokens": 10},
    })
    writer.write_event({
        "step_index": 1,
        "tool_name": "robot_navigate",
        "translated_command": "go to countertop 1",
        "feedback": "You arrive.",
        "reward": 0.0,
        "won": False,
        "task": "Your task is to: move apple.",
    })
    summary = {
        "episode_id": "episode-id",
        "goal_condition_success_rate": 0.0,
        "invalid_actions": 0,
        "run_id": "run-1",
        "runtime_status": "replied",
        "steps": 1,
        "success": False,
    }

    writer.write_summary(summary)
    writer.write_readable_trajectory(summary)

    model_payload = json.loads(writer.model_trace_path.read_text(encoding="utf-8").splitlines()[0])
    assert model_payload["state"]["api_key"] == "[REDACTED]"
    readable = writer.readable_path.read_text(encoding="utf-8")
    assert "I will navigate." in readable
    assert "short provider reasoning" in readable
    assert "go to countertop 1" in readable
