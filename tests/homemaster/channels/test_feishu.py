from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import queue
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

import homemaster.channels.impl.feishu as feishu_impl
from homemaster.artifacts import ToolOutputArtifactResolver, ToolOutputStore
from homemaster.channels.bus import BoundedPriorityBus
from homemaster.channels.contracts import (
    ChannelDeliveryContext,
    ChannelEventKind,
    ChannelIdentity,
    DeliveryReceipt,
    DeliveryStatus,
    OutboundArtifactRef,
    OutboundMessage,
)
from homemaster.channels.impl.base import BaseChannel
from homemaster.channels.impl.feishu import (
    FeishuApiError,
    FeishuApiService,
    FeishuChannel,
    FeishuDownload,
    install_feishu_logging_safety,
    render_feishu_text,
)
from homemaster.channels.impl.telegram import TelegramChannel
from homemaster.config import FeishuChannelConfig


def _blocking_ws_worker(*_args) -> None:
    while True:
        time.sleep(0.05)


def _fatal_ws_worker(*args) -> None:
    args[-1].put({"type": "fatal", "error_type": "SyntheticWsFailure"})


def _config(**updates) -> FeishuChannelConfig:
    values = {"tenant_id": "tenant-a"}
    values.update(updates)
    return FeishuChannelConfig(**values)


def test_all_concrete_channel_implementations_cover_public_interface() -> None:
    expected_methods = {"start", "stop", "send"}
    for implementation in (FeishuChannel, TelegramChannel):
        assert issubclass(implementation, BaseChannel)
        assert not implementation.__abstractmethods__
        assert expected_methods <= implementation.__dict__.keys()


