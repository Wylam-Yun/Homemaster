"""Deterministic evidence checks for fixed schema episode candidates."""

from __future__ import annotations

import json
from collections.abc import Iterable
from copy import deepcopy
from typing import Any

DOMAIN_TYPES = frozenset({"object_location", "search_observation", "task_procedure"})
_RECORD_TYPE_ALIASES = {
    "object_location": frozenset(
        {
            "object_location",
            "object_location_observation",
            "discovery",
            "placement",
            "found",
            "placed",
            "observed",
            "inspect_result",
            "location_observation",
            "object_observation",
        }
    ),
    "search_observation": frozenset({"search_observation", "search"}),
    "task_procedure": frozenset({"task_procedure", "procedure", "task_experience"}),
}


def validate_schema_episode_candidates(
    entity_type: str,
    raw_memory: dict[str, Any],
    episode: dict[str, Any],
) -> dict[str, Any]:
    """Validate provenance and sanitize executable procedure projections in-place."""

    if entity_type not in DOMAIN_TYPES:
        raise ValueError(f"unsupported schema episode entity type: {entity_type}")
    events = {
        event.get("event_id"): event
        for event in episode.get("events", [])
        if isinstance(event, dict) and isinstance(event.get("event_id"), str)
    }
    steps = {
        step.get("tool_call_id"): step
        for step in episode.get("tool_steps", [])
        if isinstance(step, dict) and isinstance(step.get("tool_call_id"), str)
    }
    trusted_goal_ids = set(episode.get("provenance", {}).get("authoritative_goal_event_ids", []))
    for entity in raw_memory.get("entities", []):
        if entity.get("entity_type") is None:
            entity["entity_type"] = entity_type
        record, prop, storage = _episode_record(entity, entity_type)
        evidence_ids = _infer_source_event_ids({**entity, **record}, episode)
        if evidence_ids and not isinstance(record.get("source"), (dict, list)):
            record["source"] = {"event_ids": evidence_ids}
        elif record.get("source") is None:
            record["source"] = {"event_ids": evidence_ids}
        source_ids = _source_event_ids(record, episode)
        _require_known_event_ids(source_ids, events, field="source.event_ids")
        record["type"] = entity_type
        record["source"] = {
            "session_id": episode.get("session_id"),
            "event_ids": source_ids,
        }
        action = record.get("search_action")
        if entity_type == "search_observation" and (
            isinstance(action, dict)
            and "tool_call_id" in action
            or isinstance(action, str)
            and action in steps
        ):
            call_id = action["tool_call_id"] if isinstance(action, dict) else action
            paired = steps.get(call_id) if isinstance(call_id, str) else None
            if paired is None or not set(paired["source_event_ids"]) & set(source_ids):
                raise ValueError(
                    "search_action reference does not match cited paired tool evidence"
                )
            if isinstance(action, dict) and any(
                key in action and action[key] != paired.get(key) for key in ("tool", "arguments")
            ):
                raise ValueError("search_action does not match cited paired tool evidence")
            record["search_action"] = {
                "tool": paired["tool"],
                "arguments": deepcopy(paired["arguments"]),
            }
        if entity_type == "search_observation" and isinstance(record.get("search_action"), str):
            action_name = record["search_action"]
            matching_steps = [
                step
                for step in steps.values()
                if step.get("tool") == action_name
                and set(step.get("source_event_ids", [])) & set(source_ids)
            ]
            if len(matching_steps) > 1:
                matching_steps = [
                    step for step in matching_steps if _search_step_matches_record(step, record)
                ]
            if len(matching_steps) == 1:
                record["search_action"] = {
                    "tool": action_name,
                    "arguments": matching_steps[0].get("arguments", {}),
                }
        if entity_type == "object_location":
            _validate_position(record, source_ids, events, steps)
        elif entity_type == "search_observation":
            _validate_search(record, source_ids, steps)
        else:
            _validate_procedure(record, events, steps, trusted_goal_ids)
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        entity["dynamic_property"] = {"episode_record": encoded}
        entity["properties"] = [
            {
                "property_name": "episode_record",
                "value": encoded,
                "operation": "set",
                "time": entity.get("record_time"),
            }
        ]
        entity["description"] = record["summary"]
        if "entity_description" in entity:
            entity["entity_description"] = record["summary"]
    return raw_memory


