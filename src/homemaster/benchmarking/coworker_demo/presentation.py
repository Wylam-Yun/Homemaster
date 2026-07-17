"""Safe, allowlisted projection of runtime events for live presentation."""

from __future__ import annotations

from typing import Any

from homemaster.benchmarking.coworker_demo.correlation import action_id_for
from homemaster.events.runtime_events import RuntimeEvent

_DISPLAY_LIMIT = 320
_TOOL_EVENTS = {"tool.call_started", "tool.call_completed", "tool.call_failed"}
_RUNTIME_STATUSES = {
    "runtime.turn_completed": "succeeded",
    "runtime.turn_failed": "failed",
}
_ARGUMENT_FIELDS = {
    "task_planner": ("goal", "current_subtask", "next_focus"),
    "task_progress_check": ("current_subtask", "next_focus"),
    "skill_view": ("skill_name",),
    "browser_navigate": ("route",),
    "browser_observe": (),
    "browser_click": ("bid",),
    "browser_fill": ("bid", "value"),
    "browser_select": ("bid", "value"),
    "browser_wait": ("job_id", "target_status"),
    "terminal_execute": ("command",),
    "sop_decide": ("stage", "decision"),
}
_CLICK_RESULT_FIELDS = (
    "check",
    "ready",
    "query",
    "stage",
    "status",
    "alarm_code",
    "job_id",
    "operation",
)


def _display_primitive(value: Any) -> str | int | float | bool | None:
    if isinstance(value, str):
        return value[:_DISPLAY_LIMIT]
    if value is None or isinstance(value, bool | int | float):
        return value
    raise TypeError("presentation fields must be primitives")


def _allow_fields(source: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    result: dict[str, Any] = {}
    for key in fields:
        if key not in source:
            continue
        try:
            result[key] = _display_primitive(source[key])
        except TypeError:
            continue
    return result


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _visible_layers(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    visible = _dict(data.get("visible_observation"))
    receipt = _dict(visible.get("receipt"))
    payload = _dict(receipt.get("payload"))
    return visible, receipt, payload


def _evidence_refs(data: dict[str, Any], receipt: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for source in (data.get("evidence_refs"), receipt.get("evidence_refs")):
        if source is None:
            continue
        items = source if isinstance(source, list | tuple) else [source]
        for item in items:
            if not isinstance(item, str | bool | int | float):
                continue
            rendered = str(item)[:_DISPLAY_LIMIT]
            if rendered and rendered not in result:
                result.append(rendered)
    return result


def summarize_tool_result(tool_name: str, data: Any) -> dict[str, Any]:
    """Build a fresh, tool-specific result summary from trusted result data."""
    safe_data = _dict(data)
    visible, _receipt, payload = _visible_layers(safe_data)
    if tool_name == "browser_click":
        summary = _allow_fields(payload, _CLICK_RESULT_FIELDS)
        if summary:
            return summary
        return _allow_fields(visible, ("job_id", "operation", "status"))
    if tool_name == "browser_wait":
        return _allow_fields(visible or safe_data, ("job_id", "operation", "status"))
    if tool_name in {"browser_fill", "browser_select"}:
        return _allow_fields(visible or safe_data, ("bid", "value", "readback"))
    if tool_name == "terminal_execute":
        return _allow_fields(safe_data, ("exit_code", "stdout", "stderr"))
    if tool_name == "sop_decide":
        return _allow_fields(safe_data, ("backend_status", "terminal", "classification"))
    if tool_name in {"task_planner", "task_progress_check"}:
        return _allow_fields(
            safe_data,
            ("success", "status", "goal", "current_subtask", "next_focus", "completion_summary"),
        )
    if tool_name == "skill_view":
        return _allow_fields(safe_data, ("success", "name", "description"))
    return _allow_fields(safe_data, ("success",))


def project_runtime_event(event: RuntimeEvent) -> dict[str, Any] | None:
    """Project a runtime event into the presentation API's safe lifecycle schema."""
    if event.type in _RUNTIME_STATUSES:
        return {
            "runtime_event_type": event.type,
            "status": _RUNTIME_STATUSES[event.type],
            "timestamp": event.timestamp,
        }
    if event.type not in _TOOL_EVENTS:
        return None
    if not all(
        isinstance(value, str) and bool(value.strip())
        for value in (event.run_id, event.tool_call_id, event.name)
    ):
        return None

    payload = _dict(event.payload)
    data = _dict(payload.get("data"))
    action_id = action_id_for(event.run_id, event.tool_call_id)
    if event.type != "tool.call_started":
        trusted_action_id = data.get("action_id")
        if isinstance(trusted_action_id, str) and trusted_action_id.strip():
            action_id = trusted_action_id[:_DISPLAY_LIMIT]

    projected: dict[str, Any] = {
        "runtime_event_type": event.type,
        "tool_call_id": event.tool_call_id,
        "action_id": action_id,
        "tool_name": event.name,
        "status": _tool_status(event.type, data),
    }
    if event.type == "tool.call_started":
        projected["arguments"] = _allow_fields(
            payload.get("arguments"), _ARGUMENT_FIELDS.get(event.name, ())
        )
    else:
        projected["result"] = summarize_tool_result(event.name, data)
        _visible, receipt, _receipt_payload = _visible_layers(data)
        projected["evidence_refs"] = _evidence_refs(data, receipt)
    projected["timestamp"] = event.timestamp
    return projected


def _tool_status(event_type: str, data: dict[str, Any]) -> str:
    if event_type == "tool.call_started":
        return "running"
    backend_status = data.get("backend_status")
    if event_type == "tool.call_completed":
        return "accepted" if backend_status == "accepted" else "succeeded"
    return "rejected" if backend_status == "rejected" else "failed"
