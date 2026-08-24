from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from homemaster.agent.messages import UserMessage
from homemaster.memory.management import (
    MemoryManagementService,
    MemoryNotFoundError,
    MemoryStats,
)

NOW = datetime(2026, 8, 24, 8, 30, tzinfo=UTC)


def raw(
    memory_id: str,
    *,
    session_id: str | None,
    status: str,
    content: str,
    memory_type: str = "fact",
    metadata: dict[str, Any] | None = None,
    created_at: datetime | None = None,
    update_at: datetime | None = None,
    status_changed_at: datetime | None = None,
    parent_ids: list[str] | None = None,
    root_id: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        memory_id=memory_id,
        session_id=session_id,
        status=status,
        content=content,
        mem_type=memory_type,
        metadata=metadata or {},
        created_at=created_at,
        update_at=update_at,
        status_changed_at=status_changed_at,
        parent_ids=parent_ids or [],
        root_id=root_id or [],
    )


class FakeMindMemOS:
    def __init__(
        self,
        rows: list[SimpleNamespace],
        history: list[SimpleNamespace] | None = None,
    ) -> None:
        self.rows = rows
        self.history_rows = history if history is not None else rows
        self.list_contexts: list[Any] = []
        self.history_calls: list[tuple[str, Any]] = []

    async def list_raw_memories(self, context: Any) -> list[SimpleNamespace]:
        self.list_contexts.append(context)
        return self.rows

    async def get_history(
        self, memory_id: str, context: Any
    ) -> list[SimpleNamespace]:
        self.history_calls.append((memory_id, context))
        return self.history_rows


class FakeSessions:
    def __init__(self, messages: dict[str, list[Any]]) -> None:
        self.messages = messages
        self.read_ids: list[str] = []

    def read_session_messages(self, session_id: str) -> tuple[Any, ...]:
        self.read_ids.append(session_id)
        if session_id not in self.messages:
            raise KeyError(session_id)
        return tuple(self.messages[session_id])


@pytest.mark.asyncio
async def test_snapshot_counts_groups_titles_and_unassigned() -> None:
    mindmemos = FakeMindMemOS(
        [
            raw(
                "m1",
                session_id="s1",
                status="active",
                content="alpha",
                update_at=NOW,
            ),
            raw(
                "m2",
                session_id="s1",
                status="archived",
                content="beta",
                update_at=NOW - timedelta(minutes=1),
            ),
            raw(
                "m3",
                session_id=None,
                status="active",
                content="gamma",
                update_at=NOW - timedelta(minutes=2),
            ),
        ]
    )
    sessions = FakeSessions({"s1": [UserMessage.from_text("first user request")]})
    service = MemoryManagementService(mindmemos=mindmemos, sessions=sessions)

    snapshot = await service.snapshot(tenant_id="local")

    assert snapshot.stats == MemoryStats(2, 1, 3, 1)
    assert [(group.session_id, group.title) for group in snapshot.groups] == [
        ("s1", "first user request"),
        (None, "未关联会话"),
    ]
    assert snapshot.groups[0].active_count == 1
    assert snapshot.groups[0].archived_count == 1
    assert mindmemos.list_contexts[0].project_id == "local"


@pytest.mark.asyncio
async def test_snapshot_falls_back_title_and_sorts_deterministically() -> None:
    service = MemoryManagementService(
        mindmemos=FakeMindMemOS(
            [
                raw(
                    "m-b",
                    session_id="abcdef123456",
                    status="active",
                    content="later id",
                    update_at=NOW,
                ),
                raw(
                    "m-a",
                    session_id="abcdef123456",
                    status="active",
                    content="earlier id",
                    update_at=NOW,
                ),
            ]
        ),
        sessions=FakeSessions({}),
    )

    snapshot = await service.snapshot(tenant_id="tenant-a")

    assert snapshot.groups[0].title == "会话 abcdef12"
    assert [item.memory_id for item in snapshot.groups[0].memories] == ["m-a", "m-b"]


@pytest.mark.asyncio
async def test_snapshot_projects_archive_type_and_safe_structured_record() -> None:
    record = {
        "schema_version": 1,
        "memory_type": "fact",
        "subject": {"type": "object", "name": "钥匙"},
        "predicate": "location",
        "value": "抽屉",
        "source": "user_statement",
    }
    service = MemoryManagementService(
        mindmemos=FakeMindMemOS(
            [
                raw(
                    "valid",
                    session_id=None,
                    status="archived",
                    content="visible",
                    memory_type="native_custom",
                    metadata={
                        "delete_reason": "user_request",
                        "request_metadata": {
                            "record_metadata": [{"record_json": json.dumps(record)}]
                        },
                    },
                    status_changed_at=NOW,
                    parent_ids=["older"],
                ),
                raw(
                    "invalid",
                    session_id=None,
                    status="active",
                    content="still visible",
                    metadata={"request_metadata": {"record_json": "not-json"}},
                ),
            ]
        ),
        sessions=FakeSessions({}),
    )

    memories = (await service.snapshot(tenant_id="local")).groups[0].memories
    valid = next(item for item in memories if item.memory_id == "valid")
    invalid = next(item for item in memories if item.memory_id == "invalid")

    assert valid.memory_type == "native_custom"
    assert valid.memory_type_label == "native_custom"
    assert valid.archive_reason == "user_request"
    assert valid.archived_at == NOW
    assert valid.record == record
    assert valid.structure_status == "valid"
    assert valid.has_history is True
    assert invalid.content == "still visible"
    assert invalid.record is None
    assert invalid.structure_status == "invalid"


@pytest.mark.asyncio
async def test_history_forwards_tenant_scope_and_missing_is_not_found() -> None:
    mindmemos = FakeMindMemOS(
        [],
        history=[raw("m1", session_id=None, status="active", content="v1")],
    )
    service = MemoryManagementService(mindmemos=mindmemos, sessions=FakeSessions({}))

    versions = await service.history("m1", tenant_id="tenant-a")

    assert [item.memory_id for item in versions] == ["m1"]
    assert mindmemos.history_calls[0][0] == "m1"
    assert mindmemos.history_calls[0][1].project_id == "tenant-a"

    mindmemos.history_rows = []
    with pytest.raises(MemoryNotFoundError):
        await service.history("missing", tenant_id="tenant-a")
