from __future__ import annotations

import json
from pathlib import Path

from homemaster.agent.messages import ContentBlock, ToolResultMessage
from homemaster.agent.session import AgentSession
from homemaster.benchmarking.alfworld.tracing import (
    AlfworldTraceWriter,
    split_trace_bucket,
    write_readable_trajectories,
)


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


def test_model_trace_redacts_and_trajectory_is_written(tmp_path: Path) -> None:
    writer = AlfworldTraceWriter(tmp_path / "run-1" / "episode-0001")
    writer.write_model_event({
        "event": "episode_started",
        "state": {
            "task": "put apple on table",
            "observation": "You see a room.",
            "frame_path": "frames/frame-0000.png",
        },
    })
    writer.write_model_event({
        "event": "model.assistant_completed",
        "payload": {
            "api_key": "secret",
            "finish_reason": "tool_calls",
            "text": "",
            "tool_calls": [{"name": "robot_inspect_view", "arguments": {"focus": "table"}}],
            "usage": {"input_tokens": 1, "output_tokens": 2},
        },
    })
    writer.write_event({
        "tool_name": "robot_navigate",
        "tool_args": {"target_receptacle": "table 1"},
        "translated_command": "go to table 1",
        "feedback": "You see apple 1.",
        "reward": 0.0,
        "done": False,
        "won": False,
        "step_index": 1,
        "invalid_action_count": 0,
        "frame_path": "frames/frame-0001.png",
    })
    summary = {
        "episode_id": "episode-1",
        "failure_reason": None,
        "goal_condition_success_rate": 0.0,
        "invalid_actions": 0,
        "run_id": "run-1-0001",
        "runtime_status": "replied",
        "steps": 1,
        "success": False,
    }

    writer.write_summary(summary)
    writer.write_trajectory(summary)
    aggregate = write_readable_trajectories(tmp_path / "run-1")

    model_trace = writer.model_trace_path.read_text(encoding="utf-8")
    assert "secret" not in model_trace
    assert "[REDACTED]" in model_trace
    assert '"input_tokens": 1' in model_trace
    assert '"output_tokens": 2' in model_trace
    trajectory = writer.trajectory_path.read_text(encoding="utf-8")
    assert "robot_navigate" in trajectory
    assert "frames/frame-0001.png" in trajectory
    assert "put apple on table" in aggregate.read_text(encoding="utf-8")


def test_model_trace_tool_result_records_only_model_visible_content(tmp_path: Path) -> None:
    writer = AlfworldTraceWriter(tmp_path / "episode-1")
    session = AgentSession(session_id="s1")
    session.append(
        ToolResultMessage(
            tool_call_id="call-1",
            name="robot_manipulate",
            content=[
                ContentBlock(text='{"error": "action_failed", "success": false}'),
                ContentBlock(
                    type="image",
                    source={"type": "base64", "media_type": "image/png", "data": "x"},
                    metadata={"path": "frames/frame-0001.png"},
                ),
            ],
            is_error=True,
            data={
                "observation": "debug text should stay out of model_trace",
                "goal_condition_success_rate": 0.5,
            },
        )
    )

    writer.write_session_messages(session)

    payload = json.loads(writer.model_trace_path.read_text(encoding="utf-8"))
    assert payload["event"] == "tool_result"
    assert "data" not in payload
    model_trace = writer.model_trace_path.read_text(encoding="utf-8")
    assert "debug text should stay out of model_trace" not in model_trace
    assert "goal_condition_success_rate" not in model_trace
    assert "action_failed" in model_trace
    assert "frames/frame-0001.png" in model_trace


def test_split_trace_bucket_mapping() -> None:
    assert split_trace_bucket("train") == "train"
    assert split_trace_bucket("valid_seen") == "valid"
    assert split_trace_bucket("valid_unseen") == "test"