def _episode_record(
    entity: dict[str, Any], entity_type: str
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if entity.get("entity_type") != entity_type:
        raise ValueError(f"fixed extractor {entity_type} emitted {entity.get('entity_type')}")
    properties = entity.get("properties")
    storage = "properties"
    if isinstance(properties, list):
        matches = [
            prop
            for prop in properties
            if isinstance(prop, dict) and prop.get("property_name") == "episode_record"
        ]
        if not matches:
            dynamic_property = entity.get("dynamic_property")
            value = (
                dynamic_property.get("episode_record")
                if isinstance(dynamic_property, dict)
                else None
            )
            if isinstance(value, (str, dict)):
                matches = [{"property_name": "episode_record", "value": value}]
                storage = "dynamic_property"
    else:
        dynamic_property = entity.get("dynamic_property")
        if not isinstance(dynamic_property, dict):
            record = _compile_semantic_candidate(entity, entity_type)
            return record, {"property_name": "episode_record", "value": record}, "semantic"
        value = dynamic_property.get("episode_record")
        matches = (
            [{"property_name": "episode_record", "value": value}]
            if isinstance(value, (str, dict))
            else []
        )
        storage = "dynamic_property"
    if not matches:
        record = _compile_semantic_candidate(entity, entity_type)
        return record, {"property_name": "episode_record", "value": record}, "semantic"
    if len(matches) != 1 or not isinstance(matches[0].get("value"), (str, dict)):
        raise ValueError("schema episode entity requires one JSON episode_record")
    value = matches[0]["value"]
    if isinstance(value, dict):
        record = value
    else:
        try:
            record = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("episode_record is not valid JSON") from exc
    if not isinstance(record, dict) or not _has_supported_record_type(record, entity_type):
        raise ValueError(f"episode_record type must be {entity_type}")
    record["type"] = entity_type
    if not isinstance(record.get("summary"), str) or not record["summary"].strip():
        raise ValueError("episode_record summary must be non-empty")
    return record, matches[0], storage


def _compile_semantic_candidate(entity: dict[str, Any], entity_type: str) -> dict[str, Any]:
    """Compile LLM semantic fields into the canonical domain record."""

    nested_record = entity.get("episode_record")
    fields = dict(nested_record) if isinstance(nested_record, dict) else {}
    fields.update(
        {
            key: value
            for key, value in entity.items()
            if key
            not in {
                "name",
                "entity_type",
                "description",
                "entity_description",
                "properties",
                "dynamic_property",
                "episode_record",
            }
        }
    )
    fields["type"] = entity_type
    evidence = fields.pop("evidence_event_ids", None)
    if evidence is not None:
        fields["source"] = {"event_ids": evidence}
    elif "source" not in fields:
        fields["source"] = {"event_ids": _infer_source_event_ids(fields, {})}
    if "summary" not in fields:
        fields["summary"] = (
            entity.get("description") or entity.get("entity_description") or entity.get("name")
        )
    required = {
        "object_location": (
            "summary",
            "object",
            "relation",
            "location",
            "observation_kind",
            "source",
        ),
        "search_observation": (
            "summary",
            "object",
            "location",
            "result",
            "search_action",
            "source",
        ),
        "task_procedure": ("summary", "task", "target", "outcome", "steps", "source"),
    }[entity_type]
    missing = [key for key in required if fields.get(key) is None]
    if missing:
        raise ValueError(f"semantic {entity_type} candidate missing fields: {missing}")
    return fields


def _infer_source_event_ids(record: dict[str, Any], episode: dict[str, Any]) -> list[str]:
    """Derive canonical provenance from semantic evidence fields, never from LLM labels."""

    candidates: list[Any] = []
    for key in ("evidence_event_ids", "outcome_evidence_event_ids"):
        value = record.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    steps = record.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict) and isinstance(step.get("evidence_event_ids"), list):
                candidates.extend(step["evidence_event_ids"])
    lesson = record.get("failure_lesson")
    if isinstance(lesson, dict) and isinstance(lesson.get("evidence_event_ids"), list):
        candidates.extend(lesson["evidence_event_ids"])
    if not candidates:
        provenance = episode.get("provenance") if isinstance(episode, dict) else None
        if isinstance(provenance, dict) and isinstance(provenance.get("source_event_ids"), list):
            candidates.extend(provenance["source_event_ids"])
    return list(dict.fromkeys(value for value in candidates if isinstance(value, str) and value))


