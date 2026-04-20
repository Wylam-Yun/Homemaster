"""Optional gateway bridge for message intake and trace summary replies."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from pydantic import BaseModel, Field

from task_brain.domain import TaskRequest
from task_brain.graph import run_task_graph
from task_brain.trace import normalize_graph_trace_events


class GatewayMessage(BaseModel):
    """Incoming gateway message envelope."""

    platform: str
    user_id: str
    session_id: str
    text: str
    request_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GatewayResponse(BaseModel):
    """Gateway output with acceptance and graph execution summary."""

    accepted: bool
    status: str
    reason: str | None = None
    task_request: TaskRequest | None = None
    final_status: str | None = None
    trace_summary: str = ""
    trace_events: list[dict[str, Any]] = Field(default_factory=list)


GraphRunner = Callable[..., dict[str, Any]]


def handle_message(
    *,
    message: GatewayMessage | dict[str, Any],
    scenario: str,
    allowlisted_users: Iterable[str] | None = None,
    graph_runner: GraphRunner = run_task_graph,
) -> GatewayResponse:
    """Handle one gateway message with allowlist + TaskRequest + graph run."""
    payload = message.model_copy(deep=True) if isinstance(message, GatewayMessage) else message
    gateway_message = (
        payload if isinstance(payload, GatewayMessage) else GatewayMessage.model_validate(payload)
    )

    allowlist = _normalize_allowlist(allowlisted_users)
    if allowlist is not None and gateway_message.user_id not in allowlist:
        return GatewayResponse(
            accepted=False,
            status="rejected",
            reason="user_not_allowlisted",
            trace_summary=f"rejected: user '{gateway_message.user_id}' is not allowlisted.",
        )

    task_request = _build_task_request(gateway_message)
    result = graph_runner(
        scenario=scenario,
        instruction=task_request.utterance,
        user_id=task_request.user_id,
    )
    final_status = str(result.get("final_status", "unknown"))
    trace_events = normalize_graph_trace_events(_coerce_trace(result.get("trace")))
    summary = summarize_trace_for_reply(
        scenario=scenario,
        final_status=final_status,
        trace_events=trace_events,
    )
    return GatewayResponse(
        accepted=True,
        status="processed",
        reason=None,
        task_request=task_request,
        final_status=final_status,
        trace_summary=summary,
        trace_events=trace_events,
    )


def summarize_trace_for_reply(
    *,
    scenario: str,
    final_status: str,
    trace_events: list[dict[str, Any]],
) -> str:
    """Render compact human-readable trace summary for gateway response."""
    event_names = [event["event"] for event in trace_events if "event" in event]
    key_path = " -> ".join(event_names[:8]) if event_names else "(no trace events)"

    lines = [
        f"scenario={scenario}",
        f"final_status={final_status}",
        f"event_count={len(trace_events)}",
        f"key_path={key_path}",
    ]
    if final_status != "success":
        failure_events = [
            name
            for name in event_names
            if name
            in {
                "analyze_failure",
                "decide_recovery",
                "post_action_verification_failed",
                "recovery_report_failure",
                "final_task_verification",
            }
        ]
        if failure_events:
            lines.append(f"failure_events={','.join(failure_events)}")
    return "; ".join(lines)


def _build_task_request(message: GatewayMessage) -> TaskRequest:
    request_id = message.request_id or f"gw-{message.session_id}"
    return TaskRequest(
        source=f"gateway:{message.platform}",
        user_id=message.user_id,
        utterance=message.text,
        request_id=request_id,
    )


def _normalize_allowlist(users: Iterable[str] | None) -> set[str] | None:
    if users is None:
        return None
    allowlist = {item for item in users if isinstance(item, str) and item}
    return allowlist


def _coerce_trace(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    events: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            events.append(item)
    return events


__all__ = [
    "GatewayMessage",
    "GatewayResponse",
    "handle_message",
    "summarize_trace_for_reply",
]
