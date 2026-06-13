"""Tests for ContextAssembler."""

from __future__ import annotations

from homemaster.agent.context_assembler import ContextAssembler
from homemaster.agent.messages import ContentBlock, UserMessage
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState
from homemaster.config.model_config import ContextPolicyConfig, ProviderProfileConfig
from homemaster.task_state.store import TaskStateStore


def _make_assembler(system_prompt: str = "You are HomeMaster.") -> ContextAssembler:
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
        policy=ContextPolicyConfig(),
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
    assert any("task_state_snapshot" in block.text for message in context.messages for block in message.content)
    assert len(session.messages) == 1


def test_assembler_includes_runtime_budget_status() -> None:
    session = AgentSession(session_id="s1")
    session.append(UserMessage(content=[ContentBlock(text="hello")]))
    assembler = _make_assembler()

    context = assembler.prepare(
        session=session,
        agent_state=AgentState(run_id="r1", session_id="s1"),
        task_state_store=None,
        tools=[],
    )

    assert any("runtime_budget_status" in block.text for message in context.messages for block in message.content)


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
