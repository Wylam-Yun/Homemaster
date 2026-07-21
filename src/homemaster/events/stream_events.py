"""Public events yielded by the HomeMaster runtime stream.

Adapted from OpenHarness ``engine/stream_events.py`` at the V1.9 locked SHA.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from homemaster.agent.messages import AssistantMessage, ContentBlock
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


def project_stream_event(event: RuntimeEvent) -> StreamEvent | None:
    """Map allowlisted private runtime events to the public stream DTOs."""
    payload = sanitize_event_payload(event.payload)
    if event.type == "assistant.reply":
        return AssistantTextDelta(text=str(payload.get("reply") or ""))
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
    if event.type == "runtime.turn_completed":
        usage = payload.get("usage")
        return AssistantTurnComplete(
            message=AssistantMessage(
                content=[ContentBlock(text=str(payload.get("final_reply") or ""))]
            ),
            usage=dict(usage) if isinstance(usage, dict) else {},
        )
    if event.type in {
        "runtime.turn_failed",
        "runtime.budget_exhausted",
        "runtime.cancelled",
        "transport.request_failed",
    }:
        message = payload.get("error") or payload.get("error_code") or event.type
        return ErrorEvent(
            message=str(message),
            recoverable=event.type not in {"runtime.budget_exhausted", "runtime.cancelled"},
        )
    if event.type == "context.compaction":
        return StatusEvent(message=str(payload.get("message") or "context compaction"))
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
