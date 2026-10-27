"""Outbox job executors (V3.8).

One executor per durable job kind. Executors are pure transitions over:

  claimed job → external call (stable operation_id) → verify → job outcome

Rules every executor obeys (spec §4.2/§6):

- Reconcile BEFORE executing: an ``outcome_unknown`` job first asks the
  server for the operation's authoritative status; only a definitely-absent
  operation may be (re)submitted.
- Retry reuses the same operation_id — never mint a new one.
- ``OutcomeUnknownError`` marks the job ``outcome_unknown``; it is NEVER
  auto-retried blindly.
- Completed results carry the server receipt plus the raw-readback verdict.
- Only ``completed``/``no_action`` results may advance consumption
  watermarks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from homemaster.memory.memory_verifier import RawMemoryVerifier
from homemaster.memory.memory_work_store import (
    JOB_DREAMING,
    JOB_IMPLICIT_FEEDBACK,
    JOB_SKILL_EVOLUTION,
    JOB_TURN_ADD,
    MemoryWorkStore,
    OutboxJob,
)
from homemaster.memory.mindmemos_client import (
    MindMemOSClient,
    OperationConflictError,
    OperationNotFoundError,
    OutcomeUnknownError,
)
from homemaster.memory.session_trace_store import SessionTraceStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobOutcome:
    """How the worker should settle the job after execute()."""

    status: str  # completed | no_action | retry | outcome_unknown | failed
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    retry_delay_seconds: float = 5.0


class JobExecutor(Protocol):
    async def execute(self, job: OutboxJob) -> JobOutcome: ...


class MemoryAddExecutor:
    """``turn_add`` → one Vanilla Add per terminal turn (spec §4.2)."""

    def __init__(
        self,
        *,
        store: MemoryWorkStore,
        trace: SessionTraceStore,
        client: MindMemOSClient,
        verifier: RawMemoryVerifier,
    ) -> None:
        self._store = store
        self._trace = trace
        self._client = client
        self._verifier = verifier

    async def execute(self, job: OutboxJob) -> JobOutcome:
        session_id = job.session_id or job.payload.get("session_id")
        turn_id = job.turn_id or job.payload.get("turn_id")
        if not session_id or not turn_id:
            return JobOutcome(status="failed", error="job missing session_id/turn_id")
        operation_id = job.operation_key

        # Reconcile a previously unknown outcome before touching the server.
        if job.status == "outcome_unknown":
            reconciled = await self._reconcile(operation_id)
            if reconciled is not None:
                return reconciled

        dialogue = self._trace.dialogue_for_turn(session_id, turn_id)
        turn = self._trace.turn(session_id, turn_id)
        if not dialogue:
            # A terminal turn with zero visible messages still satisfies the
            # contract: submit a minimal marker so the AddRecord exists.
            reason = (turn.terminal_reason if turn else None) or "empty"
            dialogue = [
                {
                    "role": "user",
                    "content": (
                        f"[homemaster] turn {turn_id} ended ({reason})"
                        " with no visible messages."
                    ),
                }
            ]
        try:
            receipt = await self._client.add_turn(
                operation_id=operation_id,
                session_id=session_id,
                turn_id=turn_id,
                dialogue=dialogue,
                metadata={"terminal_status": turn.status if turn else "unknown"},
            )
        except OutcomeUnknownError as exc:
            return JobOutcome(status="outcome_unknown", error=str(exc))
        except OperationConflictError as exc:
            return JobOutcome(status="failed", error=f"operation conflict: {exc}")

        verification = await self._verify(receipt.add_record_id, list(receipt.memory_ids))
        result = {
            "operation_id": receipt.operation_id,
            "add_record_id": receipt.add_record_id,
            "memory_ids": list(receipt.memory_ids),
            "replayed": receipt.replayed,
            "raw_verified": verification["verified"],
            "missing_ids": verification["missing_ids"],
        }
        if verification["missing_ids"]:
            # The business receipt succeeded but readback disagrees: this is a
            # storage-level inconsistency — retry-able via fail, NOT silent.
            return JobOutcome(
                status="retry",
                result=result,
                error=f"raw readback missing ids: {verification['missing_ids']}",
                retry_delay_seconds=15.0,
            )
        status = "no_action" if receipt.status == "no_action" else "completed"
        return JobOutcome(status=status, result=result)

    async def _reconcile(self, operation_id: str) -> JobOutcome | None:
        try:
            status = await self._client.operation_status(operation_id)
        except OperationNotFoundError:
            return None  # never executed — safe to submit
        except OutcomeUnknownError as exc:
            return JobOutcome(status="outcome_unknown", error=str(exc))
        if status.status == "completed" or status.status == "no_action":
            result = status.result or {}
            verification = await self._verify(
                str(result.get("add_record_id") or ""),
                [str(m) for m in (result.get("memory_ids") or ())],
            )
            return JobOutcome(
                status=status.status,
                result={**result, "reconciled": True, "raw_verified": verification["verified"]},
            )
        if status.status == "failed":
            return JobOutcome(status="failed", error=status.error or "server op failed")
        # accepted/processing server-side: retry later (server holds the work)
        return JobOutcome(status="retry", error="server still processing", retry_delay_seconds=10.0)

    async def _verify(self, add_record_id: str, memory_ids: list[str]) -> dict[str, Any]:
        missing: list[str] = []
        add_record_ok = True
        if add_record_id:
            add_record_ok = await self._verifier.add_record_exists(add_record_id)
            if not add_record_ok:
                missing.append(f"add_record:{add_record_id}")
        existence = await self._verifier.memory_ids_exist(memory_ids)
        missing.extend(f"memory:{mid}" for mid, ok in existence.items() if not ok)
        verified = not missing and (add_record_ok or not add_record_id)
        return {"verified": verified, "missing_ids": missing}


class ArchiveDreamingExecutor:
    """``archive_dreaming`` → Dreaming bound to one snapshot's Add seeds."""

    def __init__(
        self,
        *,
        store: MemoryWorkStore,
        client: MindMemOSClient,
        verifier: RawMemoryVerifier,
    ) -> None:
        self._store = store
        self._client = client
        self._verifier = verifier

    async def execute(self, job: OutboxJob) -> JobOutcome:
        archive_id = job.archive_id or job.payload.get("archive_id")
        snapshot = self._store.archive(archive_id) if archive_id else None
        if snapshot is None:
            return JobOutcome(status="failed", error=f"unknown archive {archive_id}")
        seeds = list(snapshot.add_operation_ids)
        if job.status == "outcome_unknown":
            try:
                status = await self._client.operation_status(job.operation_key)
            except OperationNotFoundError:
                pass
            except OutcomeUnknownError as exc:
                return JobOutcome(status="outcome_unknown", error=str(exc))
            else:
                if status.status in {"completed", "no_action"}:
                    return JobOutcome(status=status.status, result=status.result or {})
                if status.status == "failed":
                    return JobOutcome(status="failed", error=status.error or "server op failed")
                return JobOutcome(status="retry", retry_delay_seconds=10.0)
        if not seeds:
            # Spec: archive with zero produced memories still records the
            # decision — Dreaming short-circuits to a no-op rather than error.
            return JobOutcome(
                status="no_action", result={"archive_id": archive_id, "reason": "no_seeds"}
            )
        # Seeds are stored as HomeMaster operation ids; Dreaming needs the
        # server's AddRecord ids. Resolve each via the operations ledger —
        # a seed whose Add is still in flight means retry, never drop.
        resolved: list[str] = []
        for op_id in seeds:
            try:
                status = await self._client.operation_status(op_id)
            except OperationNotFoundError:
                return JobOutcome(
                    status="retry",
                    error=f"seed operation {op_id} not visible yet",
                    retry_delay_seconds=10.0,
                )
            except OutcomeUnknownError as exc:
                return JobOutcome(status="outcome_unknown", error=str(exc))
            if status.status not in {"completed", "no_action"}:
                return JobOutcome(
                    status="retry",
                    error=f"seed operation {op_id} still {status.status}",
                    retry_delay_seconds=10.0,
                )
            add_record_id = (status.result or {}).get("add_record_id")
            if add_record_id:
                resolved.append(str(add_record_id))
        if not resolved:
            return JobOutcome(
                status="no_action",
                result={"archive_id": archive_id, "reason": "no_add_records"},
            )
        try:
            receipt = await self._client.dreaming(
                operation_id=job.operation_key,
                seed_add_record_ids=resolved,
                session_id=snapshot.session_id,
            )
        except OutcomeUnknownError as exc:
            return JobOutcome(status="outcome_unknown", error=str(exc))
        except OperationConflictError as exc:
            return JobOutcome(status="failed", error=f"operation conflict: {exc}")
        existence = await self._verifier.memory_ids_exist(list(receipt.produced_memory_ids))
        missing = [mid for mid, ok in existence.items() if not ok]
        result = {
            "archive_id": archive_id,
            "dreaming_record_id": receipt.dreaming_record_id,
            "produced_memory_ids": list(receipt.produced_memory_ids),
            "actions": list(receipt.actions),
            "raw_verified": not missing,
            "missing_ids": missing,
        }
        if missing:
            return JobOutcome(status="retry", result=result,
                              error=f"raw readback missing: {missing}", retry_delay_seconds=15.0)
        return JobOutcome(
            status="no_action" if receipt.status == "no_action" else "completed", result=result
        )


