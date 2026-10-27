"""Durable outbox worker pool (V3.8).

``MemoryWorkerPool`` owns the background loop that claims jobs from
``MemoryWorkStore``, executes them through per-kind executors, and settles
the lease/result. Its contract (spec §4.2/§6):

- admission is durable BEFORE work starts — a worker crash never loses a job;
- every job runs under a lease; an expired lease is reclaimed by any worker;
- a job is ``completed``/``no_action`` only after the executor reports a
  verified external terminal state — transport-level success is not enough;
- every settle writes a structured JSONL audit line (no secrets, bounded
  payloads);
- ``run_once()`` exists for deterministic tests and live gates.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homemaster.events.trace import append_jsonl_event
from homemaster.memory.memory_jobs import JobExecutor, JobOutcome
from homemaster.memory.memory_work_store import MemoryWorkStore, OutboxJob, StoreAudit
from homemaster.memory.mindmemos_client import MindMemOSClient

logger = logging.getLogger(__name__)

_MAX_ERROR_LEN = 2000
_MAX_LOG_IDS = 64


def _bounded_ids(result: dict[str, Any] | None) -> dict[str, Any]:
    if not result:
        return {}
    out: dict[str, Any] = {}
    for key in ("add_record_id", "memory_ids", "produced_memory_ids", "search_record_id",
                "feedback_record_id", "dreaming_record_id", "draft_version_id", "missing_ids",
                "raw_verified", "replayed", "reconciled", "reason"):
        if key in result:
            value = result[key]
            if isinstance(value, list):
                out[key] = [str(v) for v in value[:_MAX_LOG_IDS]]
            else:
                out[key] = value
    return out


@dataclass(frozen=True)
class WorkerReadiness:
    """Startup readiness gate (spec §4.2): every component reports its state."""

    store_open: bool
    client_ready: bool
    audit: StoreAudit | None

    @property
    def ready(self) -> bool:
        return self.store_open and self.client_ready


class MemoryWorkerPool:
    def __init__(
        self,
        *,
        store: MemoryWorkStore,
        client: MindMemOSClient,
        executors: dict[str, JobExecutor],
        audit_path: Path | None = None,
        owner_id: str | None = None,
        on_tick: Callable[[], object] | None = None,
        poll_interval_seconds: float = 1.0,
        lease_seconds: float = 300.0,
        max_concurrent: int = 4,
    ) -> None:
        self._store = store
        self._client = client
        self._executors = dict(executors)
        self._audit_path = audit_path
        self._owner = owner_id or f"worker-{uuid.uuid4().hex[:12]}"
        self._on_tick = on_tick
        self._poll = poll_interval_seconds
        self._lease = lease_seconds
        self._max_concurrent = max_concurrent
        self._stop = asyncio.Event()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._loop_task: asyncio.Task[Any] | None = None
        self._inflight: set[asyncio.Task[Any]] = set()
        self.readiness: WorkerReadiness | None = None

    @property
    def owner_id(self) -> str:
        return self._owner

    # -- lifecycle -------------------------------------------------------------

    async def start(self) -> WorkerReadiness:
        """Open the store, audit/recover, probe the SDK, launch the loop.

        Readiness = durable store open + SDK health + recovery audit recorded.
        Inconsistencies found by the audit are logged and surfaced; a store
        that cannot open raises (fail closed).
        """
        await asyncio.to_thread(self._store.open)
        audit = await asyncio.to_thread(self._store.audit_and_recover, owner=self._owner)
        client_ready = await self._client.health()
        self.readiness = WorkerReadiness(store_open=True, client_ready=client_ready, audit=audit)
        self._log(
            "worker.started",
            client_ready=client_ready,
            rebuilt_intents=audit.rebuilt_intents,
            expired_jobs=list(audit.expired_processing_jobs),
            interrupted_turns=list(audit.active_turns_marked_interrupted),
            missing_intent=list(audit.terminal_turns_without_intent),
        )
        self._stop.clear()
        self._loop_task = asyncio.create_task(self._run_loop(), name="memory-worker-loop")
        return self.readiness

    async def stop(self, *, drain_timeout_seconds: float = 60.0) -> bool:
        """Stop the loop and wait for in-flight jobs within the deadline."""
        self._stop.set()
        deadline = time.monotonic() + drain_timeout_seconds
        if self._loop_task is not None:
            try:
                await asyncio.wait_for(
                    self._loop_task, timeout=max(1.0, deadline - time.monotonic())
                )
            except TimeoutError:
                self._loop_task.cancel()
        remaining = deadline - time.monotonic()
        if self._inflight and remaining > 0:
            done, pending = await asyncio.wait(self._inflight, timeout=max(0.5, remaining))
            for task in pending:
                task.cancel()
            self._log("worker.stop", drained=len(done), cancelled=len(pending))
            return not pending
        self._log("worker.stop", drained=0, cancelled=0)
        return True

    async def aclose(self) -> None:
        """Resource-scope closer: stop the loop, then close the durable store."""
        await self.stop()
        await asyncio.to_thread(self._store.close)

    # -- loop -------------------------------------------------------------------

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            if self._on_tick is not None:
                try:
                    self._on_tick()
                except Exception:  # pragma: no cover - defensive loop guard
                    logger.exception("memory worker tick hook failed")
            try:
                progressed = await self.run_once()
            except Exception:  # pragma: no cover - defensive loop guard
                logger.exception("memory worker iteration failed")
                self._log("worker.iteration_error")
                progressed = False
            if not progressed:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self._poll)
                except TimeoutError:
                    pass

    async def run_once(self) -> bool:
        """One claim+execute cycle. Returns True when any job progressed."""
        capacity = self._max_concurrent - len(self._inflight)
        if capacity <= 0:
            await self._drain_finished()
            return False
        claimed = await asyncio.to_thread(
            self._store.claim_jobs, self._owner,
            kinds=None, limit=capacity, lease_seconds=self._lease,
        )
        for job in claimed:
            task = asyncio.create_task(self._execute_and_settle(job), name=f"memjob-{job.job_id}")
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)
        await self._drain_finished()
        return bool(claimed)

    async def _drain_finished(self) -> None:
        done = [t for t in self._inflight if t.done()]
        for task in done:
            self._inflight.discard(task)
            if task.cancelled():
                continue
            exc = task.exception()
            if exc is not None:
                logger.error("memory job task crashed: %s", exc)
        await asyncio.sleep(0)

    async def _execute_and_settle(self, job: OutboxJob) -> None:
        start = time.monotonic()
        executor = self._executors.get(job.kind)
        if executor is None:
            self._settle(job, JobOutcome(status="failed", error=f"no executor for kind {job.kind}"))
            return
        self._log("job.started", job=job)
        try:
            outcome = await executor.execute(job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # executor bug → retryable failure
            logger.exception("executor crashed for %s", job.job_id)
            outcome = JobOutcome(status="retry", error=f"executor exception: {exc}")
        duration_ms = int((time.monotonic() - start) * 1000)
        self._settle(job, outcome, duration_ms=duration_ms)

    def _settle(self, job: OutboxJob, outcome: JobOutcome, *, duration_ms: int = 0) -> None:
        error = (outcome.error or "")[:_MAX_ERROR_LEN] or None
        try:
            if outcome.status == "completed":
                self._store.complete_job(job.job_id, owner=self._owner, result=outcome.result)
            elif outcome.status == "no_action":
                self._store.complete_job_no_action(
                    job.job_id, owner=self._owner, result=outcome.result
                )
            elif outcome.status == "outcome_unknown":
                self._store.mark_job_unknown(
                    job.job_id, owner=self._owner, error=error or "outcome unknown"
                )
            elif outcome.status == "deferred":
                self._store.defer_job(
                    job.job_id,
                    owner=self._owner,
                    reason=error or "waiting on dependency",
                    delay_seconds=outcome.retry_delay_seconds,
                )
            elif outcome.status == "failed":
                self._store.terminate_job(
                    job.job_id, owner=self._owner, error=error or "failed"
                )
            else:  # retry
                self._store.fail_job(
                    job.job_id, owner=self._owner, error=error or "retry",
                    retry_delay_seconds=outcome.retry_delay_seconds,
                )
        except Exception:
            logger.exception("failed to settle job %s", job.job_id)
        if job.archive_id and outcome.status in {"completed", "no_action", "failed"}:
            try:
                self._store.settle_archive(
                    job.archive_id,
                    "failed" if outcome.status == "failed" else "dreamed",
                )
            except Exception:
                logger.warning("failed to settle archive %s", job.archive_id)
        self._log(
            "job.settled",
            job=job,
            outcome_status=outcome.status,
            duration_ms=duration_ms,
            error=error,
            **_bounded_ids(outcome.result),
        )

    def _log(self, event: str, job: OutboxJob | None = None, **extra: Any) -> None:
        payload: dict[str, Any] = {"owner": self._owner, **extra}
        if job is not None:
            payload.update(
                {
                    "job_id": job.job_id,
                    "kind": job.kind,
                    "operation_key": job.operation_key,
                    "session_id": job.session_id,
                    "turn_id": job.turn_id,
                    "archive_id": job.archive_id,
                    "attempts": job.attempts,
                    "job_status": job.status,
                }
            )
        if self._audit_path is not None:
            try:
                append_jsonl_event(self._audit_path, event=event, payload=payload)
            except Exception:  # audit sink failure is sideband (project rule)
                logger.warning("memory audit sink write failed for %s", event)
        logger.debug("memory_work %s %s", event, payload)


__all__ = ["MemoryWorkerPool", "WorkerReadiness"]
