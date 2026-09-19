from __future__ import annotations

import json

import pytest

from homemaster.experience.schema_episode import (
    build_schema_episode,
    serialize_schema_episode,
)
from homemaster.memory.schema_episode_validation import (
    validate_schema_episode_candidates,
)


def _event(
    event_type: str,
    event_id: str,
    *,
    timestamp: str | None,
    tool_call_id: str | None = None,
    name: str | None = None,
    payload: dict | None = None,
) -> dict:
    event = {
        "type": event_type,
        "event_id": event_id,
        "session_id": "session-1",
        "run_id": "run-1",
        "timestamp": timestamp,
        "payload": payload or {},
    }
    if tool_call_id is not None:
        event["tool_call_id"] = tool_call_id
    if name is not None:
        event["name"] = name
    return event


def _contract_events() -> list[dict]:
    return [
        _event(
            "runtime.turn_started",
            "evt-user",
            timestamp="2026-09-17T10:00:00Z",
            payload={"user_text": "Find and place the medicine."},
        ),
        _event(
            "tool.call_started",
            "evt-call-1",
            timestamp="2026-09-17T10:00:01Z",
            tool_call_id="call-1",
            name="inspect",
            payload={"arguments": {"location": "living_room_table"}},
        ),
        _event(
            "tool.call_completed",
            "evt-result-1",
            timestamp="2026-09-17T10:00:02Z",
            tool_call_id="call-1",
            name="inspect",
            payload={
                "args": {"location": "living_room_table"},
                "result": {"result": "not_found", "object": "medicine"},
                "data": {"status": "success"},
            },
        ),
        _event(
            "tool.call_failed",
            "evt-result-2",
            timestamp="2026-09-17T10:00:03Z",
            tool_call_id="call-2",
            name="open",
            payload={
                "args": {"container": "bedroom_drawer"},
                "result": {"error": "blocked"},
                "data": {"status": "failed"},
            },
        ),
        _event(
            "tool.call_started",
            "evt-call-3",
            timestamp="2026-09-17T10:00:04Z",
            tool_call_id="call-3",
            name="inspect",
            payload={"arguments": {"location": "bedroom_drawer"}},
        ),
        _event(
            "tool.call_completed",
            "evt-result-3",
            timestamp="2026-09-17T10:00:05Z",
            tool_call_id="call-3",
            name="inspect",
            payload={
                "args": {"location": "bedroom_drawer"},
                "result": {"result": "found", "object": "medicine"},
                "data": {"status": "success"},
            },
        ),
        _event(
            "tool.call_started",
            "evt-call-4",
            timestamp=None,
            tool_call_id="call-4",
            name="put",
            payload={"arguments": {"object": "medicine", "target": "nightstand"}},
        ),
        _event(
            "tool.call_completed",
            "evt-result-5",
            timestamp=None,
            tool_call_id="call-5",
            name="inspect",
            payload={
                "args": {"location": "hallway"},
                "result": "not_found",
                "data": {"status": "success"},
            },
        ),
        _event(
            "runtime.goal_evaluated",
            "evt-goal",
            timestamp="2026-09-17T10:00:06Z",
            payload={"won": True, "task_id": "task-1"},
        ),
        _event(
            "transport.delta",
            "evt-delta",
            timestamp="2026-09-17T10:00:07Z",
            payload={"text": "ignored"},
        ),
    ]


def test_schema_episode_pairs_calls_and_results_without_adjacency_guesses() -> None:
    episode = build_schema_episode(
        session_id="session-1",
        exit_reason="episode_end",
        events=_contract_events(),
    )

    assert episode["schema_version"] == "homemaster.schema_episode.v1"
    assert episode["session_id"] == "session-1"
    assert episode["extractor_slots"] == [
        "object_location",
        "search_observation",
        "task_procedure",
    ]
    assert [step["tool_call_id"] for step in episode["tool_steps"]] == [
        "call-1",
        "call-2",
        "call-3",
        "call-4",
        "call-5",
    ]
    assert episode["tool_steps"][0]["arguments"] == {"location": "living_room_table"}
    assert episode["tool_steps"][0]["result"] == {
        "result": "not_found",
        "object": "medicine",
    }
    assert episode["tool_steps"][1]["status"] == "failed"
    assert episode["tool_steps"][1]["source_event_ids"] == ["evt-result-2"]
    assert episode["tool_steps"][2]["source_event_ids"] == [
        "evt-call-3",
        "evt-result-3",
    ]
    assert episode["tool_steps"][3]["status"] is None
    assert episode["tool_steps"][3]["result"] is None
    assert episode["tool_steps"][4]["arguments"] == {"location": "hallway"}
    assert episode["tool_steps"][4]["source_event_ids"] == ["evt-result-5"]
    assert all(event["type"] != "transport.delta" for event in episode["events"])
    assert episode["provenance"]["authoritative_goal_event_ids"] == ["evt-goal"]


