"""Tests for compaction utilities."""

from __future__ import annotations

from homemaster.agent.compact import (
    build_basic_summary,
    build_compaction_summary_message,
    compact_tool_result_text,
    split_preserving_tool_pairs,
)
from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


def test_compact_tool_result_text_short_text_unchanged() -> None:
    text = "short result"
    assert compact_tool_result_text(text) == text


def test_compact_tool_result_text_long_text_compacted() -> None:
    text = "x" * 2000
    result = compact_tool_result_text(text)
    assert result.startswith("[tool result compacted]")
    assert len(result) < len(text)


def test_split_preserving_tool_pairs_keeps_result_with_call() -> None:
    call = ToolCall(id="call_1", name="robot_observe", arguments={})
    messages = [
        UserMessage(content=[ContentBlock(text="start")]),
        AssistantMessage(tool_calls=[call]),
        ToolResultMessage(
            tool_call_id="call_1", name="robot_observe",
            content=[ContentBlock(text="obs")],
        ),
        UserMessage(content=[ContentBlock(text="next")]),
    ]

    older, recent = split_preserving_tool_pairs(messages, preserve_recent=2)

    assert older == [messages[0]]
    assert recent == messages[1:]


def test_split_preserving_tool_pairs_no_split_when_short() -> None:
    messages = [UserMessage(content=[ContentBlock(text="a")])]
    older, recent = split_preserving_tool_pairs(messages, preserve_recent=10)
    assert older == []
    assert recent == messages


def test_build_compaction_summary_message_format() -> None:
    msg = build_compaction_summary_message("test summary")
    assert "CONTEXT COMPACTION" in msg.content[0].text
    assert "test summary" in msg.content[0].text


def test_build_basic_summary_handles_empty() -> None:
    result = build_basic_summary([])
    assert "no compactable text" in result


def test_build_basic_summary_produces_output() -> None:
    messages = [
        UserMessage(content=[ContentBlock(text="find the apple")]),
        AssistantMessage(content=[ContentBlock(text="I'll look for it.")]),
    ]
    result = build_basic_summary(messages)
    assert "find the apple" in result
    assert "I'll look for it" in result
