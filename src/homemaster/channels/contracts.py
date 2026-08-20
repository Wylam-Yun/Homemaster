"""Typed channel DTOs adapted from OpenHarness channel bus events.

The DTO shape is seeded by nanobot/OpenHarness channels at nanobot commit
473ae5ef18394ab839a3364eee66836ef9776902. HomeMaster replaces free-form
identity and session overrides with an authenticated, immutable trust boundary.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from homemaster.gateway.auth import AuthenticatedPrincipal

_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}$")
_ARTIFACT_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


class ChannelEventKind(StrEnum):
    PROGRESS = "progress"
    MEDIA = "media"
    FINAL = "final"
    ERROR = "error"
    CANCEL = "cancel"

    @property
    def critical(self) -> bool:
        return self is not ChannelEventKind.PROGRESS


class DeliveryStatus(StrEnum):
    CONFIRMED_SUCCESS = "confirmed_success"
    CONFIRMED_FAILURE = "confirmed_failure"
    PARTIAL_SUCCESS = "partial_success"
    OUTCOME_UNKNOWN = "outcome_unknown"


@dataclass(frozen=True)
class DeliveryReceipt:
    status: DeliveryStatus
    operation: str
    platform_ids: tuple[str, ...] = ()
    api_code: int | str | None = None
    api_message: str = ""
    sent_count: int = 0
    failed_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.status, DeliveryStatus):
            raise TypeError("delivery status must be DeliveryStatus")
        _require_token(self.operation, "delivery operation")
        ids = tuple(self.platform_ids)
        if any(not isinstance(item, str) or not item.strip() for item in ids):
            raise ValueError("platform ids must be non-empty strings")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (
                self.sent_count,
                self.failed_count,
            )
        ):
            raise ValueError("delivery counts must be non-negative integers")
        if self.status is DeliveryStatus.CONFIRMED_SUCCESS and self.failed_count:
            raise ValueError("confirmed success cannot contain failures")
        if self.status is not DeliveryStatus.CONFIRMED_SUCCESS and not self.failed_count:
            raise ValueError("non-success delivery must contain a failed operation")
        object.__setattr__(self, "platform_ids", ids)


@dataclass(frozen=True)
class ChannelIdentity:
    tenant_id: str
    channel: str
    chat_id: str
    sender_id: str
    thread_id: str | None = None
    chat_type: str = "private"

    def __post_init__(self) -> None:
        for label, value in (
            ("tenant_id", self.tenant_id),
            ("channel", self.channel),
            ("chat_id", self.chat_id),
            ("sender_id", self.sender_id),
        ):
            _require_token(value, label)
        if self.thread_id is not None:
            _require_token(self.thread_id, "thread_id")
        if self.chat_type not in {"private", "group"}:
            raise ValueError("chat_type must be private or group")


@dataclass(frozen=True)
class ChannelDeliveryContext:
    receive_id_type: str
    receive_id: str
    source_message_id: str
    root_id: str | None = None
    thread_id: str | None = None
    chat_type: str = "private"
    source_chat_id: str | None = None

    def __post_init__(self) -> None:
        if self.receive_id_type not in {"open_id", "chat_id"}:
            raise ValueError("receive_id_type must be open_id or chat_id")
        _require_token(self.receive_id, "receive_id")
        _require_token(self.source_message_id, "source_message_id")
        if self.root_id is not None:
            _require_token(self.root_id, "root_id")
        if self.thread_id is not None:
            _require_token(self.thread_id, "thread_id")
        if self.source_chat_id is not None:
            _require_token(self.source_chat_id, "source_chat_id")
        if self.chat_type not in {"private", "group"}:
            raise ValueError("chat_type must be private or group")


@dataclass(frozen=True)
class OutboundArtifactRef:
    artifact_handle: str
    run_id: str
    filename: str
    media_type: str
    content_sha256: str

    def __post_init__(self) -> None:
        if not self.artifact_handle.startswith("hm-artifact:"):
            raise ValueError("artifact_handle must be an opaque hm-artifact handle")
        artifact_token = self.artifact_handle.removeprefix("hm-artifact:")
        if _ARTIFACT_TOKEN_RE.fullmatch(artifact_token) is None:
            raise ValueError("artifact token must be a URL-safe opaque token")
        _require_token(self.run_id, "artifact run_id")
        if not self.filename or "/" in self.filename or "\\" in self.filename:
            raise ValueError("artifact filename must be a basename")
        if not self.media_type.strip():
            raise ValueError("artifact media_type must be non-empty")
        if re.fullmatch(r"[0-9a-f]{64}", self.content_sha256) is None:
            raise ValueError("artifact content_sha256 must be lowercase SHA-256")


@dataclass(frozen=True)
class InboundMessage:
    identity: ChannelIdentity
    principal: AuthenticatedPrincipal
    content: str
    attachments: tuple[str, ...] = ()
    correlation_id: str | None = None
    delivery_context: ChannelDeliveryContext | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ChannelIdentity):
            raise TypeError("identity must be ChannelIdentity")
        if not isinstance(self.principal, AuthenticatedPrincipal):
            raise TypeError("principal must be AuthenticatedPrincipal")
        if self.principal.tenant_id != self.identity.tenant_id:
            raise ValueError("principal tenant does not match channel identity tenant")
        if self.principal.channel != self.identity.channel:
            raise ValueError("principal channel does not match channel identity channel")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("content must be a non-empty string")
        if isinstance(self.attachments, str):
            raise TypeError("attachments must be a sequence")
        attachments = tuple(self.attachments)
        if any(not isinstance(item, str) or not item.strip() for item in attachments):
            raise ValueError("attachments must contain non-empty paths")
        if self.correlation_id is not None:
            _require_token(self.correlation_id, "correlation_id")
        if self.delivery_context is not None and not isinstance(
            self.delivery_context, ChannelDeliveryContext
        ):
            raise TypeError("delivery_context must be ChannelDeliveryContext")
        object.__setattr__(self, "attachments", attachments)
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    @property
    def queue_key(self) -> tuple[str, str]:
        identity = self.identity
        return identity.tenant_id, ":".join(
            (identity.channel, identity.chat_id, identity.thread_id or "-", identity.sender_id)
        )


@dataclass(frozen=True)
class OutboundMessage:
    identity: ChannelIdentity
    session_id: str
    generation: int
    kind: ChannelEventKind
    content: str
    correlation_id: str
    attachments: tuple[OutboundArtifactRef, ...] = ()
    delivery_context: ChannelDeliveryContext | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ChannelIdentity):
            raise TypeError("identity must be ChannelIdentity")
        _require_token(self.session_id, "session_id")
        if isinstance(self.generation, bool) or not isinstance(self.generation, int):
            raise TypeError("generation must be an integer")
        if self.generation < 0:
            raise ValueError("generation must be non-negative")
        if not isinstance(self.kind, ChannelEventKind):
            raise TypeError("kind must be ChannelEventKind")
        if not isinstance(self.content, str):
            raise TypeError("content must be a string")
        _require_token(self.correlation_id, "correlation_id")
        attachments = tuple(self.attachments)
        if any(not isinstance(item, OutboundArtifactRef) for item in attachments):
            raise TypeError("attachments must contain OutboundArtifactRef values")
        if self.kind is ChannelEventKind.MEDIA and not attachments:
            raise ValueError("media outbound requires at least one artifact")
        if attachments and self.kind is not ChannelEventKind.MEDIA:
            raise ValueError("outbound artifacts require media event kind")
        if self.delivery_context is not None and not isinstance(
            self.delivery_context, ChannelDeliveryContext
        ):
            raise TypeError("delivery_context must be ChannelDeliveryContext")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))
        object.__setattr__(self, "attachments", attachments)

    @property
    def queue_key(self) -> tuple[str, str]:
        return self.identity.tenant_id, self.session_id

    @property
    def coalesce_key(self) -> tuple[str, str, ChannelEventKind]:
        return (*self.queue_key, self.kind)


def _require_token(value: object, label: str) -> None:
    if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a non-empty stable token")


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("metadata must be a mapping")
    return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})


def _freeze_json(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("metadata numbers must be finite")
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError(f"unsupported metadata value: {type(value).__name__}")


__all__ = [
    "ChannelDeliveryContext",
    "ChannelEventKind",
    "ChannelIdentity",
    "DeliveryReceipt",
    "DeliveryStatus",
    "InboundMessage",
    "OutboundArtifactRef",
    "OutboundMessage",
]