def test_schema_episode_serialization_is_deterministic_and_round_trips_json_values() -> None:
    first = build_schema_episode("session-1", "episode_end", _contract_events())
    second = build_schema_episode("session-1", "episode_end", list(_contract_events()))

    assert serialize_schema_episode(first) == serialize_schema_episode(second)
    restored = json.loads(serialize_schema_episode(first))
    assert restored["tool_steps"] == first["tool_steps"]
    assert all(event["event_id"] for event in restored["events"])


def test_compact_episode_retains_tool_evidence_without_duplicate_arguments() -> None:
    events = _contract_events()
    events.append(
        _event(
            "assistant.reply",
            "reply",
            timestamp=None,
            payload={"reply": "Non-authoritative commentary"},
        )
    )
    episode = build_schema_episode("session-1", "episode_end", events)
    by_id = {event["event_id"]: event for event in episode["events"]}
    assert "reply" not in by_id
    assert "reply" not in episode["provenance"]["source_event_ids"]
    for step in episode["tool_steps"]:
        assert all(event_id in by_id for event_id in step["source_event_ids"])
    assert by_id["evt-call-1"]["payload"] == {}
    assert "args" not in by_id["evt-result-1"]["payload"]
    assert episode["tool_steps"][0]["arguments"] == {"location": "living_room_table"}
    assert by_id["evt-result-1"]["payload"]["result"] == episode["tool_steps"][0]["result"]
    assert episode["provenance"]["authoritative_goal_event_ids"] == ["evt-goal"]


def test_compact_episode_preserves_coordinate_evidence_in_tool_arguments() -> None:
    events = _contract_events()
    coordinates = {"x": 1, "y": 2}
    events[1]["payload"]["arguments"]["coordinates"] = coordinates
    events[2]["payload"]["args"]["coordinates"] = coordinates
    episode = build_schema_episode("session-1", "episode_end", events)
    record = dict(
        type="object_location",
        summary="Located object",
        source={"event_ids": ["evt-call-1"]},
        position=dict(frame="world", unit="m", source="tool_input", coordinates=coordinates),
    )
    validate_schema_episode_candidates(
        "object_location", _raw_candidate("object_location", record), episode
    )
    record["position"]["coordinates"] = {"x": 999}
    with pytest.raises(ValueError, match="coordinates are not present"):
        validate_schema_episode_candidates(
            "object_location", _raw_candidate("object_location", record), episode
        )


def _raw_candidate(entity_type: str, record: dict) -> dict:
    return {
        "entities": [
            {
                "name": f"candidate-{entity_type}",
                "entity_type": entity_type,
                "description": "LLM summary",
                "properties": [
                    {
                        "property_name": "episode_record",
                        "value": json.dumps(record),
                    }
                ],
            }
        ],
        "edges": [],
    }


def test_schema_episode_rejects_conflicting_call_and_result_arguments() -> None:
    events = _contract_events()
    events[2]["payload"]["args"] = {"location": "different_table"}

    with pytest.raises(ValueError, match="Conflicting tool arguments for call-1"):
        build_schema_episode("session-1", "episode_end", events)


def test_schema_episode_synthesizes_stable_missing_event_id() -> None:
    event = _event(
        "runtime.turn_started",
        "discarded",
        timestamp=None,
        payload={"user_text": "Find medicine"},
    )
    event.pop("event_id")

    episode = build_schema_episode("session-1", "episode_end", [event])

    assert episode["events"][0]["event_id"] == "synthesized:trace-offset:0"
    assert episode["events"][0]["event_id_synthesized"] is True


