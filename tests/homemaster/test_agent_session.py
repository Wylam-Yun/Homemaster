from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    ToolResultMessage,
    UserMessage,
)
from homemaster.agent.session import AgentSession


def test_session_appends_turn_messages() -> None:
    session = AgentSession(session_id="s1")
    session.append(UserMessage(content=[ContentBlock(text="你好")]))
    session.append(
        AssistantMessage(content=[ContentBlock(text="你好，我在。")], finish_reason="stop")
    )
    assert [m.role for m in session.messages] == ["user", "assistant"]


def test_session_keeps_tool_result_after_assistant_tool_call() -> None:
    session = AgentSession(session_id="s1")
    session.append(
        ToolResultMessage(
            tool_call_id="call_1",
            name="memory_retriever",
            is_error=True,
            content=[ContentBlock(text='{"error":"missing"}')],
        )
    )
    assert session.messages[-1].role == "tool"


def test_session_clear() -> None:
    session = AgentSession(session_id="s1")
    session.append(UserMessage.from_text("hi"))
    assert len(session.messages) == 1
    session.clear()
    assert len(session.messages) == 0
