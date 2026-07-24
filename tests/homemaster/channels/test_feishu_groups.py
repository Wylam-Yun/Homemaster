from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from homemaster.channels.contracts import ChannelIdentity
from homemaster.channels.feishu_groups import (
    FeishuChatState,
    FeishuGroupApiResult,
    FeishuGroupOperations,
    GroupOutcomeCertainty,
    build_feishu_group_tools,
)
from homemaster.channels.impl.feishu import FeishuApiService
from homemaster.config import FeishuChannelConfig


def _operations() -> tuple[FeishuGroupOperations, FeishuApiService]:
    config = FeishuChannelConfig()
    service = FeishuApiService(config, app_id="cli_identifier", app_secret="secret")
    return FeishuGroupOperations(service), service


@pytest.mark.asyncio
async def test_create_derives_member_from_bound_sender_and_verifies_external_state() -> None:
    operations, service = _operations()
    operations.bind(
        "session-a",
        ChannelIdentity("tenant-a", "feishu", "ou-owner", "ou-owner"),
        generation=3,
    )
    service.create_group = AsyncMock(
        return_value=FeishuGroupApiResult(True, 0, "ok", chat_id="oc-created")
    )
    service.get_chat = AsyncMock(
        return_value=FeishuChatState("oc-created", "Operators", ("ou-owner",))
    )

    receipt = await operations.create(
        session_id="session-a",
        operation_id="call-create",
        name=" Operators ",
    )

    assert receipt.verified_state
    assert receipt.chat_id == "oc-created"
    assert receipt.outcome_certainty is GroupOutcomeCertainty.CONFIRMED
    service.create_group.assert_awaited_once_with(
        member_open_id="ou-owner",
        name="Operators",
        operation_id="call-create",
    )


@pytest.mark.asyncio
async def test_create_timeout_is_outcome_unknown_and_operation_is_not_retried() -> None:
    operations, service = _operations()
    operations.bind(
        "session-a",
        ChannelIdentity("tenant-a", "feishu", "ou-owner", "ou-owner"),
        generation=1,
    )
    service.create_group = AsyncMock(side_effect=TimeoutError)

    first = await operations.create(
        session_id="session-a", operation_id="call-timeout", name="Operators"
    )
    second = await operations.create(
        session_id="session-a", operation_id="call-timeout", name="Operators"
    )

    assert first is second
    assert first.outcome_certainty is GroupOutcomeCertainty.UNKNOWN
    service.create_group.assert_awaited_once()


@pytest.mark.asyncio
async def test_rename_uses_current_group_binding_and_private_route_never_calls_api() -> None:
    operations, service = _operations()
    service.rename_group = AsyncMock(
        return_value=FeishuGroupApiResult(True, 0, "ok", chat_id="oc-current")
    )
    service.get_chat = AsyncMock(
        return_value=FeishuChatState("oc-current", "New name", ("ou-owner",))
    )
    operations.bind(
        "session-group",
        ChannelIdentity("tenant-a", "feishu", "oc-current", "ou-owner", chat_type="group"),
        generation=2,
    )

    receipt = await operations.rename(
        session_id="session-group", operation_id="call-rename", name="New name"
    )

    assert receipt.chat_id == "oc-current" and receipt.verified_state
    service.rename_group.assert_awaited_once_with(chat_id="oc-current", name="New name")

    operations.bind(
        "session-private",
        ChannelIdentity("tenant-a", "feishu", "ou-owner", "ou-owner"),
        generation=1,
    )
    with pytest.raises(ValueError, match="group route"):
        await operations.rename(
            session_id="session-private", operation_id="call-private", name="Denied"
        )
    assert service.rename_group.await_count == 1


def test_group_tools_accept_only_name_and_declare_exact_capabilities() -> None:
    operations, _service = _operations()
    create, rename = build_feishu_group_tools(operations)

    assert create.definition.required_capabilities == ("channel.feishu.group.create",)
    assert rename.definition.required_capabilities == ("channel.feishu.group.rename",)
    assert set(create.definition.input_schema["properties"]) == {"name"}
    assert set(rename.definition.input_schema["properties"]) == {"name"}
    assert create.definition.state_effects == ("channel.group.create",)
    assert rename.definition.state_effects == ("channel.group.rename",)
