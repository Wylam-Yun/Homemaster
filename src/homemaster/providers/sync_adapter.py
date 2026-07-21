"""Temporary synchronous facade over the native async provider contract."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

from homemaster.providers.llm_client import LLMClient
from homemaster.providers.transports import TransportDelta


class SyncProviderAdapter:
    """Bridge the CL-16a async provider into the pre-CL-16b agent worker."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    @property
    def token_estimator(self):
        return self.client.token_estimator

    def stream(
        self,
        messages,
        tools=None,
        *,
        system_prompt: str = "",
        event_sink: Any = None,
        run_id: str = "",
        session_id: str = "",
        turn_index: int | None = None,
        iteration: int | None = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        attempt_sink: Any = None,
        model_attempt_id: str | None = None,
        provider_key_index: int = 0,
    ) -> Iterator[TransportDelta]:
        async def collect() -> list[TransportDelta]:
            return [
                delta
                async for delta in self.client.stream(
                    messages,
                    tools,
                    system_prompt=system_prompt,
                    event_sink=event_sink,
                    run_id=run_id,
                    session_id=session_id,
                    turn_index=turn_index,
                    iteration=iteration,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                    attempt_sink=attempt_sink,
                    model_attempt_id=model_attempt_id,
                    provider_key_index=provider_key_index,
                )
            ]

        yield from asyncio.run(collect())

    def complete(self, *args: Any, **kwargs: Any):
        return asyncio.run(self.client.complete(*args, **kwargs))

    def complete_json(self, *args: Any, **kwargs: Any):
        return asyncio.run(self.client.complete_json(*args, **kwargs))

    def close(self) -> None:
        asyncio.run(self.client.aclose())

    async def aclose(self) -> None:
        await self.client.aclose()


__all__ = ["SyncProviderAdapter"]