def test_procedure_executable_requires_trusted_goal_and_exact_step_evidence() -> None:
    episode = build_schema_episode("session-1", "episode_end", _contract_events())
    first = episode["tool_steps"][0]
    record = {
        "type": "task_procedure",
        "summary": "Inspect the living-room table.",
        "task": "find_object",
        "target": "medicine",
        "outcome": "success",
        "is_executable": True,
        "steps": [
            {
                "index": 1,
                "tool": first["tool"],
                "arguments": first["arguments"],
                "result": first["result"],
                "status": first["status"],
                "evidence_event_ids": first["source_event_ids"],
            }
        ],
        "reusable_steps": [1],
        "failure_lesson": None,
        "outcome_evidence_event_ids": ["evt-goal"],
        "source": {
            "session_id": "session-1",
            "event_ids": first["source_event_ids"] + ["evt-goal"],
        },
    }
    raw = _raw_candidate("task_procedure", record)

    validated = validate_schema_episode_candidates("task_procedure", raw, episode)
    stored = json.loads(validated["entities"][0]["properties"][0]["value"])

    assert stored["is_executable"] is True
    assert stored["reusable_steps"] == [1]
    record["outcome_evidence_event_ids"] = []
    downgraded = validate_schema_episode_candidates(
        "task_procedure", _raw_candidate("task_procedure", record), episode
    )
    stored = json.loads(downgraded["entities"][0]["properties"][0]["value"])
    assert stored["is_executable"] is False
    assert stored["reusable_steps"] == []


def test_validation_rejects_invented_coordinates_and_tool_arguments() -> None:
    episode = build_schema_episode("session-1", "episode_end", _contract_events())
    location = {
        "type": "object_location",
        "summary": "Medicine is in the drawer.",
        "object": {"id": "medicine", "label": "medicine"},
        "relation": "inside",
        "location": {"id": "bedroom_drawer", "label": "drawer"},
        "observation_kind": "found",
        "position": {
            "frame": "bedroom_drawer",
            "coordinates": {"x": 0.99, "y": 0.99},
            "unit": "normalized",
            "source": "tool_result",
        },
        "source": {"session_id": "session-1", "event_ids": ["evt-result-3"]},
        "observed_at": "2026-09-17T10:00:05Z",
    }
    with pytest.raises(ValueError, match="coordinates are not present"):
        validate_schema_episode_candidates(
            "object_location", _raw_candidate("object_location", location), episode
        )

    search = {
        "type": "search_observation",
        "summary": "Medicine was not found.",
        "object": "medicine",
        "location": "living_room_table",
        "result": "not_found",
        "search_action": {
            "tool": "inspect",
            "arguments": {"location": "invented_location"},
        },
        "source": {
            "session_id": "session-1",
            "event_ids": ["evt-call-1", "evt-result-1"],
        },
        "observed_at": "2026-09-17T10:00:02Z",
    }
    with pytest.raises(ValueError, match="search_action does not match"):
        validate_schema_episode_candidates(
            "search_observation", _raw_candidate("search_observation", search), episode
        )


def test_validation_accepts_normalized_dynamic_property_shape() -> None:
    episode = {
        "events": [{"event_id": "e1", "payload": {"result": "found"}}],
        "tool_steps": [],
        "provenance": {},
    }
    raw = {
        "entities": [
            {
                "entity_type": "object_location",
                "entity_description": "old",
                "dynamic_property": {
                    "episode_record": json.dumps(
                        {
                            "type": "object_location",
                            "summary": "found",
                            "source": {"event_ids": ["e1"]},
                            "object": {"id": "x", "label": "x"},
                            "relation": "inside",
                            "location": {"id": "y", "label": "y"},
                            "observation_kind": "found",
                        }
                    )
                },
            }
        ],
        "edges": [],
    }
    validate_schema_episode_candidates("object_location", raw, episode)
    entity = raw["entities"][0]
    assert json.loads(entity["dynamic_property"]["episode_record"])["summary"] == "found"
    assert entity["entity_description"] == "found"
    assert entity["properties"][0]["property_name"] == "episode_record"


