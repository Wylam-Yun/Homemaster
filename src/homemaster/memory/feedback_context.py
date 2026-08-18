"""Immutable provider-attempt context for explicit memory feedback."""

from __future__ import annotations

import copy
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any

from homemaster.agent.messages import Message, ToolResultMessage


@dataclass(frozen=True)
class FeedbackContextSnapshot:
    messages: tuple[Message, ...]
    recalled_memories: tuple[Any, ...]


def build_feedback_context_snapshot(
    messages: Sequence[Message],
    *,
    automatic_recalled_memories: Sequence[Any] = (),
    recalled_memories_by_tool_call_id: Mapping[str, Sequence[Any]] | None = None,
) -> FeedbackContextSnapshot:
    frozen_messages = tuple(message.model_copy(deep=True) for message in messages)
    visible_search_call_ids = {
        message.tool_call_id
        for message in frozen_messages
        if isinstance(message, ToolResultMessage) and message.name == "mindmemos_search"
    }
    recalled: list[Any] = []
    seen_ids: set[str] = set()
    sources = [automatic_recalled_memories]
    by_call = recalled_memories_by_tool_call_id or {}
    sources.extend(by_call.get(call_id, ()) for call_id in visible_search_call_ids)
    for source in sources:
        for memory in source:
            memory_id = str(getattr(memory, "id", "") or "")
            if not memory_id or memory_id in seen_ids:
                continue
            seen_ids.add(memory_id)
            copier = getattr(memory, "model_copy", None)
            recalled.append(
                copier(deep=True) if callable(copier) else copy.deepcopy(memory)
            )
    return FeedbackContextSnapshot(
        messages=frozen_messages,
        recalled_memories=tuple(recalled),
    )


def bind_feedback_contexts(
    *,
    tool_calls: Sequence[Any],
    frozen_messages: Sequence[Message],
    deps: MutableMapping[str, Any],
) -> None:
    feedback_calls = [call for call in tool_calls if call.name == "mindmemos_feedback"]
    if not feedback_calls:
        return
    snapshot = build_feedback_context_snapshot(
        frozen_messages,
        automatic_recalled_memories=deps.get("automatic_recalled_memories", ()),
        recalled_memories_by_tool_call_id=deps.get(
            "recalled_memories_by_tool_call_id", {}
        ),
    )
    by_call = deps.setdefault("memory_feedback_context_by_tool_call_id", {})
    for call in feedback_calls:
        by_call[call.id] = snapshot


def snapshot_to_dialogue_messages(snapshot: FeedbackContextSnapshot) -> list[Any]:
    from mindmemos.typing import DialogueMessage

    messages: list[Any] = []
    for message in snapshot.messages:
        text = "\n".join(
            block.text for block in message.content if block.type == "text" and block.text
        )
        if text:
            messages.append(DialogueMessage(role=message.role, content=text))
    return messages


__all__ = [
    "FeedbackContextSnapshot",
    "bind_feedback_contexts",
    "build_feedback_context_snapshot",
    "snapshot_to_dialogue_messages",
]