class SkillEvolutionExecutor:
    """``skill_evolution`` → run evolution on the claimed 8-trace batch."""

    def __init__(self, *, store: MemoryWorkStore, client: MindMemOSClient) -> None:
        self._store = store
        self._client = client

    async def execute(self, job: OutboxJob) -> JobOutcome:
        batch_id = job.payload.get("batch_id")
        cloud_skill_id = job.payload.get("cloud_skill_id")
        trace_ids = job.payload.get("trace_ids") or []
        if not (batch_id and cloud_skill_id and len(trace_ids) == 8):
            return JobOutcome(status="failed", error="evolution job needs batch_id/skill/8 traces")
        if job.status == "outcome_unknown":
            try:
                status = await self._client.operation_status(job.operation_key)
            except OperationNotFoundError:
                pass
            except OutcomeUnknownError as exc:
                return JobOutcome(status="outcome_unknown", error=str(exc))
            else:
                if status.status in {"completed", "no_action"}:
                    return JobOutcome(status=status.status, result=status.result or {})
                if status.status == "failed":
                    return JobOutcome(status="failed", error=status.error or "server op failed")
        # The server validates that every trace in the batch is already sealed
        # upstream — submit each sealed local trace first (idempotent on
        # invocation_id) so a retried job converges instead of 409ing forever.
        for invocation_id in trace_ids:
            inv = self._store.skill_invocation(invocation_id)
            if inv is None or inv.status not in {"sealed", "consumed"}:
                return JobOutcome(
                    status="failed", error=f"trace {invocation_id} not sealed in store"
                )
            steps = [
                {
                    "tool_call_id": s.tool_call_id,
                    "tool_name": s.tool_name,
                    "arguments": s.arguments,
                    "tool_result_tail": s.tool_result_tail,
                    "tool_result_truncated": s.tool_result_truncated,
                    "status": s.status,
                }
                for s in self._store.invocation_steps(invocation_id)
            ]
            try:
                await self._client.submit_skill_trace(
                    cloud_skill_id=cloud_skill_id,
                    trace={
                        "invocation_id": invocation_id,
                        "skill_version_id": inv.skill_version_id,
                        "skill_content_hash": inv.skill_content_hash,
                        "task_input": inv.task_input or "",
                        "tool_steps": steps,
                        "agent_completion": inv.agent_completion or "",
                        "outcome": inv.outcome or "success",
                        "score": inv.score if inv.score is not None else 1.0,
                    },
                )
            except OperationConflictError as exc:
                return JobOutcome(
                    status="failed",
                    error=f"trace {invocation_id} conflicts with a sealed upstream trace: {exc}",
                )
            except OutcomeUnknownError as exc:
                return JobOutcome(status="outcome_unknown", error=str(exc))
        try:
            receipt = await self._client.submit_evolution_batch(
                cloud_skill_id=cloud_skill_id, batch_id=batch_id, trace_ids=trace_ids
            )
        except OutcomeUnknownError as exc:
            return JobOutcome(status="outcome_unknown", error=str(exc))
        except OperationConflictError as exc:
            return JobOutcome(status="failed", error=f"operation conflict: {exc}")
        return JobOutcome(
            status="no_action" if receipt.status == "no_action" else "completed",
            result={
                "batch_id": batch_id,
                "cloud_skill_id": cloud_skill_id,
                "draft_version_id": receipt.draft_version_id,
                "reason": receipt.reason,
            },
        )


