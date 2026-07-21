"""Binding between outbound ALFWorld images and the model-authorized event view."""

from __future__ import annotations

import base64
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from homemaster.agent.messages import Message
from homemaster.providers.attempts import ProviderAttemptRecord


@dataclass(frozen=True)
class FrameLedgerRecord:
    frame_binding_id: str
    path: Path
    content_sha256: str
    pixel_sha256: str
    event_sequence: int


@dataclass(frozen=True)
class CommittedModelView:
    model_attempt_id: str
    request_sha256: str
    frame_binding_id: str
    frame_content_sha256: str
    frame_pixel_sha256: str
    event_sequence: int


ObservationReadStatus = Literal["ok", "absent", "malformed", "stale", "error"]


@dataclass(frozen=True)
class ObjectObservationRead:
    status: ObservationReadStatus
    event_sequence: int
    exact_object_id: str | None
    event_frame_sha256: str | None
    model_view_frame_sha256: str | None
    frame_matches_model_view: bool | None
    visible: bool | None
    bbox_area: float | None
    strict_visible: bool | None

    def __post_init__(self) -> None:
        if self.status not in {"ok", "absent", "malformed", "stale", "error"}:
            raise ValueError(f"unsupported observation read status: {self.status}")
        if self.event_sequence < 0:
            raise ValueError("event sequence cannot be negative")
        if self.status != "ok":
            return
        for name in ("event_frame_sha256", "model_view_frame_sha256"):
            value = getattr(self, name)
            if not _is_sha256(value):
                raise ValueError(f"ok observation requires {name}")
        if self.frame_matches_model_view is not True:
            raise ValueError("ok observation must match the committed model frame")
        if not isinstance(self.visible, bool) or not isinstance(self.strict_visible, bool):
            raise ValueError("ok observation requires boolean visibility fields")
        if self.bbox_area is not None and (
            not math.isfinite(self.bbox_area) or self.bbox_area < 0
        ):
            raise ValueError("observation bbox area must be finite and non-negative")
        expected = self.visible and self.bbox_area is not None and self.bbox_area > 0
        if self.strict_visible is not expected:
            raise ValueError("strict visibility does not match metadata and bbox area")


class VisibleObjectView:
    """Reads strict visibility only from the event committed to the current model call."""

    def __init__(
        self,
        *,
        event: Any,
        event_sequence: int,
        committed_view: CommittedModelView | None,
    ) -> None:
        self._event = event
        self._event_sequence = event_sequence
        self._committed_view = committed_view

    @property
    def event_sequence(self) -> int:
        return self._event_sequence

    def read(self, exact_object_id: str) -> ObjectObservationRead:
        model_hash = (
            self._committed_view.frame_pixel_sha256
            if self._committed_view is not None
            else None
        )
        event_hash = event_frame_pixel_sha256(getattr(self._event, "frame", None))
        if self._committed_view is None:
            return self._failed("stale", exact_object_id, event_hash, model_hash, None)
        if event_hash is None:
            return self._failed("malformed", exact_object_id, None, model_hash, None)
        frame_matches = (
            self._committed_view.event_sequence == self._event_sequence
            and event_hash == model_hash
        )
        if not frame_matches:
            return self._failed("stale", exact_object_id, event_hash, model_hash, False)

        metadata = getattr(self._event, "metadata", None)
        if not isinstance(metadata, dict) or not isinstance(metadata.get("objects"), list):
            return self._failed("malformed", exact_object_id, event_hash, model_hash, True)
        matching = [
            item
            for item in metadata["objects"]
            if isinstance(item, dict) and item.get("objectId") == exact_object_id
        ]
        if len(matching) != 1:
            status: ObservationReadStatus = "absent" if not matching else "malformed"
            return self._failed(status, exact_object_id, event_hash, model_hash, True)
        visible = matching[0].get("visible")
        if not isinstance(visible, bool):
            return self._failed("malformed", exact_object_id, event_hash, model_hash, True)

        detections = getattr(self._event, "instance_detections2D", None)
        if not isinstance(detections, dict):
            return self._failed("malformed", exact_object_id, event_hash, model_hash, True)
        bbox_area: float | None = None
        if exact_object_id in detections:
            try:
                x1, y1, x2, y2 = (float(value) for value in detections[exact_object_id])
            except (TypeError, ValueError):
                return self._failed("malformed", exact_object_id, event_hash, model_hash, True)
            if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
                return self._failed("malformed", exact_object_id, event_hash, model_hash, True)
            bbox_area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        return ObjectObservationRead(
            status="ok",
            event_sequence=self._event_sequence,
            exact_object_id=exact_object_id,
            event_frame_sha256=event_hash,
            model_view_frame_sha256=model_hash,
            frame_matches_model_view=True,
            visible=visible,
            bbox_area=bbox_area,
            strict_visible=visible and bbox_area is not None and bbox_area > 0,
        )

    def _failed(
        self,
        status: ObservationReadStatus,
        exact_object_id: str,
        event_hash: str | None,
        model_hash: str | None,
        frame_matches: bool | None,
    ) -> ObjectObservationRead:
        return ObjectObservationRead(
            status=status,
            event_sequence=self._event_sequence,
            exact_object_id=exact_object_id,
            event_frame_sha256=event_hash,
            model_view_frame_sha256=model_hash,
            frame_matches_model_view=frame_matches,
            visible=None,
            bbox_area=None,
            strict_visible=None,
        )


