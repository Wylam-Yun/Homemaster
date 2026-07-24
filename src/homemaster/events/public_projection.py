"""Strict public event boundary for remote Gateway consumers."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from homemaster.events.runtime_events import RuntimeEvent

if TYPE_CHECKING:
    from homemaster.events.bus import EventBus

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
    r"\s*[:=]\s*(?:(?:bearer|basic)\s+[^\s,;]+|\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_FREE_TEXT_AUTH_VALUE_RE = re.compile(r"(?i)\b(bearer|basic)\s+[^\s,;]+")
_HOST_PATH_RE = re.compile(
    r"(?<![\w:])/(?:data\d*|home|hpc2hdd|mnt|tmp|var|workspace)(?:/[^\s,;]*)*"
)
_STREAM_CREDENTIAL_KEYS = (
    "api_key",
    "api-key",
    "authorization",
    "auth",
    "credential",
    "password",
    "secret",
    "token",
)
_STREAM_URL_PREFIXES = ("http://", "https://")
_STREAM_DELIMITERS = frozenset(" \t\r\n,;")
_DEFAULT_STREAM_CARRY_LIMIT = 8192
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
        artifacts = self._artifact_refs(payload) if event.type == "tool.call_completed" else ()
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

    def sanitize_content(self, content: object) -> str:
        """Sanitize untrusted free text before it crosses the Gateway boundary."""

        return str(self._sanitize(content))

    def sanitize_value(self, value: object) -> object:
        """Recursively sanitize a structured public-output value."""

        return self._sanitize(value)

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
            sanitized = _FREE_TEXT_CREDENTIAL_RE.sub(
                lambda match: f"{match.group(1)}=[REDACTED]",
                sanitized,
            )
            sanitized = _FREE_TEXT_AUTH_VALUE_RE.sub(
                lambda match: f"{match.group(1)} [REDACTED]",
                sanitized,
            )
            sanitized = _HOST_PATH_RE.sub("[REDACTED_PATH]", sanitized)
            return _strip_url_query(sanitized)
        return value


class StreamingPublicTextSanitizer:
    """Incrementally release public text without splitting sensitive constructs.

    Whitespace, comma, and semicolon terminate lexical units. The scanner retains
    only a trailing configured-secret prefix or an unfinished credential, Bearer/
    Basic value, host path, or HTTP(S) token. Retained memory is capped at
    ``carry_limit``; an over-limit suspicious token becomes ``[REDACTED]`` rather
    than releasing raw bytes.
    """

    def __init__(
        self,
        *,
        sensitive_values: tuple[str, ...] = (),
        carry_limit: int = _DEFAULT_STREAM_CARRY_LIMIT,
    ) -> None:
        if carry_limit <= 0:
            raise ValueError("carry_limit must be positive")
        self._projection = PublicEventProjection(sensitive_values=sensitive_values)
        self._sensitive = tuple(value for value in sensitive_values if value)
        self._carry_limit = carry_limit
        self._carry = ""

    def feed(self, text: str) -> str:
        if not text:
            return ""
        self._carry += text
        unstable_start = self._unstable_start(self._carry)
        if unstable_start is None:
            stable, self._carry = self._carry, ""
            return self._projection.sanitize_content(stable)

        stable = self._carry[:unstable_start]
        self._carry = self._carry[unstable_start:]
        released = self._projection.sanitize_content(stable) if stable else ""
        if len(self._carry) > self._carry_limit:
            self._carry = ""
            return released + "[REDACTED]"
        return released

    def finish(self) -> str:
        carry, self._carry = self._carry, ""
        if not carry:
            return ""
        if self._is_proper_secret_prefix(carry):
            return "[REDACTED]"
        return self._projection.sanitize_content(carry)

    def _unstable_start(self, value: str) -> int | None:
        starts: list[int] = []
        secret_start = self._secret_prefix_start(value)
        if secret_start is not None:
            starts.append(secret_start)

        credential_start = self._credential_start(value)
        if credential_start is not None:
            starts.append(credential_start)

        token_start = self._token_start(value)
        token = value[token_start:]
        if token and self._is_suspicious_token(token):
            starts.append(token_start)
        return min(starts) if starts else None

    def _secret_prefix_start(self, value: str) -> int | None:
        starts = []
        for secret in self._sensitive:
            lower = max(0, len(value) - len(secret) + 1)
            for start in range(lower, len(value)):
                suffix = value[start:]
                if len(suffix) < len(secret) and secret.startswith(suffix):
                    starts.append(start)
                    break
        return min(starts) if starts else None

    def _is_proper_secret_prefix(self, value: str) -> bool:
        return any(
            len(value) < len(secret) and secret.startswith(value) for secret in self._sensitive
        )

    @staticmethod
    def _token_start(value: str) -> int:
        for index in range(len(value) - 1, -1, -1):
            if value[index] in _STREAM_DELIMITERS:
                return index + 1
        return 0

    @staticmethod
    def _credential_start(value: str) -> int | None:
        lowered = value.casefold()
        candidates: list[int] = []
        for key in _STREAM_CREDENTIAL_KEYS:
            start = lowered.rfind(key)
            if start < 0:
                continue
            if start and lowered[start - 1] not in _STREAM_DELIMITERS:
                continue
            tail = lowered[start + len(key) :]
            if not re.fullmatch(
                r"\s*[:=]\s*(?:(?:bearer|basic)\s*)?[^\s,;]*",
                tail,
            ):
                continue
            candidates.append(start)
        auth = re.search(r"(?i)(?:^|[\s,;])(bearer|basic)\s+[^\s,;]*$", value)
        if auth is not None:
            candidates.append(auth.start(1))
        return min(candidates) if candidates else None

    @staticmethod
    def _is_suspicious_token(token: str) -> bool:
        lowered = token.casefold()
        if lowered.startswith("/"):
            return True
        if any(prefix.startswith(lowered) for prefix in _STREAM_URL_PREFIXES):
            return True
        if "://" in lowered:
            return True
        if any(key.startswith(lowered) for key in _STREAM_CREDENTIAL_KEYS):
            return True
        return any(prefix.startswith(lowered) for prefix in ("bearer", "basic"))


def _strip_url_query(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[REDACTED_URL]"
    if (
        parsed.scheme
        and parsed.netloc
        and (parsed.query or parsed.fragment)
        and not any(character.isspace() for character in value)
    ):
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
    "StreamingPublicTextSanitizer",
    "public_gateway_stream",
]