class ImplicitFeedbackExecutor:
    """``implicit_feedback`` → one scheduled implicit-feedback window."""

    def __init__(
        self,
        *,
        store: MemoryWorkStore,
        client: MindMemOSClient,
        verifier: RawMemoryVerifier,
    ) -> None:
        self._store = store
        self._client = client
        self._verifier = verifier

    async def execute(self, job: OutboxJob) -> JobOutcome:
        scope_key = job.payload.get("scope_key")
        window = job.payload.get("window") or {}
        if not scope_key or "window_start" not in window:
            return JobOutcome(status="failed", error="feedback job missing scope/window")
        if job.status == "outcome_unknown":
            try:
                status = await self._client.operation_status(job.operation_key)
            except OperationNotFoundError:
                pass
            except OutcomeUnknownError as exc:
                return JobOutcome(status="outcome_unknown", error=str(exc))
            else:
                if status.status in {"completed", "no_action"}:
                    return JobOutcome(status=status.status, result=status.result or {})
                if status.status == "failed":
                    return JobOutcome(status="failed", error=status.error or "server op failed")
        try:
            receipt = await self._client.feedback_implicit(
                operation_id=job.operation_key, window=window
            )
        except OutcomeUnknownError as exc:
            return JobOutcome(status="outcome_unknown", error=str(exc))
        except OperationConflictError as exc:
            return JobOutcome(status="failed", error=f"operation conflict: {exc}")
        # Verify every mutating action by raw readback (spec §4.4/§6).
        mutated = [a.memory_id for a in receipt.actions if a.memory_id and a.action != "noop"]
        existence = await self._verifier.memory_ids_exist(mutated)
        missing = [mid for mid, ok in existence.items() if not ok]
        result = {
            "feedback_record_id": receipt.feedback_record_id,
            "actions": [
                {"action_id": a.action_id, "action": a.action, "memory_id": a.memory_id}
                for a in receipt.actions
            ],
            "raw_verified": not missing,
            "missing_ids": missing,
        }
        if missing:
            return JobOutcome(status="retry", result=result,
                              error=f"raw readback missing: {missing}", retry_delay_seconds=15.0)
        return JobOutcome(
            status="no_action" if receipt.status == "no_action" else "completed", result=result
        )


