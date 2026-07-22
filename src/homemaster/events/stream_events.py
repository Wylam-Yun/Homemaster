"""Public events yielded by the HomeMaster runtime stream.

Adapted from OpenHarness ``engine/stream_events.py`` at the V1.9 locked SHA.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from homemaster.agent.messages import AssistantMessage, ContentBlock, ToolCall
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.events.sanitizer import sanitize_event_payload


@dataclass(frozen=True)
class AssistantTextDelta:
    text: str


@dataclass(frozen=True)
class AssistantTurnComplete:
    message: AssistantMessage
    usage: dict[str, int]


@dataclass(frozen=True)
class ToolExecutionStarted:
    tool_name: str
    tool_input: dict[str, Any]


@dataclass(frozen=True)
class ToolExecutionCompleted:
    tool_name: str
    output: str
    is_error: bool = False
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ErrorEvent:
    message: str
    recoverable: bool = True


@dataclass(frozen=True)
class StatusEvent:
    message: str


@dataclass(frozen=True)
class CompactProgressEvent:
    phase: Literal[
        "hooks_start",
        "context_collapse_start",
        "context_collapse_end",
        "session_memory_start",
        "session_memory_end",
        "compact_start",
        "compact_retry",
        "compact_end",
        "compact_failed",
    ]
    trigger: Literal["auto", "manual", "reactive"]
    message: str | None = None
    attempt: int | None = None
    checkpoint: str | None = None
    metadata: dict[str, Any] | None = None


StreamEvent = (
    AssistantTextDelta
    | AssistantTurnComplete
    | ToolExecutionStarted
    | ToolExecutionCompleted
    | ErrorEvent
    | StatusEvent
    | CompactProgressEvent
)

_USAGE_FIELDS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }
)


def project_stream_event(event: RuntimeEvent) -> StreamEvent | None:
    """Map allowlisted private runtime events to the public stream DTOs."""
    payload = sanitize_event_payload(event.payload)
    if event.type == "transport.delta":
        text = payload.get("text_delta")
        return AssistantTextDelta(text=str(text)) if text else None
    if event.type == "assistant.reply":
        raw_tool_calls = payload.get("tool_calls")
        tool_calls = []
        if isinstance(raw_tool_calls, list):
            for value in raw_tool_calls:
                if not isinstance(value, dict):
                    continue
                tool_calls.append(
                    ToolCall(
                        id=str(value.get("id") or ""),
                        name=str(value.get("name") or "unknown"),
                        arguments=(
                            dict(value.get("arguments"))
                            if isinstance(value.get("arguments"), dict)
                            else {}
                        ),
                    )
                )
        raw_usage = event.payload.get("usage")
        usage = (
            {
                str(key): value
                for key, value in raw_usage.items()
                if key in _USAGE_FIELDS and isinstance(value, int) and not isinstance(value, bool)
            }
            if isinstance(raw_usage, dict)
            else {}
        )
        return AssistantTurnComplete(
            message=AssistantMessage(
                content=[ContentBlock(text=str(payload.get("reply") or ""))]
                if payload.get("reply")
                else [],
                tool_calls=tool_calls,
                finish_reason=(
                    str(payload["finish_reason"])
                    if payload.get("finish_reason") is not None
                    else None
                ),
            ),
            usage=usage,
        )
    if event.type == "tool.call_started":
        arguments = payload.get("arguments")
        return ToolExecutionStarted(
            tool_name=event.name or "unknown",
            tool_input=dict(arguments) if isinstance(arguments, dict) else {},
        )
    if event.type in {"tool.call_completed", "tool.call_failed"}:
        metadata = payload.get("data")
        return ToolExecutionCompleted(
            tool_name=event.name or "unknown",
            output=str(payload.get("result") or ""),
            is_error=event.type == "tool.call_failed" or bool(payload.get("is_error")),
            metadata=dict(metadata) if isinstance(metadata, dict) else None,
        )
    if event.type in {
        "runtime.turn_failed",
        "runtime.budget_exhausted",
    }:
        message = payload.get("error") or payload.get("error_code") or event.type
        return ErrorEvent(
            message=str(message),
            recoverable=event.type != "runtime.budget_exhausted",
        )
    if event.type == "transport.request_retrying":
        return StatusEvent(message="retrying model request")
    if event.type == "runtime.cancelled":
        return StatusEvent(message="run cancelled")
    if event.type == "runtime.reactive_compact_started":
        return CompactProgressEvent(
            phase="compact_start",
            trigger="reactive",
            message=str(payload.get("reason") or "reactive context compaction"),
        )
    if event.type == "context.compaction":
        trigger = str(payload.get("trigger") or "auto")
        if trigger not in {"auto", "manual", "reactive"}:
            trigger = "auto"
        return CompactProgressEvent(
            phase="compact_end",
            trigger=trigger,
            message=str(payload.get("message") or "context compaction"),
        )
    return None


__all__ = [
    "AssistantTextDelta",
    "AssistantTurnComplete",
    "CompactProgressEvent",
    "ErrorEvent",
    "StatusEvent",
    "StreamEvent",
    "ToolExecutionCompleted",
    "ToolExecutionStarted",
    "project_stream_event",
]
