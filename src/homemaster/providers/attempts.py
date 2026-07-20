"""Immutable provider-attempt and commit records."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class OutboundImageBinding:
    message_index: int
    block_index: int
    frame_binding_id: str | None
    content_sha256: str
    observation_id: str | None = None
    observation_content_sha256: str | None = None
    observation_pixel_sha256: str | None = None
    observation_backend_id: str | None = None
    observation_run_id: str | None = None
    observation_generation: int | None = None
    observation_state_sequence: int | None = None
    observation_capture_event_sequence: int | None = None


@dataclass(frozen=True)
class OutboundObservationBinding:
    """Metadata proving a model-visible observation survived serialization."""

    message_index: int
    block_index: int
    content_sha256: str
    media_type: str
    observation_id: str
    observation_content_sha256: str
    observation_pixel_sha256: str | None
    observation_backend_id: str
    observation_run_id: str
    observation_generation: int
    observation_state_sequence: int
    observation_capture_event_sequence: int


@dataclass(frozen=True)
class ProviderAttemptRecord:
    model_attempt_id: str
    request_sha256: str
    outbound_images: tuple[OutboundImageBinding, ...]
    stripped_images: bool
    response_completed: bool
    error_type: str | None
    cause_code: str | None
    outbound_observations: tuple[OutboundObservationBinding, ...] = ()


@dataclass(frozen=True)
class AttemptCommitState:
    assistant_committed: bool
    tool_dispatch_committed: bool
    external_action_committed: bool


class ProviderAttemptSink(Protocol):
    def record_attempt(self, record: ProviderAttemptRecord) -> None: ...


class ListProviderAttemptSink:
    def __init__(self) -> None:
        self._records: list[ProviderAttemptRecord] = []

    @property
    def records(self) -> tuple[ProviderAttemptRecord, ...]:
        return tuple(self._records)

    @property
    def last_record(self) -> ProviderAttemptRecord | None:
        return self._records[-1] if self._records else None

    def record_attempt(self, record: ProviderAttemptRecord) -> None:
        self._records.append(record)


class JsonlProviderAttemptSink:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._last_record: ProviderAttemptRecord | None = None

    @property
    def last_record(self) -> ProviderAttemptRecord | None:
        return self._last_record

    def record_attempt(self, record: ProviderAttemptRecord) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as writer:
            writer.write(json.dumps(asdict(record), ensure_ascii=False, sort_keys=True))
            writer.write("\n")
        self._last_record = record
