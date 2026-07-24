"""Feishu/Lark channel boundary backed by the optional ``lark-oapi`` SDK.

SDK request builders and live behavior remain UNVERIFIED until the Phase 0/9
live gates run against the configured test applications.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import multiprocessing
import os
import queue
import stat
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homemaster.artifacts import ToolOutputArtifactResolver
from homemaster.artifacts.tool_output_store import ArtifactStoreError
from homemaster.channels.contracts import (
    ChannelDeliveryContext,
    ChannelEventKind,
    ChannelIdentity,
    DeliveryReceipt,
    DeliveryStatus,
    InboundMessage,
    OutboundMessage,
)
from homemaster.channels.impl.base import BaseChannel
from homemaster.config.config import FeishuChannelConfig
from homemaster.events.public_projection import PublicEventProjection
from homemaster.gateway.auth import AuthenticatedPrincipal

_DOMAIN_URLS = {
    "feishu": "https://open.feishu.cn",
    "lark": "https://open.larksuite.com",
}
_FEISHU_LOGGERS = ("lark_oapi", "websockets", "urllib3")
_FEISHU_AUDIT_LOGGER = logging.getLogger("homemaster.feishu.audit")
_TRUSTED_OWNER_CAPABILITIES = (
    "tool.read",
    "tool.mutate",
    "tool.auto",
    "device.read",
    "device.control",
    "filesystem.read",
    "filesystem.write",
    "network.http",
    "process.exec",
    "process.spawn",
    "scheduler.manage",
    "config.mutate",
    "mcp.call",
    "mcp.manage",
    "channel.feishu.group.create",
    "channel.feishu.group.rename",
)
_feishu_sensitive_values: tuple[str, ...] = ()
_original_log_record_factory = logging.getLogRecordFactory()
_log_record_factory_installed = False


def _emit_feishu_audit(
    action: str,
    target: str,
    started: float,
    return_code: object,
    certainty: str,
) -> None:
    payload = {
        "action": action,
        "target_hash": hashlib.sha256(target.encode("utf-8")).hexdigest()[:16],
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "return_code": return_code,
        "certainty": certainty,
    }
    _FEISHU_AUDIT_LOGGER.info(json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


class _SanitizingLogFilter(logging.Filter):
    def __init__(self, sensitive_values: tuple[str, ...]) -> None:
        super().__init__()
        self._projection = PublicEventProjection(sensitive_values=sensitive_values)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._projection.sanitize_content(record.getMessage())
        record.args = ()
        return True


def install_feishu_logging_safety(sensitive_values: tuple[str, ...]) -> None:
    global _feishu_sensitive_values, _log_record_factory_installed
    _feishu_sensitive_values = tuple(value for value in sensitive_values if value)
    if not _log_record_factory_installed:

        def sanitizing_record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = _original_log_record_factory(*args, **kwargs)
            if record.name.startswith(_FEISHU_LOGGERS):
                _SanitizingLogFilter(_feishu_sensitive_values).filter(record)
            return record

        logging.setLogRecordFactory(sanitizing_record_factory)
        _log_record_factory_installed = True

    for name in _FEISHU_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(logging.WARNING)
        logger.filters[:] = [
            existing
            for existing in logger.filters
            if not isinstance(existing, _SanitizingLogFilter)
        ]
        logger.addFilter(_SanitizingLogFilter(sensitive_values))


def _build_feishu_event_handler(
    lark: Any,
    packets: Any,
    *,
    encrypt_key: str,
    verification_token: str,
) -> Any:
    def on_message(data: Any) -> None:
        packets.put({"type": "message", "payload": _normalize_sdk_event(data)})

    def on_p2p_chat_entered(data: Any) -> None:
        event = getattr(data, "event", None)
        chat_id = str(getattr(event, "chat_id", "") or "p2p-chat")
        _emit_feishu_audit(
            "event.p2p_chat_entered.ack",
            chat_id,
            time.monotonic(),
            0,
            DeliveryStatus.CONFIRMED_SUCCESS.value,
        )

    def on_message_read(data: Any) -> None:
        event = getattr(data, "event", None)
        message_ids = tuple(getattr(event, "message_id_list", ()) or ())
        target = str(message_ids[0]) if message_ids else "message-read"
        _emit_feishu_audit(
            "event.message_read.ack",
            target,
            time.monotonic(),
            0,
            DeliveryStatus.CONFIRMED_SUCCESS.value,
        )

    return (
        lark.EventDispatcherHandler.builder(encrypt_key, verification_token)
        .register_p2_im_message_receive_v1(on_message)
        .register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(on_p2p_chat_entered)
        .register_p2_im_message_message_read_v1(on_message_read)
        .build()
    )


def _feishu_ws_worker(
    app_id: str,
    app_secret: str,
    encrypt_key: str,
    verification_token: str,
    domain: str,
    packets,
) -> None:
    started = time.monotonic()
    try:
        install_feishu_logging_safety((app_secret, encrypt_key, verification_token))
        import lark_oapi as lark

        handler = _build_feishu_event_handler(
            lark,
            packets,
            encrypt_key=encrypt_key,
            verification_token=verification_token,
        )
        client = lark.ws.Client(
            app_id,
            app_secret,
            event_handler=handler,
            domain=_DOMAIN_URLS[domain],
            log_level=lark.LogLevel.WARNING,
        )
        _emit_feishu_audit("websocket.connect", app_id, started, None, "attempted")
        client.start()
    except BaseException as exc:
        _emit_feishu_audit("websocket.connect", app_id, started, None, "outcome_unknown")
        packets.put({"type": "fatal", "error_type": type(exc).__name__})
        return
    _emit_feishu_audit("websocket.connect", app_id, started, 0, "completed")
    packets.put({"type": "completed"})


def _normalize_sdk_event(data: object) -> dict[str, object]:
    event = getattr(data, "event", None)
    message = getattr(event, "message", None)
    sender = getattr(event, "sender", None)
    sender_id = getattr(sender, "sender_id", None)
    header = getattr(data, "header", None)
    raw_chat_type = str(getattr(message, "chat_type", "") or "")
    mentions = []
    for mention in getattr(message, "mentions", ()) or ():
        mention_id = getattr(mention, "id", None)
        mentions.append(
            {
                "id": {
                    "open_id": str(getattr(mention_id, "open_id", "") or ""),
                    "user_id": str(getattr(mention_id, "user_id", "") or ""),
                    "union_id": str(getattr(mention_id, "union_id", "") or ""),
                },
                "name": str(getattr(mention, "name", "") or ""),
                "key": str(getattr(mention, "key", "") or ""),
            }
        )
    return {
        "event_id": str(getattr(header, "event_id", "") or ""),
        "sender_open_id": str(getattr(sender_id, "open_id", "") or ""),
        "sender_type": str(getattr(sender, "sender_type", "") or ""),
        "message_id": str(getattr(message, "message_id", "") or ""),
        "chat_id": str(getattr(message, "chat_id", "") or ""),
        "chat_type": "private" if raw_chat_type == "p2p" else raw_chat_type,
        "message_type": str(getattr(message, "message_type", "") or ""),
        "content": getattr(message, "content", "") or "",
        "root_id": str(getattr(message, "root_id", "") or ""),
        "thread_id": str(getattr(message, "thread_id", "") or ""),
        "mentions": mentions,
    }


@dataclass(frozen=True)
class FeishuDownload:
    content: bytes
    filename: str
    api_code: int


@dataclass(frozen=True)
class FeishuRenderedMessage:
    msg_type: str
    content: str


class FeishuApiError(RuntimeError):
    def __init__(self, operation: str, *, code: int | None, message: str) -> None:
        super().__init__(f"{operation} failed: code={code!r}, message={message!r}")
        self.operation = operation
        self.code = code
        self.api_message = message


class FeishuApiService:
    """Application-owned holder for shared Feishu REST resources and credentials."""

    def __init__(
        self,
        config: FeishuChannelConfig,
        *,
        app_id: str,
        app_secret: str,
        encrypt_key: str = "",
        verification_token: str = "",
        credential_source: str = "explicit",
    ) -> None:
        self.config = config
        self.app_id = app_id.strip()
        self._app_secret = app_secret.strip()
        self._encrypt_key = encrypt_key.strip()
        self._verification_token = verification_token.strip()
        self._credential_source = credential_source
        self._client: Any | None = None

    @classmethod
    def from_config(
        cls,
        config: FeishuChannelConfig,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> FeishuApiService:
        values = os.environ if environ is None else environ
        direct_id = config.app_id.strip()
        direct_secret = config.app_secret.get_secret_value().strip()
        if direct_id or direct_secret:
            if not direct_id or not direct_secret:
                raise RuntimeError("Feishu YAML app id and app secret must be configured together")
            return cls(
                config,
                app_id=direct_id,
                app_secret=direct_secret,
                encrypt_key=values.get(config.encrypt_key_env, ""),
                verification_token=values.get(config.verification_token_env, ""),
                credential_source="file",
            )

        env_id = values.get(config.app_id_env, "").strip()
        env_secret = values.get(config.app_secret_env, "").strip()
        if bool(env_id) != bool(env_secret):
            raise RuntimeError(
                "Feishu environment app id and app secret must be configured together"
            )
        return cls(
            config,
            app_id=env_id,
            app_secret=env_secret,
            encrypt_key=values.get(config.encrypt_key_env, ""),
            verification_token=values.get(config.verification_token_env, ""),
            credential_source="env" if env_id else "unconfigured",
        )

    @classmethod
    def from_environment(cls, config: FeishuChannelConfig) -> FeishuApiService:
        """Compatibility alias; credential resolution now honors direct YAML first."""

        return cls.from_config(config)

    def __repr__(self) -> str:
        return (
            f"FeishuApiService(domain={self.config.domain!r}, "
            f"credential_source={self.credential_source!r}, "
            f"app_id_configured={bool(self.app_id)!r}, client_created={self.client_created!r})"
        )

    @property
    def credential_source(self) -> str:
        return self._credential_source

    @property
    def client_created(self) -> bool:
        return self._client is not None

    @property
    def encrypt_key(self) -> str:
        return self._encrypt_key

    @property
    def verification_token(self) -> str:
        return self._verification_token

    def ensure_rest_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.app_id or not self._app_secret:
            raise RuntimeError(
                "Feishu app id and app secret must be configured in YAML or environment"
            )
        install_feishu_logging_safety(
            (self._app_secret, self._encrypt_key, self._verification_token)
        )
        try:
            import lark_oapi as lark
        except ImportError as exc:
            raise RuntimeError("Feishu channel requires the optional 'gateway' dependency") from exc
        self._client = (
            lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self._app_secret)
            .domain(_DOMAIN_URLS[self.config.domain])
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
        return self._client

    async def aclose(self) -> None:
        self._client = None

    async def _audited_thread_call(
        self,
        action: str,
        target: str,
        function: Callable[..., Any],
        *args: Any,
    ) -> Any:
        started = time.monotonic()
        try:
            result = await asyncio.to_thread(function, *args)
        except Exception:
            _emit_feishu_audit(action, target, started, None, "outcome_unknown")
            raise
        if isinstance(result, DeliveryReceipt):
            return_code = result.api_code
            certainty = result.status.value
        elif isinstance(result, FeishuDownload):
            return_code = result.api_code
            certainty = DeliveryStatus.CONFIRMED_SUCCESS.value
        elif hasattr(result, "api_success"):
            return_code = getattr(result, "api_code", None)
            certainty = (
                DeliveryStatus.CONFIRMED_SUCCESS.value
                if result.api_success
                else DeliveryStatus.CONFIRMED_FAILURE.value
            )
        else:
            return_code = None
            certainty = DeliveryStatus.CONFIRMED_SUCCESS.value
        _emit_feishu_audit(action, target, started, return_code, certainty)
        return result

    async def download_message_resource(
        self, message_id: str, file_key: str, resource_type: str
    ) -> FeishuDownload:
        return await self._audited_thread_call(
            "message.resource.download",
            message_id,
            self._download_message_resource_sync,
            message_id,
            file_key,
            resource_type,
        )

    def _download_message_resource_sync(
        self, message_id: str, file_key: str, resource_type: str
    ) -> FeishuDownload:
        from lark_oapi.api.im.v1 import GetMessageResourceRequest

        request = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(file_key)
            .type("image" if resource_type == "image" else "file")
            .build()
        )
        response = self.ensure_rest_client().im.v1.message_resource.get(request)
        if not response.success():
            raise FeishuApiError(
                "download message resource",
                code=getattr(response, "code", None),
                message=str(getattr(response, "msg", "")),
            )
        content = response.file.read() if hasattr(response.file, "read") else response.file
        return FeishuDownload(
            content=bytes(content),
            filename=str(getattr(response, "file_name", "") or f"{file_key}.bin"),
            api_code=int(getattr(response, "code", 0)),
        )

    async def send_message(
        self,
        *,
        delivery: ChannelDeliveryContext,
        msg_type: str,
        content: str,
        reply_to_message_id: str | None,
    ) -> DeliveryReceipt:
        return await self._audited_thread_call(
            "message.send",
            reply_to_message_id or delivery.receive_id,
            self._send_message_sync,
            delivery,
            msg_type,
            content,
            reply_to_message_id,
        )

    async def add_reaction(self, message_id: str, emoji_type: str) -> DeliveryReceipt:
        return await self._audited_thread_call(
            "reaction.add", message_id, self._add_reaction_sync, message_id, emoji_type
        )

    def _add_reaction_sync(self, message_id: str, emoji_type: str) -> DeliveryReceipt:
        try:
            from lark_oapi.api.im.v1 import (
                CreateMessageReactionRequest,
                CreateMessageReactionRequestBody,
                Emoji,
            )

            request = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                )
                .build()
            )
            response = self.ensure_rest_client().im.v1.message_reaction.create(request)
        except Exception as exc:
            return DeliveryReceipt(
                status=DeliveryStatus.OUTCOME_UNKNOWN,
                operation="feishu.reaction.add",
                api_message=type(exc).__name__,
                failed_count=1,
            )
        if not response.success():
            return DeliveryReceipt(
                status=DeliveryStatus.CONFIRMED_FAILURE,
                operation="feishu.reaction.add",
                api_code=getattr(response, "code", None),
                api_message=str(getattr(response, "msg", "")),
                failed_count=1,
            )
        reaction_id = str(getattr(getattr(response, "data", None), "reaction_id", "") or "unknown")
        return DeliveryReceipt(
            status=DeliveryStatus.CONFIRMED_SUCCESS,
            operation="feishu.reaction.add",
            platform_ids=(reaction_id,),
            api_code=getattr(response, "code", 0),
            sent_count=1,
        )

    async def create_group(self, *, member_open_id: str, name: str, operation_id: str):
        return await self._audited_thread_call(
            "group.create",
            operation_id,
            self._create_group_sync,
            member_open_id,
            name,
            operation_id,
        )

    def _create_group_sync(self, member_open_id: str, name: str, operation_id: str):
        del operation_id
        from lark_oapi.api.im.v1 import CreateChatRequest, CreateChatRequestBody

        from homemaster.channels.feishu_groups import FeishuGroupApiResult

        request = (
            CreateChatRequest.builder()
            .user_id_type("open_id")
            .set_bot_manager(True)
            .request_body(
                CreateChatRequestBody.builder()
                .name(name)
                .user_id_list([member_open_id])
                .chat_mode("group")
                .chat_type("private")
                .build()
            )
            .build()
        )
        response = self.ensure_rest_client().im.v1.chat.create(request)
        return FeishuGroupApiResult(
            api_success=bool(response.success()),
            api_code=getattr(response, "code", None),
            api_message=str(getattr(response, "msg", "")),
            chat_id=str(getattr(getattr(response, "data", None), "chat_id", "") or "") or None,
        )

    async def rename_group(self, *, chat_id: str, name: str):
        return await self._audited_thread_call(
            "group.rename", chat_id, self._rename_group_sync, chat_id, name
        )

    def _rename_group_sync(self, chat_id: str, name: str):
        from lark_oapi.api.im.v1 import UpdateChatRequest, UpdateChatRequestBody

        from homemaster.channels.feishu_groups import FeishuGroupApiResult

        request = (
            UpdateChatRequest.builder()
            .user_id_type("open_id")
            .chat_id(chat_id)
            .request_body(UpdateChatRequestBody.builder().name(name).build())
            .build()
        )
        response = self.ensure_rest_client().im.v1.chat.update(request)
        return FeishuGroupApiResult(
            api_success=bool(response.success()),
            api_code=getattr(response, "code", None),
            api_message=str(getattr(response, "msg", "")),
            chat_id=chat_id,
        )

    async def get_chat(self, chat_id: str):
        return await self._audited_thread_call("group.read", chat_id, self._get_chat_sync, chat_id)

    def _get_chat_sync(self, chat_id: str):
        from lark_oapi.api.im.v1 import GetChatMembersRequest, GetChatRequest

        from homemaster.channels.feishu_groups import FeishuChatState

        client = self.ensure_rest_client()
        chat_response = client.im.v1.chat.get(GetChatRequest.builder().chat_id(chat_id).build())
        if not chat_response.success():
            raise FeishuApiError(
                "get Feishu chat",
                code=getattr(chat_response, "code", None),
                message=str(getattr(chat_response, "msg", "")),
            )
        members_response = client.im.v1.chat_members.get(
            GetChatMembersRequest.builder()
            .chat_id(chat_id)
            .member_id_type("open_id")
            .page_size(100)
            .build()
        )
        if not members_response.success():
            raise FeishuApiError(
                "get Feishu chat members",
                code=getattr(members_response, "code", None),
                message=str(getattr(members_response, "msg", "")),
            )
        chat = getattr(getattr(chat_response, "data", None), "chat", None)
        items = getattr(getattr(members_response, "data", None), "items", ()) or ()
        return FeishuChatState(
            chat_id=chat_id,
            name=str(getattr(chat, "name", "") or ""),
            member_open_ids=tuple(
                str(getattr(item, "member_id", "") or "")
                for item in items
                if getattr(item, "member_id", None)
            ),
        )

    def _send_message_sync(
        self,
        delivery: ChannelDeliveryContext,
        msg_type: str,
        content: str,
        reply_to_message_id: str | None,
    ) -> DeliveryReceipt:
        try:
            if reply_to_message_id:
                from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

                request = (
                    ReplyMessageRequest.builder()
                    .message_id(reply_to_message_id)
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .msg_type(msg_type)
                        .content(content)
                        .reply_in_thread(bool(delivery.thread_id or delivery.root_id))
                        .build()
                    )
                    .build()
                )
                response = self.ensure_rest_client().im.v1.message.reply(request)
            else:
                from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

                request = (
                    CreateMessageRequest.builder()
                    .receive_id_type(delivery.receive_id_type)
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(delivery.receive_id)
                        .msg_type(msg_type)
                        .content(content)
                        .build()
                    )
                    .build()
                )
                response = self.ensure_rest_client().im.v1.message.create(request)
        except Exception as exc:
            return DeliveryReceipt(
                status=DeliveryStatus.OUTCOME_UNKNOWN,
                operation="feishu.message.send",
                api_message=type(exc).__name__,
                failed_count=1,
            )
        if not response.success():
            return DeliveryReceipt(
                status=DeliveryStatus.CONFIRMED_FAILURE,
                operation="feishu.message.send",
                api_code=getattr(response, "code", None),
                api_message=str(getattr(response, "msg", "")),
                failed_count=1,
            )
        message_id = str(getattr(getattr(response, "data", None), "message_id", "") or "unknown")
        return DeliveryReceipt(
            status=DeliveryStatus.CONFIRMED_SUCCESS,
            operation="feishu.message.send",
            platform_ids=(message_id,),
            api_code=getattr(response, "code", 0),
            sent_count=1,
        )

    async def upload_and_send_artifact(
        self,
        *,
        path: Path,
        media_type: str,
        delivery: ChannelDeliveryContext,
    ) -> DeliveryReceipt:
        upload = await self._audited_thread_call(
            "media.upload", str(path), self._upload_artifact_sync, path, media_type
        )
        if isinstance(upload, DeliveryReceipt):
            return upload
        msg_type, key = upload
        content_key = "image_key" if msg_type == "image" else "file_key"
        receipt = await self.send_message(
            delivery=delivery,
            msg_type=msg_type,
            content=json.dumps({content_key: key}, ensure_ascii=False, separators=(",", ":")),
            reply_to_message_id=delivery.source_message_id,
        )
        if receipt.status is DeliveryStatus.CONFIRMED_SUCCESS:
            return DeliveryReceipt(
                status=DeliveryStatus.CONFIRMED_SUCCESS,
                operation="feishu.media.send",
                platform_ids=receipt.platform_ids,
                api_code=receipt.api_code,
                api_message=receipt.api_message,
                sent_count=1,
            )
        return DeliveryReceipt(
            status=DeliveryStatus.PARTIAL_SUCCESS,
            operation="feishu.media.send",
            platform_ids=(key, *receipt.platform_ids),
            api_code=receipt.api_code,
            api_message=receipt.api_message,
            sent_count=1,
            failed_count=1,
        )

    def _upload_artifact_sync(
        self, path: Path, media_type: str
    ) -> tuple[str, str] | DeliveryReceipt:
        try:
            with path.open("rb") as stream:
                if media_type.startswith("image/"):
                    from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody

                    request = (
                        CreateImageRequest.builder()
                        .request_body(
                            CreateImageRequestBody.builder()
                            .image_type("message")
                            .image(stream)
                            .build()
                        )
                        .build()
                    )
                    response = self.ensure_rest_client().im.v1.image.create(request)
                    msg_type = "image"
                    key = getattr(getattr(response, "data", None), "image_key", None)
                else:
                    from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody

                    file_type = (
                        "opus"
                        if media_type.startswith("audio/")
                        else "mp4"
                        if media_type.startswith("video/")
                        else "stream"
                    )
                    request = (
                        CreateFileRequest.builder()
                        .request_body(
                            CreateFileRequestBody.builder()
                            .file_type(file_type)
                            .file_name(path.name)
                            .file(stream)
                            .build()
                        )
                        .build()
                    )
                    response = self.ensure_rest_client().im.v1.file.create(request)
                    msg_type = "media" if media_type.startswith(("audio/", "video/")) else "file"
                    key = getattr(getattr(response, "data", None), "file_key", None)
        except Exception as exc:
            return DeliveryReceipt(
                status=DeliveryStatus.OUTCOME_UNKNOWN,
                operation="feishu.media.upload",
                api_message=type(exc).__name__,
                failed_count=1,
            )
        if not response.success() or not key:
            return DeliveryReceipt(
                status=DeliveryStatus.CONFIRMED_FAILURE,
                operation="feishu.media.upload",
                api_code=getattr(response, "code", None),
                api_message=str(getattr(response, "msg", "")),
                failed_count=1,
            )
        return msg_type, str(key)


class FeishuChannel(BaseChannel):
    name = "feishu"

    def __init__(
        self,
        config: FeishuChannelConfig,
        bus,
        *,
        api_service: FeishuApiService,
        artifact_resolver: ToolOutputArtifactResolver | None = None,
        ws_worker: Callable[..., None] = _feishu_ws_worker,
    ) -> None:
        super().__init__(bus)
        self.config = config
        self.api_service = api_service
        self.artifact_resolver = artifact_resolver
        self._ws_worker = ws_worker
        self._stop_event = asyncio.Event()
        self._ws_process: multiprocessing.Process | None = None
        self._ws_packets = None
        self._worker_lock = asyncio.Lock()
        self._dedup: OrderedDict[tuple[str, str, str], float] = OrderedDict()
        self._dedup_ttl_s = 3600.0
        self._dedup_capacity = 4096
        self._trusted_principal = AuthenticatedPrincipal(
            tenant_id=config.tenant_id,
            principal_id="feishu-owner",
            channel=self.name,
            roles=("admin",),
            capabilities=_TRUSTED_OWNER_CAPABILITIES,
        )
        self._attachment_root = config.attachment_root.expanduser()
        self._attachment_root.mkdir(parents=True, exist_ok=True)
        self._attachment_root = self._attachment_root.resolve(strict=True)

    def __repr__(self) -> str:
        return (
            f"FeishuChannel(enabled={self.config.enabled!r}, "
            f"tenant_id={self.config.tenant_id!r}, domain={self.config.domain!r})"
        )

    @property
    def worker_alive(self) -> bool:
        return self._ws_process is not None and self._ws_process.is_alive()

    async def accept_event(self, envelope: Mapping[str, object]) -> bool:
        started = time.monotonic()
        sender_open_id = _required_text(envelope.get("sender_open_id"))
        message_id = _required_text(envelope.get("message_id"))
        audit_target = message_id or sender_open_id or "invalid-event"
        if (
            not sender_open_id
            or not message_id
            or _required_text(envelope.get("sender_type")).casefold() == "bot"
        ):
            _emit_feishu_audit("event.principal", audit_target, started, None, "rejected")
            return False
        principal = self._trusted_principal
        _emit_feishu_audit("event.principal", audit_target, started, 0, "accepted")

        chat_type = str(envelope.get("chat_type") or "private")
        if chat_type not in {"private", "group"}:
            _emit_feishu_audit("event.chat_type", audit_target, started, None, "rejected")
            return False
        _emit_feishu_audit("event.chat_type", audit_target, started, 0, "accepted")
        if not self._claim_message(message_id):
            _emit_feishu_audit("event.claim", audit_target, started, None, "duplicate")
            return False
        _emit_feishu_audit("event.claim", audit_target, started, 0, "accepted")

        committed = False
        saved_path: Path | None = None
        try:
            content = _parse_content(
                str(envelope.get("message_type") or "text"), envelope.get("content")
            )
            if not content.strip():
                return False
            source_chat_id = _required_text(envelope.get("chat_id"))
            if chat_type == "group" and not source_chat_id:
                return False
            receive_id_type = "chat_id" if chat_type == "group" else "open_id"
            receive_id = source_chat_id if chat_type == "group" else sender_open_id
            root_id = _optional_text(envelope.get("root_id"))
            thread_id = _optional_text(envelope.get("thread_id")) or root_id
            delivery = ChannelDeliveryContext(
                receive_id_type=receive_id_type,
                receive_id=receive_id,
                source_message_id=message_id,
                root_id=root_id,
                thread_id=thread_id,
                chat_type=chat_type,
            )
            attachments: tuple[str, ...] = ()
            message_type = str(envelope.get("message_type") or "text")
            if message_type in {"image", "audio", "media", "file"}:
                content_mapping = _content_mapping(envelope.get("content"))
                key_name = "image_key" if message_type == "image" else "file_key"
                file_key = _required_text(content_mapping.get(key_name))
                if not file_key:
                    return False
                try:
                    download = await self.api_service.download_message_resource(
                        message_id,
                        file_key,
                        "image" if message_type == "image" else "file",
                    )
                    saved_path = self._persist_download(message_id, file_key, download)
                except (FeishuApiError, OSError, ValueError):
                    return False
                if saved_path is None:
                    return False
                attachments = (str(saved_path),)
            if self._running:
                await self.api_service.add_reaction(message_id, self.config.react_emoji)
            published = await self.bus.publish_inbound(
                InboundMessage(
                    identity=ChannelIdentity(
                        tenant_id=self.config.tenant_id,
                        channel=self.name,
                        chat_id=receive_id,
                        sender_id=sender_open_id,
                        thread_id=thread_id,
                        chat_type=chat_type,
                    ),
                    principal=principal,
                    content=content,
                    attachments=attachments,
                    correlation_id=message_id,
                    delivery_context=delivery,
                )
            )
            if not published:
                return False
            committed = True
            _emit_feishu_audit(
                "event.publish",
                audit_target,
                started,
                0,
                DeliveryStatus.CONFIRMED_SUCCESS.value,
            )
            return True
        finally:
            if not committed:
                self._release_message(message_id)
                if saved_path is not None:
                    saved_path.unlink(missing_ok=True)

    def _persist_download(
        self, message_id: str, file_key: str, download: FeishuDownload
    ) -> Path | None:
        filename = download.filename.strip()
        if (
            not download.content
            or not filename
            or filename in {".", ".."}
            or Path(filename).is_absolute()
            or "/" in filename
            or "\\" in filename
        ):
            return None
        digest = hashlib.sha256(f"{message_id}:{file_key}".encode()).hexdigest()[:20]
        controlled_name = f"{digest}-{filename[:180]}"
        root_fd = os.open(self._attachment_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                fd = os.open(controlled_name, flags, 0o600, dir_fd=root_fd)
            except FileExistsError:
                return None
            try:
                try:
                    with os.fdopen(fd, "wb", closefd=True) as stream:
                        stream.write(download.content)
                        stream.flush()
                        os.fsync(stream.fileno())
                except BaseException:
                    os.unlink(controlled_name, dir_fd=root_fd)
                    raise
                info = os.stat(controlled_name, dir_fd=root_fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
                    os.unlink(controlled_name, dir_fd=root_fd)
                    return None
                path = self._attachment_root / controlled_name
                if not path.resolve(strict=True).is_relative_to(self._attachment_root):
                    os.unlink(controlled_name, dir_fd=root_fd)
                    return None
                return path
            except BaseException:
                if os.path.exists(self._attachment_root / controlled_name):
                    os.unlink(controlled_name, dir_fd=root_fd)
                raise
        finally:
            os.close(root_fd)

    def _claim_message(self, message_id: str) -> bool:
        now = time.monotonic()
        while self._dedup:
            first_key = next(iter(self._dedup))
            if now - self._dedup[first_key] <= self._dedup_ttl_s:
                break
            self._dedup.popitem(last=False)
        app_hash = hashlib.sha256(self.api_service.app_id.encode("utf-8")).hexdigest()[:16]
        key = (app_hash, self.config.tenant_id, message_id)
        if key in self._dedup:
            return False
        self._dedup[key] = now
        while len(self._dedup) > self._dedup_capacity:
            self._dedup.popitem(last=False)
        return True

    def _release_message(self, message_id: str) -> None:
        app_hash = hashlib.sha256(self.api_service.app_id.encode("utf-8")).hexdigest()[:16]
        self._dedup.pop((app_hash, self.config.tenant_id, message_id), None)

    async def start(self) -> None:
        if not self.config.enabled:
            return
        self.api_service.ensure_rest_client()
        self._running = True
        self._stop_event.clear()
        context = multiprocessing.get_context("spawn")
        self._ws_packets = context.Queue(maxsize=256)
        self._ws_process = context.Process(
            target=self._ws_worker,
            args=(
                self.api_service.app_id,
                self.api_service._app_secret,
                self.api_service.encrypt_key,
                self.api_service.verification_token,
                self.config.domain,
                self._ws_packets,
            ),
            name="homemaster-feishu-ws",
        )
        self._ws_process.start()
        _emit_feishu_audit(
            "websocket.worker_start",
            self.api_service.app_id,
            time.monotonic(),
            0,
            "attempted",
        )
        try:
            while self._running:
                packet = await asyncio.to_thread(_queue_packet, self._ws_packets, 0.1)
                if packet is None:
                    if self._ws_process is not None and not self._ws_process.is_alive():
                        raise RuntimeError("Feishu WebSocket worker exited without terminal packet")
                    continue
                packet_type = packet.get("type")
                if packet_type == "message":
                    payload = packet.get("payload")
                    if isinstance(payload, Mapping):
                        await self.accept_event(payload)
                elif packet_type == "fatal":
                    _emit_feishu_audit(
                        "websocket.worker",
                        self.api_service.app_id,
                        time.monotonic(),
                        None,
                        "outcome_unknown",
                    )
                    raise RuntimeError(
                        f"Feishu WebSocket worker failed: {packet.get('error_type', 'unknown')}"
                    )
                elif packet_type == "completed":
                    raise RuntimeError("Feishu WebSocket worker completed unexpectedly")
        finally:
            self._running = False
            await self._stop_ws_worker()

    async def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        await self._stop_ws_worker()

    async def _stop_ws_worker(self) -> None:
        started = time.monotonic()
        async with self._worker_lock:
            process = self._ws_process
            packets = self._ws_packets
            if process is not None:
                if process.is_alive():
                    process.terminate()
                await asyncio.to_thread(process.join, 1.0)
                if process.is_alive():
                    process.kill()
                    await asyncio.to_thread(process.join, 1.0)
                if process.is_alive():
                    raise RuntimeError("Feishu WebSocket worker could not be joined")
                process.close()
            self._ws_process = None
            if packets is not None:
                packets.close()
                packets.join_thread()
            self._ws_packets = None
        _emit_feishu_audit(
            "websocket.stop",
            self.api_service.app_id,
            started,
            0,
            DeliveryStatus.CONFIRMED_SUCCESS.value,
        )

    async def send(self, message: OutboundMessage) -> DeliveryReceipt:
        if not self._running:
            return DeliveryReceipt(
                status=DeliveryStatus.CONFIRMED_FAILURE,
                operation="feishu.send",
                api_message="Feishu channel is not running",
                failed_count=1,
            )
        delivery = message.delivery_context
        if delivery is None:
            return DeliveryReceipt(
                status=DeliveryStatus.CONFIRMED_FAILURE,
                operation="feishu.send",
                api_message="typed delivery context is required",
                failed_count=1,
            )
        if message.kind is not ChannelEventKind.MEDIA:
            rendered = render_feishu_text(message.content)
            return await self.api_service.send_message(
                delivery=delivery,
                msg_type=rendered.msg_type,
                content=rendered.content,
                reply_to_message_id=delivery.source_message_id,
            )
        if self.artifact_resolver is None:
            return DeliveryReceipt(
                status=DeliveryStatus.CONFIRMED_FAILURE,
                operation="feishu.media.resolve",
                api_message="artifact resolver is not configured",
                failed_count=len(message.attachments),
            )
        completed_ids: list[str] = []
        for index, ref in enumerate(message.attachments):
            try:
                artifact = self.artifact_resolver.resolve(
                    ref,
                    tenant_id=message.identity.tenant_id,
                    session_id=message.session_id,
                )
                with self._stage_artifact(artifact.content, artifact.filename) as path:
                    receipt = await self.api_service.upload_and_send_artifact(
                        path=path,
                        media_type=artifact.media_type,
                        delivery=delivery,
                    )
            except (ArtifactStoreError, OSError, ValueError) as exc:
                receipt = DeliveryReceipt(
                    status=DeliveryStatus.CONFIRMED_FAILURE,
                    operation="feishu.media.resolve",
                    api_message=type(exc).__name__,
                    failed_count=1,
                )
            if receipt.status is not DeliveryStatus.CONFIRMED_SUCCESS:
                if not completed_ids:
                    return receipt
                return DeliveryReceipt(
                    status=DeliveryStatus.PARTIAL_SUCCESS,
                    operation="feishu.media.send",
                    platform_ids=tuple((*completed_ids, *receipt.platform_ids)),
                    api_code=receipt.api_code,
                    api_message=receipt.api_message,
                    sent_count=index,
                    failed_count=len(message.attachments) - index,
                )
            completed_ids.extend(receipt.platform_ids)
        return DeliveryReceipt(
            status=DeliveryStatus.CONFIRMED_SUCCESS,
            operation="feishu.media.send",
            platform_ids=tuple(completed_ids),
            sent_count=len(message.attachments),
        )

    @contextmanager
    def _stage_artifact(self, content: bytes, filename: str):
        root = self._attachment_root / ".outbound-staging"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        digest = hashlib.sha256(content).hexdigest()
        controlled_name = f"{digest[:20]}-{filename[:180]}"
        path = root / controlled_name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            resolved = path.resolve(strict=True)
            if (
                path.is_symlink()
                or not path.is_file()
                or not resolved.is_relative_to(root.resolve(strict=True))
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest
            ):
                raise ValueError("staged artifact verification failed")
            yield path
        finally:
            path.unlink(missing_ok=True)


def _required_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _queue_packet(packets, timeout_s: float):
    try:
        packet = packets.get(timeout=timeout_s)
    except queue.Empty:
        return None
    return packet if isinstance(packet, Mapping) else None


def _optional_text(value: object) -> str | None:
    normalized = _required_text(value)
    return normalized or None


def _parse_content(message_type: str, raw_content: object) -> str:
    content = _content_mapping(raw_content)
    if message_type == "text":
        return str(content.get("text") or "").strip()
    if message_type == "post":
        lines: list[str] = []
        post_content = content.get("content")
        if isinstance(post_content, list):
            for paragraph in post_content:
                if not isinstance(paragraph, list):
                    continue
                text = "".join(
                    str(item.get("text") or item.get("name") or "")
                    for item in paragraph
                    if isinstance(item, Mapping)
                ).strip()
                if text:
                    lines.append(text)
        return "\n".join(lines)
    if message_type.startswith("share_"):
        label = message_type.removeprefix("share_").replace("_", " ")
        name = str(content.get(f"{message_type.removeprefix('share_')}_name") or "").strip()
        return f"[shared {label}: {name}]" if name else f"[shared {label}]"
    if message_type == "interactive":
        values = [str(content.get(key) or "").strip() for key in ("title", "text")]
        return "\n".join(value for value in values if value)
    return f"[{message_type}]"


def _content_mapping(raw_content: object) -> Mapping[str, object]:
    if isinstance(raw_content, str):
        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError:
            return {"text": raw_content}
        return parsed if isinstance(parsed, Mapping) else {}
    return raw_content if isinstance(raw_content, Mapping) else {}


def render_feishu_text(content: str) -> FeishuRenderedMessage:
    if not isinstance(content, str):
        raise TypeError("Feishu outbound content must be text")
    if "```" in content or _looks_like_markdown_table(content) or len(content) > 2000:
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [{"tag": "markdown", "content": content}],
        }
        return FeishuRenderedMessage(
            "interactive", json.dumps(card, ensure_ascii=False, separators=(",", ":"))
        )
    if "[" in content and "](" in content or "\n" in content:
        post = {
            "zh_cn": {
                "content": [[{"tag": "text", "text": content}]],
            }
        }
        return FeishuRenderedMessage(
            "post", json.dumps(post, ensure_ascii=False, separators=(",", ":"))
        )
    return FeishuRenderedMessage(
        "text", json.dumps({"text": content}, ensure_ascii=False, separators=(",", ":"))
    )


def _looks_like_markdown_table(content: str) -> bool:
    lines = content.splitlines()
    return len(lines) >= 2 and "|" in lines[0] and "---" in lines[1]


__all__ = [
    "FeishuApiError",
    "FeishuApiService",
    "FeishuChannel",
    "FeishuDownload",
    "FeishuRenderedMessage",
    "install_feishu_logging_safety",
    "render_feishu_text",
]