def _has_supported_record_type(record: dict[str, Any], entity_type: str) -> bool:
    record_type = record.get("type")
    if record_type in _RECORD_TYPE_ALIASES[entity_type]:
        return True
    if not isinstance(record_type, str):
        return False
    required_fields = {
        "object_location": ("object", "relation", "location", "observation_kind", "source"),
        "search_observation": ("object", "location", "result", "search_action", "source"),
        "task_procedure": ("task", "target", "outcome", "steps", "source"),
    }
    fields = required_fields.get(entity_type)
    return fields is not None and all(record.get(field) is not None for field in fields)


def _source_event_ids(record: dict[str, Any], episode: dict[str, Any]) -> list[str]:
    source = record.get("source")
    if isinstance(source, dict):
        event_ids = source.get("event_ids")
        if event_ids is None:
            event_ids = source.get("source_event_ids")
        if event_ids is None:
            event_ids = source.get("event_id")
    elif isinstance(source, list):
        event_ids = [item.get("event_id") if isinstance(item, dict) else item for item in source]
    elif isinstance(source, str):
        provenance = episode.get("provenance")
        event_keys = {
            event.get("event_id")
            for event in episode.get("events", [])
            if isinstance(event, dict) and isinstance(event.get("event_id"), str)
        }
        if source.startswith("events:"):
            event_ids = [value for value in source[7:].split(",") if value]
        elif source in event_keys:
            event_ids = [source]
        elif not isinstance(provenance, dict) or source != provenance.get("source"):
            event_ids = None
        else:
            event_ids = provenance.get("source_event_ids")
    else:
        event_ids = None
    if isinstance(event_ids, str):
        event_ids = [event_ids]
    if not isinstance(event_ids, list):
        raise ValueError("episode_record source.event_ids must be a list")
    normalized = [value for value in event_ids if isinstance(value, str) and value]
    if not normalized:
        raise ValueError("episode_record source.event_ids must be non-empty")
    return normalized


def _require_known_event_ids(
    values: Iterable[Any], events: dict[str, dict[str, Any]], *, field: str
) -> None:
    values = list(values)
    missing = [value for value in values if not isinstance(value, str) or value not in events]
    if missing:
        raise ValueError(f"{field} references unknown events: {missing}")


def _validate_position(
    record: dict[str, Any],
    source_ids: list[str],
    events: dict[str, dict[str, Any]],
    paired_steps: dict[str, dict[str, Any]],
) -> None:
    position = record.get("position")
    if position is None:
        return
    if not isinstance(position, dict):
        raise ValueError("object_location position must be an object")
    coordinates = position.get("coordinates")
    if coordinates is None:
        return
    if not position.get("frame") or not position.get("unit"):
        raise ValueError("coordinates require frame and unit")
    source = position.get("source")
    if source not in {"tool_input", "tool_result"}:
        raise ValueError("numeric coordinates require tool_input or tool_result source")
    candidate_payloads = [events[event_id].get("payload") for event_id in source_ids]
    candidate_payloads.extend(
        step.get("arguments")
        for step in paired_steps.values()
        if set(step.get("source_event_ids", [])) & set(source_ids)
    )
    if not any(_contains_json_value(payload, coordinates) for payload in candidate_payloads):
        raise ValueError("coordinates are not present in cited tool evidence")


