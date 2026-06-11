from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from homemaster.providers.mimo_transport import MimoTransport


def test_parse_text_response() -> None:
    payload = {"content": [{"type": "text", "text": "你好，我在。"}]}
    msg = MimoTransport.parse_response_payload(payload)
    assert isinstance(msg, AssistantMessage)
    assert msg.content[0].text == "你好，我在。"
    assert msg.tool_calls == []


def test_parse_tool_use_response() -> None:
    payload = {
        "content": [
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "memory_retriever",
                "input": {"query": "水杯"},
            }
        ]
    }
    msg = MimoTransport.parse_response_payload(payload)
    assert msg.tool_calls[0].name == "memory_retriever"
    assert msg.tool_calls[0].arguments == {"query": "水杯"}


def test_empty_text_with_reasoning_is_not_response_missing_text() -> None:
    payload = {
        "content": [{"type": "thinking", "thinking": "checking"}],
        "stop_reason": "tool_use",
    }
    msg = MimoTransport.parse_response_payload(payload)
    assert msg.content == []
    assert msg.reasoning_content == "checking"


def test_parse_mixed_content_with_text_and_tool_use() -> None:
    payload = {
        "content": [
            {"type": "text", "text": "让我帮你查一下。"},
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "memory_retriever",
                "input": {"query": "水杯"},
            },
        ],
        "stop_reason": "tool_use",
    }
    msg = MimoTransport.parse_response_payload(payload)
    assert msg.content[0].text == "让我帮你查一下。"
    assert len(msg.tool_calls) == 1
    assert msg.finish_reason == "tool_calls"


def test_parse_stop_reason_normalization() -> None:
    payload = {
        "content": [{"type": "text", "text": "done"}],
        "stop_reason": "end_turn",
    }
    msg = MimoTransport.parse_response_payload(payload)
    assert msg.finish_reason == "stop"


def test_parse_usage() -> None:
    payload = {
        "content": [{"type": "text", "text": "hi"}],
        "stop_reason": "stop",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    msg = MimoTransport.parse_response_payload(payload)
    assert msg.usage == {"input_tokens": 10, "output_tokens": 5}


def test_complete_aggregates_stream(monkeypatch) -> None:
    calls = {"stream": 0}
    transport = MimoTransport(
        base_url="https://example.invalid",
        model="m",
        api_key="secret",
    )

    def fake_stream(*args, **kwargs):
        calls["stream"] += 1
        from homemaster.providers.transport import TransportDelta

        yield TransportDelta(type="transport.delta", text_delta="hi")
        yield TransportDelta(type="transport.delta", finish_reason="stop")

    monkeypatch.setattr(transport, "stream", fake_stream)
    msg = transport.complete([UserMessage.from_text("hello")])
    assert calls["stream"] == 1
    assert msg.text == "hi"


def test_anthropic_tool_result_content_is_text() -> None:
    transport = MimoTransport(
        base_url="https://example.invalid",
        model="m",
        api_key="secret",
    )
    payload = transport._build_anthropic_payload([
        ToolResultMessage(
            tool_call_id="call_1",
            name="task_interpreter",
            content=[ContentBlock(text='{"success": true}')],
        )
    ])
    content = payload["messages"][0]["content"][0]
    assert content["type"] == "tool_result"
    assert content["content"] == '{"success": true}'


def test_anthropic_payload_omits_max_tokens_by_default() -> None:
    transport = MimoTransport(
        base_url="https://example.invalid",
        model="m",
        api_key="secret",
    )

    payload = transport._build_anthropic_payload([UserMessage.from_text("hello")])

    assert "max_tokens" not in payload


def test_anthropic_payload_replays_reasoning_before_tool_use() -> None:
    transport = MimoTransport(
        base_url="https://example.invalid",
        model="m",
        api_key="secret",
    )
    payload = transport._build_anthropic_payload([
        AssistantMessage(
            reasoning_content="thinking",
            tool_calls=[
                ToolCall(id="call_1", name="task_interpreter", arguments={"utterance": "hi"})
            ],
        )
    ])
    content = payload["messages"][0]["content"]
    assert content[0] == {"type": "thinking", "thinking": "thinking"}
    assert content[1]["type"] == "tool_use"
