"""Tier-1 goal verdict protocol — design-phase3-pipelines.md (定稿).

The LLM verdict path is intentionally NOT the vendored ``GoalPipeline``:
both its inner ``while`` loops are unbounded, ``max_retries`` is dead, and
``Agent._reply_impl`` converts cancellation into an interrupted reply that
the pipeline would retry forever. Instead this module provides a small
HM-owned verifier protocol on top of ``AsLLMClient.complete_json`` — no
Agent, no tools, no lifecycle takeover.

Discipline red lines (CLAUDE.md benchmark-goal rules):

- ``verdict_provenance`` separates ``env``/``typed``/``llm`` sources on the
  audit surface. ALFWorld's ``won`` stays environment-authoritative — an
  LLM verdict NEVER maps onto it.
- ``CancelledError`` propagates to the caller; a cancelled verdict is not
  a "fail"/"impossible" outcome.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel


class VerdictResult(BaseModel):
    """Typed goal verdict — mirrors the GoalPipeline schema shape."""

    result: Literal["pass", "fail", "impossible"]
    """``pass``: goal satisfied; ``fail``: not satisfied but retryable;
    ``impossible``: verifier cannot judge / budget exhausted."""
    message: str = ""
    """Human-readable explanation (model-facing when surfaced)."""
    verdict_provenance: Literal["env", "typed", "llm"] = "llm"
    """Audit surface: who produced this verdict — the environment, a typed
    verifier (e.g. receipt checks), or an LLM judge."""


@runtime_checkable
class VerdictVerifier(Protocol):
    """One-shot goal judgment — no tools, no run-lifecycle ownership."""

    async def verdict(
        self,
        *,
        goal: str,
        transcript: Sequence[Any],
        evidence: Mapping[str, Any],
        timeout_s: float | None = None,
    ) -> VerdictResult: ...


_VERDICT_PROMPT = """You are a strict goal verifier. Judge whether the
agent's run achieved the stated goal. Answer with JSON only:

{{"result": "pass" | "fail" | "impossible", "message": "<short reason>"}}

- "pass" only when the transcript/evidence unambiguously shows the goal
  achieved. A model claiming success is NOT evidence.
- "fail" when the run ended without achieving it (but retrying could).
- "impossible" when you cannot judge from the given evidence.

Goal: {goal}

Evidence: {evidence}

Transcript (tail):
{transcript}
"""


class LLMVerdictVerifier:
    """LLM-backed verifier on ``AsLLMClient.complete_json`` — the only
    sanctioned LLM judgment path. No toolkit, no permission hooks, one
    bounded call + at most one retry; overruns degrade to ``impossible``.
    """

    def __init__(
        self,
        client: Any,
        *,
        timeout_s: float = 30.0,
        max_attempts: int = 2,
    ) -> None:
        self._client = client
        self._timeout_s = timeout_s
        self._max_attempts = max(1, max_attempts)

    async def verdict(
        self,
        *,
        goal: str,
        transcript: Sequence[Any],
        evidence: Mapping[str, Any],
        timeout_s: float | None = None,
    ) -> VerdictResult:
        budget = timeout_s if timeout_s is not None else self._timeout_s
        prompt = _VERDICT_PROMPT.format(
            goal=goal,
            evidence=dict(evidence),
            transcript=_render_transcript(transcript),
        )
        last_error: str = ""
        attempts = min(self._max_attempts, 2)  # at most one retry
        for _ in range(attempts):
            try:
                # Awaitable cancellation propagates — never convert a
                # cancelled verdict into "impossible".
                response = await asyncio.wait_for(
                    self._client.complete_json(prompt, temperature=0.0),
                    timeout=budget,
                )
            except TimeoutError:
                return VerdictResult(
                    result="impossible",
                    message=f"verdict deadline exceeded ({budget}s)",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # provider/parse failure → retryable
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            payload = response.json_payload
            try:
                return VerdictResult(
                    result=str(payload["result"]),
                    message=str(payload.get("message", "")),
                    verdict_provenance="llm",
                )
            except (KeyError, ValueError) as exc:
                last_error = f"malformed verdict payload: {exc}"
                continue
        return VerdictResult(
            result="impossible",
            message=f"verdict attempts exhausted: {last_error}",
        )


def _render_transcript(transcript: Sequence[Any], *, limit: int = 8) -> str:
    """Last-N messages as ``role: text`` lines (plain text only — the
    verdict path must not forward images/binary blobs)."""
    lines: list[str] = []
    for message in list(transcript)[-limit:]:
        role = getattr(message, "role", "?")
        text = getattr(message, "text", "") or ""
        if not text:
            content = getattr(message, "content", None) or []
            text = " ".join(
                getattr(b, "text", "") or "" for b in content
            )
        lines.append(f"{role}: {text[:500]}")
    return "\n".join(lines)
