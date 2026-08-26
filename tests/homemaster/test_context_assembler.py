"""Tests for ContextAssembler."""

from __future__ import annotations

import json

import pytest

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
from homemaster.skills.loader import load_skill_registry
from homemaster.task_state.store import TaskStateStore


class FakeSummaryClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    def complete(self, messages, *, system_prompt: str = "", **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "system_prompt": system_prompt,
                "kwargs": kwargs,
            }
        )
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
        for message in context.messages
        for block in message.content
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
        for message in context.messages
        for block in message.content
    )
    assert has_budget
    assert any(
        '"max_tool_iterations": 99' in block.text
        for message in context.messages
        for block in message.content
    )


def test_assembler_lists_skill_summaries_without_inlining_full_instructions() -> None:
    registry = load_skill_registry()
    assembler = ContextAssembler(
        provider=ProviderProfileConfig(
            name="mimo_v25",
            protocol="anthropic",
            base_url="https://mimo.example",
            model="MiMo-V2.5",
            api_keys=["secret"],
            context_window_tokens=None,
            max_output_tokens=None,
        ),
        policy=ContextPolicyConfig(),
        system_prompt="You are HomeMaster.",
        skill_registry=registry,
    )
    session = AgentSession(session_id="s1")
    session.append(UserMessage(content=[ContentBlock(text="help")]))

    context = assembler.prepare(
        session=session,
        agent_state=AgentState(run_id="r1", session_id="s1"),
        task_state_store=None,
        tools=[],
    )

    text = "\n".join(
        block.text for message in context.messages for block in message.content if block.text
    )
    assert "# Available Skills" in text
    assert "**skill-creator**" in text
    assert "Use `load_skill(name=...)`" in text
    assert "Read `references" not in text


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
    assembler = _make_assembler(policy=ContextPolicyConfig(enabled_providers=("conversation",)))

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
        session.append(
            ToolResultMessage(
                tool_call_id=f"call_{index}",
                name="robot_observe",
                content=[ContentBlock(text=f"observation {index} " + "y" * 40)],
            )
        )

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


def test_manual_force_compaction_summarizes_even_below_threshold() -> None:
    summary_client = FakeSummaryClient()
    policy = ContextPolicyConfig(
        compression_threshold_ratio=0.99,
        protect_first_n=1,
        aggressive_protect_first_n=1,
        aggressive_tail_token_ratio=0.01,
    )
    assembler = ContextAssembler(
        provider=ProviderProfileConfig(
            name="large",
            api_format="anthropic",
            base_url="https://mimo.example",
            model="large",
            api_keys=["secret"],
            context_window_tokens=1_000_000,
            max_output_tokens=None,
        ),
        policy=policy.model_copy(update={"output_reserve_tokens": 100}),
        system_prompt="system",
        summary_client=summary_client,
    )
    session = AgentSession(session_id="s1")
    for index in range(4):
        session.append(
            UserMessage(content=[ContentBlock(text=f"old user turn {index} " + "x" * 400)])
        )
        session.append(
            AssistantMessage(content=[ContentBlock(text=f"old answer {index} " + "y" * 400)])
        )
    session.append(UserMessage(content=[ContentBlock(text="current request")]))
    agent_state = AgentState(run_id="r1", session_id="s1")

    context = assembler.prepare(
        session=session,
        agent_state=agent_state,
        task_state_store=None,
        tools=[],
        force_compact="manual",
    )

    text = "\n".join(
        block.text for message in context.messages for block in message.content if block.text
    )
    assert context.metrics.compaction_triggered is True
    assert context.metrics.compaction_kind == "manual_summary"
    assert agent_state.last_compaction is not None
    assert agent_state.last_compaction.kind == "manual"
    assert agent_state.last_compaction.reason == "manual"
    assert context.metrics.estimated_tokens == agent_state.last_compaction.after_tokens
    assert agent_state.last_compaction.after_tokens < agent_state.last_compaction.before_tokens
    assert summary_client.calls
    assert "LLM SUMMARY: cup found on table" in text
    assert "current request" in text
    assert len(session.messages) < 9


def test_manual_force_compaction_noops_without_old_history() -> None:
    summary_client = FakeSummaryClient()
    assembler = ContextAssembler(
        provider=ProviderProfileConfig(
            name="large",
            api_format="anthropic",
            base_url="https://mimo.example",
            model="large",
            api_keys=["secret"],
            context_window_tokens=1_000_000,
            max_output_tokens=None,
        ),
        policy=ContextPolicyConfig(),
        system_prompt="system",
        summary_client=summary_client,
    )
    session = AgentSession(session_id="s1")
    session.append(UserMessage(content=[ContentBlock(text="current request")]))
    context = assembler.prepare(
        session=session,
        agent_state=AgentState(run_id="r1", session_id="s1"),
        task_state_store=None,
        tools=[],
        force_compact="manual",
    )

    assert context.metrics.compaction_triggered is False
    assert context.metrics.compaction_kind == "none"
    assert not summary_client.calls
    assert len(session.messages) == 1


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


