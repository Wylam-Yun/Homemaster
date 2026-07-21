"""End-to-end test with real LLM API."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from homemaster.agent.context import ContextAssembler
from homemaster.agent.generic_runtime import AgentRuntime
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
def test_system_prompt_delivered_to_model(transport) -> None:
    """Verify system prompt appears in the model's behavior."""
    system_prompt = "You are a helpful assistant. Always reply with exactly one word: 'hello'."
    msg = transport.complete(
        [UserMessage(content=[ContentBlock(text="Follow the system instruction now.")])],
        system_prompt=system_prompt,
    )
    assert "hello" in msg.text.lower()


@pytest.mark.live_api
def test_context_assembler_with_task_snapshot(transport, provider_profile) -> None:
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
    msg = transport.complete(
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


@pytest.mark.live_api
def test_full_agent_loop_with_real_api(transport, provider_profile, runtime_settings) -> None:
    """Full agent loop: user message -> model -> tool -> model -> reply."""
    from homemaster.tools.catalog import ToolCatalog
    from homemaster.tools.dispatcher import ToolDispatcher
    from homemaster.tools.legacy_adapter import adapt_legacy_tool_spec
    from homemaster.tools.results import ToolResult
    from homemaster.tools.spec import ToolSpec

    def echo_executor(*, arguments, run_context):
        return ToolResult(
            success=True,
            tool_name="echo",
            data={"echo": arguments.get("text", "")},
        )

    spec = ToolSpec(
        name="echo",
        description="Echo back the input text.",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        executor_mode="programmatic",
        executor=echo_executor,
    )

    dispatcher = ToolDispatcher()
    dispatcher.register(spec)
    catalog = ToolCatalog()
    adapted = adapt_legacy_tool_spec(spec, internal_id="test.echo.v1", version="1.9.0")
    catalog.register(adapted.registered_tool)
    tool_view = catalog.freeze((adapted.definition.internal_id,))

    system_prompt = (
        "You are a helpful assistant. When the user says hello, "
        "use the echo tool with text='world', then tell the user "
        "what the tool returned."
    )
    assembler = ContextAssembler(
        provider=provider_profile,
        policy=ContextPolicyConfig(),
        system_prompt=system_prompt,
    )

    runtime = AgentRuntime(
        transport=transport,
        tool_executor=dispatcher,
        max_tool_iterations=5,
        context_assembler=assembler,
        system_prompt=system_prompt,
    )

    session = AgentSession(session_id="e2e-loop")
    result = asyncio.run(
        runtime.run(
            session,
            "hello",
            settings=runtime_settings,
            tool_view=tool_view,
        )
    )

    assert result.status == "replied"
    assert result.final_reply
    # Model should mention "world" from the echo tool
    assert "world" in result.final_reply.lower()
