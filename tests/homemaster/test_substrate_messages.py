"""Round-trip and fail-closed tests for the substrate message converters.

Implements the assertions in plan/V3.7/decision-message-matrix.md §6.
"""

import base64

import pytest

from agentscope.message import (
    DataBlock,
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
)
from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from homemaster.substrate.messages import (
    MessageConversionError,
    assert_semantic_equal,
    from_agent_scope,
    to_agent_scope,
)

_PNG_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")


def _image_block(path: str = "/tmp/screenshot.png") -> ContentBlock:
    return ContentBlock(
        type="image",
        source={"type": "base64", "media_type": "image/png", "data": _PNG_B64},
        metadata={"path": path},
    )


def test_user_message_round_trip_text_only() -> None:
    original = [UserMessage.from_text("hello there")]
    back = from_agent_scope(to_agent_scope(original))
    assert back == original


def test_user_message_round_trip_multimodal() -> None:
    original = [
        UserMessage(
            content=[ContentBlock(text="look"), _image_block(), ContentBlock(text="?")]
        )
    ]
    converted = to_agent_scope(original)
    assert len(converted) == 1
    assert converted[0].role == "user"
    assert any(isinstance(b, DataBlock) for b in converted[0].content)
    back = from_agent_scope(converted)
    assert back == original


