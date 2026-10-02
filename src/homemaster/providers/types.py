"""Provider transport shared types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homemaster.agent.messages import AssistantMessage, ContentBlock, ToolCall


@dataclass
class TransportDelta:
    """A normalized streaming delta from any provider SDK."""

    type: str
    text_delta: str | None = None
    reasoning_delta: str | None = None
    tool_call_delta: ToolCall | None = None
    finish_reason: str | None = None
    usage: dict[str, int] | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)


def aggregate_deltas(deltas: list[TransportDelta]) -> AssistantMessage:
    """Aggregate streaming deltas into one normalized assistant message."""

    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    finish_reason: str | None = None
    usage: dict[str, int] | None = None
    provider_metadata: dict[str, Any] = {}

    for delta in deltas:
        if delta.text_delta:
            text_parts.append(delta.text_delta)
        if delta.reasoning_delta:
            reasoning_parts.append(delta.reasoning_delta)
        if delta.tool_call_delta:
            tool_calls.append(delta.tool_call_delta)
        if delta.finish_reason:
            finish_reason = delta.finish_reason
        if delta.usage:
            usage = delta.usage
        if delta.provider_metadata:
            provider_metadata.update(delta.provider_metadata)

    return AssistantMessage(
        content=[ContentBlock(text="".join(text_parts))] if text_parts else [],
        reasoning_content="".join(reasoning_parts) if reasoning_parts else None,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage=usage,
        provider_metadata=provider_metadata,
    )
