"""Tests for Anthropic/OpenAI provider transport conversion."""

from __future__ import annotations

from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from homemaster.providers.transports import (
    AnthropicTransport,
    OpenAIChatTransport,
    TransportDelta,
    aggregate_deltas,
)


def test_anthropic_normalizes_text_response() -> None:
    msg = AnthropicTransport().normalize_response(
        {"content": [{"type": "text", "text": "你好，我在。"}]}
    )
    assert msg.text == "你好，我在。"
    assert msg.tool_calls == []


def test_anthropic_normalizes_tool_use_response() -> None:
    msg = AnthropicTransport().normalize_response({
        "content": [
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "memory_retriever",
                "input": {"query": "水杯"},
            }
        ],
        "stop_reason": "tool_use",
    })
    assert msg.tool_calls[0].name == "memory_retriever"
    assert msg.tool_calls[0].arguments == {"query": "水杯"}
    assert msg.finish_reason == "tool_calls"


def test_anthropic_normalizes_thinking_without_text() -> None:
    msg = AnthropicTransport().normalize_response({
        "content": [{"type": "thinking", "thinking": "checking"}],
        "stop_reason": "tool_use",
    })
    assert msg.content == []
    assert msg.reasoning_content == "checking"


def test_anthropic_stop_reason_and_usage() -> None:
    msg = AnthropicTransport().normalize_response({
        "content": [{"type": "text", "text": "done"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    })
    assert msg.finish_reason == "stop"
    assert msg.usage == {"input_tokens": 10, "output_tokens": 5}


def test_aggregate_deltas_builds_assistant_message() -> None:
    msg = aggregate_deltas([
        TransportDelta(type="transport.delta", text_delta="hi"),
        TransportDelta(type="transport.delta", finish_reason="stop"),
    ])
    assert msg.text == "hi"
    assert msg.finish_reason == "stop"


def test_anthropic_tool_result_content_is_text() -> None:
    payload = AnthropicTransport().build_create_kwargs(
        model="m",
        messages=[
            ToolResultMessage(
                tool_call_id="call_1",
                name="task_interpreter",
                content=[ContentBlock(text='{"success": true}')],
            )
        ],
    )
    content = payload["messages"][0]["content"][0]
    assert content["type"] == "tool_result"
    assert content["content"] == '{"success": true}'


def test_anthropic_payload_supports_latest_image_blocks(tmp_path) -> None:
    first_image = tmp_path / "frame-0000.png"
    latest_image = tmp_path / "frame-0001.png"
    first_image.write_bytes(b"first-png")
    latest_image.write_bytes(b"latest-png")

    payload = AnthropicTransport().build_create_kwargs(
        model="m",
        messages=[
            UserMessage(
                content=[
                    ContentBlock(text="Initial scene."),
                    ContentBlock.from_image_path(first_image),
                ]
            ),
            AssistantMessage(
                tool_calls=[
                    ToolCall(id="call_1", name="robot_observe", arguments={"mode": "look"})
                ],
                finish_reason="tool_calls",
            ),
            ToolResultMessage(
                tool_call_id="call_1",
                name="robot_observe",
                content=[
                    ContentBlock(text='{"frame_path": "frame-0001.png"}'),
                    ContentBlock.from_image_path(latest_image),
                ],
            ),
        ],
    )

    image_blocks = [
        block
        for message in payload["messages"]
        for block in message["content"]
        if block["type"] == "image"
    ]
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"]["data"] == ContentBlock.from_image_path(
        latest_image
    ).source["data"]


def test_anthropic_payload_includes_system_prompt_and_max_tokens() -> None:
    payload = AnthropicTransport().build_create_kwargs(
        model="m",
        messages=[UserMessage.from_text("hello")],
        system_prompt="You are HomeMaster.",
        max_output_tokens=8192,
    )
    assert payload["system"] == "You are HomeMaster."
    assert payload["max_tokens"] == 8192


def test_openai_payload_prepends_system_message_and_max_tokens() -> None:
    payload = OpenAIChatTransport().build_create_kwargs(
        model="m",
        messages=[UserMessage.from_text("hello")],
        system_prompt="You are HomeMaster.",
        max_output_tokens=4096,
    )
    assert payload["messages"][0] == {"role": "system", "content": "You are HomeMaster."}
    assert payload["messages"][1]["role"] == "user"
    assert payload["max_tokens"] == 4096
