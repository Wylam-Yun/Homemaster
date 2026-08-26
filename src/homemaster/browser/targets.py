"""Deterministic target references and post-render recovery."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from homemaster.browser.contracts import BrowserElement, BrowserSnapshot, Target

_HAN_CHARACTER = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_HAN_DISPLAY_SPACING = re.compile(rf"(?<=[{_HAN_CHARACTER}])\s+(?=[{_HAN_CHARACTER}])")
_CELL_ROLES = frozenset({"cell", "gridcell"})


class TargetResolutionError(ValueError):
    def __init__(
        self, code: str, message: str, *, details: dict[str, object] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class Resolution:
    element: BrowserElement
    level: str
    candidates: tuple[dict[str, object], ...] = ()


class SnapshotStore:
    """Retain bounded snapshots so target refs survive ordinary re-rendering."""

    def __init__(self, *, session_id: str, max_snapshots: int = 24) -> None:
        self._session_id = session_id
        self._max_snapshots = max(2, max_snapshots)
        self._snapshots: dict[str, BrowserSnapshot] = {}
        self._order: list[str] = []
        self._current_id: str | None = None

    @property
    def current(self) -> BrowserSnapshot | None:
        return self._snapshots.get(self._current_id or "")

    @property
    def snapshots(self) -> tuple[BrowserSnapshot, ...]:
        return tuple(self._snapshots[key] for key in self._order if key in self._snapshots)

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
        view: str = "hybrid",
        created_at_ms: int = 0,
        diff: dict[str, object] | None = None,
    ) -> BrowserSnapshot:
        snapshot_id = f"s-{self._session_id}-{uuid.uuid4().hex[:12]}"
        normalized = tuple(
            element if element.target_ref else _with_target_ref(element, snapshot_id)
            for element in elements
        )
        snapshot = BrowserSnapshot(
            snapshot_id=snapshot_id,
            generation=generation,
            url=url,
            title=title,
            text=text,
            elements=normalized,
            total_matches=len(normalized) if total_matches is None else total_matches,
            truncated=truncated,
            frames=tuple(frames),
            view=view,
            created_at_ms=created_at_ms,
            diff=dict(diff or {}),
        )
        self._snapshots[snapshot_id] = snapshot
        self._order.append(snapshot_id)
        self._current_id = snapshot_id
        while len(self._order) > self._max_snapshots:
            old = self._order.pop(0)
            self._snapshots.pop(old, None)
        return snapshot

    def get(self, snapshot_id: str) -> BrowserSnapshot:
        snapshot = self._snapshots.get(snapshot_id)
        if snapshot is None:
            raise TargetResolutionError("stale_ref", "snapshot is no longer retained")
        return snapshot

    def resolve(
        self, snapshot_id: str, element_id: str, *, generation: int | None = None
    ) -> BrowserElement:
        snapshot = self.get(snapshot_id)
        if generation is not None and snapshot.generation != generation:
            raise TargetResolutionError(
                "stale_ref",
                "snapshot belongs to an older page generation",
                details={
                    "snapshot_generation": snapshot.generation,
                    "current_generation": generation,
                },
            )
        matches = [element for element in snapshot.elements if element.element_id == element_id]
        if len(matches) != 1:
            raise TargetResolutionError(
                "target_not_found" if not matches else "target_ambiguous",
                "target reference is not unique",
            )
        return matches[0]

    def resolve_target_ref(self, target_ref: str) -> tuple[BrowserSnapshot, BrowserElement]:
        for snapshot in reversed(self.snapshots):
            for element in snapshot.elements:
                if element.target_ref == target_ref or element.element_id == target_ref:
                    return snapshot, element
        raise TargetResolutionError("stale_ref", "target_ref is not retained")

    def invalidate(self) -> None:
        """Drop only the current pointer; retained refs remain recoverable."""

        self._current_id = None


def resolve_semantic(
    elements: Sequence[BrowserElement],
    target: Target,
    *,
    writable: bool,
) -> Resolution:
    if target.match not in {"exact", "contains", "regex"}:
        raise TargetResolutionError("invalid_match", "match must be exact, contains, or regex")
    if target.match == "regex" and writable:
        raise TargetResolutionError("invalid_match", "regex matching is read-only")
    selected = [element for element in elements if _matches(element, target)]
    candidates = tuple(_candidate(element) for element in selected[:10])
    if not selected:
        raise TargetResolutionError(
            "target_not_found",
            "no target matched the requested semantic target",
            details={"requested": target.to_public_dict(), "candidates": []},
        )
    if target.nth is not None:
        if target.nth < 0 or target.nth >= len(selected):
            raise TargetResolutionError(
                "target_not_found",
                "requested nth candidate is out of range",
                details={"matches_n": len(selected), "nth": target.nth, "candidates": candidates},
            )
        return Resolution(
            selected[target.nth], "exact" if target.match == "exact" else target.match, candidates
        )
    if len(selected) != 1:
        raise TargetResolutionError(
            "target_ambiguous",
            "target matched multiple elements; specify a stronger target or nth",
            details={"matches_n": len(selected), "candidates": candidates},
        )
    return Resolution(selected[0], "exact" if target.match == "exact" else target.match, candidates)


def semantic_text_matches(actual: str, query: str, match: str) -> bool:
    """Match semantic text while ignoring framework display spacing between Han characters."""

    if match == "regex":
        return re.search(query, actual, flags=re.IGNORECASE) is not None
    normalized_actual = _HAN_DISPLAY_SPACING.sub("", actual).casefold()
    normalized_query = _HAN_DISPLAY_SPACING.sub("", query).casefold()
    if match == "exact":
        return normalized_actual == normalized_query
    return normalized_query in normalized_actual


def _matches(element: BrowserElement, target: Target) -> bool:
    if target.frame_ref and element.frame_id != target.frame_ref:
        return False
    fields = {
        "role": element.role,
        "name": element.name,
        "label": element.label,
        "text": element.text,
        "testid": element.testid,
    }
    requested = {key: getattr(target, key) for key in fields if getattr(target, key) is not None}
    if not requested:
        return False
    for key, needle in requested.items():
        value = str(needle)
        actual = str(fields[key])
        if key == "role" and {actual, value} <= _CELL_ROLES:
            continue
        if key == "name" and element.role in _CELL_ROLES:
            if semantic_text_matches(element.text, value, target.match):
                continue
        if not semantic_text_matches(actual, value, target.match):
            return False
    return True


def _candidate(element: BrowserElement) -> dict[str, object]:
    return {
        "target_ref": element.target_ref or element.element_id,
        "role": element.role,
        "name": element.name,
        "label": element.label,
        "text": element.text[:120],
        "frame_ref": element.frame_id,
        "enabled": element.enabled,
        "visible": element.visible,
    }


def _with_target_ref(element: BrowserElement, snapshot_id: str) -> BrowserElement:
    return BrowserElement(
        element_id=element.element_id,
        tag=element.tag,
        control_type=element.control_type,
        role=element.role,
        name=element.name,
        label=element.label,
        text=element.text,
        value=element.value,
        frame_id=element.frame_id,
        visible=element.visible,
        enabled=element.enabled,
        editable=element.editable,
        required=element.required,
        readonly=element.readonly,
        checked=element.checked,
        selected=element.selected,
        expanded=element.expanded,
        obscured=element.obscured,
        options=element.options,
        fingerprint=element.fingerprint,
        handle=element.handle,
        target_ref=f"{snapshot_id}:{element.element_id}",
        testid=element.testid,
        stable_id=element.stable_id,
        compound=element.compound,
    )


__all__ = [
    "Resolution",
    "SnapshotStore",
    "TargetResolutionError",
    "resolve_semantic",
    "semantic_text_matches",
]
