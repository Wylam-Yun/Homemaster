from __future__ import annotations

import pytest

from homemaster.agent.generic_runtime import AgentRuntime


class _Delta:
    def __init__(self, text: str = "", reasoning: str = "") -> None:
        self.text_delta = text
        self.reasoning_delta = reasoning


@pytest.mark.asyncio
async def test_provider_deltas_are_emitted_immediately_and_exactly() -> None:
    emitted: list[tuple[str, dict[str, str]]] = []

    async def emit(event_type: str, *, payload: dict[str, str]) -> None:
        emitted.append((event_type, payload))

    values = [
        "token=raw-token",
        " /hpc2hdd/home/operator/private.txt",
        " https://example.test/path?token=raw#fragment",
    ]
    for value in values:
        await AgentRuntime._publish_text_delta(_Delta(value), emit=emit)

    assert emitted == [
        ("transport.delta", {"text_delta": value}) for value in values
    ]


@pytest.mark.asyncio
async def test_provider_reasoning_and_text_share_one_exact_delta_payload() -> None:
    emitted: list[tuple[str, dict[str, str]]] = []

    async def emit(event_type: str, *, payload: dict[str, str]) -> None:
        emitted.append((event_type, payload))

    await AgentRuntime._publish_text_delta(
        _Delta(text="answer", reasoning="private reasoning"),
        emit=emit,
    )
    await AgentRuntime._publish_text_delta(_Delta(reasoning="reasoning only"), emit=emit)

    assert emitted == [
        (
            "transport.delta",
            {"text_delta": "answer", "reasoning_delta": "private reasoning"},
        ),
        ("transport.delta", {"reasoning_delta": "reasoning only"}),
    ]
