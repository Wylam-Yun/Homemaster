"""Dedicated Skill Trace tracking (V3.8 §4.6).

Skill invocations get their own trace store + entry point — they no longer
piggyback on ordinary AddRecord ``skill_bindings``.

Lifecycle:

1. A skill command resolves (user ``/skill args`` or model-invoked skill):
   :meth:`SkillInvocationTracker.begin` records the immutable identity —
   skill id, cloud id, version, content hash — as a ``pending`` row bound to
   the session (the turn id is not known until the turn opens).
2. At the turn's terminal commit the tracker binds the pending invocation to
   the turn, materializes tool steps FROM THE CANONICAL TRACE (exact
   tool_call/result pairing, input order, 500-char result tails), derives
   outcome/score from the terminal status, and seals the record.
3. Sealing evaluates the evolution threshold: exactly 8 sealed-but-unclaimed
   traces for a cloud skill → atomically claim the batch and enqueue ONE
   persistent evolution job (concurrent writers cannot double-claim).
4. Startup recovery re-evaluates ``pending_evolution_skills`` so a threshold
   reached before a crash still produces its job.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

from homemaster.memory.memory_work_store import (
    JOB_SKILL_EVOLUTION,
    MemoryWorkStore,
    SkillInvocationStep,
)

logger = logging.getLogger(__name__)

EVOLUTION_MIN_TRACES = 8
EVOLUTION_MAX_TRACES = 8

_OUTCOME_SCORE = {"completed": 1.0, "failed": 0.0, "budget_exhausted": 0.0,
                  "cancelled": 0.0, "interrupted": 0.0}
_STEP_RESULT_TAIL_CHARS = 500


@dataclass(frozen=True)
class SkillIdentity:
    """Immutable identity of the invoked skill version."""

    skill_id: str
    cloud_skill_id: str
    skill_version_id: str
    skill_content_hash: str

    @classmethod
    def for_local_skill(cls, name: str, content: str) -> SkillIdentity:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return cls(
            skill_id=name,
            cloud_skill_id=name,
            skill_version_id=f"local-{digest[:12]}",
            skill_content_hash=digest,
        )


@dataclass(frozen=True)
class SealedTrace:
    invocation_id: str
    cloud_skill_id: str
    outcome: str
    score: float


@dataclass(frozen=True)
class SkillInvocation:
    """RunRequest.dependencies payload marking a turn as a skill invocation.

    Produced by the resolve boundary (CLI shell / one-shot run / any caller
    that resolved a ``ResolvedSkillCommand``) and consumed by the runtime:
    the tracker opens the invocation once the turn id exists and seals it at
    the turn's terminal commit.
    """

    identity: SkillIdentity
    task_input: str

    @classmethod
    def from_resolved(cls, resolved: Any) -> SkillInvocation:
        """Build from a ``ResolvedSkillCommand`` (duck-typed to avoid an
        import cycle between memory and skills packages)."""
        name = getattr(resolved, "skill_name", None)
        sha = getattr(resolved, "skill_content_sha256", None)
        if not name or not sha:
            raise ValueError("resolved skill carries no identity")
        return cls(
            identity=SkillIdentity(
                skill_id=name,
                cloud_skill_id=name,
                skill_version_id=f"local-{sha[:12]}",
                skill_content_hash=sha,
            ),
            task_input=str(getattr(resolved, "arguments", "") or ""),
        )


class SkillInvocationTracker:
    def __init__(self, store: MemoryWorkStore) -> None:
        self._store = store

    # -- open -------------------------------------------------------------------

    def begin(
        self,
        *,
        session_id: str,
        turn_id: str,
        identity: SkillIdentity,
    ) -> str:
        """Open the invocation record at the skill-run boundary.

        Idempotent on ``invocation_id`` (derived deterministically from
        session+turn+skill): a replayed resolve cannot duplicate the record.
        """
        invocation_id = (
            f"skilltrace:{session_id}:{turn_id}:{identity.skill_id}"
        )
        self._store.open_skill_invocation(
            invocation_id,
            session_id=session_id,
            turn_id=turn_id,
            skill_id=identity.skill_id,
            cloud_skill_id=identity.cloud_skill_id,
            skill_version_id=identity.skill_version_id,
            skill_content_hash=identity.skill_content_hash,
        )
        return invocation_id

    def record_steps_from_events(
        self, invocation_id: str, *, session_id: str, turn_id: str
    ) -> None:
        """Materialize tool steps from the turn's canonical trace events."""
        events = self._store.events_for_turn(session_id, turn_id)
        results_by_call: dict[str, dict[str, Any]] = {}
        call_order: list[str] = []
        calls_by_id: dict[str, dict[str, Any]] = {}
        for event in events:
            payload = event.payload
            if payload.get("role") == "assistant":
                for call in payload.get("tool_calls") or []:
                    cid = call.get("id")
                    if cid and cid not in calls_by_id:
                        calls_by_id[cid] = call
                        call_order.append(cid)
            elif payload.get("role") == "tool":
                cid = payload.get("tool_call_id")
                if cid:
                    results_by_call[cid] = payload
        step_seq = 0
        for cid in call_order:
            call = calls_by_id[cid]
            result = results_by_call.get(cid)
            tail = ""
            truncated = False
            status = "missing_result"
            if result is not None:
                content = str(result.get("content") or "")
                if len(content) > _STEP_RESULT_TAIL_CHARS:
                    # Multibyte-safe: slice on codepoints, never mid-sequence.
                    tail = content[-_STEP_RESULT_TAIL_CHARS:]
                    truncated = True
                else:
                    tail = content
                status = "error" if result.get("is_error") else "success"
            self._store.add_invocation_step(
                invocation_id,
                SkillInvocationStep(
                    invocation_id=invocation_id,
                    step_seq=step_seq,
                    tool_call_id=cid,
                    tool_name=str(call.get("name") or ""),
                    arguments=call.get("arguments") or {},
                    tool_result_tail=tail,
                    tool_result_truncated=truncated,
                    status=status,
                ),
            )
            step_seq += 1

    # -- seal ---------------------------------------------------------------------

    def seal(
        self,
        invocation_id: str,
        *,
        session_id: str,
        turn_id: str,
        terminal_status: str,
        task_input: str,
        agent_completion: str,
    ) -> SealedTrace | None:
        """Seal the invocation and evaluate the evolution threshold.

        Returns the sealed trace, or ``None`` when the invocation was already
        sealed (replay-safe). Outcome derives independently from the terminal
        status; score follows the spec bands (success ≥ 0.5, failure ≤ 0.5).
        """
        inv = self._store.skill_invocation(invocation_id)
        if inv is None:
            return None
        if inv.status != "open":
            return SealedTrace(
                invocation_id=inv.invocation_id,
                cloud_skill_id=inv.cloud_skill_id,
                outcome=inv.outcome or "success",
                score=inv.score if inv.score is not None else 1.0,
            )
        outcome = "success" if terminal_status == "completed" else "failure"
        score = _OUTCOME_SCORE.get(terminal_status, 0.0)
        self.record_steps_from_events(invocation_id, session_id=session_id, turn_id=turn_id)
        sealed = self._store.seal_skill_invocation(
            invocation_id,
            task_input=task_input,
            agent_completion=agent_completion,
            outcome=outcome,
            score=score,
        )
        self.maybe_evolve(sealed.cloud_skill_id)
        return SealedTrace(
            invocation_id=invocation_id,
            cloud_skill_id=sealed.cloud_skill_id,
            outcome=outcome,
            score=score,
        )

    # -- evolution ------------------------------------------------------------------

    def maybe_evolve(self, cloud_skill_id: str) -> str | None:
        """Claim exactly 8 sealed traces and enqueue one evolution job.

        Atomic across concurrent workers: ``claim_skill_traces`` marks the
        rows consumed inside the same transaction that returns them, so two
        callers can never claim the same trace.
        """
        batch_id = f"evolve:{cloud_skill_id}:{self._store.unconsumed_trace_count(cloud_skill_id)}"
        claimed = self._store.claim_skill_traces(
            cloud_skill_id, batch_id=batch_id, count=EVOLUTION_MAX_TRACES
        )
        if len(claimed) < EVOLUTION_MIN_TRACES:
            return None
        batch_id = f"evolve:{cloud_skill_id}:{claimed[0]}:{claimed[-1]}"
        self._store.create_evolution_batch(
            batch_id, cloud_skill_id=cloud_skill_id, trace_ids=claimed, job_id=None
        )
        job = self._store.enqueue_job(
            JOB_SKILL_EVOLUTION,
            f"skillevolve:{batch_id}",
            {"batch_id": batch_id, "cloud_skill_id": cloud_skill_id, "trace_ids": claimed},
            session_id=None,
        )
        self._store.create_evolution_batch(
            batch_id, cloud_skill_id=cloud_skill_id, trace_ids=claimed, job_id=job.job_id
        )
        return batch_id

    def recover_pending_evolutions(self) -> list[str]:
        """Startup recovery: create jobs for thresholds reached pre-crash."""
        created: list[str] = []
        for skill_id in self._store.pending_evolution_skills(EVOLUTION_MIN_TRACES):
            batch = self.maybe_evolve(skill_id)
            if batch is not None:
                created.append(batch)
        return created


__all__ = [
    "EVOLUTION_MAX_TRACES",
    "EVOLUTION_MIN_TRACES",
    "SealedTrace",
    "SkillIdentity",
    "SkillInvocationTracker",
]