def test_validation_canonicalizes_object_location_event_type_alias() -> None:
    episode = {
        "events": [{"event_id": "e1", "payload": {"result": "found"}}],
        "tool_steps": [],
        "provenance": {},
    }
    raw = {
        "entities": [
            {
                "entity_type": "object_location",
                "dynamic_property": {
                    "episode_record": json.dumps(
                        {
                            "type": "found",
                            "summary": "Medicine was found in the drawer.",
                            "source": {"event_ids": ["e1"]},
                            "object": {"id": "medicine", "label": "medicine"},
                            "relation": "found in",
                            "location": {"id": "drawer", "label": "drawer"},
                            "observation_kind": "found",
                        }
                    )
                },
            }
        ],
        "edges": [],
    }

    validate_schema_episode_candidates("object_location", raw, episode)

    stored = json.loads(raw["entities"][0]["dynamic_property"]["episode_record"])
    assert stored["type"] == "object_location"


def test_validation_accepts_source_event_id_list_shape() -> None:
    episode = {
        "events": [{"event_id": "e1", "payload": {"result": "found"}}],
        "tool_steps": [],
        "provenance": {},
    }
    raw = {
        "entities": [
            {
                "entity_type": "object_location",
                "dynamic_property": {
                    "episode_record": json.dumps(
                        {
                            "type": "inspect_result",
                            "summary": "Medicine was found in the drawer.",
                            "source": ["e1"],
                            "object": "medicine",
                            "relation": "found in",
                            "location": "drawer",
                            "observation_kind": "found",
                        }
                    )
                },
            }
        ],
        "edges": [],
    }

    validate_schema_episode_candidates("object_location", raw, episode)

    stored = json.loads(raw["entities"][0]["dynamic_property"]["episode_record"])
    assert stored["type"] == "object_location"


def test_validation_compiles_semantic_candidate_to_canonical_record() -> None:
    episode = {
        "session_id": "session-1",
        "events": [{"event_id": "e1", "payload": {"result": "found"}}],
        "tool_steps": [],
        "provenance": {},
    }
    raw = {
        "entities": [
            {
                "name": "candidate-1",
                "description": "Medicine was found in the drawer.",
                "object": "medicine",
                "relation": "found in",
                "location": "drawer",
                "observation_kind": "found",
                "evidence_event_ids": ["e1"],
            }
        ],
        "edges": [],
    }

    validated = validate_schema_episode_candidates("object_location", raw, episode)
    entity = validated["entities"][0]
    record = json.loads(entity["properties"][0]["value"])
    assert entity["entity_type"] == "object_location"
    assert record["type"] == "object_location"
    assert record["source"] == {"session_id": "session-1", "event_ids": ["e1"]}
    assert json.loads(entity["dynamic_property"]["episode_record"]) == record


@pytest.mark.parametrize(
    "reference", ["event_ids", "source_event_ids", "evidence_event_ids", "tool_call_id"]
)
@pytest.mark.parametrize("base", [None, 0, 1])
def test_procedure_compiles_trace_references(reference, base) -> None:
    episode = build_schema_episode("session-1", "episode_end", _contract_events())
    first = episode["tool_steps"][0]
    step = {reference: "call-1" if reference == "tool_call_id" else first["source_event_ids"]}
    # Real extractor output copied a summary instead of the original result.
    step.update(
        tool=first["tool"], arguments=first["arguments"], result="success", status="success"
    )
    if base is not None:
        step["index"] = base
    record = dict(
        type="task_procedure",
        summary="Observed attempt",
        task="find",
        target="medicine",
        outcome="unknown",
        steps=[step],
        reusable_steps=[base if base is not None else 1],
        failure_lesson="",
        source={"event_ids": first["source_event_ids"]},
    )
    raw = validate_schema_episode_candidates(
        "task_procedure", _raw_candidate("task_procedure", record), episode
    )
    stored = json.loads(raw["entities"][0]["properties"][0]["value"])
    assert stored["steps"][0]["index"] == 1
    for field in ("tool_call_id", "tool", "arguments", "result", "status"):
        assert stored["steps"][0][field] == first[field]
    assert stored["steps"][0]["evidence_event_ids"] == first["source_event_ids"]
    assert stored["is_executable"] is False
    assert stored["reusable_steps"] == []
    assert stored["failure_lesson"] is None


