"""End-to-end test with real LLM API."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from homemaster.agent.context import ContextAssembler
from homemaster.agent.messages import ContentBlock, UserMessage
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState
from homemaster.config import ContextPolicyConfig, ProviderProfileConfig, load_config
from homemaster.prompts.loader import PromptId, load_prompt
from homemaster.providers.llm_client import LLMClient
from homemaster.task_state.store import TaskStateStore


@pytest.fixture
def provider_config():
    config = load_config().get_provider(kind="chat")
    if not config.api_keys:
        pytest.skip("set a real chat provider API key to run live_api tests")
    return config


@pytest.fixture
def transport(provider_config):
    return LLMClient(provider_config)


@pytest.fixture
def provider_profile(provider_config):
    return ProviderProfileConfig(
        name=provider_config.name,
        api_format=provider_config.api_format,
        transport=provider_config.transport,
        base_url=provider_config.base_url,
        model=provider_config.model,
        api_keys=provider_config.api_keys,
        context_window_tokens=provider_config.context_window_tokens,
        max_output_tokens=provider_config.max_output_tokens,
    )


@pytest.fixture
def runtime_settings(tmp_path):
    return SimpleNamespace(
        run_id="e2e-loop",
        runtime_root=tmp_path / "runs",
        debug_root=tmp_path / "debug",
        results_root=tmp_path / "results",
    )


@pytest.mark.live_api
@pytest.mark.asyncio
async def test_system_prompt_delivered_to_model(transport) -> None:
    """Verify system prompt appears in the model's behavior."""
    system_prompt = "You are a helpful assistant. Always reply with exactly one word: 'hello'."
    msg = await transport.complete(
        [UserMessage(content=[ContentBlock(text="Follow the system instruction now.")])],
        system_prompt=system_prompt,
    )
    assert "hello" in msg.text.lower()


@pytest.mark.live_api
@pytest.mark.asyncio
async def test_context_assembler_with_task_snapshot(transport, provider_profile) -> None:
    """Verify context assembler injects task snapshot into model context."""
    session = AgentSession(session_id="e2e-ctx")
    session.append(UserMessage(content=[ContentBlock(text="What is the current goal?")]))

    store = TaskStateStore(run_id="e2e-r1")
    store.create_or_replace_plan(
        goal="find the red cup",
        subtasks=[
            {"id": "search_kitchen", "description": "Search the kitchen."},
            {"id": "search_living", "description": "Search the living room."},
        ],
        current_subtask="search_kitchen",
    )

    system_prompt = load_prompt(PromptId.AGENT_SYSTEM)
    assembler = ContextAssembler(
        provider=provider_profile,
        policy=ContextPolicyConfig(),
        system_prompt=system_prompt,
    )

    context = assembler.prepare(
        session=session,
        agent_state=AgentState(run_id="e2e-r1", session_id="e2e-ctx"),
        task_state_store=store,
        tools=None,
    )

    # System prompt should be set
    assert context.system_prompt
    assert "HomeMaster" in context.system_prompt or "assistant" in context.system_prompt.lower()

    # Messages should include task snapshot prelude
    assert len(context.messages) > 1
    prelude_text = context.messages[0].content[0].text
    assert "task_state_snapshot" in prelude_text
    assert "find the red cup" in prelude_text

    # Send to real model and verify it can see the task context
    msg = await transport.complete(
        context.messages,
        system_prompt=context.system_prompt,
    )
    # The model should reference the goal or subtasks
    lower_reply = msg.text.lower()
    assert any(
        word in lower_reply
        for word in [
            "cup",
            "kitchen",
            "goal",
            "find",
            "search",
            "杯",
            "厨房",
            "目标",
            "寻找",
            "搜索",
        ]
    )
