"""Latest-only exact element reference storage."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from homemaster.browser.contracts import BrowserElement, BrowserSnapshot


class TargetResolutionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class SnapshotStore:
    def __init__(self, *, session_id: str) -> None:
        self._session_id = session_id
        self._current: BrowserSnapshot | None = None

    @property
    def current(self) -> BrowserSnapshot | None:
        return self._current

    def replace(
        self,
        *,
        generation: int,
        url: str,
        title: str,
        elements: Sequence[BrowserElement],
        text: str = "",
        total_matches: int | None = None,
        truncated: bool = False,
        frames: Sequence[dict[str, object]] = (),
    ) -> BrowserSnapshot:
        snapshot = BrowserSnapshot(
            snapshot_id=f"s-{self._session_id}-{uuid.uuid4().hex[:12]}",
            generation=generation,
            url=url,
            title=title,
            text=text,
            elements=tuple(elements),
            total_matches=len(elements) if total_matches is None else total_matches,
            truncated=truncated,
            frames=tuple(frames),
        )
        self._current = snapshot
        return snapshot

    def resolve(self, snapshot_id: str, element_id: str, *, generation: int) -> BrowserElement:
        snapshot = self._current
        if (
            snapshot is None
            or snapshot.snapshot_id != snapshot_id
            or snapshot.generation != generation
        ):
            raise TargetResolutionError("stale_ref", "snapshot is no longer current")
        matches = [element for element in snapshot.elements if element.element_id == element_id]
        if not matches:
            raise TargetResolutionError("unknown_element", "element is not in this snapshot")
        if len(matches) != 1:
            raise TargetResolutionError("ambiguous_target", "element reference is not unique")
        return matches[0]

    def invalidate(self) -> None:
        self._current = None


__all__ = ["SnapshotStore", "TargetResolutionError"]