def _search_step_matches_record(step: dict[str, Any], record: dict[str, Any]) -> bool:
    arguments = step.get("arguments")
    if not isinstance(arguments, dict):
        return False
    location = record.get("location")
    if isinstance(location, dict):
        location = location.get("label") or location.get("id")
    if isinstance(location, str) and location:
        return any(value == location for value in arguments.values())
    obj = record.get("object")
    if isinstance(obj, dict):
        obj = obj.get("label") or obj.get("id")
    if isinstance(obj, str) and obj:
        return any(value == obj for value in arguments.values())
    return False


def _validate_search(
    record: dict[str, Any],
    source_ids: list[str],
    steps: dict[str, dict[str, Any]],
) -> None:
    if record.get("result") not in {"found", "not_found", "blocked", "error"}:
        raise ValueError("invalid search_observation result")
    action = record.get("search_action")
    if not isinstance(action, dict):
        raise ValueError("search_observation search_action must be an object")
    matching = [
        step for step in steps.values() if set(step.get("source_event_ids", [])) & set(source_ids)
    ]
    if not any(
        step.get("tool") == action.get("tool")
        and step.get("arguments") == action.get("arguments")
        and step.get("result") is not None
        for step in matching
    ):
        raise ValueError("search_action does not match cited paired tool evidence")


