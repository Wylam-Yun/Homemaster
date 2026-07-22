"""Strict public event boundary for remote Gateway consumers."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from homemaster.events.bus import EventBus
from homemaster.events.runtime_events import RuntimeEvent

_PRIVATE_KEY_PARTS = (
    "api_key",
    "authorization",
    "credential",
    "password",
    "private",
    "prompt",
    "provider",
    "raw",
    "reasoning",
    "secret",
    "token",
)
_PATH_KEYS = frozenset({"file", "filename", "media", "path", "paths", "uri", "url"})
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
_FREE_TEXT_CREDENTIAL_RE = re.compile(
    r"(?i)\b(api[_-]?key|auth(?:orization)?|credential|password|secret|token)"
    r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_HOST_PATH_RE = re.compile(
    r"(?<![\w:])/(?:data\d*|home|hpc2hdd|mnt|tmp|var|workspace)(?:/[^\s,;]*)*"
)


@dataclass(frozen=True)
class PublicGatewayEvent:
    event_type: str
    session_id: str
    run_id: str
    turn_index: int | None
    correlation_id: str
    content: str
    metadata: Mapping[str, object]
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
            "gateway_generation": self.gateway_generation,
        }


class PublicEventProjection:
    """Allowlist public fields and recursively remove private material."""

    def __init__(self, *, sensitive_values: tuple[str, ...] = ()) -> None:
        self._sensitive = tuple(value for value in sensitive_values if value)

    def project(self, event: RuntimeEvent) -> PublicGatewayEvent | None:
        if event.type not in _ALLOWED_TYPES:
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        content = self._content(event.type, payload)
        metadata = {
            key: self._sanitize(value)
            for key, value in payload.items()
            if key in _SAFE_METADATA_KEYS
        }
        if event.name and event.type.startswith("tool.call_"):
            metadata["tool_name"] = self._sanitize(event.name)
        return PublicGatewayEvent(
            event_type=event.type,
            session_id=event.session_id,
            run_id=event.run_id,
            turn_index=event.turn_index,
            correlation_id=event.tool_call_id or event.event_id,
            content=self.sanitize_content(content),
            metadata=metadata,
            gateway_generation=event.gateway_generation,
        )

    def sanitize_content(self, content: object) -> str:
        """Sanitize untrusted free text before it crosses the Gateway boundary."""

        return str(self._sanitize(content))

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

    def _sanitize(self, value: object, *, key: str = "") -> object:
        lowered = key.casefold()
        if lowered in _PATH_KEYS or any(part in lowered for part in _PRIVATE_KEY_PARTS):
            return "[REDACTED]"
        if isinstance(value, Mapping):
            return {
                str(child_key): self._sanitize(child, key=str(child_key))
                for child_key, child in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self._sanitize(item) for item in value]
        if isinstance(value, str):
            sanitized = value
            for secret in self._sensitive:
                sanitized = sanitized.replace(secret, "[REDACTED]")
            if sanitized.casefold().startswith(("bearer ", "basic ")):
                return "[REDACTED]"
            sanitized = _FREE_TEXT_CREDENTIAL_RE.sub(
                lambda match: f"{match.group(1)}=[REDACTED]",
                sanitized,
            )
            sanitized = _HOST_PATH_RE.sub("[REDACTED_PATH]", sanitized)
            return _strip_url_query(sanitized)
        return value


def _strip_url_query(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[REDACTED_URL]"
    if parsed.scheme and parsed.netloc and (parsed.query or parsed.fragment):
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return re.sub(r"https?://[^\s?]+\?[^\s]+", "[REDACTED_URL]", value)


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


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
