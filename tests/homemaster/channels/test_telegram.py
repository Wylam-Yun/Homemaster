from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from homemaster.channels.bus import BoundedPriorityBus
from homemaster.channels.impl.telegram import (
    TelegramChannel,
    silence_telegram_token_url_loggers,
)
from homemaster.config import ChannelPrincipalConfig, TelegramChannelConfig


def _config(**updates) -> TelegramChannelConfig:
    return TelegramChannelConfig(
        tenant_id="tenant-a",
        principals={
            "1001": ChannelPrincipalConfig(
                principal_id="operator-a",
                roles=("operator",),
                capabilities=("tool.read",),
            )
        },
        **updates,
    )


@pytest.mark.asyncio
async def test_telegram_exact_sender_mapping_creates_authoritative_identity() -> None:
    bus = BoundedPriorityBus()
    channel = TelegramChannel(_config(), bus)

    assert not await channel.accept_message(sender_id="1002", chat_id="chat", content="denied")
    assert await channel.accept_message(
        sender_id="1001",
        chat_id="chat",
        content="accepted",
        chat_type="group",
        thread_id="7",
    )
    inbound = await bus.receive_inbound()

    assert inbound.identity.tenant_id == "tenant-a"
    assert inbound.identity.sender_id == "1001"
    assert inbound.identity.thread_id == "7"
    assert inbound.principal.principal_id == "operator-a"
    assert inbound.principal.capabilities == ("tool.read",)


def test_telegram_configuration_fails_closed_and_repr_has_no_token(monkeypatch) -> None:
    with pytest.raises(ValueError, match="explicit principals"):
        TelegramChannelConfig(enabled=True)
    with pytest.raises(ValueError, match="wildcard"):
        TelegramChannelConfig(principals={"*": ChannelPrincipalConfig(principal_id="operator")})

    monkeypatch.setenv("HOMEMASTER_TELEGRAM_BOT_TOKEN", "raw-bot-token")
    channel = TelegramChannel(_config(), BoundedPriorityBus())
    assert "raw-bot-token" not in repr(channel)


def test_telegram_dependency_loggers_are_silenced() -> None:
    for name in ("httpx", "httpcore", "telegram.ext"):
        logging.getLogger(name).setLevel(logging.INFO)
    silence_telegram_token_url_loggers()
    assert all(
        logging.getLogger(name).level == logging.WARNING
        for name in ("httpx", "httpcore", "telegram.ext")
    )


@pytest.mark.asyncio
async def test_disabled_telegram_channel_does_not_import_or_start_network() -> None:
    channel = TelegramChannel(_config(enabled=False), BoundedPriorityBus())
    await channel.start()
    assert not channel.is_running


@pytest.mark.asyncio
async def test_unmapped_sender_is_rejected_before_attachment_download() -> None:
    channel = TelegramChannel(_config(), BoundedPriorityBus())
    channel._download_attachment = AsyncMock(return_value=("unexpected",))
    message = SimpleNamespace(
        text="",
        caption="",
        chat_id=7,
        chat=SimpleNamespace(type="private"),
        message_id=11,
        photo=(SimpleNamespace(file_id="remote-file"),),
    )
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=1002),
    )

    await channel._on_message(update, None)

    channel._download_attachment.assert_not_awaited()
    assert channel.bus.inbound_size == 0
