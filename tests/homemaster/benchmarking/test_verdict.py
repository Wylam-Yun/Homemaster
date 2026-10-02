"""Tier-1 VerdictVerifier gates — see design doc §4."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from homemaster.benchmarking.verdict import (
    LLMVerdictVerifier,
    VerdictResult,
    VerdictVerifier,
)


class _StubClient:
    def __init__(self, *, payload=None, error=None, delay=0.0) -> None:
        self._payload = payload or {}
        self._error = error
        self._delay = delay
        self.calls = 0

    async def complete_json(self, prompt: str, *, temperature: float = 0.0):
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return SimpleNamespace(json_payload=self._payload)


def test_verdict_result_typed_shape() -> None:
    v = VerdictResult(result="pass", message="ok", verdict_provenance="llm")
    assert v.result == "pass"
    assert v.verdict_provenance == "llm"
    # provenance is the audit seam — env/typed verdicts are distinguishable.
    assert VerdictResult(result="pass", verdict_provenance="env").model_dump()[
        "verdict_provenance"
    ] == "env"


@pytest.mark.asyncio
async def test_llm_verdict_happy_path() -> None:
    client = _StubClient(payload={"result": "pass", "message": "goal met"})
    verifier = LLMVerdictVerifier(client)
    v = await verifier.verdict(goal="g", transcript=[], evidence={})
    assert v.result == "pass"
    assert v.message == "goal met"
    assert v.verdict_provenance == "llm"
    assert client.calls == 1


@pytest.mark.asyncio
async def test_llm_verdict_malformed_then_retry_then_impossible() -> None:
    client = _StubClient(payload={"nope": True})
    verifier = LLMVerdictVerifier(client)
    v = await verifier.verdict(goal="g", transcript=[], evidence={})
    assert v.result == "impossible"
    assert "malformed" in v.message
    assert client.calls == 2  # bounded: exactly one retry


@pytest.mark.asyncio
async def test_llm_verdict_provider_error_bounded_retry() -> None:
    client = _StubClient(error=RuntimeError("boom"))
    verifier = LLMVerdictVerifier(client)
    v = await verifier.verdict(goal="g", transcript=[], evidence={})
    assert v.result == "impossible"
    assert "boom" in v.message
    assert client.calls == 2


@pytest.mark.asyncio
async def test_llm_verdict_deadline_impossible() -> None:
    client = _StubClient(
        payload={"result": "pass"}, delay=5.0
    )
    verifier = LLMVerdictVerifier(client, timeout_s=0.05)
    v = await verifier.verdict(goal="g", transcript=[], evidence={})
    assert v.result == "impossible"
    assert "deadline" in v.message


@pytest.mark.asyncio
async def test_llm_verdict_cancellation_propagates() -> None:
    """CancelledError must reach the caller — a cancelled verdict is not a
    fail/impossible outcome (GoalPipeline's interrupt-swallow inverted)."""

    async def _hang(prompt: str, *, temperature: float = 0.0):
        await asyncio.sleep(60)

    verifier = LLMVerdictVerifier(_StubClient())
    verifier._client.complete_json = _hang  # type: ignore[attr-defined]
    task = asyncio.ensure_future(
        verifier.verdict(goal="g", transcript=[], evidence={})
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_verdict_verifier_protocol_shape() -> None:
    assert isinstance(LLMVerdictVerifier(_StubClient()), VerdictVerifier)
