"""Application-owned serial queue for accepted structured MindMemOS writes."""

from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from homemaster.events.trace import append_jsonl_event
from homemaster.memory.models import MemoryRecord

_STOP = object()


class MemoryAddQueueClosed(RuntimeError):
    """Raised when a structured Add arrives after queue admission is sealed."""


@dataclass(frozen=True)
class MemoryAddReceipt:
    job_id: str
    status: Literal["accepted"] = "accepted"


@dataclass(frozen=True)
class _MemoryAddJob:
    job_id: str
    record: MemoryRecord
    provenance_seq: int
    context: Any
    run_id: str | None


class MemoryAddQueue:
    """Run accepted structured Add jobs in FIFO order on one worker."""

    def __init__(self, mindmemos: Any, *, audit_path: Path) -> None:
        self._mindmemos = mindmemos
        self._audit_path = audit_path
        self._queue: asyncio.Queue[_MemoryAddJob | object] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._sealed = False
        self._closed = False
        self._close_lock = asyncio.Lock()

    @property
    def sealed(self) -> bool:
        return self._sealed

    async def start(self) -> None:
        if self._closed or self._sealed:
            raise MemoryAddQueueClosed("memory Add queue is closed")
        if self._worker is None:
            self._worker = asyncio.create_task(
                self._run(), name="homemaster:memory-add-worker"
            )

    async def enqueue(
        self,
        *,
        record: MemoryRecord,
        provenance_seq: int,
        context: Any,
        run_id: str | None = None,
    ) -> MemoryAddReceipt:
        if self._sealed or self._closed:
            raise MemoryAddQueueClosed("memory Add queue is closing")
        if self._worker is None:
            raise RuntimeError("memory Add queue is not started")
        job = _MemoryAddJob(
            job_id=str(uuid4()),
            record=copy.deepcopy(record),
            provenance_seq=provenance_seq,
            context=copy.deepcopy(context),
            run_id=run_id,
        )
        self._queue.put_nowait(job)
        self._log(job, status="queued")
        return MemoryAddReceipt(job_id=job.job_id)

    async def wait_idle(self) -> None:
        await self._queue.join()

    async def aclose(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._sealed = True
            worker = self._worker
            if worker is not None:
                await self._queue.join()
                self._queue.put_nowait(_STOP)
                await worker
            self._closed = True

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            started = time.monotonic()
            try:
                if item is _STOP:
                    return
                assert isinstance(item, _MemoryAddJob)
                self._log(item, status="processing")
                result = await self._mindmemos.add_record(
                    item.record,
                    provenance_seq=item.provenance_seq,
                    context=item.context,
                )
                memory_id = result.get("memory_id") if isinstance(result, dict) else None
                if not isinstance(memory_id, str) or not memory_id:
                    raise RuntimeError("MindMemOS Add returned no verified raw memory")
                self._log(
                    item,
                    status="completed",
                    duration_ms=(time.monotonic() - started) * 1000,
                    memory_id=memory_id,
                )
            except Exception as exc:
                assert isinstance(item, _MemoryAddJob)
                self._log(
                    item,
                    status="failed",
                    duration_ms=(time.monotonic() - started) * 1000,
                    error=f"{type(exc).__name__}: {exc}",
                )
            finally:
                self._queue.task_done()

    def _log(
        self,
        job: _MemoryAddJob,
        *,
        status: str,
        duration_ms: float | None = None,
        memory_id: str | None = None,
        error: str | None = None,
    ) -> None:
        context = job.context
        append_jsonl_event(
            self._audit_path,
            event="memory_add_job",
            payload={
                "job_id": job.job_id,
                "job_type": "structured_add",
                "status": status,
                "tenant_id": getattr(context, "account_id", None),
                "project_id": getattr(context, "project_id", None),
                "session_id": getattr(context, "session_id", None),
                "run_id": job.run_id,
                "request_id": getattr(context, "request_id", None),
                "duration_ms": round(duration_ms, 3) if duration_ms is not None else None,
                "memory_id": memory_id,
                "error": error,
            },
        )


__all__ = ["MemoryAddQueue", "MemoryAddQueueClosed", "MemoryAddReceipt"]
