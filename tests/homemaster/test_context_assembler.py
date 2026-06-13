"""Tests for ContextAssembler."""

from __future__ import annotations

from homemaster.agent.context_assembler import ContextAssembler
from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState
from homemaster.config.model_config import ContextPolicyConfig, ProviderProfileConfig
from homemaster.task_state.store import TaskStateStore


def _make_assembler(
    system_prompt: str = "You are HomeMaster.",
    policy: ContextPolicyConfig | None = None,
) -> ContextAssembler:
    return ContextAssembler(
        provider=ProviderProfileConfig(
            name="mimo_v25",
            protocol="anthropic",
            base_url="https://mimo.example",
            model="MiMo-V2.5",
            api_keys=["secret"],
            context_window_tokens=1_000_000,
            max_output_tokens=8192,
        ),
        policy=policy or ContextPolicyConfig(),
        system_prompt=system_prompt,
    )


def test_assembler_injects_snapshot_without_appending_to_session() -> None:
    session = AgentSession(session_id="s1")
    session.append(UserMessage(content=[ContentBlock(text="put a hot apple in fridge")]))
    store = TaskStateStore(run_id="r1")
    store.create_or_replace_plan(
        goal="put a hot apple in fridge",
        subtasks=[{"id": "find_apple", "description": "Find apple"}],
    )
    assembler = _make_assembler()

    context = assembler.prepare(
        session=session,
        agent_state=AgentState(run_id="r1", session_id="s1"),
        task_state_store=store,
        tools=[],
    )

    assert context.system_prompt == "You are HomeMaster."
    has_snapshot = any(
        "task_state_snapshot" in block.text
        for message in context.messages for block in message.content
    )
    assert has_snapshot
    assert len(session.messages) == 1


def test_assembler_includes_runtime_budget_status() -> None:
    session = AgentSession(session_id="s1")
    session.append(UserMessage(content=[ContentBlock(text="hello")]))
    assembler = _make_assembler()

    context = assembler.prepare(
        session=session,
        agent_state=AgentState(run_id="r1", session_id="s1", max_tool_iterations=99),
        task_state_store=None,
        tools=[],
    )

    has_budget = any(
        "runtime_budget_status" in block.text
        for message in context.messages for block in message.content
    )
    assert has_budget
    assert any(
        '"max_tool_iterations": 99' in block.text
        for message in context.messages for block in message.content
    )


def test_assembler_estimates_tokens() -> None:
    session = AgentSession(session_id="s1")
    session.append(UserMessage(content=[ContentBlock(text="hello world")]))
    assembler = _make_assembler()

    context = assembler.prepare(
        session=session,
        agent_state=AgentState(run_id="r1", session_id="s1"),
        task_state_store=None,
        tools=[],
    )

    assert context.metrics.estimated_tokens > 0


def test_assembler_respects_enabled_context_providers() -> None:
    session = AgentSession(session_id="s1")
    session.append(UserMessage(content=[ContentBlock(text="hello")]))
    store = TaskStateStore(run_id="r1")
    store.create_or_replace_plan(
        goal="goal",
        subtasks=[{"id": "a", "description": "A"}],
    )
    assembler = _make_assembler(
        policy=ContextPolicyConfig(enabled_providers=("conversation",))
    )

    context = assembler.prepare(
        session=session,
        agent_state=AgentState(run_id="r1", session_id="s1"),
        task_state_store=store,
        tools=[],
    )

    text = "\n".join(
        block.text for message in context.messages for block in message.content if block.text
    )
    assert "task_state_snapshot" not in text
    assert "runtime_budget_status" not in text


def test_completed_snapshot_renders_short_summary_not_full_detail() -> None:
    session = AgentSession(session_id="s1")
    session.append(UserMessage(content=[ContentBlock(text="new user request")]))
    store = TaskStateStore(run_id="r1")
    store.create_or_replace_plan(
        goal="fetch medicine",
        subtasks=[
            {
                "id": "find_medicine",
                "description": "Find the medicine bottle",
                "evidence": ["old detail"],
            }
        ],
    )
    store.mark_completed(final_summary="Medicine was delivered.")
    assembler = _make_assembler()

    context = assembler.prepare(
        session=session,
        agent_state=AgentState(run_id="r1", session_id="s1"),
        task_state_store=store,
        tools=[],
    )

    text = "\n".join(
        block.text for message in context.messages for block in message.content if block.text
    )
    assert "Medicine was delivered." in text
    assert "Find the medicine bottle" not in text
    assert "old detail" not in text


def test_compaction_preserves_recent_user_turns_and_tool_pairs() -> None:
    policy = ContextPolicyConfig(
        compression_threshold_ratio=0.01,
        preserve_recent_agent_steps=1,
        preserve_recent_user_turns=3,
    )
    assembler = ContextAssembler(
        provider=ProviderProfileConfig(
            name="tiny",
            protocol="anthropic",
            base_url="https://mimo.example",
            model="tiny",
            api_keys=["secret"],
            context_window_tokens=1_000,
            max_output_tokens=100,
        ),
        policy=policy,
        system_prompt="system",
    )
    session = AgentSession(session_id="s1")
    for index in range(8):
        session.append(UserMessage(content=[ContentBlock(text=f"user turn {index} " + "x" * 40)]))
        call = ToolCall(id=f"call_{index}", name="robot_observe", arguments={})
        session.append(AssistantMessage(tool_calls=[call], finish_reason="tool_calls"))
        session.append(ToolResultMessage(
            tool_call_id=f"call_{index}",
            name="robot_observe",
            content=[ContentBlock(text=f"observation {index} " + "y" * 40)],
        ))

    context = assembler.prepare(
        session=session,
        agent_state=AgentState(run_id="r1", session_id="s1"),
        task_state_store=None,
        tools=[],
    )

    text = "\n".join(
        block.text for message in context.messages for block in message.content if block.text
    )
    assert context.metrics.compaction_triggered is True
    assert "user turn 5" in text
    assert "user turn 6" in text
    assert "user turn 7" in text

    assistant_calls = {
        call.id
        for message in context.messages
        if isinstance(message, AssistantMessage)
        for call in message.tool_calls
    }
    result_ids = {
        message.tool_call_id
        for message in context.messages
        if isinstance(message, ToolResultMessage)
    }
    assert assistant_calls <= result_ids
