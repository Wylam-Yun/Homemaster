from __future__ import annotations

from pathlib import Path

import pytest

from homemaster.channels.contracts import ChannelIdentity, InboundMessage
from homemaster.channels.router import AttachmentPolicy, ChannelRouter, RoutingError
from homemaster.gateway.auth import AuthenticatedPrincipal


def _message(*, chat_type: str = "private", thread_id: str | None = None) -> InboundMessage:
    return InboundMessage(
        identity=ChannelIdentity(
            tenant_id="tenant-a",
            channel="telegram",
            chat_id="chat-1",
            sender_id="sender-1",
            thread_id=thread_id,
            chat_type=chat_type,
        ),
        principal=AuthenticatedPrincipal(
            tenant_id="tenant-a",
            principal_id="operator-1",
            channel="telegram",
            capabilities=("tool.read",),
        ),
        content="hello",
        metadata={
            "tenant_id": "tenant-b",
            "sender_id": "attacker",
            "session_key_override": "admin-session",
        },
    )


def test_router_uses_only_typed_identity_and_group_routes_include_sender() -> None:
    router = ChannelRouter()

    private = router.route(_message())
    group_a = router.route(_message(chat_type="group"))
    group_b = router.route(
        InboundMessage(
            identity=ChannelIdentity(
                tenant_id="tenant-a",
                channel="telegram",
                chat_id="chat-1",
                sender_id="sender-2",
                chat_type="group",
            ),
            principal=AuthenticatedPrincipal(
                tenant_id="tenant-a",
                principal_id="operator-2",
                channel="telegram",
            ),
            content="hello",
        )
    )
    threaded = router.route(_message(chat_type="group", thread_id="topic-7"))

    assert private.session_id != group_a.session_id
    assert group_a.session_id != group_b.session_id
    assert threaded.session_id != group_a.session_id
    assert "admin-session" not in private.session_id
    assert private.tenant_id == "tenant-a"
    assert private.sender_id == "sender-1"


def test_identity_and_principal_must_match_at_trust_boundary() -> None:
    with pytest.raises(ValueError, match="tenant"):
        InboundMessage(
            identity=ChannelIdentity("tenant-a", "telegram", "chat", "sender"),
            principal=AuthenticatedPrincipal("tenant-b", "operator", "telegram"),
            content="hello",
        )


def test_channel_identity_rejects_path_and_delimiter_injection() -> None:
    for value in ("../tenant", "bad tenant", "tenant/child", ""):
        with pytest.raises(ValueError):
            ChannelIdentity(value, "telegram", "chat", "sender")


def test_attachment_policy_accepts_only_resolved_files_under_allowed_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    safe = allowed / "safe.txt"
    safe.write_text("ok", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = allowed / "link.txt"
    link.symlink_to(outside)
    policy = AttachmentPolicy((allowed,))

    assert policy.resolve(str(safe)) == safe.resolve()
    with pytest.raises(RoutingError, match="allowed roots"):
        policy.resolve(str(link))
    with pytest.raises(RoutingError, match="allowed roots"):
        policy.resolve(str(allowed / ".." / "secret.txt"))