class GenericWorkExecutor:
    """``generic_work`` → registered named callables for app-owned tasks.

    Payload: ``{"task": <name>, "args": {...}}``. Tasks are registered by the
    application at composition time (e.g. stale-job inventory); they are
    plain async callables receiving ``(store, client, args)``.
    """

    def __init__(self, *, store: MemoryWorkStore, client: MindMemOSClient) -> None:
        self._store = store
        self._client = client
        self._tasks: dict[str, Any] = {}

    def register(self, name: str, fn: Any) -> None:
        self._tasks[name] = fn

    async def execute(self, job: OutboxJob) -> JobOutcome:
        name = job.payload.get("task")
        fn = self._tasks.get(str(name))
        if fn is None:
            return JobOutcome(status="failed", error=f"unknown work task {name!r}")
        result = await fn(self._store, self._client, job.payload.get("args") or {})
        if isinstance(result, JobOutcome):
            return result
        return JobOutcome(status="completed", result={"task_result": result})


def default_executors(
    *,
    store: MemoryWorkStore,
    trace: SessionTraceStore,
    client: MindMemOSClient,
    verifier: RawMemoryVerifier,
) -> dict[str, JobExecutor]:
    generic = GenericWorkExecutor(store=store, client=client)
    return {
        JOB_TURN_ADD: MemoryAddExecutor(store=store, trace=trace, client=client, verifier=verifier),
        JOB_DREAMING: ArchiveDreamingExecutor(store=store, client=client, verifier=verifier),
        JOB_SKILL_EVOLUTION: SkillEvolutionExecutor(store=store, client=client),
        JOB_IMPLICIT_FEEDBACK: ImplicitFeedbackExecutor(
            store=store, client=client, verifier=verifier
        ),
        "generic_work": generic,
    }


__all__ = [
    "ArchiveDreamingExecutor",
    "GenericWorkExecutor",
    "ImplicitFeedbackExecutor",
    "JobExecutor",
    "JobOutcome",
    "MemoryAddExecutor",
    "SkillEvolutionExecutor",
    "default_executors",
]
