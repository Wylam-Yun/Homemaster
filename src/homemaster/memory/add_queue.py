"""Application-owned serial queue for accepted flat MindMemOS writes."""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from homemaster.events.trace import append_jsonl_event

_STOP = object()


class MemoryAddQueueClosed(RuntimeError):
    """Raised when a structured Add arrives after queue admission is sealed."""


@dataclass(frozen=True)
class MemoryAddReceipt:
    job_id: str
    status: Literal["accepted"] = "accepted"


@dataclass(frozen=True)
class MemoryWorkReceipt:
    job_id: str
    status: Literal["accepted"] = "accepted"


@dataclass(frozen=True)
class _MemoryAddJob:
    job_id: str
    content: str
    memory_type: Literal["fact", "procedure"]
    provenance_seq: int
    evidence_kind: Literal["user_statement", "environment_observation"]
    context: Any
    run_id: str | None


@dataclass(frozen=True)
class _MemoryWorkJob:
    job_id: str
    job_type: str
    session_id: str
    work: Callable[[], Awaitable[None]]


class MemoryAddQueue:
    """Run accepted flat Adds and ordered memory work on one FIFO worker."""

    def __init__(self, mindmemos: Any, *, audit_path: Path) -> None:
        self._mindmemos = mindmemos
        self._audit_path = audit_path
        self._queue: asyncio.Queue[_MemoryAddJob | _MemoryWorkJob | object] = (
            asyncio.Queue()
        )
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
        content: str,
        memory_type: Literal["fact", "procedure"],
        provenance_seq: int,
        evidence_kind: Literal["user_statement", "environment_observation"],
        context: Any,
        run_id: str | None = None,
    ) -> MemoryAddReceipt:
        if self._sealed or self._closed:
            raise MemoryAddQueueClosed("memory Add queue is closing")
        if self._worker is None:
            raise RuntimeError("memory Add queue is not started")
        job = _MemoryAddJob(
            job_id=str(uuid4()),
            content=copy.deepcopy(content),
            memory_type=memory_type,
            provenance_seq=provenance_seq,
            evidence_kind=evidence_kind,
            context=copy.deepcopy(context),
            run_id=run_id,
        )
        self._queue.put_nowait(job)
        self._log(job, status="queued")
        return MemoryAddReceipt(job_id=job.job_id)

    def enqueue_work(
        self,
        *,
        job_type: str,
        session_id: str,
        work: Callable[[], Awaitable[None]],
    ) -> MemoryWorkReceipt:
        if self._sealed or self._closed:
            raise MemoryAddQueueClosed("memory queue is closing")
        if self._worker is None:
            raise RuntimeError("memory queue is not started")
        if not job_type:
            raise ValueError("job_type must not be empty")
        if not session_id:
            raise ValueError("session_id must not be empty")
        if not callable(work):
            raise TypeError("work must be callable")
        job = _MemoryWorkJob(
            job_id=str(uuid4()),
            job_type=job_type,
            session_id=session_id,
            work=work,
        )
        self._queue.put_nowait(job)
        self._log_work(job, status="queued")
        return MemoryWorkReceipt(job_id=job.job_id)

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
                if isinstance(item, _MemoryAddJob):
                    self._log(item, status="processing")
                    result = await self._mindmemos.add_flat(
                        item.content,
                        item.memory_type,
                        provenance_seq=item.provenance_seq,
                        evidence_kind=item.evidence_kind,
                        context=item.context,
                    )
                    memory_id = (
                        result.get("memory_id") if isinstance(result, dict) else None
                    )
                    if not isinstance(memory_id, str) or not memory_id:
                        raise RuntimeError("MindMemOS Add returned no verified raw memory")
                    self._log(
                        item,
                        status="completed",
                        duration_ms=(time.monotonic() - started) * 1000,
                        memory_id=memory_id,
                    )
                else:
                    assert isinstance(item, _MemoryWorkJob)
                    self._log_work(item, status="processing")
                    await item.work()
                    self._log_work(
                        item,
                        status="completed",
                        duration_ms=(time.monotonic() - started) * 1000,
                    )
            except Exception as exc:
                if isinstance(item, _MemoryAddJob):
                    self._log(
                        item,
                        status="failed",
                        duration_ms=(time.monotonic() - started) * 1000,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                else:
                    assert isinstance(item, _MemoryWorkJob)
                    self._log_work(
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
                "job_type": "flat_add",
                "memory_type": job.memory_type,
                "evidence_kind": job.evidence_kind,
                "provenance_seq": job.provenance_seq,
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

    def _log_work(
        self,
        job: _MemoryWorkJob,
        *,
        status: str,
        duration_ms: float | None = None,
        error: str | None = None,
    ) -> None:
        append_jsonl_event(
            self._audit_path,
            event="memory_work_job",
            payload={
                "job_id": job.job_id,
                "job_type": job.job_type,
                "status": status,
                "session_id": job.session_id,
                "duration_ms": round(duration_ms, 3) if duration_ms is not None else None,
                "error": error,
            },
        )


__all__ = [
    "MemoryAddQueue",
    "MemoryAddQueueClosed",
    "MemoryAddReceipt",
    "MemoryWorkReceipt",
]
