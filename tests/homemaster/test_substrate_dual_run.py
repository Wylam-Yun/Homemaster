"""Phase-1 dual-run gate: same input through LLMClient vs AsLLMClient.

The same provider exchange is replayed against the legacy transport path
(fake anthropic SDK stream) and the AgentScope path (scripted ChatResponse
chunks). The aggregated ``AssistantMessage`` must be equivalent per field —
not merely similar.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatResponse
from agentscope.model._model_usage import ChatUsage
from homemaster.agent.messages import UserMessage
from homemaster.config.config import ProviderProfileConfig
from homemaster.providers.llm_client import LLMClient
from homemaster.providers.types import aggregate_deltas
from homemaster.substrate.as_llm_client import AsLLMClient

# ---- fake anthropic SDK stream (same contract as the HM transport sees) ----


class _FakeAnthropicStream:
    async def __aenter__(self) -> _FakeAnthropicStream:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def __aiter__(self):
        return self._events().__aiter__()

    async def _events(self):
        yield {
            "type": "message_start",
            "message": {"usage": {"input_tokens": 11, "output_tokens": 0}},
        }
        yield {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "hel"},
        }
        yield {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "lo"},
        }
        yield {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}

    async def get_final_message(self) -> dict[str, Any]:
        return {
            "content": [
                {"type": "text", "text": "hello"},
                {
                    "type": "tool_use",
                    "id": "tc1",
                    "name": "echo",
                    "input": {"x": 1},
                },
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 11, "output_tokens": 7},
        }


class _FakeMessages:
    def stream(self, **kwargs: Any) -> _FakeAnthropicStream:
        return _FakeAnthropicStream()


class _FakeClient:
    def __init__(self) -> None:
        self.messages = _FakeMessages()

    async def aclose(self) -> None:
        return None


# ---- scripted AS model producing the equivalent response -------------------


class _FakeAsModel:
    async def __call__(self, messages: list, tools: Any = None, **kw: Any) -> Any:
        usage = ChatUsage(input_tokens=11, output_tokens=7, time=0.0)

        async def _gen():
            yield ChatResponse(
                content=[TextBlock(text="hel", id="t1")], is_last=False
            )
            yield ChatResponse(
                content=[TextBlock(text="lo", id="t1")], is_last=False
            )
            yield ChatResponse(
                content=[
                    TextBlock(text="hello", id="t1"),
                    ToolCallBlock(id="tc1", name="echo", input='{"x": 1}'),
                ],
                is_last=True,
                usage=usage,
                metadata={"stop_reason": "tool_use"},
            )

        return _gen()


def _profile() -> ProviderProfileConfig:
    return ProviderProfileConfig(
        name="p1",
        api_format="anthropic",
        transport="anthropic_sdk",
        base_url="https://api.example.com",
        model="claude-x",
        api_keys=("sk-a",),
        kind="chat",
    )


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "echo",
            "description": "echo",
            "input_schema": {
                "type": "object",
                "properties": {"x": {"type": "integer"}},
            },
        }
    ]


@pytest.mark.asyncio
async def test_dual_run_assistant_message_equivalence() -> None:
    msgs = [UserMessage.from_text("hi")]
    legacy = LLMClient(
        _profile(), anthropic_client_factory=lambda **kw: _FakeClient()
    )
    legacy_msg = await legacy.complete(
        msgs, _tools(), system_prompt="sys"
    )

    as_client = AsLLMClient(
        _profile(), model_factory=lambda *a, **kw: _FakeAsModel()
    )
    as_deltas = [
        d
        async for d in as_client.stream(
            msgs, _tools(), system_prompt="sys"
        )
    ]
    as_msg = aggregate_deltas(as_deltas)

    # Field-by-field equivalence (per-instance, not aggregate).
    assert as_msg.text == legacy_msg.text == "hello"
    assert as_msg.finish_reason == legacy_msg.finish_reason == "tool_calls"
    assert [c.id for c in as_msg.tool_calls] == [
        c.id for c in legacy_msg.tool_calls
    ] == ["tc1"]
    assert as_msg.tool_calls[0].name == legacy_msg.tool_calls[0].name == "echo"
    assert (
        as_msg.tool_calls[0].arguments
        == legacy_msg.tool_calls[0].arguments
        == {"x": 1}
    )
    assert as_msg.usage == legacy_msg.usage
    assert (
        as_msg.provider_metadata.get("raw_stop_reason")
        == legacy_msg.provider_metadata.get("raw_stop_reason")
        == "tool_use"
    )
