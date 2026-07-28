"""Contracts for the generic run-scoped browser capability."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class BrowserElement:
    element_id: str
    tag: str
    control_type: str
    role: str
    name: str
    label: str
    text: str
    value: str | None
    frame_id: str
    visible: bool
    enabled: bool
    editable: bool
    required: bool = False
    readonly: bool = False
    checked: bool | None = None
    selected: bool | None = None
    expanded: bool | None = None
    obscured: bool = False
    options: tuple[Mapping[str, object], ...] = ()
    fingerprint: tuple[str, ...] = ()
    handle: Any = field(default=None, repr=False, compare=False)

    def to_public_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "element_id": self.element_id,
            "tag": self.tag,
            "control_type": self.control_type,
            "role": self.role,
            "name": self.name,
            "label": self.label,
            "text": self.text,
            "value": self.value,
            "frame_id": self.frame_id,
            "visible": self.visible,
            "enabled": self.enabled,
            "editable": self.editable,
            "required": self.required,
            "readonly": self.readonly,
            "checked": self.checked,
            "selected": self.selected,
            "expanded": self.expanded,
            "obscured": self.obscured,
        }
        if self.options:
            payload["options"] = [dict(option) for option in self.options]
        return payload


@dataclass(frozen=True)
class BrowserSnapshot:
    snapshot_id: str
    generation: int
    url: str
    title: str
    text: str
    elements: tuple[BrowserElement, ...]
    total_matches: int
    truncated: bool
    frames: tuple[Mapping[str, object], ...] = ()

    def to_public_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "generation": self.generation,
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "elements": [element.to_public_dict() for element in self.elements],
            "total_matches": self.total_matches,
            "truncated": self.truncated,
            "frames": [dict(frame) for frame in self.frames],
        }


class BrowserSessionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
        backend_attempted: bool = False,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})
        self.backend_attempted = backend_attempted
        self.outcome_unknown = outcome_unknown


@runtime_checkable
class BrowserSession(Protocol):
    async def navigate(self, url: str) -> Mapping[str, object]: ...

    async def inspect(self, filters: Mapping[str, object]) -> BrowserSnapshot: ...

    async def fill(self, snapshot_id: str, element_id: str, value: str) -> Mapping[str, object]: ...

    async def select(
        self, snapshot_id: str, element_id: str, option: str
    ) -> Mapping[str, object]: ...

    async def check(self, snapshot_id: str, element_id: str) -> Mapping[str, object]: ...

    async def uncheck(self, snapshot_id: str, element_id: str) -> Mapping[str, object]: ...

    async def click(self, snapshot_id: str, element_id: str) -> Mapping[str, object]: ...

    async def wait(self, condition: Mapping[str, object]) -> Mapping[str, object]: ...

    async def screenshot(self) -> bytes: ...

    async def aclose(self) -> None: ...


def audit_browser_session_implementation(implementation: object) -> None:
    missing = [
        name
        for name in (
            "navigate",
            "inspect",
            "fill",
            "select",
            "check",
            "uncheck",
            "click",
            "wait",
            "screenshot",
            "aclose",
        )
        if not callable(getattr(implementation, name, None))
    ]
    if missing:
        raise TypeError(f"BrowserSession implementation missing methods: {missing}")


__all__ = [
    "BrowserElement",
    "BrowserSession",
    "BrowserSessionError",
    "BrowserSnapshot",
    "audit_browser_session_implementation",
]
