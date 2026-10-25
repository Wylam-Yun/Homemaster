"""Deterministically normalize one HomeMaster session for schema memory extraction."""

from __future__ import annotations

import json
from typing import Any

SCHEMA_EPISODE_VERSION = "homemaster.schema_episode.v1"
EXTRACTOR_SLOTS = (
    "object_location",
    "search_observation",
    "task_procedure",
)

_ACTIONABLE_EVENT_TYPES = frozenset(
    {
        "runtime.turn_started",
        "tool.call_started",
        "tool.call_completed",
        "tool.call_failed",
        "runtime.goal_evaluated",
        "runtime.turn_completed",
        "runtime.task_completed",
        "runtime.task_failed",
    }
)

# Stored-step compaction bounds: compiled task_procedure records must stay far
# below the embedding endpoint's per-item limit (~32k tokens). Verbatim tool
# results are the dominant size driver (a single mindmemos_search result was
# 226KB → the record hit 337KB / 110k tokens), so oversized results keep only a
# bounded tail plus provenance metadata; errors and outcome summaries live at
# the end of tool output, and the full result remains in the runtime trace.
MAX_STORED_RESULT_TAIL_CHARS = 500
_MEMORY_TOOL_PREFIX = "mindmemos_"


def _compact_tool_result(name: Any, result: Any) -> Any:
    """Bound the result payload materialized into schema-episode tool steps.

    Memory-tool results are replaced by a compact reference (their records
    contain memory content verbatim, which recursively nests prior episodes).
    Other oversized results keep only the tail — errors and outcome fields
    appear at the end of tool output — plus the original length so readers can
    detect truncation.
    """
    if isinstance(name, str) and name.startswith(_MEMORY_TOOL_PREFIX):
        return _memory_result_projection(result)
    serialized = (
        result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    )
    if len(serialized) <= MAX_STORED_RESULT_TAIL_CHARS:
        return result
    return {
        "_hm_result_truncated": True,
        "result_chars": len(serialized),
        "tail": serialized[-MAX_STORED_RESULT_TAIL_CHARS:],
    }


def _memory_result_projection(result: Any) -> dict[str, Any]:
    """Project a mindmemos_* tool result down to ids/counts, never raw content."""
    parsed = result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (ValueError, TypeError):
            return {"_hm_result_compacted": True, "result_chars": len(result)}
    if not isinstance(parsed, dict):
        return {"_hm_result_compacted": True}
    records = parsed.get("records")
    projection: dict[str, Any] = {
        "_hm_result_compacted": True,
        "operation": parsed.get("operation"),
        "count": parsed.get("count"),
    }
    if isinstance(records, list):
        memory_ids = [
            record.get("memory_id")
            for record in records
            if isinstance(record, dict) and record.get("memory_id")
        ]
        projection["record_count"] = len(records)
        if memory_ids:
            projection["memory_ids"] = memory_ids
    return projection


