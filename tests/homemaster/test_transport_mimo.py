from homemaster.agent.messages import AssistantMessage
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
