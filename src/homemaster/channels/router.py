"""Authoritative channel routing and attachment containment."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from homemaster.channels.contracts import InboundMessage


class RoutingError(ValueError):
    """An inbound route or attachment failed closed."""


@dataclass(frozen=True)
class ChannelRoute:
    session_id: str
    tenant_id: str
    channel: str
    chat_id: str
    thread_id: str | None
    sender_id: str


class ChannelRouter:
    """Derive path-safe session ids exclusively from authenticated identity."""

    def route(self, message: InboundMessage) -> ChannelRoute:
        if not isinstance(message, InboundMessage):
            raise TypeError("message must be InboundMessage")
        identity = message.identity
        scope = {
            "tenant": identity.tenant_id,
            "channel": identity.channel,
            "chat": identity.chat_id,
            "thread": identity.thread_id,
            "sender": identity.sender_id,
            "chat_type": identity.chat_type,
        }
        digest = hashlib.sha256(
            json.dumps(scope, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:32]
        return ChannelRoute(
            session_id=f"gw-{digest}",
            tenant_id=identity.tenant_id,
            channel=identity.channel,
            chat_id=identity.chat_id,
            thread_id=identity.thread_id,
            sender_id=identity.sender_id,
        )


class AttachmentPolicy:
    def __init__(self, allowed_roots: tuple[Path, ...]) -> None:
        if not allowed_roots:
            raise ValueError("at least one attachment root is required")
        roots = tuple(Path(root).expanduser().resolve(strict=True) for root in allowed_roots)
        if any(not root.is_dir() for root in roots):
            raise ValueError("attachment roots must be directories")
        self._roots = roots

    @property
    def roots(self) -> tuple[Path, ...]:
        return self._roots

    def resolve(self, candidate: str | Path) -> Path:
        try:
            resolved = Path(candidate).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise RoutingError("attachment is not a readable file under allowed roots") from exc
        if not resolved.is_file() or not any(resolved.is_relative_to(root) for root in self._roots):
            raise RoutingError("attachment resolved outside allowed roots")
        return resolved

    def resolve_all(self, candidates: tuple[str, ...]) -> tuple[Path, ...]:
        return tuple(self.resolve(candidate) for candidate in candidates)


__all__ = ["AttachmentPolicy", "ChannelRoute", "ChannelRouter", "RoutingError"]
