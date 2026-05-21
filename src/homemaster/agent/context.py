"""ContextComposer — builds normalized request context for LLMTransport.

Composes system prompt, message history, and tool schemas. Keeps tool_call
and tool_result pairs intact when truncating.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homemaster.agent.messages import (
    AssistantMessage,
    Message,
    ToolResultMessage,
)


@dataclass
class ComposedContext:
    messages: list[Message]
    system_prompt: str = ""
    tools: list[dict[str, Any]] | None = None


class ContextComposer:
    """Baseline context composer that preserves tool_call/tool_result pairs."""

    def __init__(
        self,
        max_messages: int = 50,
        system_prompt: str = "",
    ) -> None:
        self._max_messages = max_messages
        self._system_prompt = system_prompt

    def compose(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        system_prompt: str | None = None,
    ) -> ComposedContext:
        prompt = system_prompt if system_prompt is not None else self._system_prompt
        selected = self._select_messages(messages)
        return ComposedContext(
            messages=selected,
            system_prompt=prompt,
            tools=tools,
        )

    def _select_messages(self, messages: list[Message]) -> list[Message]:
        if len(messages) <= self._max_messages:
            return list(messages)

        # Keep the last max_messages, but ensure tool_call/tool_result pairs
        # are not broken: if we keep a tool result, we must keep its preceding
        # assistant message with the matching tool_call.
        selected = messages[-self._max_messages:]

        # Scan from start: if first message is a ToolResultMessage, find and
        # include the preceding AssistantMessage that has the matching tool_call.
        if selected and isinstance(selected[0], ToolResultMessage):
            needed_ids = set()
            for msg in selected:
                if isinstance(msg, ToolResultMessage):
                    needed_ids.add(msg.tool_call_id)

            # Look backwards in the original message list for matching assistant
            prefix_start = len(messages) - self._max_messages - 1
            while prefix_start >= 0 and needed_ids:
                msg = messages[prefix_start]
                if isinstance(msg, AssistantMessage):
                    for tc in msg.tool_calls:
                        if tc.id in needed_ids:
                            selected = [msg] + selected
                            needed_ids.discard(tc.id)
                prefix_start -= 1

        return selected
