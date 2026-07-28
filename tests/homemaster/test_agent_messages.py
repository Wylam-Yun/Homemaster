from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    normalize_content,
)


def test_tool_result_message_round_trips() -> None:
    msg = ToolResultMessage(
        tool_call_id="call_1",
        name="memory_retriever",
        is_error=True,
        content=[ContentBlock(text='{"error":"memory file missing"}')],
        data={"path": "missing.json"},
    )
    assert msg.role == "tool"
    assert msg.tool_call_id == "call_1"
    assert msg.is_error is True
    assert msg.content[0].text == '{"error":"memory file missing"}'


def test_assistant_message_can_hold_parallel_tool_calls() -> None:
    msg = AssistantMessage(
        content=[],
        tool_calls=[
            ToolCall(
                id="call_1",
                name="memory_retriever",
                arguments={"query": "水杯"},
            ),
            ToolCall(
                id="call_2",
                name="load_skill",
                arguments={"name": "fetch_object"},
            ),
        ],
        finish_reason="tool_calls",
    )
    assert msg.tool_calls[0].name == "memory_retriever"
    assert [call.id for call in msg.tool_calls] == ["call_1", "call_2"]


def test_reasoning_content_is_not_visible_content() -> None:
    msg = AssistantMessage(
        content=[ContentBlock(text="我可以帮你。")],
        reasoning_content="private reasoning replay",
        finish_reason="stop",
    )
    assert msg.content[0].text == "我可以帮你。"
    assert "private" not in msg.content[0].text


def test_user_message_keeps_text() -> None:
    msg = UserMessage(content=[ContentBlock(text="你好")])
    assert isinstance(msg.content, list)
    assert msg.content[0].text == "你好"


def test_normalize_content_accepts_external_strings() -> None:
    assert normalize_content("") == []
    assert normalize_content("你好")[0].text == "你好"


def test_assistant_message_text_property() -> None:
    msg = AssistantMessage(
        content=[ContentBlock(text="Hello"), ContentBlock(text="World")],
        finish_reason="stop",
    )
    assert msg.text == "Hello\nWorld"
