from __future__ import annotations

import pytest

from homemaster.benchmarking.alfworld.trajectory_memory import AlfworldTrajectoryRecord
from homemaster.experience.alfworld_compiler import compile_alfworld_trajectory


def _record(outcome="success", **updates):
    payload = {
        "trajectory_id": "traj-1",
        "source_session_id": "session-1",
        "run_id": "run-1",
        "episode_id": "episode-1",
        "task": "put mug in cabinet",
        "goal_type": "pick_and_place_simple",
        "outcome": outcome,
        "classification": "agent_success" if outcome == "success" else "runtime_failure",
        "failure_reason": None if outcome == "success" else "runtime_failure",
        "source_trace_path": "/private/trace.jsonl",
        "source_trace_sha256": "a" * 64,
        "final_environment_state": {"won": outcome == "success", "done": True},
        "steps": [
        {
            "index": 0,
            "payload": {
                "action": "navigate",
                "target": "cabinet 1",
                "expect": {"visible_text": "cabinet reached"},
            },
        },
        {
            "index": 1,
            "payload": {
                "action": "put",
                "object": "mug",
                "receptacle": "cabinet",
                "expect": {"visible_text": "mug placed"},
            },
        },
        ],
    }
    payload.update(updates)
    return AlfworldTrajectoryRecord.model_validate(payload)


def test_success_compiles_to_executable_safe_procedure():
    result = compile_alfworld_trajectory(_record(), source_trace_sha256="a" * 64)
    assert result.is_executable is True
    assert result.procedure is not None
    assert [step.action for step in result.procedure.steps] == ["navigate", "put"]
    assert result.procedure.success.all_of == ("environment.won == true",)
    assert "/private" not in result.model_dump_json()


def test_failure_compiles_only_to_diagnostic():
    result = compile_alfworld_trajectory(_record("failure"), source_trace_sha256="a" * 64)
    assert result.is_executable is False
    assert result.procedure is None
    assert result.diagnostic is not None


def test_hash_tampering_and_empty_success_fail_closed():
    with pytest.raises(ValueError, match="hash mismatch"):
        compile_alfworld_trajectory(_record(), source_trace_sha256="b" * 64)
    with pytest.raises(ValueError, match="no compilable steps"):
        compile_alfworld_trajectory(_record(steps=()), source_trace_sha256="a" * 64)


def test_internal_target_fields_are_not_compiled():
    with pytest.raises(ValueError, match="unsupported"):
        compile_alfworld_trajectory(
            _record(steps=[{"index": 0, "payload": {"action": "put", "target": "objectId=Mug|1"}}]),
            source_trace_sha256="a" * 64,
        )
