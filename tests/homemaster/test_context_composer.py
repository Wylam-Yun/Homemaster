from homemaster.agent.context import ContextComposer
from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


def test_context_composer_keeps_tool_call_pairs_when_truncating() -> None:
    composer = ContextComposer(max_messages=3)
    messages = [
        UserMessage(content=[ContentBlock(text="old")]),
        AssistantMessage(
            content=[],
            tool_calls=[
                ToolCall(id="call_1", name="memory_retriever", arguments={"query": "水杯"})
            ],
            finish_reason="tool_calls",
        ),
        ToolResultMessage(
            tool_call_id="call_1",
            name="memory_retriever",
            content=[ContentBlock(text='{"items":[]}')],
        ),
        UserMessage(content=[ContentBlock(text="现在继续")]),
    ]
    context = composer.compose(messages=messages, tools=[])
    roles = [message.role for message in context.messages]
    assert roles[-3:] == ["assistant", "tool", "user"]
    assert context.messages[-2].tool_call_id == "call_1"


def test_context_composer_does_not_include_home_state_fields() -> None:
    composer = ContextComposer()
    context = composer.compose(
        messages=[UserMessage(content=[ContentBlock(text="你好")])], tools=[]
    )
    serialized = str(context.messages)
    assert "current_location" not in serialized
    assert "holding_object" not in serialized
    assert "memory_hits" not in serialized


def test_context_composer_respects_max_messages() -> None:
    composer = ContextComposer(max_messages=2)
    messages = [
        UserMessage(content=[ContentBlock(text="m1")]),
        AssistantMessage(content=[ContentBlock(text="r1")], finish_reason="stop"),
        UserMessage(content=[ContentBlock(text="m2")]),
        AssistantMessage(content=[ContentBlock(text="r2")], finish_reason="stop"),
    ]
    context = composer.compose(messages=messages)
    assert len(context.messages) == 2


def test_context_composer_passes_system_prompt() -> None:
    composer = ContextComposer(system_prompt="You are helpful.")
    context = composer.compose(messages=[], tools=[])
    assert context.system_prompt == "You are helpful."
