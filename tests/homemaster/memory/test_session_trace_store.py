"""Tests for SessionTraceStore canonical projection (V3.8 §4.1/§4.2)."""

from __future__ import annotations

import json

import pytest

from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from homemaster.memory.memory_work_store import SQLiteMemoryWorkStore
from homemaster.memory.session_trace_store import (
    SessionTraceStore,
    message_to_trace_event,
)


@pytest.fixture()
def trace(tmp_path):
    store = SQLiteMemoryWorkStore(tmp_path / "work.db")
    store.open()
    yield SessionTraceStore(store)
    store.close()


def _user(text: str) -> UserMessage:
    return UserMessage(content=[ContentBlock(type="text", text=text)])


def _assistant(
    text: str = "", calls: list[ToolCall] | None = None, reasoning: str | None = None
) -> AssistantMessage:
    return AssistantMessage(
        content=[ContentBlock(type="text", text=text)] if text else [],
        tool_calls=calls or [],
        reasoning_content=reasoning,
    )


def _tool(
    call_id: str, name: str, content: str, *, is_error: bool = False, data=None
) -> ToolResultMessage:
    return ToolResultMessage(
        tool_call_id=call_id,
        name=name,
        content=[ContentBlock(type="text", text=content)],
        is_error=is_error,
        data=data,
    )


class TestDialogue:
    def test_tool_call_and_result_preserved_with_matching_id(self, trace):
        call = ToolCall(id="call-1", name="mindmemos_search", arguments={"query": "q"})
        msgs = [
            _user("find it"),
            _assistant("", [call], reasoning="private chain-of-thought"),
            _tool("call-1", "mindmemos_search", '{"hits":[]}', data={"backend_attempted": True}),
            _assistant("answer"),
        ]
        trace.begin_turn("s1", 1, "run-1", message_start_index=0)
        trace.commit_terminal("s1", "turn-1", status="completed", messages=msgs)
        dialogue = trace.dialogue_for_turn("s1", "turn-1")

        assert dialogue[0] == {"role": "user", "content": "find it"}
        # assistant tool call → JSON envelope with tool_call_id
        call_env = json.loads(dialogue[1]["content"])
        assert call_env == {
            "tool_call_id": "call-1",
            "name": "mindmemos_search",
            "arguments": {"query": "q"},
        }
        assert dialogue[1]["role"] == "assistant"
        # tool result → tool envelope, same tool_call_id
        tool_env = json.loads(dialogue[2]["content"])
        assert tool_env["tool_call_id"] == "call-1"
        assert tool_env["status"] == "success"
        assert dialogue[2]["role"] == "tool"
        assert dialogue[3] == {"role": "assistant", "content": "answer"}

    def test_reasoning_never_enters_dialogue(self, trace):
        msgs = [_user("u"), _assistant("visible", reasoning="SECRET-CHAIN")]
        trace.begin_turn("s1", 1, "run-1", message_start_index=0)
        trace.commit_terminal("s1", "turn-1", status="completed", messages=msgs)
        dialogue = trace.dialogue_for_turn("s1", "turn-1")
        blob = json.dumps(dialogue)
        assert "SECRET-CHAIN" not in blob

    def test_whitespace_only_text_never_enters_dialogue(self, trace):
        # Streaming assistants emit whitespace-only text blocks alongside tool
        # calls. MindMemOS rejects any message whose content strips to empty,
        # which would fail the entire Add batch — the projection must drop them.
        call = ToolCall(id="c1", name="t", arguments={})
        msgs = [
            _user("u"),
            _assistant("\n\n", [call]),
            _tool("c1", "t", "ok"),
            _assistant("  \n\t  "),
            _assistant("answer"),
        ]
        trace.begin_turn("s1", 1, "run-1", message_start_index=0)
        trace.commit_terminal("s1", "turn-1", status="completed", messages=msgs)
        dialogue = trace.dialogue_for_turn("s1", "turn-1")

        contents = [m["content"] for m in dialogue]
        assert all(str(c).strip() for c in contents)
        assert dialogue[-1] == {"role": "assistant", "content": "answer"}
        assert sum(1 for m in dialogue if m["role"] == "assistant") == 2

    def test_empty_user_message_is_dropped(self, trace):
        # An empty/whitespace user message (e.g. attachment-only carry no text
        # in the canonical event) would fail server validation just like
        # whitespace assistant text.
        msgs = [_user(""), _user("   "), _user("real question")]
        trace.begin_turn("s1", 1, "run-1", message_start_index=0)
        trace.commit_terminal("s1", "turn-1", status="completed", messages=msgs)
        dialogue = trace.dialogue_for_turn("s1", "turn-1")

        assert dialogue == [{"role": "user", "content": "real question"}]

    def test_error_tool_result_marks_status(self, trace):
        msgs = [
            _user("u"),
            _assistant("", [ToolCall(id="c", name="t", arguments={})]),
            _tool("c", "t", "boom", is_error=True),
        ]
        trace.begin_turn("s1", 1, "run-1", message_start_index=0)
        trace.commit_terminal("s1", "turn-1", status="failed", messages=msgs)
        env = json.loads(trace.dialogue_for_turn("s1", "turn-1")[2]["content"])
        assert env["status"] == "error"

    def test_media_block_becomes_reference_not_binary(self, trace):
        msgs = [
            UserMessage(content=[
                ContentBlock(type="text", text="look"),
                ContentBlock(type="image", metadata={"path": "/tmp/a.png"}, media=b"\x89PNG"),
            ])
        ]
        event = message_to_trace_event(msgs[0], seq_hint=0)
        dumped = json.dumps(event["payload"]).lower()
        assert "media" not in dumped or "[media" in event["payload"]["content"]
        trace.begin_turn("s1", 1, "run-1", message_start_index=0)
        trace.commit_terminal("s1", "turn-1", status="completed", messages=msgs)
        dialogue = trace.dialogue_for_turn("s1", "turn-1")
        assert "[media: /tmp/a.png]" in dialogue[0]["content"]
        assert "\x89" not in json.dumps(dialogue)

    def test_atomic_commit_turn_and_intent(self, trace):
        trace.begin_turn("s1", 2, "run-2", message_start_index=3)
        commit = trace.commit_terminal(
            "s1", "turn-2", status="completed", messages=[_user("hi")], message_end_index=4
        )
        store = trace.store
        turn = store.turn("s1", "turn-2")
        assert turn.status == "completed"
        assert turn.add_job_id == commit.add_job_id
        job = store.job(commit.add_job_id)
        assert job.operation_key == "turnadd:s1:turn-2"
        assert job.payload["terminal_status"] == "completed"

    def test_duplicate_terminal_conflict(self, trace):
        trace.begin_turn("s1", 1, "run-1", message_start_index=0)
        trace.commit_terminal("s1", "turn-1", status="completed", messages=[])
        from homemaster.memory.memory_work_store import TerminalConflictError

        with pytest.raises(TerminalConflictError):
            trace.commit_terminal("s1", "turn-1", status="failed", messages=[])

    def test_restart_dialogue_replay(self, tmp_path):
        db = tmp_path / "w.db"
        s = SQLiteMemoryWorkStore(db)
        s.open()
        t = SessionTraceStore(s)
        t.begin_turn("s1", 1, "r", message_start_index=0)
        t.commit_terminal("s1", "turn-1", status="completed", messages=[_user("persisted")])
        s.close()
        s2 = SQLiteMemoryWorkStore(db)
        s2.open()
        t2 = SessionTraceStore(s2)
        assert t2.dialogue_for_turn("s1", "turn-1") == [{"role": "user", "content": "persisted"}]
        s2.close()