@pytest.mark.asyncio
async def test_every_sender_maps_to_same_trusted_owner_but_keeps_sender_identity() -> None:
    config = _config(enabled=True)
    channel = FeishuChannel(
        config,
        BoundedPriorityBus(),
        api_service=FeishuApiService(config, app_id="cli_identifier", app_secret="secret"),
    )

    assert await channel.accept_event(
        _event(sender_open_id="ou_first", message_id="om-first", chat_type="private")
    )
    assert await channel.accept_event(
        _event(sender_open_id="ou_second", message_id="om-second", chat_type="private")
    )
    first = await channel.bus.receive_inbound()
    second = await channel.bus.receive_inbound()

    assert first.identity.sender_id == "ou_first"
    assert second.identity.sender_id == "ou_second"
    assert first.principal == second.principal
    assert first.principal.tenant_id == "tenant-a"
    assert first.principal.principal_id == "feishu-owner"
    assert first.principal.roles == ("admin",)
    assert first.principal.capabilities == (
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


@pytest.mark.asyncio
async def test_disabled_feishu_channel_does_not_import_or_start_network() -> None:
    config = _config(enabled=False)
    service = FeishuApiService(config, app_id="", app_secret="")
    channel = FeishuChannel(config, BoundedPriorityBus(), api_service=service)

    await channel.start()
    await channel.stop()

    assert not channel.is_running
    assert not service.client_created


def test_service_repr_never_contains_credentials() -> None:
    service = FeishuApiService(
        _config(),
        app_id="cli_identifier",
        app_secret="raw-app-secret",
        encrypt_key="raw-encrypt-key",
        verification_token="raw-verification-token",
    )

    rendered = repr(service)

    assert "raw-app-secret" not in rendered
    assert "raw-encrypt-key" not in rendered
    assert "raw-verification-token" not in rendered


def test_feishu_dependency_log_filter_redacts_credentials_and_queries(caplog) -> None:
    install_feishu_logging_safety(("raw-app-secret",))
    logger = logging.getLogger("lark_oapi.ws")

    with caplog.at_level(logging.WARNING, logger="lark_oapi.ws"):
        logger.warning(
            "Authorization: Bearer raw-token url=https://example.test/ws?token=raw-app-secret"
        )

    rendered = caplog.text
    assert "raw-app-secret" not in rendered
    assert "raw-token" not in rendered
    assert "?token=" not in rendered


@pytest.mark.asyncio
async def test_p2p_access_is_acknowledged_and_sdk_message_reaches_private_bus(
    tmp_path,
) -> None:
    lark = pytest.importorskip("lark_oapi")
    packets: queue.Queue[dict[str, object]] = queue.Queue()
    handler = feishu_impl._build_feishu_event_handler(
        lark,
        packets,
        encrypt_key="",
        verification_token="",
    )
    payload = {
        "schema": "2.0",
        "header": {
            "event_id": "evt-p2p-entered",
            "event_type": "im.chat.access_event.bot_p2p_chat_entered_v1",
            "create_time": "1784860800000",
            "token": "",
            "app_id": "cli_identifier",
            "tenant_key": "tenant-key",
        },
        "event": {
            "chat_id": "oc-private",
            "operator_id": {"open_id": "ou-owner"},
            "last_message_id": "om-last",
            "last_message_create_time": "1784860799000",
        },
    }

    assert handler._do_without_validation(json.dumps(payload).encode("utf-8")) is None
    assert packets.empty()

    payload["header"] = {
        **payload["header"],
        "event_id": "evt-message-read",
        "event_type": "im.message.message_read_v1",
    }
    payload["event"] = {
        "reader": {
            "reader_id": {"open_id": "ou-owner"},
            "read_time": "1784860801000",
            "tenant_key": "tenant-key",
        },
        "message_id_list": ["om-last"],
    }

    assert handler._do_without_validation(json.dumps(payload).encode("utf-8")) is None
    assert packets.empty()

    payload["header"] = {
        **payload["header"],
        "event_id": "evt-message",
        "event_type": "im.message.receive_v1",
    }
    payload["event"] = {
        "sender": {
            "sender_id": {"open_id": "ou-owner"},
            "sender_type": "user",
        },
        "message": {
            "message_id": "om-message",
            "chat_id": "oc-private",
            "chat_type": "p2p",
            "message_type": "text",
            "content": '{"text":"hello"}',
        },
    }

    assert handler._do_without_validation(json.dumps(payload).encode("utf-8")) is None
    packet = packets.get_nowait()
    assert packet["type"] == "message"
    assert packet["payload"]["message_id"] == "om-message"
    assert packets.empty()

    config = _config(attachment_root=tmp_path)
    channel = FeishuChannel(
        config,
        BoundedPriorityBus(),
        api_service=FeishuApiService(
            config,
            app_id="cli_identifier",
            app_secret="secret",
        ),
    )
    assert await channel.accept_event(packet["payload"])
    inbound = await channel.bus.receive_inbound()
    assert inbound.identity.chat_type == "private"
    assert inbound.delivery_context is not None
    assert inbound.delivery_context.chat_type == "private"
    assert inbound.delivery_context.receive_id_type == "open_id"
    assert inbound.delivery_context.receive_id == "ou-owner"


@pytest.mark.asyncio
async def test_feishu_api_audit_is_structured_and_hashes_target(caplog) -> None:
    service = FeishuApiService(_config(), app_id="cli_identifier", app_secret="secret")
    service._send_message_sync = Mock(
        return_value=DeliveryReceipt(
            status=DeliveryStatus.CONFIRMED_SUCCESS,
            operation="feishu.message.send",
            api_code=0,
            sent_count=1,
        )
    )
    delivery = ChannelDeliveryContext(
        receive_id_type="chat_id",
        receive_id="oc-sensitive-chat",
        source_message_id="om-sensitive-message",
        chat_type="group",
    )

    with caplog.at_level(logging.INFO, logger="homemaster.feishu.audit"):
        await service.send_message(
            delivery=delivery,
            msg_type="text",
            content='{"text":"sensitive body"}',
            reply_to_message_id=None,
        )

    payload = json.loads(caplog.records[-1].message)
    duration_ms = payload.pop("duration_ms")
    assert duration_ms >= 0
    assert payload == {
        "action": "message.send",
        "target_hash": hashlib.sha256(b"oc-sensitive-chat").hexdigest()[:16],
        "return_code": 0,
        "certainty": "confirmed_success",
    }
    assert "oc-sensitive-chat" not in caplog.text
    assert "sensitive body" not in caplog.text


@pytest.mark.asyncio
async def test_ws_subprocess_is_terminated_and_joined_on_stop(tmp_path) -> None:
    config = _config(enabled=True, attachment_root=tmp_path)
    service = FeishuApiService(config, app_id="cli_identifier", app_secret="secret")
    service.ensure_rest_client = Mock(return_value=object())
    channel = FeishuChannel(
        config,
        BoundedPriorityBus(),
        api_service=service,
        ws_worker=_blocking_ws_worker,
    )

    start = asyncio.create_task(channel.start())
    for _ in range(100):
        if channel.worker_alive:
            break
        await asyncio.sleep(0.01)
    assert channel.worker_alive

    await channel.stop()
    await asyncio.wait_for(start, timeout=2)

    assert not channel.worker_alive


@pytest.mark.asyncio
async def test_ws_subprocess_fatal_packet_propagates_from_channel_start(tmp_path) -> None:
    config = _config(enabled=True, attachment_root=tmp_path)
    service = FeishuApiService(config, app_id="cli_identifier", app_secret="secret")
    service.ensure_rest_client = Mock(return_value=object())
    channel = FeishuChannel(
        config,
        BoundedPriorityBus(),
        api_service=service,
        ws_worker=_fatal_ws_worker,
    )

    with pytest.raises(RuntimeError, match="SyntheticWsFailure"):
        await channel.start()
    await channel.stop()
    assert not channel.worker_alive


def _event(**updates) -> dict[str, object]:
    event: dict[str, object] = {
        "event_id": "evt-1",
        "sender_open_id": "ou_owner",
        "message_id": "om-1",
        "chat_id": "oc-group",
        "chat_type": "group",
        "message_type": "text",
        "content": {"text": "hello"},
        "mentions": [{"id": {"open_id": "ou_bot"}, "name": "HomeMaster"}],
    }
    event.update(updates)
    return event


@pytest.mark.asyncio
async def test_inbound_audit_records_policy_and_dedup_without_raw_ids(caplog) -> None:
    config = _config(enabled=True)
    service = FeishuApiService(config, app_id="cli_identifier", app_secret="secret")
    channel = FeishuChannel(config, BoundedPriorityBus(), api_service=service)

    with caplog.at_level(logging.INFO, logger="homemaster.feishu.audit"):
        assert await channel.accept_event(_event(sender_open_id="ou_anyone"))
        assert not await channel.accept_event(
            _event(sender_open_id="ou_bot", message_id="om-bot", sender_type="bot")
        )
        assert not await channel.accept_event(_event(event_id="evt-reconnect"))

    payloads = [json.loads(record.message) for record in caplog.records]
    actions = [(item["action"], item["certainty"]) for item in payloads]
    assert ("event.principal", "accepted") in actions
    assert ("event.principal", "rejected") in actions
    assert ("event.claim", "accepted") in actions
    assert ("event.claim", "duplicate") in actions
    assert ("event.publish", "confirmed_success") in actions
    assert "ou_anyone" not in caplog.text
    assert "ou_bot" not in caplog.text
    assert "om-1" not in caplog.text


@pytest.mark.asyncio
async def test_group_text_maps_typed_delivery_context_and_deduplicates_message_id() -> None:
    config = _config(enabled=True)
    service = FeishuApiService(config, app_id="cli_identifier", app_secret="secret")
    channel = FeishuChannel(config, BoundedPriorityBus(), api_service=service)

    assert await channel.accept_event(_event())
    assert not await channel.accept_event(_event(event_id="evt-reconnect"))
    inbound = await channel.bus.receive_inbound()

    assert inbound.content == "hello"
    assert inbound.correlation_id == "om-1"
    assert inbound.identity.chat_id == "oc-group"
    assert inbound.identity.thread_id is None
    assert inbound.delivery_context is not None
    assert inbound.delivery_context.receive_id_type == "chat_id"
    assert inbound.delivery_context.receive_id == "oc-group"
    assert inbound.delivery_context.source_message_id == "om-1"
    assert channel.bus.inbound_size == 0


@pytest.mark.asyncio
async def test_reaction_runs_only_after_authorization_policy_and_dedup_claim() -> None:
    config = _config(enabled=True)
    service = FeishuApiService(config, app_id="cli_identifier", app_secret="secret")
    service.add_reaction = AsyncMock(
        return_value=DeliveryReceipt(
            status=DeliveryStatus.CONFIRMED_SUCCESS,
            operation="feishu.reaction.add",
            platform_ids=("reaction-1",),
            sent_count=1,
        )
    )
    channel = FeishuChannel(config, BoundedPriorityBus(), api_service=service)
    channel._running = True

    assert not await channel.accept_event(_event(sender_open_id="", message_id="om-malformed"))
    assert not await channel.accept_event(_event(message_id="om-bot", sender_type="bot"))
    assert await channel.accept_event(_event(message_id="om-react", mentions=[]))
    assert not await channel.accept_event(
        _event(message_id="om-react", event_id="evt-repeat", mentions=[])
    )

    service.add_reaction.assert_awaited_once_with("om-react", "EYES")


@pytest.mark.asyncio
async def test_group_messages_do_not_require_any_bot_mention() -> None:
    config = _config(enabled=True)
    channel = FeishuChannel(
        config,
        BoundedPriorityBus(),
        api_service=FeishuApiService(config, app_id="cli_identifier", app_secret="secret"),
    )

    assert await channel.accept_event(
        _event(message_id="om-text", content={"text": "@HomeMaster hello"}, mentions=[])
    )
    assert await channel.accept_event(
        _event(
            message_id="om-wrong",
            mentions=[{"id": {"open_id": "ou_same_name"}, "name": "HomeMaster"}],
        )
    )
    assert channel.bus.inbound_size == 2


@pytest.mark.asyncio
async def test_private_thread_mapping_uses_sender_address_and_source_message() -> None:
    config = _config()
    channel = FeishuChannel(
        config,
        BoundedPriorityBus(),
        api_service=FeishuApiService(config, app_id="cli_identifier", app_secret="secret"),
    )

    assert await channel.accept_event(
        _event(
            message_id="om-private",
            chat_type="private",
            chat_id="oc-sdk-private",
            thread_id="omt-thread",
            root_id="om-root",
            mentions=[],
        )
    )
    inbound = await channel.bus.receive_inbound()

    assert inbound.identity.chat_id == "ou_owner"
    assert inbound.identity.thread_id == "omt-thread"
    assert inbound.delivery_context is not None
    assert inbound.delivery_context.receive_id_type == "open_id"
    assert inbound.delivery_context.receive_id == "ou_owner"
    assert inbound.delivery_context.root_id == "om-root"
    assert inbound.delivery_context.thread_id == "omt-thread"


@pytest.mark.parametrize(
    ("message_type", "content", "expected"),
    [
        ("post", {"content": [[{"tag": "text", "text": "rich"}]]}, "rich"),
        ("share_chat", {"chat_name": "operators"}, "[shared chat: operators]"),
        ("interactive", {"title": "Approval", "text": "Confirm"}, "Approval\nConfirm"),
    ],
)
@pytest.mark.asyncio
async def test_supported_non_text_content_is_normalized(message_type, content, expected) -> None:
    config = _config()
    channel = FeishuChannel(
        config,
        BoundedPriorityBus(),
        api_service=FeishuApiService(config, app_id="cli_identifier", app_secret="secret"),
    )

    assert await channel.accept_event(
        _event(
            message_id=f"om-{message_type}",
            chat_type="private",
            message_type=message_type,
            content=content,
            mentions=[],
        )
    )
    assert (await channel.bus.receive_inbound()).content == expected


@pytest.mark.asyncio
async def test_bot_media_sender_is_rejected_before_download(tmp_path) -> None:
    config = _config(attachment_root=tmp_path)
    service = FeishuApiService(config, app_id="cli_identifier", app_secret="secret")
    service.download_message_resource = AsyncMock()
    channel = FeishuChannel(config, BoundedPriorityBus(), api_service=service)

    assert not await channel.accept_event(
        _event(
            sender_open_id="ou_unknown",
            message_id="om-denied-file",
            sender_type="bot",
            chat_type="private",
            message_type="file",
            content={"file_key": "file-denied"},
            mentions=[],
        )
    )

    service.download_message_resource.assert_not_awaited()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_authorized_file_download_is_persisted_once_under_attachment_root(tmp_path) -> None:
    config = _config(attachment_root=tmp_path)
    service = FeishuApiService(config, app_id="cli_identifier", app_secret="secret")
    service.download_message_resource = AsyncMock(
        return_value=FeishuDownload(b"payload", "report.pdf", api_code=0)
    )
    channel = FeishuChannel(config, BoundedPriorityBus(), api_service=service)
    event = _event(
        message_id="om-file",
        chat_type="private",
        message_type="file",
        content={"file_key": "file-key"},
        mentions=[],
    )

    assert await channel.accept_event(event)
    assert not await channel.accept_event(event)
    inbound = await channel.bus.receive_inbound()

    assert len(inbound.attachments) == 1
    saved = tmp_path / inbound.attachments[0].split("/")[-1]
    assert saved.resolve().is_relative_to(tmp_path.resolve())
    assert saved.is_file() and not saved.is_symlink()
    assert saved.read_bytes() == b"payload"
    service.download_message_resource.assert_awaited_once_with("om-file", "file-key", "file")


@pytest.mark.asyncio
async def test_transient_download_failure_releases_dedup_claim_for_redelivery(tmp_path) -> None:
    config = _config(attachment_root=tmp_path)
    service = FeishuApiService(config, app_id="cli_identifier", app_secret="secret")
    service.download_message_resource = AsyncMock(
        side_effect=(
            FeishuApiError("message.resource.download", code=503, message="retry"),
            FeishuDownload(b"payload", "report.pdf", api_code=0),
        )
    )
    channel = FeishuChannel(config, BoundedPriorityBus(), api_service=service)
    event = _event(
        message_id="om-retry-file",
        chat_type="private",
        message_type="file",
        content={"file_key": "file-key"},
        mentions=[],
    )

    assert not await channel.accept_event(event)
    assert await channel.accept_event(event)
    assert not await channel.accept_event(event)
    inbound = await channel.bus.receive_inbound()

    assert len(inbound.attachments) == 1
    assert (tmp_path / Path(inbound.attachments[0]).name).read_bytes() == b"payload"
    assert service.download_message_resource.await_count == 2


@pytest.mark.asyncio
async def test_inbound_bus_rejection_releases_dedup_claim_for_redelivery() -> None:
    config = _config()
    bus = BoundedPriorityBus()
    original_publish = bus.publish_inbound
    attempts = 0

    async def flaky_publish(message):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return False
        return await original_publish(message)

    bus.publish_inbound = AsyncMock(side_effect=flaky_publish)
    channel = FeishuChannel(
        config,
        bus,
        api_service=FeishuApiService(config, app_id="cli_identifier", app_secret="secret"),
    )
    event = _event(message_id="om-retry-publish", chat_type="private")

    assert not await channel.accept_event(event)
    assert await channel.accept_event(event)
    assert not await channel.accept_event(event)
    assert (await bus.receive_inbound()).correlation_id == "om-retry-publish"
    assert attempts == 2


@pytest.mark.asyncio
async def test_download_rejects_traversal_empty_content_and_symlink_target(tmp_path) -> None:
    config = _config(attachment_root=tmp_path)
    service = FeishuApiService(config, app_id="cli_identifier", app_secret="secret")
    channel = FeishuChannel(config, BoundedPriorityBus(), api_service=service)

    service.download_message_resource = AsyncMock(
        return_value=FeishuDownload(b"payload", "../escape.txt", api_code=0)
    )
    assert not await channel.accept_event(
        _event(
            message_id="om-traversal",
            chat_type="private",
            message_type="file",
            content={"file_key": "traversal-key"},
            mentions=[],
        )
    )

    service.download_message_resource = AsyncMock(
        return_value=FeishuDownload(b"", "empty.txt", api_code=0)
    )
    assert not await channel.accept_event(
        _event(
            message_id="om-empty",
            chat_type="private",
            message_type="file",
            content={"file_key": "empty-key"},
            mentions=[],
        )
    )
    assert channel.bus.inbound_size == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_download_never_follows_preexisting_symlink(tmp_path) -> None:
    config = _config(attachment_root=tmp_path)
    service = FeishuApiService(config, app_id="cli_identifier", app_secret="secret")
    service.download_message_resource = AsyncMock(
        return_value=FeishuDownload(b"replacement", "safe.txt", api_code=0)
    )
    channel = FeishuChannel(config, BoundedPriorityBus(), api_service=service)
    digest = hashlib.sha256(b"om-symlink:file-key").hexdigest()[:20]
    target = tmp_path / "owned-target.txt"
    target.write_bytes(b"original")
    (tmp_path / f"{digest}-safe.txt").symlink_to(target)

    accepted = await channel.accept_event(
        _event(
            message_id="om-symlink",
            chat_type="private",
            message_type="file",
            content={"file_key": "file-key"},
            mentions=[],
        )
    )

    assert accepted is False
    assert target.read_bytes() == b"original"


@pytest.mark.parametrize(
    ("content", "expected_type"),
    [
        ("short plain text", "text"),
        ("See [HomeMaster](https://example.test)", "post"),
        ("```python\nprint('hello')\n```", "interactive"),
        ("| name | value |\n| --- | --- |\n| a | 1 |", "interactive"),
    ],
)
def test_static_renderer_selects_locked_message_type(content, expected_type) -> None:
    rendered = render_feishu_text(content)

    assert rendered.msg_type == expected_type
    assert content.splitlines()[0].split("[")[0].strip() in rendered.content


@pytest.mark.asyncio
async def test_text_send_replies_using_source_message_id_from_delivery_context(tmp_path) -> None:
    config = _config(attachment_root=tmp_path)
    service = FeishuApiService(config, app_id="cli_identifier", app_secret="secret")
    success = DeliveryReceipt(
        status=DeliveryStatus.CONFIRMED_SUCCESS,
        operation="feishu.message.send",
        platform_ids=("om-out",),
        sent_count=1,
    )
    service.send_message = AsyncMock(return_value=success)
    channel = FeishuChannel(config, BoundedPriorityBus(), api_service=service)
    channel._running = True
    delivery = ChannelDeliveryContext(
        receive_id_type="chat_id",
        receive_id="oc-group",
        source_message_id="om-source",
        root_id="om-root",
        thread_id="omt-thread",
        chat_type="group",
    )
    message = OutboundMessage(
        identity=ChannelIdentity(
            "tenant-a", "feishu", "oc-group", "ou_owner", "omt-thread", "group"
        ),
        session_id="session-a",
        generation=1,
        kind=ChannelEventKind.FINAL,
        content="done",
        correlation_id="corr-text",
        delivery_context=delivery,
    )

    assert await channel.send(message) is success
    service.send_message.assert_awaited_once()
    call = service.send_message.await_args.kwargs
    assert call["delivery"] is delivery
    assert call["reply_to_message_id"] == "om-source"


@pytest.mark.asyncio
async def test_media_reads_exact_artifact_partition_and_cleans_staging(tmp_path) -> None:
    store = ToolOutputStore(tmp_path / "store", quota_bytes=4096, ttl_seconds=60)
    stored = store.write(
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-media",
        content=b"image-bytes",
        media_type="image/png",
    )
    ref = OutboundArtifactRef(
        artifact_handle=stored.handle,
        run_id="run-media",
        filename="result.png",
        media_type="image/png",
        content_sha256=stored.content_sha256,
    )
    config = _config(attachment_root=tmp_path / "attachments")
    service = FeishuApiService(config, app_id="cli_identifier", app_secret="secret")
    observed_paths = []

    async def upload(*, path, media_type, delivery):
        observed_paths.append(path)
        assert path.is_file() and path.read_bytes() == b"image-bytes"
        assert media_type == "image/png"
        assert delivery.source_message_id == "om-source"
        return DeliveryReceipt(
            status=DeliveryStatus.CONFIRMED_SUCCESS,
            operation="feishu.media.send",
            platform_ids=("om-media",),
            sent_count=1,
        )

    service.upload_and_send_artifact = AsyncMock(side_effect=upload)
    channel = FeishuChannel(
        config,
        BoundedPriorityBus(),
        api_service=service,
        artifact_resolver=ToolOutputArtifactResolver(store),
    )
    channel._running = True
    delivery = ChannelDeliveryContext("chat_id", "oc-group", "om-source", chat_type="group")
    message = OutboundMessage(
        identity=ChannelIdentity("tenant-a", "feishu", "oc-group", "ou_owner", chat_type="group"),
        session_id="session-a",
        generation=1,
        kind=ChannelEventKind.MEDIA,
        content="result.png",
        correlation_id="corr-media",
        attachments=(ref,),
        delivery_context=delivery,
    )

    receipt = await channel.send(message)

    assert receipt.status is DeliveryStatus.CONFIRMED_SUCCESS
    assert len(observed_paths) == 1
    assert not observed_paths[0].exists()