def _compile_procedure_steps(
    record: dict[str, Any],
    events: dict[str, dict[str, Any]],
    paired_steps: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve exact trace identities before generating storage-owned fields."""
    proposed = record.get("steps")
    if not isinstance(proposed, list):
        raise ValueError("task_procedure steps must be a list")
    raw_indices = [step.get("index") if isinstance(step, dict) else None for step in proposed]
    one_based = raw_indices == list(range(1, len(proposed) + 1))
    zero_based = raw_indices == list(range(len(proposed)))
    unnumbered = all(index is None for index in raw_indices)
    if not (one_based or zero_based or unnumbered):
        raise ValueError("task_procedure step indices must be contiguous and ordered")
    canonical = []
    order = {call_id: index for index, call_id in enumerate(paired_steps)}
    previous = -1
    for index, step in enumerate(proposed, 1):
        if isinstance(step, str):
            step = {"tool_call_id": step}
        if not isinstance(step, dict):
            raise ValueError("procedure step must be an object")
        candidates = list(paired_steps.values())
        has_reference = False
        if "tool_call_id" in step:
            has_reference = True
            candidates = [p for p in candidates if p["tool_call_id"] == step["tool_call_id"]]
        for key in ("evidence_event_ids", "event_ids", "source_event_ids"):
            if key not in step:
                continue
            has_reference = True
            ids = step[key]
            if not isinstance(ids, list) or not ids:
                raise ValueError("procedure step evidence_event_ids must be a non-empty list")
            _require_known_event_ids(ids, events, field="steps." + key)
            candidates = [p for p in candidates if set(p["source_event_ids"]) == set(ids)]
        if not has_reference or len(candidates) != 1:
            raise ValueError("procedure step requires one unambiguous paired tool reference")
        paired = candidates[0]
        for field in ("tool", "arguments"):
            if field in step and step[field] != paired.get(field):
                raise ValueError("procedure step does not match paired tool evidence")
        trace_index = order[paired["tool_call_id"]]
        if trace_index <= previous:
            raise ValueError("procedure step references must follow trace order without duplicates")
        previous = trace_index
        compiled = {
            field: deepcopy(paired.get(field))
            for field in ("tool_call_id", "tool", "arguments", "result", "status")
        }
        compiled.update(index=index, evidence_event_ids=list(paired["source_event_ids"]))
        canonical.append(compiled)
    reusable = record.get("reusable_steps")
    if not isinstance(reusable, list) or any(type(i) is not int for i in reusable):
        reusable = []
    if zero_based and not one_based:
        reusable = [i + 1 for i in reusable]
    elif unnumbered:
        # An unnumbered proposal cannot safely bind numeric reusable references.
        reusable = []
    if "reusable_tool_call_ids" in record:
        refs = record.pop("reusable_tool_call_ids")
        selected = {step["tool_call_id"]: step["index"] for step in canonical}
        if isinstance(refs, list) and all(isinstance(ref, str) and ref in selected for ref in refs):
            reusable = [selected[ref] for ref in refs]
            if reusable != sorted(set(reusable)):
                reusable = []
        else:
            reusable = []
    record["reusable_steps"] = reusable
    record["steps"] = canonical
    return canonical


def _validate_procedure(
    record: dict[str, Any],
    events: dict[str, dict[str, Any]],
    paired_steps: dict[str, dict[str, Any]],
    trusted_goal_ids: set[str],
) -> None:
    outcome = record.get("outcome")
    if outcome not in {"success", "failure", "partial", "unknown"}:
        raise ValueError("invalid task_procedure outcome")
    observed_steps = _compile_procedure_steps(record, events, paired_steps)
    indices: set[int] = set()
    for expected_index, step in enumerate(observed_steps, start=1):
        if not isinstance(step, dict) or step.get("index") != expected_index:
            raise ValueError("task_procedure step indices must be contiguous and ordered")
        indices.add(expected_index)
        evidence_ids = step.get("evidence_event_ids")
        if not isinstance(evidence_ids, list):
            raise ValueError("procedure step evidence_event_ids must be a list")
        _require_known_event_ids(evidence_ids, events, field="steps.evidence_event_ids")
        candidates = [
            paired
            for paired in paired_steps.values()
            if set(paired.get("source_event_ids", [])) == set(evidence_ids)
        ]
        if not any(
            paired.get("tool") == step.get("tool")
            and paired.get("arguments") == step.get("arguments")
            and paired.get("result") == step.get("result")
            and paired.get("status") == step.get("status")
            for paired in candidates
        ):
            raise ValueError("procedure step does not match paired tool evidence")
    outcome_ids = record.setdefault("outcome_evidence_event_ids", [])
    if not isinstance(outcome_ids, list):
        raise ValueError("outcome_evidence_event_ids must be a list")
    _require_known_event_ids(outcome_ids, events, field="outcome_evidence_event_ids")
    lesson = record.get("failure_lesson")
    if lesson == "":
        lesson = record["failure_lesson"] = None
    if lesson is not None:
        if not isinstance(lesson, dict) or not isinstance(lesson.get("evidence_event_ids"), list):
            raise ValueError("failure_lesson requires evidence_event_ids")
        _require_known_event_ids(
            lesson["evidence_event_ids"], events, field="failure_lesson.evidence_event_ids"
        )
    if outcome == "unknown":
        record["failure_lesson"] = None
    reusable = record.get("reusable_steps")
    if not isinstance(reusable, list) or any(index not in indices for index in reusable):
        reusable = []
    reusable_valid = all(observed_steps[index - 1].get("status") == "success" for index in reusable)
    externally_verified = bool(
        outcome == "success"
        and outcome_ids
        and set(outcome_ids).issubset(trusted_goal_ids)
        and reusable
        and reusable_valid
    )
    record["is_executable"] = externally_verified
    if not externally_verified:
        record["reusable_steps"] = []
        record["executability_validation"] = "missing_or_invalid_external_goal_evidence"


def _contains_json_value(container: Any, expected: Any) -> bool:
    if container == expected:
        return True
    if isinstance(container, dict):
        return any(_contains_json_value(value, expected) for value in container.values())
    if isinstance(container, list):
        return any(_contains_json_value(value, expected) for value in container)
    return False
