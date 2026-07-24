from __future__ import annotations

import asyncio

import pytest

from homemaster.channels.bus import BoundedPriorityBus, BusClosedError
from homemaster.channels.contracts import (
    ChannelEventKind,
    ChannelIdentity,
    OutboundArtifactRef,
    OutboundMessage,
)


@pytest.mark.parametrize("prefix", ["_", "-"])
def test_outbound_artifact_accepts_urlsafe_store_token_prefixes(prefix) -> None:
    artifact = OutboundArtifactRef(
        artifact_handle=f"hm-artifact:{prefix}{'a' * 42}",
        run_id="run-a",
        filename="result.bin",
        media_type="application/octet-stream",
        content_sha256="0" * 64,
    )

    assert artifact.artifact_handle.startswith(f"hm-artifact:{prefix}")


def _out(
    kind: ChannelEventKind,
    sequence: int,
    *,
    session: str = "session-a",
    tenant: str = "tenant-a",
) -> OutboundMessage:
    return OutboundMessage(
        identity=ChannelIdentity(tenant, "telegram", "chat-a", "sender-a"),
        session_id=session,
        generation=1,
        kind=kind,
        content=f"{kind.value}-{sequence}",
        correlation_id=f"corr-{sequence}",
    )


@pytest.mark.asyncio
async def test_progress_flood_is_coalesced_and_critical_events_are_retained() -> None:
    bus = BoundedPriorityBus(capacity=8, per_session_capacity=4)
    for index in range(1000):
        assert await bus.publish_outbound(_out(ChannelEventKind.PROGRESS, index))

    for index, kind in enumerate(
        (ChannelEventKind.FINAL, ChannelEventKind.ERROR, ChannelEventKind.CANCEL),
        start=1000,
    ):
        assert await bus.publish_outbound(_out(kind, index))

    items = [await bus.receive_outbound() for _ in range(bus.outbound_size)]
    kinds = [item.kind for item in items]
    assert kinds.count(ChannelEventKind.PROGRESS) <= 1
    assert ChannelEventKind.FINAL in kinds
    assert ChannelEventKind.ERROR in kinds
    assert ChannelEventKind.CANCEL in kinds


@pytest.mark.asyncio
async def test_media_is_never_coalesced_or_evicted_and_each_artifact_is_retained() -> None:
    bus = BoundedPriorityBus(capacity=4, per_session_capacity=4)
    for index in range(20):
        assert await bus.publish_outbound(_out(ChannelEventKind.PROGRESS, index))
    for index in range(2):
        artifact = OutboundArtifactRef(
            artifact_handle=f"hm-artifact:{'a' * 31}{index}",
            run_id="run-media",
            filename=f"file-{index}.bin",
            media_type="application/octet-stream",
            content_sha256="b" * 64,
        )
        message = _out(ChannelEventKind.FINAL, 100 + index)
        message = OutboundMessage(
            identity=message.identity,
            session_id=message.session_id,
            generation=message.generation,
            kind=ChannelEventKind.MEDIA,
            content=message.content,
            correlation_id=message.correlation_id,
            attachments=(artifact,),
        )
        assert await bus.publish_outbound(message)

    items = [await bus.receive_outbound() for _ in range(bus.outbound_size)]
    media = [item for item in items if item.kind is ChannelEventKind.MEDIA]

    assert len(media) == 2
    assert [item.attachments[0].filename for item in media] == ["file-0.bin", "file-1.bin"]


@pytest.mark.asyncio
async def test_full_critical_queue_backpressures_producer_until_consumer_advances() -> None:
    bus = BoundedPriorityBus(capacity=2, per_session_capacity=2)
    await bus.publish_outbound(_out(ChannelEventKind.FINAL, 1))
    await bus.publish_outbound(_out(ChannelEventKind.ERROR, 2))
    blocked = asyncio.create_task(bus.publish_outbound(_out(ChannelEventKind.CANCEL, 3)))
    await asyncio.sleep(0.02)
    assert not blocked.done()

    await bus.receive_outbound()
    assert await asyncio.wait_for(blocked, timeout=1)


@pytest.mark.asyncio
async def test_tenant_quota_prevents_one_tenant_from_consuming_global_capacity() -> None:
    bus = BoundedPriorityBus(
        capacity=6,
        per_tenant_capacity=3,
        per_session_capacity=3,
    )
    for index in range(3):
        await bus.publish_outbound(_out(ChannelEventKind.FINAL, index))
    blocked = asyncio.create_task(
        bus.publish_outbound(_out(ChannelEventKind.ERROR, 4, session="session-b"))
    )
    await asyncio.sleep(0.02)
    assert not blocked.done()

    assert await bus.publish_outbound(_out(ChannelEventKind.FINAL, 5, tenant="tenant-b"))
    await bus.receive_outbound()
    assert await asyncio.wait_for(blocked, timeout=1)


@pytest.mark.asyncio
async def test_shutdown_drains_before_deadline_and_rejects_new_producers() -> None:
    bus = BoundedPriorityBus(capacity=2)
    await bus.publish_outbound(_out(ChannelEventKind.FINAL, 1))
    closing = asyncio.create_task(bus.aclose(deadline_s=1))
    await asyncio.sleep(0)
    with pytest.raises(BusClosedError):
        await bus.publish_outbound(_out(ChannelEventKind.FINAL, 2))
    await bus.receive_outbound()
    assert await closing is True
