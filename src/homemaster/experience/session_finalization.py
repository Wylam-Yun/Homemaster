"""Application-owned admission and result tracking for session finalization."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homemaster.experience.finalizer import FinalizeResult, SessionFinalizer
from homemaster.memory.add_queue import MemoryWorkReceipt

logger = logging.getLogger(__name__)


@dataclass
class _PendingFinalization:
    """One admitted-but-unfinished archive intent for a single session.

    A second trigger while the queued job has not started merges into the
    pending job: the trace boundary and exit reason move forward so the job
    covers everything up to the latest request, and the caller reuses the
    existing receipt instead of spawning a duplicate extraction.
    """

    exit_reason: str
    trace_end: int | None
    receipt: MemoryWorkReceipt | None = None
    started: bool = False


class SessionFinalizationController:
    def __init__(
        self,
        finalizer: SessionFinalizer,
        queue: Any,
        *,
        ready: Callable[[], bool] | None = None,
        event_sink: Any | None = None,
    ) -> None:
        self._finalizer = finalizer
        self._queue = queue
        self._ready = ready or (lambda: True)
        self._event_sink = event_sink
        self._results: dict[str, concurrent.futures.Future[FinalizeResult]] = {}
        self._inflight: dict[str, _PendingFinalization] = {}
        # Latest in-memory result per session, for sessions whose finalization
        # produced no durable receipt ("empty" / "already_completed").
        self._latest_results: dict[str, FinalizeResult] = {}

    def enqueue(self, session_id: str, exit_reason: str) -> MemoryWorkReceipt | None:
        if not self._queue.started or not self._ready():
            # Admission denial must be observable — a silently dropped
            # finalization is an unwritten episode nobody can retry.
            logger.warning(
                "session finalization not admitted for %s (queue_started=%s, backend_ready=%s)",
                session_id,
                self._queue.started,
                self._ready(),
            )
            return None
        pending = self._inflight.get(session_id)
        if pending is not None and not pending.started:
            pending.exit_reason = exit_reason
            pending.trace_end = self._capture_boundary()
            return pending.receipt
        pending = _PendingFinalization(
            exit_reason=exit_reason,
            trace_end=self._capture_boundary(),
        )
        result_future: concurrent.futures.Future[FinalizeResult] = concurrent.futures.Future()

        async def work() -> None:
            pending.started = True
            job_id = pending.receipt.job_id if pending.receipt is not None else ""
            await self._emit(
                "memory.work.processing",
                session_id,
                job_id,
                {"job_type": "session_finalization"},
            )
            failed_emitted = False
            try:
                result = await self._finalizer.finalize(
                    session_id,
                    pending.exit_reason,
                    trace_end=pending.trace_end,
                )
                self._latest_results[session_id] = result
                if not result_future.done():
                    result_future.set_result(result)
                if result.status == "failed":
                    failed_emitted = True
                    await self._emit(
                        "memory.work.failed",
                        session_id,
                        job_id,
                        {
                            "job_type": "session_finalization",
                            "error": result.error or "session finalization failed",
                        },
                    )
                    raise RuntimeError(result.error or "session finalization failed")
                await self._emit(
                    "memory.work.completed",
                    session_id,
                    job_id,
                    {
                        "job_type": "session_finalization",
                        "status": result.status,
                        "collected_events": result.collected_events,
                        "extracted_count": len(result.operations),
                        "duration_ms": result.duration_ms,
                    },
                )
            except BaseException as exc:
                if not result_future.done():
                    result_future.set_exception(exc)
                if not failed_emitted:
                    await self._emit(
                        "memory.work.failed",
                        session_id,
                        job_id,
                        {"job_type": "session_finalization", "error": str(exc)},
                    )
                raise
            finally:
                if self._inflight.get(session_id) is pending:
                    del self._inflight[session_id]

        receipt = self._queue.enqueue_work(
            job_type="session_finalization",
            session_id=session_id,
            work=work,
        )
        pending.receipt = receipt
        self._inflight[session_id] = pending
        for job_id in [key for key, future in self._results.items() if future.done()]:
            del self._results[job_id]
        self._results[receipt.job_id] = result_future
        return receipt

    def _capture_boundary(self) -> int | None:
        capture = getattr(self._finalizer, "capture_trace_boundary", None)
        if not callable(capture):
            return None
        try:
            return capture()
        except OSError:
            return None

    async def wait(self, receipt: MemoryWorkReceipt) -> FinalizeResult:
        try:
            future = self._results[receipt.job_id]
        except KeyError as exc:
            raise KeyError(f"unknown session finalization job: {receipt.job_id}") from exc
        return await asyncio.shield(asyncio.wrap_future(future))

    def status_for(self, session_id: str) -> dict[str, Any] | None:
        """Latest finalization view for one session: live state first, then the
        durable job receipt under ``experience_jobs``."""
        pending = self._inflight.get(session_id)
        if pending is not None and not pending.started:
            return _live_view(pending)
        job = self._latest_job(session_id)
        if pending is not None and pending.started:
            view = finalization_job_view(job) if job is not None else {}
            view.setdefault(
                "job_id", pending.receipt.job_id if pending.receipt is not None else None
            )
            view["state"] = "processing"
            return view
        if job is not None:
            return finalization_job_view(job)
        result = self._latest_results.get(session_id)
        return None if result is None else _result_view(result)

    def statuses(self) -> dict[str, dict[str, Any]]:
        """Latest finalization view per session, merged with live queue state."""
        views = {
            session_id: finalization_job_view(job) for session_id, job in self._job_index().items()
        }
        for session_id, pending in self._inflight.items():
            if not pending.started:
                views[session_id] = _live_view(pending)
            elif session_id in views:
                views[session_id]["state"] = "processing"
            else:
                views[session_id] = _live_view(pending)
        for session_id, result in self._latest_results.items():
            views.setdefault(session_id, _result_view(result))
        return views

    def _job_index(self) -> dict[str, dict[str, Any]]:
        job_index = getattr(self._finalizer, "job_index", None)
        if not callable(job_index):
            return {}
        return job_index()

    def _latest_job(self, session_id: str) -> dict[str, Any] | None:
        return self._job_index().get(session_id)

    async def _emit(
        self,
        event_type: str,
        session_id: str,
        job_id: str,
        payload: dict[str, Any],
    ) -> None:
        if self._event_sink is None:
            return
        from homemaster.events.runtime_events import RuntimeEvent

        event = RuntimeEvent(
            type=event_type,
            session_id=session_id,
            run_id=job_id,
            turn_index=None,
            payload={"job_id": job_id, **payload},
        )
        try:
            aemit = getattr(self._event_sink, "aemit", None)
            if callable(aemit):
                await aemit(event)
                return
            emitted = self._event_sink.emit(event)
            if hasattr(emitted, "__await__"):
                await emitted
        except Exception:
            # Job lifecycle events are observability; a sink failure must not
            # turn into a finalization failure.
            logger.warning(
                "failed to emit %s for session %s", event_type, session_id, exc_info=True
            )


def _live_view(pending: _PendingFinalization) -> dict[str, Any]:
    return {
        "state": "processing" if pending.started else "queued",
        "job_id": pending.receipt.job_id if pending.receipt is not None else None,
        "exit_reason": pending.exit_reason,
    }


def _result_view(result: FinalizeResult) -> dict[str, Any]:
    """Status view for a finished in-memory result that left no job receipt."""
    return {
        "state": "failed" if result.status == "failed" else "completed",
        "job_id": None,
        "result_status": result.status,
        "error": result.error,
        "collected_events": result.collected_events,
        "extracted_count": len(result.operations),
        "duration_ms": result.duration_ms,
    }


def finalization_job_view(job: dict[str, Any]) -> dict[str, Any]:
    """Coarse archive status view over one durable ``job.json`` receipt."""
    raw_status = job.get("status")
    state = {"completed": "completed", "failed": "failed"}.get(raw_status, "processing")
    stages = {}
    for stage in ("add", "implicit_feedback", "dreaming_counter", "dreaming"):
        value = job.get(stage)
        stages[stage] = value.get("status") if isinstance(value, dict) else value
    add = job.get("add") if isinstance(job.get("add"), dict) else {}
    operations = add.get("operations")
    return {
        "state": state,
        "job_id": job.get("job_id"),
        "exit_reason": job.get("exit_reason"),
        "stages": stages,
        "failed_phase": job.get("failed_phase"),
        "error": job.get("error") or add.get("error"),
        "collected_events": job.get("collected_events"),
        "extracted_count": len(operations) if isinstance(operations, list) else None,
    }