@pytest.mark.parametrize(
    "steps",
    [
        [{"tool_call_id": "missing"}],
        [{"tool": "inspect"}],
        [{"tool_call_id": "call-1", "event_ids": ["evt-call-3", "evt-result-3"]}],
        [{"tool_call_id": "call-1", "event_ids": ["invented"]}],
        [{"tool_call_id": "call-3"}, {"tool_call_id": "call-1"}],
        [{"tool_call_id": "call-1"}, {"tool_call_id": "call-1"}],
        [{"tool_call_id": "call-1", "arguments": {"location": "invented"}}],
    ],
)
def test_procedure_rejects_invalid_trace_references(steps) -> None:
    episode = build_schema_episode("session-1", "episode_end", _contract_events())
    record = dict(
        type="task_procedure",
        summary="Attempt",
        task="find",
        target="medicine",
        outcome="unknown",
        steps=steps,
        source={"event_ids": ["evt-call-1"]},
    )
    with pytest.raises(ValueError):
        validate_schema_episode_candidates(
            "task_procedure", _raw_candidate("task_procedure", record), episode
        )


def test_procedure_accepts_exact_call_id_shorthand() -> None:
    episode = build_schema_episode("session-1", "episode_end", _contract_events())
    record = dict(
        type="task_procedure",
        summary="Attempt",
        task="find",
        target="medicine",
        outcome="unknown",
        steps=["call-1", "call-3"],
        source={"event_ids": ["evt-call-1"]},
    )
    raw = validate_schema_episode_candidates(
        "task_procedure", _raw_candidate("task_procedure", record), episode
    )
    stored = json.loads(raw["entities"][0]["properties"][0]["value"])
    assert [step["tool_call_id"] for step in stored["steps"]] == ["call-1", "call-3"]
    assert stored["is_executable"] is False


@pytest.mark.parametrize("action", [{"tool_call_id": "call-1"}, "call-1"])
def test_search_hydrates_exact_call_reference(action) -> None:
    episode = build_schema_episode("session-1", "episode_end", _contract_events())
    record = dict(
        type="search_observation",
        summary="Not found",
        object="medicine",
        location="living_room_table",
        result="not_found",
        search_action=action,
        source={"event_ids": ["evt-result-1"]},
    )
    raw = validate_schema_episode_candidates(
        "search_observation", _raw_candidate("search_observation", record), episode
    )
    stored = json.loads(raw["entities"][0]["properties"][0]["value"])
    assert stored["search_action"] == {
        "tool": "inspect",
        "arguments": {"location": "living_room_table"},
    }


def test_search_rejects_reference_outside_its_evidence() -> None:
    episode = build_schema_episode("session-1", "episode_end", _contract_events())
    record = dict(
        type="search_observation",
        summary="Not found",
        object="medicine",
        location="living_room_table",
        result="not_found",
        search_action={"tool_call_id": "call-3"},
        source={"event_ids": ["evt-result-1"]},
    )
    with pytest.raises(ValueError):
        validate_schema_episode_candidates(
            "search_observation", _raw_candidate("search_observation", record), episode
        )


@pytest.mark.parametrize(
    "goal_ids,refs,executable",
    [
        (["evt-goal"], ["call-1"], True),
        ([], ["call-1"], False),
        (["evt-goal"], ["call-2"], False),
        (["evt-goal"], ["missing"], False),
    ],
)
def test_reusable_call_references_require_goal_and_success(goal_ids, refs, executable) -> None:
    episode = build_schema_episode("session-1", "episode_end", _contract_events())
    record = dict(
        type="task_procedure",
        summary="Attempt",
        task="find",
        target="medicine",
        outcome="success",
        steps=["call-1", "call-2"],
        source={"event_ids": ["evt-call-1"]},
        outcome_evidence_event_ids=goal_ids,
        reusable_tool_call_ids=refs,
    )
    raw = validate_schema_episode_candidates(
        "task_procedure", _raw_candidate("task_procedure", record), episode
    )
    stored = json.loads(raw["entities"][0]["properties"][0]["value"])
    assert stored["is_executable"] is executable
    assert stored["reusable_steps"] == ([1] if executable else [])
