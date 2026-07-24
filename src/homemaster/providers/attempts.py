"""Immutable provider-attempt and commit records."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class OutboundImageBinding:
    message_index: int
    block_index: int
    content_sha256: str


@dataclass(frozen=True)
class ProviderAttemptRecord:
    model_attempt_id: str
    request_sha256: str
    outbound_images: tuple[OutboundImageBinding, ...]
    stripped_images: bool
    response_completed: bool
    error_type: str | None
    cause_code: str | None


@dataclass(frozen=True)
class AttemptCommitState:
    assistant_committed: bool
    tool_dispatch_committed: bool
    external_action_committed: bool


class ProviderAttemptSink(Protocol):
    async def arecord_attempt(self, record: ProviderAttemptRecord) -> None: ...


class ListProviderAttemptSink:
    def __init__(self) -> None:
        self._records: list[ProviderAttemptRecord] = []
        self._lock = asyncio.Lock()

    @property
    def records(self) -> tuple[ProviderAttemptRecord, ...]:
        return tuple(self._records)

    @property
    def last_record(self) -> ProviderAttemptRecord | None:
        return self._records[-1] if self._records else None

    def record_attempt(self, record: ProviderAttemptRecord) -> None:
        """Compatibility hook for synchronous fake transports during CL-16a."""
        self._records.append(record)

    async def arecord_attempt(self, record: ProviderAttemptRecord) -> None:
        async with self._lock:
            self.record_attempt(record)


class JsonlProviderAttemptSink:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._last_record: ProviderAttemptRecord | None = None
        self._lock = asyncio.Lock()

    @property
    def last_record(self) -> ProviderAttemptRecord | None:
        return self._last_record

    def record_attempt(self, record: ProviderAttemptRecord) -> None:
        """Compatibility hook for synchronous fake transports during CL-16a."""
        self._write_record(record)

    async def arecord_attempt(self, record: ProviderAttemptRecord) -> None:
        async with self._lock:
            await asyncio.to_thread(self._write_record, record)

    def _write_record(self, record: ProviderAttemptRecord) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as writer:
            fcntl.flock(writer.fileno(), fcntl.LOCK_EX)
            writer.write(json.dumps(asdict(record), ensure_ascii=False, sort_keys=True))
            writer.write("\n")
            writer.flush()
            os.fsync(writer.fileno())
            fcntl.flock(writer.fileno(), fcntl.LOCK_UN)
        self._last_record = record