class FrameLedger:
    def __init__(self) -> None:
        self._by_path: dict[Path, FrameLedgerRecord] = {}
        self._by_binding: dict[str, FrameLedgerRecord] = {}

    def record_frame(self, path: str | Path, *, event_sequence: int) -> FrameLedgerRecord:
        resolved = Path(path).resolve()
        content = resolved.read_bytes()
        content_sha256 = hashlib.sha256(content).hexdigest()
        pixel_sha256 = _decoded_pixel_sha256(resolved)
        binding_id = hashlib.sha256(
            f"{event_sequence}\0{resolved.name}\0{content_sha256}".encode()
        ).hexdigest()
        record = FrameLedgerRecord(
            frame_binding_id=f"frame-{binding_id[:24]}",
            path=resolved,
            content_sha256=content_sha256,
            pixel_sha256=pixel_sha256,
            event_sequence=event_sequence,
        )
        self._by_path = {**self._by_path, resolved: record}
        self._by_binding = {**self._by_binding, record.frame_binding_id: record}
        return record

    def get_by_path(self, path: str | Path) -> FrameLedgerRecord | None:
        return self._by_path.get(Path(path).resolve())

    def get_by_binding(self, binding_id: str) -> FrameLedgerRecord | None:
        return self._by_binding.get(binding_id)

    def find_observation_frame(
        self,
        *,
        content_sha256: str,
        pixel_sha256: str | None,
        event_sequence: int,
    ) -> FrameLedgerRecord | None:
        matches = [
            record
            for record in self._by_binding.values()
            if record.content_sha256 == content_sha256
            and record.pixel_sha256 == pixel_sha256
            and record.event_sequence == event_sequence
        ]
        return matches[0] if len(matches) == 1 else None


