"""LLMTransport — abstract transport interface for the generic agent loop.

Transport is responsible for sending normalized messages to a provider and
returning normalized AssistantMessage objects. Provider-specific response
handling (MiMo thinking, Anthropic tool_use, etc.) is encapsulated here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from homemaster.agent.messages import AssistantMessage, Message, ToolCall


class TransportDelta:
    """A single streaming delta from the provider."""

    def __init__(
        self,
        *,
        type: str,
        text_delta: str | None = None,
        reasoning_delta: str | None = None,
        tool_call_delta: ToolCall | None = None,
        finish_reason: str | None = None,
        usage: dict[str, int] | None = None,
        provider_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.type = type
        self.text_delta = text_delta
        self.reasoning_delta = reasoning_delta
        self.tool_call_delta = tool_call_delta
        self.finish_reason = finish_reason
        self.usage = usage
        self.provider_metadata = provider_metadata or {}


class LLMTransport(ABC):
    """Abstract transport for the generic agent loop."""

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        event_sink: Any = None,
        run_id: str = "",
    ) -> Iterator[TransportDelta]:
        """Stream deltas from the provider. Yields transport.delta events."""
        ...

    def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        event_sink: Any = None,
        run_id: str = "",
    ) -> AssistantMessage:
        """Convenience wrapper: stream + aggregate into a single AssistantMessage."""
        deltas = list(self.stream(messages, tools, event_sink=event_sink, run_id=run_id))
        return self._aggregate(deltas)

    @staticmethod
    def _aggregate(deltas: list[TransportDelta]) -> AssistantMessage:
        """Aggregate streaming deltas into a single AssistantMessage."""
        from homemaster.agent.messages import ContentBlock

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

        content = [ContentBlock(text="".join(text_parts))] if text_parts else []
        reasoning = "".join(reasoning_parts) if reasoning_parts else None

        return AssistantMessage(
            content=content,
            reasoning_content=reasoning,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            provider_metadata=provider_metadata,
        )
