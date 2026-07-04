"""Tests for compaction utilities."""

from __future__ import annotations

from homemaster.agent.compact import (
    build_compaction_summary_message,
    microcompact_tool_results_by_type,
    split_preserving_tool_pairs,
    strip_old_images,
)
from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


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
    assert "END OF CONTEXT SUMMARY" in msg.content[0].text


def test_strip_old_images_keeps_recent_images_and_records_tool_args() -> None:
    messages = []
    for index in range(3):
        call_id = f"call_{index}"
        messages.append(
            AssistantMessage(
                tool_calls=[
                    ToolCall(
                        id=call_id,
                        name="robot_observe",
                        arguments={"room": f"room-{index}"},
                    )
                ]
            )
        )
        messages.append(
            ToolResultMessage(
                tool_call_id=call_id,
                name="robot_observe",
                content=[
                    ContentBlock(text="observation"),
                    ContentBlock(type="image", source={"type": "base64", "data": "abc"}),
                ],
            )
        )

    stripped, count = strip_old_images(messages, keep_recent_images=2)

    assert count == 1
    image_count = sum(
        1
        for message in stripped
        for block in message.content
        if block.type == "image"
    )
    assert image_count == 2
    text = "\n".join(
        block.text for message in stripped for block in message.content if block.text
    )
    assert "image stripped" in text
    assert "room-0" in text


def test_microcompact_tool_results_by_type_uses_per_tool_retention() -> None:
    messages = [
        ToolResultMessage(
            tool_call_id="m1",
            name="memory_retriever",
            content=[ContentBlock(text="old memory result " + "x" * 1000)],
            data={"hits": [{"summary": "cup on table"}]},
        ),
        ToolResultMessage(
            tool_call_id="m2",
            name="memory_retriever",
            content=[ContentBlock(text="recent memory result")],
        ),
        ToolResultMessage(
            tool_call_id="v1",
            name="robot_verify",
            content=[ContentBlock(text="verify result")],
        ),
    ]

    compacted, saved = microcompact_tool_results_by_type(
        messages,
        keep_recent_per_type={"memory_retriever": 1, "robot_verify": 1},
        default_keep_recent=0,
    )

    assert saved > 0
    assert compacted[0].content[0].text.startswith("[memory]")
    assert "cup on table" in compacted[0].content[0].text
    assert compacted[1].content[0].text == "recent memory result"
    assert compacted[2].content[0].text == "verify result"
