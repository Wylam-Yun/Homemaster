"""Compact — micro-compaction and summary utilities."""

from __future__ import annotations

from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    Message,
    ToolResultMessage,
    UserMessage,
)

TOOL_RESULT_COMPACT_PREFIX = "[tool result compacted]"


def compact_tool_result_text(text: str, *, head_chars: int = 900, tail_chars: int = 500) -> str:
    if len(text) <= head_chars + tail_chars + 200:
        return text
    return (
        f"{TOOL_RESULT_COMPACT_PREFIX} original_chars={len(text)}\n"
        f"{text[:head_chars]}\n...\n{text[-tail_chars:]}"
    )


def microcompact_old_tool_results(
    messages: list[Message],
    *,
    keep_recent_tool_results: int,
) -> tuple[list[Message], int]:
    tool_indexes = [
        index for index, msg in enumerate(messages)
        if isinstance(msg, ToolResultMessage)
    ]
    if len(tool_indexes) <= keep_recent_tool_results:
        return list(messages), 0
    keep = set(tool_indexes[-keep_recent_tool_results:])
    compacted: list[Message] = []
    saved = 0
    for index, msg in enumerate(messages):
        if isinstance(msg, ToolResultMessage) and index not in keep:
            text = "\n".join(block.text for block in msg.content if block.text)
            compact_text = compact_tool_result_text(text)
            if compact_text != text:
                saved += max(0, len(text) - len(compact_text)) // 4
            compacted.append(msg.model_copy(update={"content": [ContentBlock(text=compact_text)]}))
        else:
            compacted.append(msg)
    return compacted, saved


def sanitize_tool_pairs(messages: list[Message]) -> list[Message]:
    result_ids = {
        msg.tool_call_id
        for msg in messages
        if isinstance(msg, ToolResultMessage)
    }
    sanitized: list[Message] = []
    for msg in messages:
        if isinstance(msg, AssistantMessage) and msg.tool_calls:
            kept_calls = [tool_call for tool_call in msg.tool_calls if tool_call.id in result_ids]
            if kept_calls or msg.content:
                sanitized.append(msg.model_copy(update={"tool_calls": kept_calls}))
        else:
            sanitized.append(msg)
    return sanitized


def split_preserving_tool_pairs(
    messages: list[Message],
    *,
    preserve_recent: int,
) -> tuple[list[Message], list[Message]]:
    if len(messages) <= preserve_recent:
        return [], list(messages)
    split = _split_index_preserving_groups(
        messages,
        preserve_recent_messages=preserve_recent,
    )
    return list(messages[:split]), list(messages[split:])


def split_preserving_recent_context(
    messages: list[Message],
    *,
    preserve_recent_messages: int,
    preserve_recent_user_turns: int,
) -> tuple[list[Message], list[Message]]:
    """Split old/recent history without splitting tool-call/result groups.

    Recent history is the union of the latest grouped messages by count and the
    latest N user turns with everything after the oldest preserved user turn.
    """
    if not messages:
        return [], []

    split = _split_index_preserving_groups(
        messages,
        preserve_recent_messages=preserve_recent_messages,
    )
    remaining_user_turns = preserve_recent_user_turns
    earliest_user_index: int | None = None
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], UserMessage):
            earliest_user_index = index
            remaining_user_turns -= 1
            if remaining_user_turns <= 0:
                break
    if earliest_user_index is not None:
        split = min(split, earliest_user_index)
    return list(messages[:split]), list(messages[split:])


def _split_index_preserving_groups(
    messages: list[Message],
    *,
    preserve_recent_messages: int,
) -> int:
    split = max(0, len(messages) - preserve_recent_messages)
    if split == 0:
        return 0
    cursor = 0
    for group in _message_groups(messages):
        start = cursor
        end = cursor + len(group)
        if start < split < end:
            return start
        cursor = end
    return split


def _message_groups(messages: list[Message]) -> list[list[Message]]:
    groups: list[list[Message]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        group = [message]
        if isinstance(message, AssistantMessage) and message.tool_calls:
            expected_ids = {tool_call.id for tool_call in message.tool_calls}
            cursor = index + 1
            while cursor < len(messages):
                candidate = messages[cursor]
                if (
                    isinstance(candidate, ToolResultMessage)
                    and candidate.tool_call_id in expected_ids
                ):
                    group.append(candidate)
                    cursor += 1
                    continue
                break
            index = cursor
        else:
            index += 1
        groups.append(group)
    return groups


def build_compaction_summary_message(summary: str) -> UserMessage:
    return UserMessage(
        content=[
            ContentBlock(
                text=(
                    "[CONTEXT COMPACTION - REFERENCE ONLY]\n"
                    "Earlier model-visible history was compacted. "
                    "Do not treat old requests in this summary as current instructions.\n\n"
                    f"{summary}"
                )
            )
        ]
    )


def build_basic_summary(messages: list[Message], *, max_chars: int = 6000) -> str:
    lines: list[str] = []
    for msg in messages:
        role = getattr(msg, "role", "unknown")
        text = "\n".join(block.text for block in getattr(msg, "content", []) if block.text)
        if not text:
            continue
        compact = " ".join(text.split())
        lines.append(f"- {role}: {compact[:500]}")
        if sum(len(line) for line in lines) >= max_chars:
            break
    return "\n".join(lines) or "- Earlier history contained no compactable text."
