"""Strict public event boundary for remote Gateway consumers."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homemaster.events.runtime_events import RuntimeEvent

if TYPE_CHECKING:
    from homemaster.events.bus import EventBus

_ALLOWED_TYPES = frozenset(
    {
        "assistant.reply",
        "context.compaction",
        "runtime.budget_exhausted",
        "runtime.cancelled",
        "runtime.turn_completed",
        "runtime.turn_failed",
        "tool.call_completed",
        "tool.call_failed",
        "tool.call_started",
        "transport.request_failed",
        "usage.update",
    }
)
_SAFE_METADATA_KEYS = frozenset(
    {"attempt", "checkpoint", "error_code", "is_error", "phase", "status", "usage"}
)
_ARTIFACT_HANDLE_RE = re.compile(r"^hm-artifact:[A-Za-z0-9_-]{32,128}$")
_ARTIFACT_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class PublicGatewayEvent:
    event_type: str
    session_id: str
    run_id: str
    turn_index: int | None
    correlation_id: str
    content: str
    metadata: Mapping[str, object]
    artifacts: tuple[Mapping[str, str], ...] = ()
    gateway_generation: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "turn_index": self.turn_index,
            "correlation_id": self.correlation_id,
            "content": self.content,
            "metadata": _thaw(self.metadata),
            "artifacts": [_thaw(item) for item in self.artifacts],
            "gateway_generation": self.gateway_generation,
        }


class PublicEventProjection:
    """Project allowlisted event fields without rewriting their values."""

    def project(self, event: RuntimeEvent) -> PublicGatewayEvent | None:
        if event.type not in _ALLOWED_TYPES:
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        content = self._content(event.type, payload)
        metadata = {
            key: _copy_value(value)
            for key, value in payload.items()
            if key in _SAFE_METADATA_KEYS
        }
        artifacts = self._artifact_refs(payload) if event.type == "tool.call_completed" else ()
        if event.name and event.type.startswith("tool.call_"):
            metadata["tool_name"] = _copy_value(event.name)
        return PublicGatewayEvent(
            event_type=event.type,
            session_id=event.session_id,
            run_id=event.run_id,
            turn_index=event.turn_index,
            correlation_id=event.tool_call_id or event.event_id,
            content=self.project_content(content),
            metadata=metadata,
            artifacts=artifacts,
            gateway_generation=event.gateway_generation,
        )

    @staticmethod
    def _artifact_refs(payload: Mapping[str, object]) -> tuple[Mapping[str, str], ...]:
        data = payload.get("data")
        if not isinstance(data, Mapping):
            return ()
        raw_refs = data.get("artifacts")
        if not isinstance(raw_refs, (list, tuple)):
            return ()
        valid: list[Mapping[str, str]] = []
        required = {
            "artifact_handle",
            "run_id",
            "filename",
            "media_type",
            "content_sha256",
        }
        for raw in raw_refs:
            if not isinstance(raw, Mapping) or set(raw) != required:
                continue
            ref = {key: str(raw[key]) for key in required}
            if (
                _ARTIFACT_HANDLE_RE.fullmatch(ref["artifact_handle"]) is None
                or _ARTIFACT_TOKEN_RE.fullmatch(ref["run_id"]) is None
                or not ref["filename"]
                or "/" in ref["filename"]
                or "\\" in ref["filename"]
                or not ref["media_type"].strip()
                or _SHA256_RE.fullmatch(ref["content_sha256"]) is None
            ):
                continue
            valid.append(ref)
        return tuple(valid)

    def project_content(self, content: object) -> str:
        """Compatibility name for exact public text projection."""

        return str(content)

    def copy_value(self, value: object) -> object:
        """Compatibility name for an exact recursive value copy."""

        return _copy_value(value)

    def _content(self, event_type: str, payload: dict[str, Any]) -> str:
        if event_type == "assistant.reply":
            return str(payload.get("reply") or "")
        if event_type == "runtime.turn_completed":
            return str(payload.get("final_reply") or "")
        if event_type == "context.compaction":
            return str(payload.get("message") or "context compaction")
        if event_type in {
            "runtime.turn_failed",
            "runtime.budget_exhausted",
            "runtime.cancelled",
            "transport.request_failed",
        }:
            return str(payload.get("error_code") or event_type)
        return ""

def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _copy_value(value: object) -> object:
    return _thaw(value)


async def public_gateway_stream(
    bus: EventBus,
    projection: PublicEventProjection,
) -> AsyncIterator[PublicGatewayEvent]:
    """Keep private RuntimeEvent values inside the events trust-boundary module."""

    async for private_event in bus.stream():
        public_event = projection.project(private_event)
        if public_event is not None:
            yield public_event


__all__ = [
    "PublicEventProjection",
    "PublicGatewayEvent",
    "public_gateway_stream",
]
