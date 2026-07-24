"""Telegram long-polling adapter copied and hardened from OpenHarness.

External python-telegram-bot API symbols remain UNVERIFIED until the user-led
hkust4 live gate. Import is delayed so local and benchmark entry points do not
require the optional ``gateway`` dependency.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from homemaster.channels.contracts import (
    ChannelIdentity,
    DeliveryReceipt,
    DeliveryStatus,
    InboundMessage,
    OutboundMessage,
)
from homemaster.channels.impl.base import BaseChannel
from homemaster.config.config import TelegramChannelConfig
from homemaster.gateway.auth import AuthenticatedPrincipal

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE_LEN = 4000
_TOKEN_URL_LOGGERS = ("httpx", "httpcore", "telegram.ext")


def silence_telegram_token_url_loggers() -> None:
    for name in _TOKEN_URL_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


class TelegramChannel(BaseChannel):
    name = "telegram"

    def __init__(self, config: TelegramChannelConfig, bus) -> None:
        super().__init__(bus)
        self.config = config
        self._application: Any | None = None
        self._stop_event = asyncio.Event()
        self.last_error: str | None = None

    def __repr__(self) -> str:
        return (
            f"TelegramChannel(enabled={self.config.enabled!r}, "
            f"tenant_id={self.config.tenant_id!r}, token_env={self.config.token_env!r})"
        )

    def principal_for_sender(self, sender_id: str) -> AuthenticatedPrincipal | None:
        configured = self.config.principals.get(str(sender_id))
        if configured is None:
            return None
        return AuthenticatedPrincipal(
            tenant_id=self.config.tenant_id,
            principal_id=configured.principal_id,
            channel=self.name,
            roles=configured.roles,
            capabilities=configured.capabilities,
        )

    async def accept_message(
        self,
        *,
        sender_id: str,
        chat_id: str,
        content: str,
        thread_id: str | None = None,
        chat_type: str = "private",
        attachments: tuple[str, ...] = (),
        correlation_id: str | None = None,
    ) -> bool:
        principal = self.principal_for_sender(sender_id)
        if principal is None:
            logger.warning("Telegram access denied for unmapped sender_id=%s", sender_id)
            return False
        await self.bus.publish_inbound(
            InboundMessage(
                identity=ChannelIdentity(
                    tenant_id=self.config.tenant_id,
                    channel=self.name,
                    chat_id=str(chat_id),
                    sender_id=str(sender_id),
                    thread_id=str(thread_id) if thread_id is not None else None,
                    chat_type=chat_type,
                ),
                principal=principal,
                content=content,
                attachments=attachments,
                correlation_id=correlation_id,
            )
        )
        return True

    async def start(self) -> None:
        if not self.config.enabled:
            return
        token = os.environ.get(self.config.token_env, "").strip()
        if not token:
            raise RuntimeError(
                f"Telegram token environment variable {self.config.token_env!r} is empty"
            )
        try:
            from telegram.ext import Application, MessageHandler, filters
            from telegram.request import HTTPXRequest
        except ImportError as exc:
            raise RuntimeError(
                "Telegram channel requires the optional 'gateway' dependency"
            ) from exc

        silence_telegram_token_url_loggers()
        request = HTTPXRequest(
            connection_pool_size=16,
            pool_timeout=5.0,
            connect_timeout=30.0,
            read_timeout=30.0,
        )
        self._application = (
            Application.builder().token(token).request(request).get_updates_request(request).build()
        )
        self._application.add_error_handler(self._on_error)
        self._application.add_handler(
            MessageHandler(
                (
                    filters.TEXT
                    | filters.PHOTO
                    | filters.VOICE
                    | filters.AUDIO
                    | filters.Document.ALL
                ),
                self._on_message,
            )
        )
        await self._application.initialize()
        await self._application.start()
        await self._application.bot.get_me()
        await self._application.updater.start_polling(
            allowed_updates=["message"],
            drop_pending_updates=False,
        )
        self._running = True
        self._stop_event.clear()
        await self._stop_event.wait()

    async def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        application = self._application
        self._application = None
        if application is None:
            return
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

    async def send(self, message: OutboundMessage) -> DeliveryReceipt:
        if self._application is None:
            return DeliveryReceipt(
                status=DeliveryStatus.CONFIRMED_FAILURE,
                operation="telegram.send",
                api_message="Telegram channel is not running",
                failed_count=1,
            )
        thread_id = message.identity.thread_id
        platform_ids: list[str] = []
        chunks = _split_message(message.content, TELEGRAM_MAX_MESSAGE_LEN)
        for chunk in chunks:
            try:
                sent = await self._application.bot.send_message(
                    chat_id=int(message.identity.chat_id),
                    text=chunk,
                    message_thread_id=int(thread_id) if thread_id is not None else None,
                )
            except Exception as exc:
                return DeliveryReceipt(
                    status=(
                        DeliveryStatus.PARTIAL_SUCCESS
                        if platform_ids
                        else DeliveryStatus.OUTCOME_UNKNOWN
                    ),
                    operation="telegram.send",
                    platform_ids=tuple(platform_ids),
                    api_message=type(exc).__name__,
                    sent_count=len(platform_ids),
                    failed_count=max(1, len(chunks) - len(platform_ids)),
                )
            platform_ids.append(str(getattr(sent, "message_id", "unknown")))
        return DeliveryReceipt(
            status=DeliveryStatus.CONFIRMED_SUCCESS,
            operation="telegram.send",
            platform_ids=tuple(platform_ids),
            sent_count=len(platform_ids),
        )

    async def _on_message(self, update: Any, _context: Any) -> None:
        message = getattr(update, "message", None)
        user = getattr(update, "effective_user", None)
        if message is None or user is None:
            return
        sender_id = str(user.id)
        if self.principal_for_sender(sender_id) is None:
            logger.warning("Telegram access denied for unmapped sender_id=%s", sender_id)
            return
        content = str(message.text or message.caption or "").strip()
        attachments = await self._download_attachment(message)
        if not content and attachments:
            content = "[attachment]"
        if not content:
            return
        await self.accept_message(
            sender_id=sender_id,
            chat_id=str(message.chat_id),
            content=content,
            thread_id=getattr(message, "message_thread_id", None),
            chat_type=("private" if message.chat.type == "private" else "group"),
            attachments=attachments,
            correlation_id=f"telegram-{message.message_id}",
        )

    async def _download_attachment(self, message: Any) -> tuple[str, ...]:
        media = None
        if getattr(message, "photo", None):
            media = message.photo[-1]
        else:
            for field in ("voice", "audio", "document"):
                if getattr(message, field, None) is not None:
                    media = getattr(message, field)
                    break
        if media is None or self._application is None:
            return ()
        file_id = str(media.file_id)
        safe_name = "".join(
            character for character in file_id[:64] if character.isalnum() or character in "-_"
        )
        if not safe_name:
            raise ValueError("Telegram attachment has no safe file id")
        root = Path(self.config.attachment_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        target = root / safe_name
        remote = await self._application.bot.get_file(file_id)
        await remote.download_to_drive(str(target))
        resolved = target.resolve(strict=True)
        if not resolved.is_file() or not resolved.is_relative_to(root):
            raise ValueError("downloaded Telegram attachment escaped configured root")
        return (str(resolved),)

    async def _on_error(self, _update: object, context: Any) -> None:
        self.last_error = type(context.error).__name__
        logger.error("Telegram polling or handler failure: %s", self.last_error)


def _split_message(text: str, limit: int) -> tuple[str, ...]:
    if not text:
        return ()
    return tuple(text[index : index + limit] for index in range(0, len(text), limit))


__all__ = ["TelegramChannel", "silence_telegram_token_url_loggers"]
