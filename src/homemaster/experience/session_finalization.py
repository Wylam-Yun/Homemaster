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
    ) -> None:
        self._finalizer = finalizer
        self._queue = queue
        self._ready = ready or (lambda: True)
        self._results: dict[str, concurrent.futures.Future[FinalizeResult]] = {}
        self._inflight: dict[str, _PendingFinalization] = {}

    def enqueue(self, session_id: str, exit_reason: str) -> MemoryWorkReceipt | None:
        if not self._queue.started or not self._ready():
            # Admission denial must be observable — a silently dropped
            # finalization is an unwritten episode nobody can retry.
            logger.warning(
                "session finalization not admitted for %s "
                "(queue_started=%s, backend_ready=%s)",
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
            try:
                result = await self._finalizer.finalize(
                    session_id,
                    pending.exit_reason,
                    trace_end=pending.trace_end,
                )
                if not result_future.done():
                    result_future.set_result(result)
                if result.status == "failed":
                    raise RuntimeError(result.error or "session finalization failed")
            except BaseException as exc:
                if not result_future.done():
                    result_future.set_exception(exc)
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
        for job_id in [
            key for key, future in self._results.items() if future.done()
        ]:
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