def _browser_result(
    *,
    tool_call_id: str,
    name: str,
    data: dict[str, object],
) -> ToolResultMessage:
    payload = {
        "status": "success",
        "success": True,
        "text": "",
        "data": data,
        "images": [],
        "attachments": [],
        "evidence_refs": [],
        "error": None,
        "failure_reason": None,
        "retryable": False,
        "outcome_certainty": "confirmed",
        "verification": {"status": "not_required", "detail": "", "evidence_refs": []},
        "terminal": None,
        "backend_attempted": name != "browser_inspect",
        "model_projection": "standard",
    }
    return ToolResultMessage(
        tool_call_id=tool_call_id,
        name=name,
        content=[
            ContentBlock(
                text=json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )
        ],
        data=payload,
    )


def _append_browser_pair(
    session: AgentSession,
    *,
    tool_call_id: str,
    name: str,
    arguments: dict[str, object],
    data: dict[str, object],
) -> None:
    session.append(
        AssistantMessage(
            tool_calls=[ToolCall(id=tool_call_id, name=name, arguments=arguments)],
            finish_reason="tool_calls",
        )
    )
    session.append(_browser_result(tool_call_id=tool_call_id, name=name, data=data))


def _browser_tools() -> list[dict[str, object]]:
    return [
        {
            "name": "browser_inspect",
            "description": "inspect",
            "input_schema": {"type": "object", "properties": {"view": {"type": "string"}}},
        },
        {
            "name": "browser_click",
            "description": "click",
            "input_schema": {
                "type": "object",
                "properties": {"target": {"type": "object"}},
            },
        },
    ]


def _context_result(context, tool_call_id: str) -> ToolResultMessage:
    return next(
        message
        for message in context.messages
        if isinstance(message, ToolResultMessage)
        and message.tool_call_id == tool_call_id
    )


def test_browser_context_preserves_v31_semantic_targets_and_stable_refs() -> None:
    session = AgentSession(session_id="browser-stable-refs")
    session.append(UserMessage(content=[ContentBlock(text="continue")]))
    _append_browser_pair(
        session,
        tool_call_id="click-before",
        name="browser_click",
        arguments={"target": {"role": "button", "name": "查 询", "match": "exact"}},
        data={
            "interaction_verified": True,
            "target": {"name": "查 询", "role": "button"},
        },
    )
    _append_browser_pair(
        session,
        tool_call_id="inspect-old",
        name="browser_inspect",
        arguments={"view": "hybrid", "text": "07", "limit": 10},
        data={
            "elements": [
                {
                    "name": "second 07",
                    "text": "07",
                    "target_ref": "ref-old-second-07",
                }
            ],
            "total_matches": 3,
        },
    )
    _append_browser_pair(
        session,
        tool_call_id="inspect-current",
        name="browser_inspect",
        arguments={"view": "hybrid", "name": "确 定", "limit": 10},
        data={
            "elements": [
                {
                    "name": "确 定",
                    "text": "确 定",
                    "target_ref": "ref-current-confirm",
                }
            ],
            "total_matches": 1,
        },
    )
    canonical = [message.model_dump(mode="json") for message in session.messages]

    context = _make_assembler().prepare(
        session=session,
        agent_state=AgentState(run_id="r1", session_id=session.session_id),
        task_state_store=None,
        tools=_browser_tools(),
    )

    projected_text = "\n".join(
        block.text
        for message in context.messages
        for block in message.content
        if block.text
    )
    assert "ref-old-second-07" in projected_text
    assert "ref-current-confirm" in projected_text
    assert '"interaction_verified":true' in projected_text
    assert [message.model_dump(mode="json") for message in session.messages] == canonical


@pytest.mark.asyncio
async def test_browser_semantic_context_matches_sync_and_async_assembly() -> None:
    session = AgentSession(session_id="sync-async-browser-reference")
    _append_browser_pair(
        session,
        tool_call_id="inspect-current",
        name="browser_inspect",
        arguments={"view": "hybrid", "name": "确 定", "limit": 10},
        data={
            "elements": [{"target_ref": "ref-current", "name": "确 定"}],
            "total_matches": 1,
        },
    )
    assembler = _make_assembler()
    sync_context = assembler.prepare(
        session=session,
        agent_state=AgentState(run_id="sync", session_id=session.session_id),
        task_state_store=None,
        tools=_browser_tools(),
    )
    async_context = await assembler.aprepare(
        session=session,
        agent_state=AgentState(run_id="async", session_id=session.session_id),
        task_state_store=None,
        tools=_browser_tools(),
    )

    assert [message.model_dump(mode="json") for message in async_context.messages] == [
        message.model_dump(mode="json") for message in sync_context.messages
    ]


def test_non_browser_context_keeps_tool_results_unchanged() -> None:
    session = AgentSession(session_id="non-browser")
    _append_browser_pair(
        session,
        tool_call_id="inspect-old",
        name="browser_inspect",
        arguments={"view": "hybrid", "text": "07", "limit": 10},
        data={
            "elements": [{"target_ref": "ref-old", "name": "second 07"}],
            "total_matches": 1,
        },
    )

    context = _make_assembler().prepare(
        session=session,
        agent_state=AgentState(run_id="r1", session_id=session.session_id),
        task_state_store=None,
        tools=[],
    )

    unchanged = _context_result(context, "inspect-old")
    assert unchanged.data["data"]["elements"][0]["target_ref"] == "ref-old"
