"""Tests for ContextAssembler."""

from __future__ import annotations

from homemaster.agent.context import ContextAssembler
from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState
from homemaster.config import ContextPolicyConfig, ProviderProfileConfig
from homemaster.task_state.store import TaskStateStore


class FakeSummaryClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def complete(self, messages, *, system_prompt: str = "", **kwargs):
        self.calls.append({
            "messages": messages,
            "system_prompt": system_prompt,
            "kwargs": kwargs,
        })
        if self.fail:
            raise RuntimeError("summary failed")
        return AssistantMessage(content=[ContentBlock(text="LLM SUMMARY: cup found on table")])


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
            context_window_tokens=None,
            max_output_tokens=None,
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
        protect_first_n=1,
        tail_token_ratio=0.35,
        abort_on_summary_failure=False,
    )
    assembler = ContextAssembler(
        provider=ProviderProfileConfig(
            name="tiny",
            protocol="anthropic",
            base_url="https://mimo.example",
            model="tiny",
            api_keys=["secret"],
            context_window_tokens=1_000,
            max_output_tokens=None,
        ),
        policy=policy.model_copy(update={"output_reserve_tokens": 100}),
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


def test_compaction_uses_summary_client_when_available() -> None:
    summary_client = FakeSummaryClient()
    policy = ContextPolicyConfig(
        compression_threshold_ratio=0.01,
        protect_first_n=1,
        tail_token_ratio=0.1,
    )
    assembler = ContextAssembler(
        provider=ProviderProfileConfig(
            name="tiny",
            api_format="anthropic",
            base_url="https://mimo.example",
            model="tiny",
            api_keys=["secret"],
            context_window_tokens=1_000,
            max_output_tokens=None,
        ),
        policy=policy.model_copy(update={"output_reserve_tokens": 100}),
        system_prompt="system",
        summary_client=summary_client,
    )
    session = AgentSession(session_id="s1")
    for index in range(8):
        session.append(UserMessage(content=[ContentBlock(text=f"user turn {index} " + "x" * 80)]))
        session.append(AssistantMessage(content=[ContentBlock(text="assistant " + "y" * 80)]))

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
    assert summary_client.calls
    assert "LLM SUMMARY: cup found on table" in text
    assert "END OF CONTEXT SUMMARY" in text


def test_compaction_aborts_when_summary_client_fails_and_policy_requires_abort() -> None:
    summary_client = FakeSummaryClient(fail=True)
    policy = ContextPolicyConfig(
        compression_threshold_ratio=0.01,
        protect_first_n=1,
        tail_token_ratio=0.1,
        abort_on_summary_failure=True,
    )
    assembler = ContextAssembler(
        provider=ProviderProfileConfig(
            name="tiny",
            api_format="anthropic",
            base_url="https://mimo.example",
            model="tiny",
            api_keys=["secret"],
            context_window_tokens=1_000,
            max_output_tokens=None,
        ),
        policy=policy.model_copy(update={"output_reserve_tokens": 100}),
        system_prompt="system",
        summary_client=summary_client,
    )
    session = AgentSession(session_id="s1")
    for index in range(8):
        session.append(UserMessage(content=[ContentBlock(text=f"user turn {index} " + "x" * 80)]))
        session.append(AssistantMessage(content=[ContentBlock(text="assistant " + "y" * 80)]))

    context = assembler.prepare(
        session=session,
        agent_state=AgentState(run_id="r1", session_id="s1"),
        task_state_store=None,
        tools=[],
    )

    assert summary_client.calls
    assert context.metrics.compaction_triggered is False
    assert len(session.messages) == 16
    text = "\n".join(
        block.text for message in context.messages for block in message.content if block.text
    )
    assert "CONTEXT COMPACTION" not in text


def test_compaction_without_summary_client_does_not_build_basic_summary() -> None:
    policy = ContextPolicyConfig(
        compression_threshold_ratio=0.01,
        protect_first_n=1,
        tail_token_ratio=0.1,
        abort_on_summary_failure=True,
    )
    assembler = ContextAssembler(
        provider=ProviderProfileConfig(
            name="tiny",
            api_format="anthropic",
            base_url="https://mimo.example",
            model="tiny",
            api_keys=["secret"],
            context_window_tokens=1_000,
            max_output_tokens=None,
        ),
        policy=policy.model_copy(update={"output_reserve_tokens": 100}),
        system_prompt="system",
        summary_client=None,
    )
    session = AgentSession(session_id="s1")
    for index in range(8):
        session.append(UserMessage(content=[ContentBlock(text=f"user turn {index} " + "x" * 80)]))
        session.append(AssistantMessage(content=[ContentBlock(text="assistant " + "y" * 80)]))

    context = assembler.prepare(
        session=session,
        agent_state=AgentState(run_id="r1", session_id="s1"),
        task_state_store=None,
        tools=[],
    )

    text = "\n".join(
        block.text for message in context.messages for block in message.content if block.text
    )
    assert context.metrics.compaction_triggered is False
    assert "CONTEXT COMPACTION" not in text
    assert "Earlier history contained no compactable text" not in text