def test_assistant_with_tool_calls_and_results_round_trip() -> None:
    original = [
        UserMessage.from_text("turn off the light"),
        AssistantMessage(
            content=[ContentBlock(text="doing it")],
            reasoning_content="user asked politely",
            tool_calls=[
                ToolCall(id="call_1", name="home_control", arguments={"on": False}),
                ToolCall(id="call_2", name="home_control", arguments={"on": True}),
            ],
            finish_reason="tool_calls",
            usage={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
            provider_metadata={"provider_request_id": "req_1"},
        ),
        ToolResultMessage(
            tool_call_id="call_1",
            name="home_control",
            content=[ContentBlock(text="ok")],
            data={"backend_attempted": True, "status": "success"},
        ),
        ToolResultMessage(
            tool_call_id="call_2",
            name="home_control",
            content=[ContentBlock(text="boom"), _image_block("trace.png")],
            is_error=True,
            data={"status": "outcome_unknown", "backend_attempted": True},
            provider_metadata={"latency_ms": 42},
        ),
        AssistantMessage(content=[ContentBlock(text="done")]),
    ]
    converted = to_agent_scope(original)
    # one reply Msg accumulates the whole turn: calls, results, trailing text
    assert [m.role for m in converted] == ["user", "assistant"]
    reply = converted[1]
    call_blocks = [b for b in reply.content if isinstance(b, ToolCallBlock)]
    result_blocks = [b for b in reply.content if isinstance(b, ToolResultBlock)]
    assert [b.id for b in call_blocks] == ["call_1", "call_2"]
    assert all(b.state == "finished" for b in call_blocks)
    assert result_blocks[1].state == ToolResultState.ERROR
    assert result_blocks[1].metadata["hm"]["data"]["backend_attempted"] is True
    assert isinstance(reply.content[0], ThinkingBlock)
    assert reply.metadata["hm"]["segments"][0]["finish_reason"] == "tool_calls"
    assert reply.usage.input_tokens == 11
    assert reply.metadata["hm"]["segments"][0]["usage"] == {
        "input_tokens": 11,
        "output_tokens": 7,
        "total_tokens": 18,
    }

    back = from_agent_scope(converted)
    assert back == original


def test_tool_result_without_assistant_parent_fails_closed() -> None:
    orphan = [ToolResultMessage(tool_call_id="x", name="t", content=[])]
    with pytest.raises(MessageConversionError):
        to_agent_scope(orphan)


def test_from_agent_scope_rejects_system_role() -> None:
    msg = Msg(role="system", name="sys", content=[TextBlock(text="s")])
    with pytest.raises(MessageConversionError):
        from_agent_scope([msg])


def test_from_agent_scope_tolerates_unparseable_tool_input() -> None:
    # A malformed provider frame must not abort the session mirror: the call
    # identity survives with empty arguments (its paired result block records
    # the real failure).
    msg = Msg(
        role="assistant",
        name="a",
        content=[
            ToolCallBlock(id="c1", name="t", input='{"broken": '),
        ],
    )
    converted = from_agent_scope([msg])
    assert len(converted) == 1
    assert converted[0].tool_calls[0].id == "c1"
    assert converted[0].tool_calls[0].arguments == {}


def test_from_agent_scope_tolerates_non_dict_tool_input() -> None:
    msg = Msg(
        role="assistant",
        name="a",
        content=[ToolCallBlock(id="c1", name="t", input='[1, 2]')],
    )
    converted = from_agent_scope([msg])
    assert converted[0].tool_calls[0].arguments == {}


def test_from_agent_scope_tolerates_non_image_data_block() -> None:
    msg = Msg(
        role="assistant",
        name="a",
        content=[
            DataBlock(
                source={
                    "type": "base64",
                    "media_type": "audio/mpeg",
                    "data": _PNG_B64,
                }
            )
        ],
    )
    converted = from_agent_scope([msg])
    assert len(converted) == 1
    assert "unconvertible" in converted[0].content[0].text
    assert converted[0].content[0].metadata["hm_unconvertible"] == "DataBlock"


def test_to_agent_scope_rejects_unknown_source_type() -> None:
    bad = UserMessage(
        content=[
            ContentBlock(
                type="image",
                source={"type": "mystery", "data": _PNG_B64},
            )
        ]
    )
    with pytest.raises(MessageConversionError):
        to_agent_scope(bad)


def test_as_to_hm_to_as_semantic_equality() -> None:
    original = Msg(
        role="assistant",
        name="homemaster",
        content=[
            ThinkingBlock(thinking="plan"),
            TextBlock(text="calling tool"),
            ToolCallBlock(id="tc1", name="echo", input='{"value": "x"}'),
            ToolResultBlock(
                id="tc1",
                name="echo",
                output=[TextBlock(text="x")],
                state=ToolResultState.SUCCESS,
                metadata={"hm": {"data": {"backend_attempted": False}}},
            ),
            TextBlock(text="done"),
        ],
    )
    round_tripped = to_agent_scope(from_agent_scope([original]))
    assert len(round_tripped) == 1
    assert_semantic_equal(original, round_tripped[0])


def test_block_metadata_survives_round_trip() -> None:
    original = [
        UserMessage(
            content=[
                ContentBlock(text="a", metadata={"trace": "t1"}),
                _image_block("evidence/pic.png"),
            ]
        )
    ]
    back = from_agent_scope(to_agent_scope(original))
    assert back == original


def test_empty_history_and_empty_content() -> None:
    assert to_agent_scope([]) == []
    assert from_agent_scope([]) == []
    original = [AssistantMessage()]
    back = from_agent_scope(to_agent_scope(original))
    assert back == original


def test_assert_semantic_equal_detects_payload_drift() -> None:
    a = Msg(role="assistant", name="a", content=[TextBlock(text="one")])
    b = Msg(role="assistant", name="a", content=[TextBlock(text="two")])
    with pytest.raises(AssertionError, match="block payload"):
        assert_semantic_equal(a, b)


def test_leading_empty_segment_keeps_positional_metadata() -> None:
    # Segments with n=0 are originally-empty AssistantMessages; a leading one
    # must emit its own message instead of being consumed by the next
    # segment's text (which previously swallowed its metadata record).
    original = [
        AssistantMessage(provider_metadata={"kind": "empty-lead"}),
        AssistantMessage(
            content=[ContentBlock(text="real answer")],
            provider_metadata={"kind": "text-seg"},
        ),
    ]
    back = from_agent_scope(to_agent_scope(original))
    assert len(back) == 2
    assert not back[0].content
    assert back[0].provider_metadata.get("kind") == "empty-lead"
    assert back[1].content[0].text == "real answer"
    assert back[1].provider_metadata.get("kind") == "text-seg"
