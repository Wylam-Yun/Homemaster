"""Compact — micro-compaction and summary utilities."""

from __future__ import annotations

import json
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


def strip_old_images(
    messages: list[Message],
    *,
    keep_recent_images: int,
) -> tuple[list[Message], int]:
    """Replace older image blocks with text placeholders, preserving latest N images."""

    image_positions: list[tuple[int, int]] = []
    tool_args_by_result_id = _tool_args_by_result_id(messages)
    for message_index, message in enumerate(messages):
        for block_index, block in enumerate(message.content):
            if block.type == "image":
                image_positions.append((message_index, block_index))
    if len(image_positions) <= keep_recent_images:
        return list(messages), 0
    keep = set(image_positions[-keep_recent_images:])
    stripped = 0
    result: list[Message] = []
    for message_index, message in enumerate(messages):
        next_content: list[ContentBlock] = []
        changed = False
        for block_index, block in enumerate(message.content):
            if block.type == "image" and (message_index, block_index) not in keep:
                tool_name = getattr(message, "name", getattr(message, "role", "unknown"))
                args = {}
                if isinstance(message, ToolResultMessage):
                    args = tool_args_by_result_id.get(message.tool_call_id, {})
                next_content.append(
                    ContentBlock(
                        text=(
                            f"[image stripped - {tool_name} @ message {message_index}, "
                            f"args={json.dumps(args, ensure_ascii=False, sort_keys=True)}]"
                        )
                    )
                )
                changed = True
                stripped += 1
            else:
                next_content.append(block)
        result.append(message.model_copy(update={"content": next_content}) if changed else message)
    return result, stripped


def microcompact_tool_results_by_type(
    messages: list[Message],
    *,
    keep_recent_per_type: dict[str, int],
    default_keep_recent: int,
) -> tuple[list[Message], int]:
    """Compact older tool results using per-tool retention counts."""

    indexes_by_name: dict[str, list[int]] = {}
    for index, message in enumerate(messages):
        if isinstance(message, ToolResultMessage):
            indexes_by_name.setdefault(message.name, []).append(index)
    keep_indexes: set[int] = set()
    for name, indexes in indexes_by_name.items():
        keep_count = keep_recent_per_type.get(name, default_keep_recent)
        if keep_count > 0:
            keep_indexes.update(indexes[-keep_count:])
    result: list[Message] = []
    saved = 0
    for index, message in enumerate(messages):
        if isinstance(message, ToolResultMessage) and index not in keep_indexes:
            text = "\n".join(block.text for block in message.content if block.text)
            compact_text = summarize_tool_result(
                tool_name=message.name,
                text=text,
                data=message.data,
            )
            saved += max(0, len(text) - len(compact_text)) // 4
            result.append(message.model_copy(update={"content": [ContentBlock(text=compact_text)]}))
        else:
            result.append(message)
    return result, saved


def summarize_tool_result(
    *,
    tool_name: str,
    text: str,
    data: dict | None = None,
) -> str:
    """Summarize old tool results for compacted context."""

    data = data or {}
    if tool_name == "memory_retriever":
        hits = data.get("hits")
        hit_count = len(hits) if isinstance(hits, list) else None
        top_hit = ""
        if isinstance(hits, list) and hits:
            top_hit = str(hits[0])[:200]
        count_text = "unknown" if hit_count is None else str(hit_count)
        return f"[memory] {count_text} hits, top-1: {top_hit or text[:200]}"
    if tool_name == "robot_navigate":
        return f"[navigate] {text[:300]}"
    if tool_name == "robot_verify":
        return f"[verify] {text[:300]}"
    if tool_name == "robot_observe":
        return f"[observe] {text[:300]}"
    lines = text.splitlines()
    tail = "\n".join(lines[-10:]) if lines else text[-500:]
    return f"[{tool_name}] {len(lines)} lines output, last 10:\n{tail}"


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
                    f"{summary}\n\n--- END OF CONTEXT SUMMARY ---"
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


def _tool_args_by_result_id(messages: list[Message]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for message in messages:
        if isinstance(message, AssistantMessage):
            for tool_call in message.tool_calls:
                result[tool_call.id] = tool_call.arguments
    return result
