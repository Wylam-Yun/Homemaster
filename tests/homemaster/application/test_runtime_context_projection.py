from __future__ import annotations

import json

import pytest

from homemaster.agent.context import ContextAssembler
from homemaster.agent.messages import AssistantMessage, ToolCall
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState
from homemaster.application.contracts import RunRequest, RunStatus
from homemaster.application.factory import create_application
from homemaster.application.tool_executor import ApplicationToolExecutor
from homemaster.config import ContextPolicyConfig, HomeMasterConfig
from homemaster.tools.adapters import from_registered_tool
from homemaster.tools.base import ToolRegistry
from homemaster.tools.bash import build_terminal_tool
from homemaster.tools.contracts import PermissionSubject, ToolExecutionContext


class _RecordingChatModel:
    """Duck-typed AgentScope provider: captures the system-role Msg that the
    vendored agent prepends from ``AsAgentRuntime.system_prompt``."""

    def __init__(self) -> None:
        self.system_prompts: list[str] = []
        self.api_format = "openai"

    def chat_model(self, **_kwargs):
        from agentscope.credential import OpenAICredential
        from agentscope.message import Msg, TextBlock
        from agentscope.model import ChatModelBase, ChatResponse
        from agentscope.model._model_usage import ChatUsage

        outer = self

        class _Model(ChatModelBase):
            def __init__(self) -> None:
                super().__init__(
                    credential=OpenAICredential(api_key="sk-stub"),
                    model="stub-model",
                    parameters=ChatModelBase.Parameters(),
                )
                from agentscope.formatter import OpenAIChatFormatter

                self.formatter = OpenAIChatFormatter()

            async def _call_api(
                self,
                model_name: str,
                messages: list[Msg],
                **_kwargs,
            ) -> ChatResponse:
                for msg in messages:
                    if msg.role == "system":
                        outer.system_prompts.append(
                            "".join(b.text or "" for b in msg.content)
                        )
                return ChatResponse(
                    content=[TextBlock(text="done")],
                    is_last=True,
                    usage=ChatUsage(input_tokens=1, output_tokens=1, time=0.0),
                    metadata={"stop_reason": "end_turn"},
                )

        return _Model()


def _provider_config(tmp_path) -> HomeMasterConfig:
    return HomeMasterConfig.model_validate(
        {
            "providers": {
                "default": "fake",
                "items": [
                    {
                        "name": "fake",
                        "api_format": "anthropic",
                        "base_url": "https://provider.invalid",
                        "model": "fake-model",
                    }
                ],
            },
            "runtime_defaults": {"default_provider_name": "fake"},
            "observability": {"session_dir": str(tmp_path / "sessions")},
        }
    )


class _FrozenMemory:
    def snapshot(self, session_id: str) -> str:
        assert session_id == "context-order"
        return "# Assistant Identity\n\nidentity\n\n# Persistent Memory\n\nmemory"


@pytest.mark.asyncio
async def test_workspace_precedes_frozen_memory_in_sync_and_async_system_prompt(tmp_path) -> None:
    config = _provider_config(tmp_path)
    assembler = ContextAssembler(
        provider=config.providers.items[0],
        policy=ContextPolicyConfig(),
        system_prompt="BASE SYSTEM",
        frozen_memory_context=_FrozenMemory(),
    )
    assembler.bind_working_directory(tmp_path)
    session = AgentSession(session_id="context-order")

    sync_context = assembler.prepare(
        session=session,
        agent_state=AgentState(run_id="sync", session_id=session.session_id),
        task_state_store=None,
        tools=[],
    )
    async_context = await assembler.aprepare(
        session=session,
        agent_state=AgentState(run_id="async", session_id=session.session_id),
        task_state_store=None,
        tools=[],
    )

    assert async_context.system_prompt == sync_context.system_prompt
    assert sync_context.system_prompt.index("BASE SYSTEM") < sync_context.system_prompt.index(
        "Current workspace:"
    )
    assert sync_context.system_prompt.index("Current workspace:") < (
        sync_context.system_prompt.index("# Assistant Identity")
    )


@pytest.mark.asyncio
async def test_default_runtime_sends_authoritative_workspace_in_system_prompt(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    provider = _RecordingChatModel()
    application = create_application(
        config=_provider_config(tmp_path),
        registry=ToolRegistry(),
        provider_factory=lambda request, run_id: provider,
    )

    result = await application.run(RunRequest(text="Where am I?", profile="home"))

    assert result.status is RunStatus.REPLIED
    assert len(provider.system_prompts) == 1
    system_prompt = provider.system_prompts[0]
    assert f'Current workspace: {json.dumps(str(tmp_path))}.' in system_prompt
    assert "Relative paths passed to terminal and file tools resolve from this workspace" in (
        system_prompt
    )
    assert "is not automatically this workspace" in system_prompt
    await application.aclose()


def _projector() -> ApplicationToolExecutor:
    projector = object.__new__(ApplicationToolExecutor)
    projector._artifact_publisher = None
    return projector


async def _anthropic_tool_payload(assistant, message) -> dict:
    from agentscope.formatter import AnthropicChatFormatter
    from homemaster.substrate.messages import to_agent_scope

    msgs = to_agent_scope([assistant, message])
    blocks = await AnthropicChatFormatter().format(msgs)
    tool_result = next(
        block["content"][0]
        for block in blocks
        if block["role"] == "user" and block["content"][0]["type"] == "tool_result"
    )
    return json.loads(tool_result["content"][0]["text"])


async def _openai_tool_payload(assistant, message) -> dict:
    from agentscope.formatter import OpenAIChatFormatter
    from homemaster.substrate.messages import to_agent_scope

    msgs = to_agent_scope([assistant, message])
    blocks = await OpenAIChatFormatter().format(msgs)
    tool_msg = next(block for block in blocks if block["role"] == "tool")
    return json.loads(tool_msg["content"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "expected_status", "expected_returncode"),
    (("pwd", "success", 0), ("sh -c 'exit 7'", "failure", 7)),
)
async def test_terminal_receipt_survives_actual_provider_serialization(
    tmp_path,
    command: str,
    expected_status: str,
    expected_returncode: int,
) -> None:
    terminal = from_registered_tool(build_terminal_tool())
    arguments = terminal.input_model.model_validate({"command": command})
    normalized = await terminal.execute(
        arguments,
        ToolExecutionContext(
            session_id="session",
            run_id="run",
            turn_index=0,
            tool_call_id="terminal-call",
            internal_tool_id="homemaster.terminal.v1",
            permission_subject=PermissionSubject(subject_id="test", channel="pytest"),
            backend=None,
            deadline=None,
            cancellation=None,
            domain_observer=None,
            working_directory=tmp_path,
        ),
    )
    tool_call = ToolCall(
        id="terminal-call", name="terminal", arguments={"command": command}
    )
    message = _projector()._message(tool_call, normalized)
    assistant = AssistantMessage(content=[], tool_calls=[tool_call])

    for payload in (
        await _anthropic_tool_payload(assistant, message),
        await _openai_tool_payload(assistant, message),
    ):
        assert payload["status"] == expected_status
        assert payload["data"]["cwd"] == str(tmp_path)
        assert payload["data"]["returncode"] == expected_returncode
        assert payload["data"]["timed_out"] is False
        assert payload["backend_attempted"] is True
        if expected_returncode == 0:
            assert payload["text"] == str(tmp_path)
            assert payload["error"] is None
        else:
            assert payload["error"]["code"] == "command_failed"
            assert "return code 7" in payload["error"]["message"]

    assert message.data is not None
    assert message.data["backend_attempted"] is True
    assert message.data["returncode"] == expected_returncode
