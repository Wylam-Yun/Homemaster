from __future__ import annotations

import pytest

from homemaster.agent.context import ContextAssembler
from homemaster.agent.messages import AssistantMessage, ContentBlock, UserMessage
from homemaster.application.session import SessionManager
from homemaster.config import ContextPolicyConfig, ProviderProfileConfig


class _SummaryClient:
    def complete(self, *args, **kwargs):
        del args, kwargs
        return AssistantMessage(content=[ContentBlock(text="summary")])


def _assembler() -> ContextAssembler:
    return ContextAssembler(
        provider=ProviderProfileConfig(
            name="scope-test",
            protocol="anthropic",
            base_url="https://example.invalid",
            model="scope-test",
            api_keys=["not-a-real-key"],
            context_window_tokens=1_000_000,
            max_output_tokens=None,
        ),
        policy=ContextPolicyConfig(),
        system_prompt="system",
        summary_client=_SummaryClient(),
    )


def _text(context) -> str:
    return "\n".join(
        block.text
        for message in context.messages
        for block in message.content
        if block.text
    )


@pytest.mark.asyncio
async def test_automatic_memory_context_is_prelude_not_history() -> None:
    manager = SessionManager()
    runtime = await manager.open_or_resume("recall-context")
    runtime.session.append(UserMessage.from_text("当前任务"))
    assembler = _assembler()
    assembler.bind_automatic_memory_context(
        '<memory-context>\n[{"id":"memory-1"}]\n</memory-context>'
    )

    composed = assembler.prepare(
        session=runtime.session,
        agent_state=runtime.agent_state,
        task_state_store=runtime.task_state_store,
        tools=[],
    )

    assert _text(composed).count("<memory-context>") == 1
    assert runtime.session.messages[-1].content[0].text == "当前任务"
    assert composed.messages[-1].content[0].text == "当前任务"


def test_automatic_context_does_not_leak_between_assemblers() -> None:
    first = _assembler()
    second = _assembler()
    first.bind_automatic_memory_context("<memory-context>first</memory-context>")

    assert first._automatic_memory_context is not None
    assert second._automatic_memory_context is None


@pytest.mark.asyncio
async def test_context_reprojects_exact_session_task_store_each_iteration() -> None:
    manager = SessionManager()
    runtime = await manager.open_or_resume("scope")
    runtime.session.append(UserMessage.from_text("request"))
    runtime.task_state_store.create_or_replace_plan(
        goal="goal",
        subtasks=[{"id": "a", "description": "first description"}],
    )
    assembler = _assembler()

    first = assembler.prepare(
        session=runtime.session,
        agent_state=runtime.agent_state,
        task_state_store=runtime.task_state_store,
        tools=[],
    )
    runtime.task_state_store.create_or_replace_plan(
        goal="goal",
        subtasks=[{"id": "b", "description": "second description"}],
    )
    second = assembler.prepare(
        session=runtime.session,
        agent_state=runtime.agent_state,
        task_state_store=runtime.task_state_store,
        tools=[],
    )

    assert "first description" in _text(first)
    assert "second description" not in _text(first)
    assert "second description" in _text(second)
    assert "first description" not in _text(second)


def test_compaction_request_is_an_explicit_call_value_not_assembler_state() -> None:
    assembler = _assembler()
    session = SessionManager()

    assert not hasattr(assembler, "force_compact_next")
    assert session.sessions == ()


@pytest.mark.asyncio
async def test_one_session_manual_compaction_does_not_leak_to_another() -> None:
    manager = SessionManager()
    first = await manager.open_or_resume("first")
    second = await manager.open_or_resume("second")
    for index in range(4):
        first.session.append(UserMessage.from_text(f"first {index} " + "x" * 400))
        first.session.append(AssistantMessage(content=[ContentBlock(text="y" * 400)]))
        second.session.append(UserMessage.from_text(f"second {index} " + "x" * 400))
        second.session.append(AssistantMessage(content=[ContentBlock(text="y" * 400)]))
    assembler = _assembler()

    first_context = assembler.prepare(
        session=first.session,
        agent_state=first.agent_state,
        task_state_store=first.task_state_store,
        tools=[],
        force_compact="manual",
    )
    second_context = assembler.prepare(
        session=second.session,
        agent_state=second.agent_state,
        task_state_store=second.task_state_store,
        tools=[],
    )

    assert first_context.metrics.compaction_triggered is True
    assert first_context.metrics.compaction_kind.startswith("manual_")
    assert second_context.metrics.compaction_triggered is False
