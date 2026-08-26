"""Contracts for the V3.1 run-scoped browser capability."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


def _optional_target_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


@dataclass(frozen=True)
class BrowserElement:
    """A bounded, redacted description of one actionable DOM target."""

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
    target_ref: str | None = None
    testid: str = ""
    stable_id: str = ""
    compound: Mapping[str, object] = field(default_factory=dict)

    @property
    def accessible_name(self) -> str:
        return self.name

    def to_public_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "element_id": self.element_id,
            "target_ref": self.target_ref or self.element_id,
            "tag": self.tag,
            "control_type": self.control_type,
            "role": self.role,
            "accessible_name": self.name,
            "name": self.name,
            "label": self.label,
            "visible_text": self.text,
            "text": self.text,
            "value": None if self.control_type == "password" else self.value,
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
            "testid": self.testid,
            "stable_id": self.stable_id,
        }
        if self.options:
            payload["options"] = [dict(option) for option in self.options]
        if self.compound:
            payload["compound"] = dict(self.compound)
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
    view: str = "hybrid"
    created_at_ms: int = 0
    diff: Mapping[str, object] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "generation": self.generation,
            "page_generation": self.generation,
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "elements": [element.to_public_dict() for element in self.elements],
            "total_matches": self.total_matches,
            "truncated": self.truncated,
            "frames": [dict(frame) for frame in self.frames],
            "view": self.view,
            "created_at_ms": self.created_at_ms,
            "diff": dict(self.diff),
        }


@dataclass(frozen=True)
class Target:
    """Normalized target input accepted by every browser action."""

    role: str | None = None
    name: str | None = None
    label: str | None = None
    text: str | None = None
    testid: str | None = None
    match: str = "exact"
    nth: int | None = None
    frame_ref: str | None = None
    tab_ref: str | None = None
    target_ref: str | None = None

    @classmethod
    def from_value(cls, value: Mapping[str, object] | str) -> Target:
        if isinstance(value, str):
            return cls(text=value)
        if not isinstance(value, Mapping):
            raise ValueError("target must be a semantic object or target_ref string")
        allowed = {
            "role",
            "name",
            "label",
            "text",
            "testid",
            "match",
            "nth",
            "frame_ref",
            "tab_ref",
            "target_ref",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"unknown target fields: {unknown}")
        return cls(
            role=_optional_target_text(value.get("role")),
            name=_optional_target_text(value.get("name")),
            label=_optional_target_text(value.get("label")),
            text=_optional_target_text(value.get("text")),
            testid=_optional_target_text(value.get("testid")),
            match=str(value.get("match", "exact")),
            nth=int(value["nth"]) if value.get("nth") is not None else None,
            frame_ref=_optional_target_text(value.get("frame_ref")),
            tab_ref=_optional_target_text(value.get("tab_ref")),
            target_ref=_optional_target_text(value.get("target_ref")),
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            key: value
            for key, value in {
                "role": self.role,
                "name": self.name,
                "label": self.label,
                "text": self.text,
                "testid": self.testid,
                "match": self.match,
                "nth": self.nth,
                "frame_ref": self.frame_ref,
                "tab_ref": self.tab_ref,
                "target_ref": self.target_ref,
            }.items()
            if value is not None
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
    async def navigate(self, url: str, **kwargs: object) -> Mapping[str, object]: ...

    async def history(self, action: str, **kwargs: object) -> Mapping[str, object]: ...

    async def inspect(self, filters: Mapping[str, object]) -> BrowserSnapshot: ...

    async def find(self, query: Mapping[str, object]) -> Mapping[str, object]: ...

    async def read(self, query: Mapping[str, object]) -> Mapping[str, object]: ...

    async def extract(self, query: Mapping[str, object]) -> Mapping[str, object]: ...

    async def fill(
        self, target: Mapping[str, object] | str, value: str
    ) -> Mapping[str, object]: ...

    async def type(
        self, target: Mapping[str, object] | str, text: str, **kwargs: object
    ) -> Mapping[str, object]: ...

    async def select(
        self, target: Mapping[str, object] | str, option: object, **kwargs: object
    ) -> Mapping[str, object]: ...

    async def check(self, target: Mapping[str, object] | str) -> Mapping[str, object]: ...

    async def uncheck(self, target: Mapping[str, object] | str) -> Mapping[str, object]: ...

    async def click(
        self, target: Mapping[str, object] | str, **kwargs: object
    ) -> Mapping[str, object]: ...

    async def hover(
        self, target: Mapping[str, object] | str, **kwargs: object
    ) -> Mapping[str, object]: ...

    async def focus(self, target: Mapping[str, object] | str) -> Mapping[str, object]: ...

    async def press(
        self, key: str, target: Mapping[str, object] | str | None = None, **kwargs: object
    ) -> Mapping[str, object]: ...

    async def scroll(self, query: Mapping[str, object]) -> Mapping[str, object]: ...

    async def upload(
        self, target: Mapping[str, object] | str, artifact_refs: list[str]
    ) -> Mapping[str, object]: ...

    async def drag(
        self,
        source: Mapping[str, object] | str,
        destination: Mapping[str, object] | str,
        **kwargs: object,
    ) -> Mapping[str, object]: ...

    async def backfill(
        self, target: Mapping[str, object] | str, **kwargs: object
    ) -> Mapping[str, object]: ...

    async def tabs(self, query: Mapping[str, object]) -> Mapping[str, object]: ...

    async def dialog(self, query: Mapping[str, object]) -> Mapping[str, object]: ...

    async def network(self, query: Mapping[str, object]) -> Mapping[str, object]: ...

    async def download(self, query: Mapping[str, object]) -> Mapping[str, object]: ...

    async def wait(self, condition: Mapping[str, object]) -> Mapping[str, object]: ...

    async def screenshot(self, **kwargs: object) -> Mapping[str, object] | bytes: ...

    async def eval(self, query: Mapping[str, object]) -> Mapping[str, object]: ...

    async def analyze(self, query: Mapping[str, object]) -> Mapping[str, object]: ...

    async def aclose(self) -> None: ...


def audit_browser_session_implementation(implementation: object) -> None:
    missing = [
        name
        for name in (
            "navigate",
            "history",
            "inspect",
            "find",
            "read",
            "extract",
            "fill",
            "type",
            "select",
            "check",
            "uncheck",
            "click",
            "hover",
            "focus",
            "press",
            "scroll",
            "upload",
            "drag",
            "backfill",
            "tabs",
            "dialog",
            "network",
            "download",
            "wait",
            "screenshot",
            "eval",
            "analyze",
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
    "Target",
    "audit_browser_session_implementation",
]
