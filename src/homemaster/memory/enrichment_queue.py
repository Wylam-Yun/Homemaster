"""Application-owned background enrichment for already stored memories."""

from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from homemaster.events.trace import append_jsonl_event

_STOP = object()


class MemoryEnrichmentQueueClosed(RuntimeError):
    """Raised when enrichment arrives after queue admission is sealed."""


@dataclass(frozen=True)
class _MemoryEnrichmentJob:
    job_id: str
    memory_id: str
    content: str
    context: Any
    run_id: str | None


class MemoryEnrichmentQueue:
    """Enrich stored memories without delaying their model-visible receipt."""

    def __init__(
        self,
        mindmemos: Any,
        *,
        audit_path: Path,
        concurrency: int = 2,
    ) -> None:
        if concurrency < 1:
            raise ValueError("memory enrichment concurrency must be positive")
        self._mindmemos = mindmemos
        self._audit_path = audit_path
        self._concurrency = concurrency
        self._queue: asyncio.Queue[_MemoryEnrichmentJob | object] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._sealed = False
        self._closed = False
        self._close_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._closed or self._sealed:
            raise MemoryEnrichmentQueueClosed("memory enrichment queue is closed")
        if self._workers:
            return
        self._workers = [
            asyncio.create_task(
                self._run(), name=f"homemaster:memory-enrichment-worker:{index}"
            )
            for index in range(self._concurrency)
        ]

    def enqueue(
        self,
        *,
        memory_id: str,
        content: str,
        context: Any,
        run_id: str | None = None,
    ) -> None:
        if self._sealed or self._closed:
            raise MemoryEnrichmentQueueClosed("memory enrichment queue is closing")
        if not self._workers:
            raise RuntimeError("memory enrichment queue is not started")
        if not memory_id or not content:
            raise ValueError("memory enrichment requires memory_id and content")
        job = _MemoryEnrichmentJob(
            job_id=str(uuid4()),
            memory_id=memory_id,
            content=content,
            context=copy.deepcopy(context),
            run_id=run_id,
        )
        self._queue.put_nowait(job)
        self._log(job, status="queued")

    async def wait_idle(self) -> None:
        await self._queue.join()

    async def aclose(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._sealed = True
            if self._workers:
                await self._queue.join()
                for _worker in self._workers:
                    self._queue.put_nowait(_STOP)
                await asyncio.gather(*self._workers)
                self._workers.clear()
            self._closed = True

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            started = time.monotonic()
            try:
                if item is _STOP:
                    return
                assert isinstance(item, _MemoryEnrichmentJob)
                self._log(item, status="processing")
                result = await self._mindmemos.enrich_flat_memory(
                    memory_id=item.memory_id,
                    content=item.content,
                    context=item.context,
                )
                self._log(
                    item,
                    status="completed",
                    duration_ms=(time.monotonic() - started) * 1000,
                    entity_ids=list(result.get("entity_ids", ())),
                )
            except Exception as exc:
                if isinstance(item, _MemoryEnrichmentJob):
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
        job: _MemoryEnrichmentJob,
        *,
        status: str,
        duration_ms: float | None = None,
        entity_ids: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        context = job.context
        append_jsonl_event(
            self._audit_path,
            event="memory_enrichment_job",
            payload={
                "job_id": job.job_id,
                "memory_id": job.memory_id,
                "status": status,
                "tenant_id": getattr(context, "account_id", None),
                "project_id": getattr(context, "project_id", None),
                "session_id": getattr(context, "session_id", None),
                "run_id": job.run_id,
                "request_id": getattr(context, "request_id", None),
                "duration_ms": (
                    round(duration_ms, 3) if duration_ms is not None else None
                ),
                "entity_ids": entity_ids,
                "error": error,
            },
        )


__all__ = [
    "MemoryEnrichmentQueue",
    "MemoryEnrichmentQueueClosed",
]