def serialize_schema_episode(episode: dict[str, Any]) -> str:
    """Return the canonical UTF-8-safe JSON representation used for hashing."""

    return json.dumps(
        episode,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def build_schema_episode(
    session_id: str,
    exit_reason: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Normalize events and pair tool calls with results by stable identifiers."""

    normalized: list[tuple[int, dict[str, Any]]] = []
    for offset, source in enumerate(events):
        if source.get("type") not in _ACTIONABLE_EVENT_TYPES:
            continue
        event_session = source.get("session_id") or session_id
        if event_session != session_id:
            continue
        event_id = source.get("event_id")
        synthesized = not isinstance(event_id, str) or not event_id
        if synthesized:
            event_id = f"synthesized:trace-offset:{offset}"
        payload = source.get("payload")
        payload = dict(payload) if isinstance(payload, dict) else {}
        event = {
            "event_id": event_id,
            "event_id_synthesized": synthesized,
            "session_id": event_session,
            "run_id": source.get("run_id"),
            "timestamp": source.get("timestamp"),
            "type": source.get("type"),
            "name": source.get("name"),
            "tool_call_id": source.get("tool_call_id"),
            "payload": _actionable_payload(source.get("type"), payload, source.get("name")),
            "trace_offset": offset,
        }
        normalized.append((offset, event))

    normalized.sort(
        key=lambda item: (
            item[1].get("timestamp") is None,
            str(item[1].get("timestamp") or ""),
            item[0],
        )
    )
    ordered_events = [event for _, event in normalized]
    tool_steps, diagnostics = _pair_tool_steps(ordered_events)
    # Pair before compacting: evidence IDs/results remain available to validators,
    # while exact tool arguments have one authoritative home in tool_steps.
    for event in ordered_events:
        if event["type"] in {"tool.call_started", "tool.call_completed", "tool.call_failed"}:
            event["payload"].pop("arguments", None)
            event["payload"].pop("args", None)
    trusted_goal_ids = [
        event["event_id"] for event in ordered_events if _is_authoritative_goal_event(event)
    ]
    return {
        "schema_version": SCHEMA_EPISODE_VERSION,
        "session_id": session_id,
        "exit_reason": exit_reason,
        "extractor_slots": list(EXTRACTOR_SLOTS),
        "events": ordered_events,
        "tool_steps": tool_steps,
        "diagnostics": diagnostics,
        "provenance": {
            "source": "homemaster_runtime_trace",
            "source_event_ids": [event["event_id"] for event in ordered_events],
            "authoritative_goal_event_ids": trusted_goal_ids,
        },
    }


def _actionable_payload(
    event_type: Any, payload: dict[str, Any], name: Any = None
) -> dict[str, Any]:
    if event_type == "runtime.turn_started":
        return {"user_text": payload.get("user_text")}
    if event_type == "tool.call_started":
        return {"arguments": payload.get("arguments")}
    if event_type in {"tool.call_completed", "tool.call_failed"}:
        return {
            "args": payload.get("args"),
            "result": _compact_tool_result(name, payload.get("result")),
            "status": _result_status(event_type, payload),
        }
    if event_type in {
        "runtime.goal_evaluated",
        "runtime.turn_completed",
        "runtime.task_completed",
        "runtime.task_failed",
    }:
        return payload
    return {}


def _result_status(event_type: str, payload: dict[str, Any]) -> Any:
    data = payload.get("data")
    if isinstance(data, dict) and data.get("status") is not None:
        return data["status"]
    if payload.get("status") is not None:
        return payload["status"]
    return "failed" if event_type == "tool.call_failed" else "success"


def _pair_tool_steps(
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    states: dict[tuple[Any, Any, str], dict[str, Any]] = {}
    order: list[tuple[Any, Any, str]] = []
    diagnostics: list[dict[str, Any]] = []
    for event in events:
        if event["type"] not in {
            "tool.call_started",
            "tool.call_completed",
            "tool.call_failed",
        }:
            continue
        call_id = event.get("tool_call_id")
        if not isinstance(call_id, str) or not call_id:
            diagnostics.append({"code": "missing_tool_call_id", "event_id": event["event_id"]})
            continue
        key = (event.get("session_id"), event.get("run_id"), call_id)
        if key not in states:
            states[key] = {
                "session_id": event.get("session_id"),
                "run_id": event.get("run_id"),
                "tool_call_id": call_id,
                "tool": event.get("name"),
                "arguments": None,
                "result": None,
                "status": None,
                "timestamp": event.get("timestamp"),
                "source_event_ids": [],
            }
            order.append(key)
        state = states[key]
        state["source_event_ids"].append(event["event_id"])
        if not state.get("tool"):
            state["tool"] = event.get("name")
        elif event.get("name") and state["tool"] != event.get("name"):
            raise ValueError(f"Conflicting tool names for {call_id}")
        payload = event["payload"]
        candidate_args = (
            payload.get("arguments")
            if event["type"] == "tool.call_started"
            else payload.get("args")
        )
        if candidate_args is not None:
            if state["arguments"] is not None and state["arguments"] != candidate_args:
                raise ValueError(f"Conflicting tool arguments for {call_id}")
            state["arguments"] = candidate_args
        if event["type"] in {"tool.call_completed", "tool.call_failed"}:
            candidate_result = payload.get("result")
            if state["status"] is not None:
                if state["result"] != candidate_result or state["status"] != payload.get("status"):
                    raise ValueError(f"Conflicting tool results for {call_id}")
                diagnostics.append(
                    {
                        "code": "duplicate_tool_result",
                        "tool_call_id": call_id,
                        "event_id": event["event_id"],
                    }
                )
            state["result"] = candidate_result
            state["status"] = payload.get("status")
            state["timestamp"] = event.get("timestamp") or state["timestamp"]
    return [states[key] for key in order], diagnostics


def _is_authoritative_goal_event(event: dict[str, Any]) -> bool:
    if event.get("type") != "runtime.goal_evaluated":
        return False
    payload = event.get("payload") or {}
    return bool(payload.get("won") is True or payload.get("success") is True)