class AlfworldModelViewObserver:
    def __init__(self, *, frame_ledger: FrameLedger) -> None:
        self._frame_ledger = frame_ledger
        self._current_view: CommittedModelView | None = None
        self._last_error: str | None = None

    @property
    def current_view(self) -> CommittedModelView | None:
        return self._current_view

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def invalidate(self, reason: str) -> None:
        self._current_view = None
        self._last_error = reason

    def bind_messages(self, messages: list[Message]) -> list[Message]:
        bound_messages: list[Message] = []
        for message in messages:
            content = []
            for block in message.content:
                path = block.metadata.get("path") if block.type == "image" else None
                record = (
                    self._frame_ledger.get_by_path(path)
                    if isinstance(path, str)
                    else None
                )
                if record is None:
                    content.append(block)
                    continue
                content.append(
                    block.model_copy(
                        update={
                            "metadata": {
                                **block.metadata,
                                "frame_binding_id": record.frame_binding_id,
                            }
                        }
                    )
                )
            bound_messages.append(message.model_copy(update={"content": content}))
        return bound_messages

    def commit_from_messages(
        self,
        messages: list[Message],
        *,
        model_attempt_id: str,
        request_sha256: str,
    ) -> CommittedModelView:
        selected: tuple[FrameLedgerRecord, str] | None = None
        for message in messages:
            for block in message.content:
                if block.type != "image" or not isinstance(block.source, dict):
                    continue
                path = block.metadata.get("path")
                if not isinstance(path, str):
                    continue
                record = self._frame_ledger.get_by_path(path)
                if record is None:
                    continue
                data = block.source.get("data")
                if not isinstance(data, str):
                    continue
                try:
                    content_sha256 = hashlib.sha256(
                        base64.b64decode(data, validate=True)
                    ).hexdigest()
                except ValueError:
                    continue
                selected = (record, content_sha256)
        if selected is None:
            raise ValueError("provider attempt did not contain a bound ALFWorld frame")
        record, content_sha256 = selected
        if content_sha256 != record.content_sha256:
            raise ValueError("outbound image bytes do not match the frame ledger")
        view = CommittedModelView(
            model_attempt_id=model_attempt_id,
            request_sha256=request_sha256,
            frame_binding_id=record.frame_binding_id,
            frame_content_sha256=record.content_sha256,
            frame_pixel_sha256=record.pixel_sha256,
            event_sequence=record.event_sequence,
        )
        self._current_view = view
        self._last_error = None
        return view

    def commit_successful_response(
        self,
        *,
        attempt: ProviderAttemptRecord,
    ) -> CommittedModelView:
        if (
            not attempt.response_completed
            or attempt.stripped_images
            or attempt.error_type is not None
            or attempt.cause_code is not None
        ):
            raise ValueError("only a complete unmodified provider attempt can authorize a view")
        if not _is_sha256(attempt.request_sha256):
            raise ValueError("provider attempt request hash is malformed")
        binding = next(
            (
                item
                for item in reversed(attempt.outbound_images)
                if item.frame_binding_id is not None
            ),
            None,
        )
        record = (
            self._frame_ledger.get_by_binding(binding.frame_binding_id)
            if binding is not None and binding.frame_binding_id is not None
            else None
        )
        if record is None:
            observation = next(
                (
                    item
                    for item in reversed(attempt.outbound_observations)
                    if item.observation_pixel_sha256 is not None
                ),
                None,
            )
            if observation is not None:
                record = self._frame_ledger.find_observation_frame(
                    content_sha256=observation.observation_content_sha256,
                    pixel_sha256=observation.observation_pixel_sha256,
                    event_sequence=observation.observation_capture_event_sequence,
                )
        if record is None:
            raise ValueError("provider attempt did not contain a known ALFWorld observation")
        if binding is not None and binding.content_sha256 != record.content_sha256:
            raise ValueError("outbound image bytes do not match the frame ledger")
        view = CommittedModelView(
            model_attempt_id=attempt.model_attempt_id,
            request_sha256=attempt.request_sha256,
            frame_binding_id=record.frame_binding_id,
            frame_content_sha256=record.content_sha256,
            frame_pixel_sha256=record.pixel_sha256,
            event_sequence=record.event_sequence,
        )
        self._current_view = view
        self._last_error = None
        return view


def outbound_request_sha256(messages: list[Message]) -> str:
    digest = hashlib.sha256()
    for message_index, message in enumerate(messages):
        digest.update(f"{message_index}:{message.role}\0".encode())
        for block_index, block in enumerate(message.content):
            digest.update(f"{block_index}:{block.type}\0".encode())
            if block.type == "text":
                digest.update(block.text.encode())
            elif isinstance(block.source, dict) and isinstance(block.source.get("data"), str):
                digest.update(block.source["data"].encode("ascii"))
            digest.update(b"\0")
    return digest.hexdigest()


def event_frame_pixel_sha256(frame: Any) -> str | None:
    if frame is None:
        return None
    tobytes = getattr(frame, "tobytes", None)
    if not callable(tobytes):
        return None
    return hashlib.sha256(tobytes()).hexdigest()


def _decoded_pixel_sha256(path: Path) -> str:
    from PIL import Image

    with Image.open(path) as image:
        return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
