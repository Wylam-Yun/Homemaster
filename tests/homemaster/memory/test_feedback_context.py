from types import SimpleNamespace

from homemaster.agent.generic_runtime import _bind_provider_attempt_contexts
from homemaster.agent.messages import ContentBlock, ToolCall, ToolResultMessage, UserMessage
from homemaster.agent.normalized import RunContext
from homemaster.memory.feedback_context import (
    bind_feedback_contexts,
    build_feedback_context_snapshot,
    snapshot_to_dialogue_messages,
)


def _memory(memory_id: str):
    return SimpleNamespace(id=memory_id, memory=f"memory {memory_id}")


def test_snapshot_keeps_automatic_and_only_visible_search_records() -> None:
    messages = [
        UserMessage.from_text("correct the old memory"),
        ToolResultMessage(
            tool_call_id="visible-search",
            name="mindmemos_search",
            content=[ContentBlock(text='{"records":[]}')],
        ),
    ]

    snapshot = build_feedback_context_snapshot(
        messages,
        automatic_recalled_memories=[_memory("automatic")],
        recalled_memories_by_tool_call_id={
            "visible-search": [_memory("visible")],
            "compacted-search": [_memory("compacted")],
        },
    )

    assert [item.id for item in snapshot.recalled_memories] == ["automatic", "visible"]


def test_snapshot_deep_copies_messages_and_converts_text_only() -> None:
    source = UserMessage.from_text("original")
    snapshot = build_feedback_context_snapshot([source])
    source.content[0].text = "changed"

    assert snapshot.messages[0].content[0].text == "original"
    dialogue = snapshot_to_dialogue_messages(snapshot)
    assert [(item.role, item.content) for item in dialogue] == [("user", "original")]


def test_snapshot_deep_copies_non_pydantic_recalled_memory() -> None:
    recalled = _memory("raw-1")

    snapshot = build_feedback_context_snapshot(
        [UserMessage.from_text("remember this")],
        automatic_recalled_memories=[recalled],
    )
    recalled.memory = "changed after snapshot"

    assert snapshot.recalled_memories[0].memory == "memory raw-1"


def test_generic_runtime_binds_exact_visible_records_to_feedback_call() -> None:
    from mindmemos.typing import MemorySearchItem

    source = UserMessage.from_text("Use uv, not conda.")
    frozen_messages = [
        source,
        ToolResultMessage(
            tool_call_id="visible-search",
            name="mindmemos_search",
            content=[ContentBlock(text='{"records":[]}')],
        ),
    ]
    run_context = RunContext(
        session_id="session-a",
        run_id="run-a",
        turn_index=1,
        settings=None,
        event_sink=None,
        deps={
            "automatic_recalled_memories": [
                MemorySearchItem(
                    id="automatic",
                    memory="automatic memory",
                    last_update_at="2026-08-17 00:00:00",
                )
            ],
            "recalled_memories_by_tool_call_id": {
                "visible-search": [
                    MemorySearchItem(
                        id="visible",
                        memory="visible memory",
                        last_update_at="2026-08-17 00:00:00",
                    )
                ],
                "compacted-search": [
                    MemorySearchItem(
                        id="compacted",
                        memory="compacted memory",
                        last_update_at="2026-08-17 00:00:00",
                    )
                ],
            },
            "provider_attempt_context_binder": bind_feedback_contexts,
        },
    )

    _bind_provider_attempt_contexts(
        [ToolCall(id="feedback-call", name="mindmemos_feedback", arguments={})],
        frozen_messages,
        run_context,
    )
    source.content[0].text = "changed after binding"

    snapshot = run_context.deps["memory_feedback_context_by_tool_call_id"][
        "feedback-call"
    ]
    assert snapshot.messages[0].content[0].text == "Use uv, not conda."
    assert [item.id for item in snapshot.recalled_memories] == [
        "automatic",
        "visible",
    ]
