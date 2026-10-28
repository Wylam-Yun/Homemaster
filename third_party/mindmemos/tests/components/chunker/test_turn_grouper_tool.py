"""Tool-message preservation tests for ``TurnGrouper`` (V3.8 regression fix).

Tool-role messages carry tool-call results inside an agent exchange. They must
attach to the surrounding turn — never be dropped, never start a new turn, and
count as assistant-side evidence for boundary classification.
"""

from __future__ import annotations

from mindmemos.components.chunker.vanilla.turn_grouper import TurnGrouper
from mindmemos.typing.memory import DialogueMessage


def _dm(role: str, content: str, timestamp: int | None = None) -> DialogueMessage:
    return DialogueMessage(role=role, content=content, timestamp=timestamp)


def _group(messages: list[DialogueMessage]):
    return TurnGrouper().group(list(enumerate(messages)))


def _roles(turns: list) -> list[list[str]]:
    return [[m.role for m in t.messages] for t in turns]


class TestToolMessagesAttachToCurrentTurn:
    def test_tool_message_inside_exchange_is_kept(self) -> None:
        turns = _group(
            [
                _dm("user", "weather?", 1000),
                _dm("assistant", "checking…", 1001),
                _dm("tool", '{"temp": "21C"}', 1002),
                _dm("assistant", "It's 21C", 1003),
            ]
        )
        assert _roles(turns) == [["user", "assistant", "tool", "assistant"]]
        assert turns[0].boundary == "complete"

    def test_tool_message_does_not_start_a_new_turn(self) -> None:
        turns = _group(
            [
                _dm("user", "q1", 1000),
                _dm("assistant", "a1", 1001),
                _dm("tool", "t1", 1002),
                _dm("user", "q2", 1003),
                _dm("assistant", "a2", 1004),
            ]
        )
        assert _roles(turns) == [
            ["user", "assistant", "tool"],
            ["user", "assistant"],
        ]
        assert [t.boundary for t in turns] == ["complete", "complete"]

    def test_leading_tool_message_is_not_dropped(self) -> None:
        # A tool result arriving before any user/assistant message must be
        # buffered into the first turn rather than silently discarded.
        turns = _group(
            [
                _dm("tool", "t0", 1000),
                _dm("user", "q", 1001),
                _dm("assistant", "a", 1002),
            ]
        )
        assert _roles(turns) == [["tool", "user", "assistant"]]

    def test_consecutive_tool_messages_stay_together(self) -> None:
        turns = _group(
            [
                _dm("user", "q", 1000),
                _dm("assistant", "a1", 1001),
                _dm("tool", "t1", 1002),
                _dm("tool", "t2", 1003),
                _dm("assistant", "a2", 1004),
            ]
        )
        assert _roles(turns) == [["user", "assistant", "tool", "tool", "assistant"]]

    def test_no_tool_messages_lost_across_many_turns(self) -> None:
        messages = [
            _dm("user", "u1", 1000),
            _dm("assistant", "a1", 1001),
            _dm("tool", "t1", 1002),
            _dm("user", "u2", 2000),
            _dm("assistant", "a2", 2001),
            _dm("tool", "t2", 2002),
            _dm("tool", "t3", 2003),
        ]
        turns = _group(messages)
        flat = [m.role for t in turns for m in t.messages]
        assert flat.count("tool") == 3
        assert len(flat) == len(messages)


class TestToolMessagesBoundaryClassification:
    def test_tool_counts_as_assistant_side_evidence(self) -> None:
        # [user, tool] without a literal assistant message still reads as a
        # complete exchange (the assistant ran a tool and replied via it).
        turns = _group([_dm("user", "q", 1000), _dm("tool", "t", 1001)])
        assert turns[0].boundary == "complete"

    def test_tool_only_turn_is_orphan_not_open_tail(self) -> None:
        turns = _group([_dm("tool", "t", 1000), _dm("assistant", "a", 1001)])
        assert turns[0].boundary == "orphan"

    def test_turn_starting_with_tool_then_user_is_open_head(self) -> None:
        turns = _group([_dm("tool", "t", 1000), _dm("user", "u", 1001)])
        assert turns[0].boundary == "open_head"
