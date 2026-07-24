"""Read physical object visibility from the current ALFWorld event."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

ObservationReadStatus = Literal["ok", "absent", "malformed"]


@dataclass(frozen=True)
class ObjectObservationRead:
    status: ObservationReadStatus
    event_sequence: int
    exact_object_id: str | None
    visible: bool | None
    bbox_area: float | None
    strict_visible: bool | None

    def __post_init__(self) -> None:
        if self.status not in {"ok", "absent", "malformed"}:
            raise ValueError(f"unsupported object observation status: {self.status}")
        if self.event_sequence < 0:
            raise ValueError("event sequence cannot be negative")
        if self.status != "ok":
            return
        if not isinstance(self.visible, bool) or not isinstance(self.strict_visible, bool):
            raise ValueError("ok observation requires boolean visibility fields")
        if self.bbox_area is not None and (
            not math.isfinite(self.bbox_area) or self.bbox_area < 0
        ):
            raise ValueError("observation bbox area must be finite and non-negative")
        expected = self.visible and self.bbox_area is not None and self.bbox_area > 0
        if self.strict_visible is not expected:
            raise ValueError("strict visibility does not match metadata and bbox area")


class CurrentObjectView:
    """Physical object state for the event that an ALFWorld action will use.

    This intentionally has no dependency on a provider request or screenshot. Images
    are model context only; action safety comes from the current simulator event.
    """

    def __init__(self, *, event: Any, event_sequence: int) -> None:
        self._event = event
        self._event_sequence = event_sequence

    @property
    def event_sequence(self) -> int:
        return self._event_sequence

    def read(self, exact_object_id: str) -> ObjectObservationRead:
        metadata = getattr(self._event, "metadata", None)
        if not isinstance(metadata, dict) or not isinstance(metadata.get("objects"), list):
            return self._failed("malformed", exact_object_id)
        matching = [
            item
            for item in metadata["objects"]
            if isinstance(item, dict) and item.get("objectId") == exact_object_id
        ]
        if len(matching) != 1:
            return self._failed("absent" if not matching else "malformed", exact_object_id)
        visible = matching[0].get("visible")
        if not isinstance(visible, bool):
            return self._failed("malformed", exact_object_id)

        detections = getattr(self._event, "instance_detections2D", None)
        if not isinstance(detections, dict):
            return self._failed("malformed", exact_object_id)
        bbox_area: float | None = None
        if exact_object_id in detections:
            try:
                x1, y1, x2, y2 = (float(value) for value in detections[exact_object_id])
            except (TypeError, ValueError):
                return self._failed("malformed", exact_object_id)
            if not all(math.isfinite(value) for value in (x1, y1, x2, y2)):
                return self._failed("malformed", exact_object_id)
            bbox_area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        return ObjectObservationRead(
            status="ok",
            event_sequence=self._event_sequence,
            exact_object_id=exact_object_id,
            visible=visible,
            bbox_area=bbox_area,
            strict_visible=visible and bbox_area is not None and bbox_area > 0,
        )

    def _failed(
        self,
        status: Literal["absent", "malformed"],
        exact_object_id: str,
    ) -> ObjectObservationRead:
        return ObjectObservationRead(
            status=status,
            event_sequence=self._event_sequence,
            exact_object_id=exact_object_id,
            visible=None,
            bbox_area=None,
            strict_visible=None,
        )
