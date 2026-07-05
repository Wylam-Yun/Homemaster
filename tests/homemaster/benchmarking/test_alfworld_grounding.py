from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from homemaster.benchmarking.alfworld.grounding import (
    GroundingCandidate,
    build_grounding_candidates,
    canonical_command_name,
    ground_text,
    normalized_key,
)
from homemaster.benchmarking.alfworld.types import AlfworldEnvState, Subtask


def test_canonical_command_name_flattens_alfworld_camel_case() -> None:
    assert canonical_command_name("RemoteControl") == "remotecontrol"
    assert canonical_command_name("FloorLamp") == "floorlamp"
    assert normalized_key("Floor Lamp 1", drop_instance=True) == "floorlamp"


def test_ground_text_prefers_unique_visible_instance_over_type_name() -> None:
    state = AlfworldEnvState(
        episode_id="game-1",
        task="look at remotecontrol",
        observation="On the sofa 1, you see a remotecontrol 1, and a box 1.",
        inventory=None,
        last_command=None,
        last_feedback=None,
        reward=0.0,
        done=False,
        won=False,
        goal_condition_success_rate=0.0,
        frame_path=None,
        step_index=0,
        invalid_action_count=0,
        admissible_commands=("take remotecontrol 1 from sof1",),
    )
    subtask = Subtask(
        goal_type="look_at_obj_in_light",
        object="RemoteControl",
        toggle="FloorLamp",
    )

    candidates = build_grounding_candidates(state=state, subtask=subtask)
    assert all(candidate.source != "admissible" for candidate in candidates)
    result = ground_text(
        "remote control",
        candidates=candidates,
        allowed_kinds={"object"},
    )

    assert result.value == "remotecontrol 1"
    assert result.method == "unique_instance"


def test_ground_text_ignores_corrupt_admissible_candidate_for_visible_receptacle() -> None:
    state = AlfworldEnvState(
        episode_id="game-1",
        task="You are in the middle of a room. Looking quickly around you, you see a sofa 1.",
        observation="Nothing happens.",
        inventory=None,
        last_command=None,
        last_feedback=None,
        reward=0.0,
        done=False,
        won=False,
        goal_condition_success_rate=0.0,
        frame_path=None,
        step_index=0,
        invalid_action_count=0,
        admissible_commands=("go to sof1",),
    )

    candidates = build_grounding_candidates(state=state)
    result = ground_text("sofa", candidates=candidates, allowed_kinds={"receptacle"})

    assert result.value == "sofa 1"
    assert result.method == "unique_instance"


def test_ground_text_does_not_guess_when_instances_are_ambiguous() -> None:
    candidates = [
        GroundingCandidate("pillow 1", "object"),
        GroundingCandidate("pillow 2", "object"),
    ]

    result = ground_text("pillow", candidates=candidates, allowed_kinds={"object"})

    assert result.value == "pillow"
    assert result.method == "unchanged"


def test_explicit_instance_is_not_replaced_by_subtask_type_only_object() -> None:
    subtask = Subtask(
        goal_type="pick_and_place_simple",
        object="RemoteControl",
        parent="Sofa",
    )
    state = SimpleNamespace(admissible_commands=(), observation="", last_feedback="", inventory="")

    candidates = build_grounding_candidates(state=state, subtask=subtask)
    result = ground_text(
        "remote control 1",
        candidates=candidates,
        allowed_kinds={"object"},
    )

    assert result.value == "remote control 1"
    assert result.method == "unchanged"


def test_explicit_instance_can_match_exact_visible_instance() -> None:
    state = AlfworldEnvState(
        episode_id="game-1",
        task="look at remotecontrol",
        observation="On the sofa 1, you see a remotecontrol 1.",
        inventory=None,
        last_command=None,
        last_feedback=None,
        reward=0.0,
        done=False,
        won=False,
        goal_condition_success_rate=0.0,
        frame_path=None,
        step_index=0,
        invalid_action_count=0,
        admissible_commands=(),
    )
    subtask = Subtask(
        goal_type="look_at_obj_in_light",
        object="RemoteControl",
        toggle="FloorLamp",
    )

    candidates = build_grounding_candidates(state=state, subtask=subtask)
    result = ground_text(
        "remote control 1",
        candidates=candidates,
        allowed_kinds={"object"},
    )

    assert result.value == "remotecontrol 1"
    assert result.method == "exact"


def test_floor_lamp_phrase_matches_current_toggle_target() -> None:
    state = SimpleNamespace(admissible_commands=(), observation="", last_feedback="", inventory="")
    subtask = Subtask(
        goal_type="look_at_obj_in_light",
        object="RemoteControl",
        toggle="FloorLamp",
    )

    candidates = build_grounding_candidates(state=state, subtask=subtask)
    result = ground_text(
        "floor lamp 1",
        candidates=candidates,
        allowed_kinds={"toggle"},
    )

    assert result.value == "floorlamp"
    assert result.method == "normalized"


def test_semantic_judge_checks_yes_no_without_candidate_selection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "semantic_judge.yaml"
    config_path.write_text(
        "\n".join([
            "semantic_judge:",
            "  base_url: https://judge.example/v1",
            "  api_key: test-key",
            "  model: agnes-2.0-flash",
            "  timeout_s: 1",
        ]),
        encoding="utf-8",
    )
    requests: list[dict] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": '{"match": true, "confidence": 0.9}'}}]}

    def fake_post(*args, **kwargs):
        requests.append(kwargs["json"])
        return _Response()

    monkeypatch.setattr("homemaster.benchmarking.alfworld.grounding.httpx.post", fake_post)

    result = ground_text(
        "standing light",
        candidates=[
            GroundingCandidate("floorlamp", "toggle", "subtask"),
            GroundingCandidate("sofa 1", "receptacle", "observation"),
        ],
        allowed_kinds={"toggle"},
        judge_config_path=config_path,
    )

    assert result.value == "floorlamp"
    assert result.method == "semantic_judge"
    assert len(requests) == 1
    prompt = requests[0]["messages"][0]["content"]
    assert "model_phrase" in prompt
    assert "ground_truth_label" in prompt
    assert "candidates" not in prompt
    assert "canonical" not in prompt
