"""Application-owned admission and result tracking for session finalization."""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Callable
from typing import Any

from homemaster.experience.finalizer import FinalizeResult, SessionFinalizer
from homemaster.memory.add_queue import MemoryWorkReceipt


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

    def enqueue(self, session_id: str, exit_reason: str) -> MemoryWorkReceipt | None:
        if not self._queue.started or not self._ready():
            return None
        result_future: concurrent.futures.Future[FinalizeResult] = concurrent.futures.Future()

        async def work() -> None:
            try:
                result = await self._finalizer.finalize(session_id, exit_reason)
                if not result_future.done():
                    result_future.set_result(result)
                if result.status == "failed":
                    raise RuntimeError(result.error or "session finalization failed")
            except BaseException as exc:
                if not result_future.done():
                    result_future.set_exception(exc)
                raise

        receipt = self._queue.enqueue_work(
            job_type="session_finalization",
            session_id=session_id,
            work=work,
        )
        self._results[receipt.job_id] = result_future
        return receipt

    async def wait(self, receipt: MemoryWorkReceipt) -> FinalizeResult:
        try:
            future = self._results[receipt.job_id]
        except KeyError as exc:
            raise KeyError(f"unknown session finalization job: {receipt.job_id}") from exc
        return await asyncio.shield(asyncio.wrap_future(future))
