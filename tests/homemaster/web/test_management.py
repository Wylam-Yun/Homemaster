from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from homemaster.agent.messages import UserMessage
from homemaster.memory.mindmemos_client import (
    MindMemOSClientError,
    MutationReceipt,
    SearchHit,
    SearchReceipt,
)
from homemaster.web.memory_views import (
    MemoryManagementService,
    MemoryNotFoundError,
    MemoryStats,
    MemoryUnavailableError,
)

NOW = datetime(2026, 8, 24, 8, 30, tzinfo=UTC)


def row(
    memory_id: str,
    *,
    session_id: str | None,
    status: str,
    content: str,
    memory_type: str = "fact",
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
    update_at: str | None = None,
    status_changed_at: str | None = None,
    parent_ids: list[str] | None = None,
    root_id: list[str] | None = None,
    structured_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Server-side memory dict as returned by list/scroll/get payloads."""

    payload: dict[str, Any] = {
        "memory_id": memory_id,
        "session_id": session_id,
        "status": status,
        "content": content,
        "mem_type": memory_type,
        "metadata": metadata or {},
        "created_at": created_at,
        "updated_at": update_at,
        "status_changed_at": status_changed_at,
        "parent_ids": parent_ids or [],
        "root_id": root_id or [],
    }
    if structured_record is not None:
        payload["structured_record"] = structured_record
    return payload


class FakeMindMemOSClient:
    """Duck-typed stand-in for ``MindMemOSClient`` returning dict payloads."""

    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        history: dict[str, list[dict[str, Any]]] | None = None,
        search_hits: list[SearchHit] | None = None,
    ) -> None:
        self.rows = {str(item.get("memory_id") or item.get("id")): item for item in rows}
        self.history = history or {}
        self.search_hits = list(search_hits or [])
        self.scroll_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.delete_calls: list[str] = []
        self.search_calls: list[dict[str, Any]] = []
        self.create_error: Exception | None = None
        self.update_error: Exception | None = None
        self.delete_error: Exception | None = None
        self.scroll_error: Exception | None = None

    async def scroll_memories(
        self,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        self.scroll_calls.append({"filters": filters, "limit": limit, "cursor": cursor})
        if self.scroll_error is not None:
            raise self.scroll_error
        return {"memories": list(self.rows.values()), "next_cursor": None}

    async def list_memories(
        self,
        *,
        filters: dict[str, Any] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        return {"memories": list(self.rows.values()), "total": len(self.rows)}

    async def get_memory(self, *, filters: dict[str, Any]) -> dict[str, Any]:
        self.get_calls.append(dict(filters))
        memory_id = (filters or {}).get("memory_id")
        row = self.rows.get(memory_id)
        if row is None:
            return {"memories": []}
        payload: dict[str, Any] = {"memories": [row]}
        versions = self.history.get(memory_id)
        if versions:
            payload["history"] = versions
        return payload

    async def create_memory(
        self,
        *,
        operation_id: str,
        content: str,
        mem_type: str = "fact",
        metadata: dict[str, Any] | None = None,
    ) -> MutationReceipt:
        self.create_calls.append(
            {
                "operation_id": operation_id,
                "content": content,
                "mem_type": mem_type,
                "metadata": metadata,
            }
        )
        if self.create_error is not None:
            raise self.create_error
        memory_id = f"mem-{len(self.rows) + 1:03d}"
        self.rows[memory_id] = row(
            memory_id,
            session_id=None,
            status="active",
            content=content,
            memory_type=mem_type,
        )
        return MutationReceipt(status="completed", memory_id=memory_id, request_id=None)

    async def update_memory(
        self,
        *,
        memory_id: str,
        operation_id: str,
        content: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> MutationReceipt:
        self.update_calls.append(
            {
                "memory_id": memory_id,
                "operation_id": operation_id,
                "content": content,
                "fields": fields,
            }
        )
        if self.update_error is not None:
            raise self.update_error
        stored = self.rows.get(memory_id)
        if stored is not None and content is not None:
            stored["content"] = content
        return MutationReceipt(status="completed", memory_id=memory_id, request_id=None)

    async def delete_memory(
        self, *, memory_id: str, operation_id: str
    ) -> MutationReceipt:
        del operation_id
        self.delete_calls.append(memory_id)
        if self.delete_error is not None:
            raise self.delete_error
        return MutationReceipt(status="completed", memory_id=memory_id, request_id=None)

    async def search(
        self,
        query: str,
        *,
        session_id: str | None = None,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> SearchReceipt:
        self.search_calls.append({"query": query, "top_k": top_k, "filters": filters})
        return SearchReceipt(
            search_record_id="search-record-01",
            hits=tuple(self.search_hits),
            request_id=None,
        )


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
    client = FakeMindMemOSClient(
        [
            row(
                "m1",
                session_id="s1",
                status="active",
                content="alpha",
                update_at="2026-08-24T08:30:00+00:00",
            ),
            row(
                "m2",
                session_id="s1",
                status="archived",
                content="beta",
                update_at="2026-08-24T08:29:00+00:00",
            ),
            row(
                "m3",
                session_id=None,
                status="active",
                content="gamma",
                update_at="2026-08-24T08:28:00+00:00",
            ),
        ]
    )
    sessions = FakeSessions({"s1": [UserMessage.from_text("first user request")]})
    service = MemoryManagementService(client, sessions)

    snapshot = await service.snapshot(tenant_id="local")

    assert snapshot.stats == MemoryStats(2, 1, 3, 1)
    assert [(group.session_id, group.title) for group in snapshot.groups] == [
        ("s1", "first user request"),
        (None, "未关联会话"),
    ]
    assert snapshot.groups[0].active_count == 1
    assert snapshot.groups[0].archived_count == 1
    assert client.scroll_calls[0]["limit"] == 500


@pytest.mark.asyncio
async def test_snapshot_accepts_search_item_field_spellings() -> None:
    """Upstream ``MemorySearchItem`` uses id/memory/memory_type/last_update_at
    plus ``lineage.role`` — the projection must map those spellings too."""
    client = FakeMindMemOSClient(
        [
            {
                "id": "alt-1",
                "memory": "aliased content",
                "memory_type": "procedure",
                "last_update_at": "2026-08-24 08:30:00",
                "lineage": {"role": "archived"},
                "metadata": {"session_id": "s-meta"},
            }
        ]
    )
    service = MemoryManagementService(client, FakeSessions({}))

    snapshot = await service.snapshot(tenant_id="local")

    (memory,) = snapshot.groups[0].memories
    assert memory.memory_id == "alt-1"
    assert memory.content == "aliased content"
    assert memory.memory_type == "procedure"
    assert memory.status == "archived"
    assert memory.session_id == "s-meta"
    assert memory.updated_at == datetime(2026, 8, 24, 8, 30)


@pytest.mark.asyncio
async def test_snapshot_paginates_scroll_cursor() -> None:
    client = FakeMindMemOSClient([])
    pages = iter(
        [
            {
                "memories": [
                    row("m1", session_id=None, status="active", content="a"),
                ],
                "next_cursor": "cursor-2",
            },
            {
                "memories": [
                    row("m2", session_id=None, status="active", content="b"),
                ],
                "next_cursor": None,
            },
        ]
    )

    async def scroll(**kwargs: Any) -> dict[str, Any]:
        client.scroll_calls.append(kwargs)
        return next(pages)

    client.scroll_memories = scroll  # type: ignore[method-assign]
    service = MemoryManagementService(client, FakeSessions({}))

    snapshot = await service.snapshot(tenant_id="local")

    assert {item.memory_id for item in snapshot.groups[0].memories} == {"m1", "m2"}
    assert client.scroll_calls[1]["cursor"] == "cursor-2"


@pytest.mark.asyncio
async def test_snapshot_falls_back_title_and_sorts_deterministically() -> None:
    service = MemoryManagementService(
        FakeMindMemOSClient(
            [
                row(
                    "m-b",
                    session_id="abcdef123456",
                    status="active",
                    content="later id",
                    update_at="2026-08-24T08:30:00+00:00",
                ),
                row(
                    "m-a",
                    session_id="abcdef123456",
                    status="active",
                    content="earlier id",
                    update_at="2026-08-24T08:30:00+00:00",
                ),
            ]
        ),
        FakeSessions({}),
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
        FakeMindMemOSClient(
            [
                row(
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
                    status_changed_at="2026-08-24T08:30:00+00:00",
                    parent_ids=["older"],
                ),
                row(
                    "invalid",
                    session_id=None,
                    status="active",
                    content="still visible",
                    metadata={"request_metadata": {"record_json": "not-json"}},
                ),
                row(
                    "direct",
                    session_id=None,
                    status="active",
                    content="native structured payload",
                    structured_record={"memory_type": "fact", "predicate": "location"},
                ),
            ]
        ),
        FakeSessions({}),
    )

    memories = (await service.snapshot(tenant_id="local")).groups[0].memories
    valid = next(item for item in memories if item.memory_id == "valid")
    invalid = next(item for item in memories if item.memory_id == "invalid")
    direct = next(item for item in memories if item.memory_id == "direct")

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
    assert direct.structure_status == "valid"
    assert direct.record == {"memory_type": "fact", "predicate": "location"}


@pytest.mark.asyncio
async def test_detail_projects_single_memory_and_missing_is_not_found() -> None:
    client = FakeMindMemOSClient(
        [row("m1", session_id=None, status="active", content="v1")]
    )
    service = MemoryManagementService(client, FakeSessions({}))

    memory = await service.detail("m1", tenant_id="local")

    assert memory.memory_id == "m1"
    assert client.get_calls == [{"memory_id": "m1"}]
    with pytest.raises(MemoryNotFoundError):
        await service.detail("missing", tenant_id="local")


@pytest.mark.asyncio
async def test_history_returns_versions_when_present_else_single_item() -> None:
    client = FakeMindMemOSClient(
        [row("m1", session_id=None, status="active", content="current")],
        history={
            "m1": [
                row("m1", session_id=None, status="active", content="v2",
                    update_at="2026-08-24T08:30:00+00:00"),
                row(
                    "m1-old",
                    session_id=None,
                    status="archived",
                    content="v1",
                    update_at="2026-08-24T08:00:00+00:00",
                ),
            ]
        },
    )
    service = MemoryManagementService(client, FakeSessions({}))

    versions = await service.history("m1", tenant_id="tenant-a")

    assert [item.content for item in versions] == ["v2", "v1"]
    assert client.get_calls == [{"memory_id": "m1"}]

    # Single-version memory: no history list → project the item itself.
    client.history = {}
    versions = await service.history("m1", tenant_id="tenant-a")
    assert [item.memory_id for item in versions] == ["m1"]

    with pytest.raises(MemoryNotFoundError):
        await service.history("missing", tenant_id="tenant-a")


@pytest.mark.asyncio
async def test_search_returns_projected_hits() -> None:
    client = FakeMindMemOSClient(
        [],
        search_hits=[
            SearchHit(
                id="m1",
                memory="hit one",
                memory_type="fact",
                score=0.9,
                metadata={"session_id": "s1"},
            )
        ],
    )
    service = MemoryManagementService(client, FakeSessions({}))

    result = await service.search("hit", tenant_id="local")

    assert result.query == "hit"
    assert result.search_record_id == "search-record-01"
    (hit,) = result.hits
    assert hit.memory_id == "m1"
    assert hit.content == "hit one"
    assert hit.memory_type_label == "事实"
    assert hit.score == 0.9
    assert hit.session_id == "s1"
    assert client.search_calls == [{"query": "hit", "top_k": 10, "filters": None}]


@pytest.mark.asyncio
async def test_create_uses_native_operation_then_reads_back() -> None:
    client = FakeMindMemOSClient([])
    service = MemoryManagementService(client, FakeSessions({}))

    created = await service.create("new fact", "procedure", tenant_id="local")

    (call,) = client.create_calls
    assert call["operation_id"].startswith("webcreate:")
    assert call["content"] == "new fact"
    assert call["mem_type"] == "procedure"
    assert created.memory_id == "mem-001"
    assert created.content == "new fact"
    assert client.get_calls == [{"memory_id": "mem-001"}]


@pytest.mark.asyncio
async def test_create_validates_input_and_surfaces_backend_failure() -> None:
    client = FakeMindMemOSClient([])
    service = MemoryManagementService(client, FakeSessions({}))

    with pytest.raises(ValueError, match="empty"):
        await service.create("   ", tenant_id="local")
    with pytest.raises(ValueError, match="fact or procedure"):
        await service.create("text", "episodic", tenant_id="local")

    client.create_error = MindMemOSClientError("transport down")
    with pytest.raises(MemoryUnavailableError):
        await service.create("text", "fact", tenant_id="local")


@pytest.mark.asyncio
async def test_update_plain_memory_uses_native_update() -> None:
    client = FakeMindMemOSClient(
        [row("m1", session_id=None, status="active", content="old text")]
    )
    service = MemoryManagementService(client, FakeSessions({}))

    updated = await service.update("m1", "new text", tenant_id="local")

    assert updated.memory_id == "m1"
    (call,) = client.update_calls
    assert call["memory_id"] == "m1"
    assert call["content"] == "new text"
    assert call["fields"] is None
    assert call["operation_id"].startswith("webupdate:")
    # No flat fallback: nothing is recreated or deleted behind the caller.
    assert client.create_calls == []
    assert client.delete_calls == []


@pytest.mark.asyncio
async def test_update_failure_surfaces_error_without_fallback_recreate() -> None:
    """V3.8 removed the delete + add_flat fallback: a failed native update
    must surface the error and leave the stored memory untouched."""
    client = FakeMindMemOSClient(
        [row("m1", session_id=None, status="active", content="old text")]
    )
    client.update_error = MindMemOSClientError("server rejected update")
    service = MemoryManagementService(client, FakeSessions({}))

    with pytest.raises(MemoryUnavailableError):
        await service.update("m1", "new text", tenant_id="local")

    assert client.create_calls == []
    assert client.delete_calls == []
    assert client.rows["m1"]["content"] == "old text"


@pytest.mark.asyncio
async def test_update_not_found_error_maps_to_not_found() -> None:
    client = FakeMindMemOSClient(
        [row("m1", session_id=None, status="active", content="old text")]
    )
    client.update_error = MindMemOSClientError("/v1/memory/update: memory not_found")
    service = MemoryManagementService(client, FakeSessions({}))

    with pytest.raises(MemoryNotFoundError):
        await service.update("m1", "new text", tenant_id="local")


@pytest.mark.asyncio
async def test_update_fails_closed_on_structured_record() -> None:
    record = {
        "schema_version": 1,
        "memory_type": "fact",
        "subject": {"type": "object", "name": "钥匙"},
        "predicate": "location",
        "value": "抽屉",
        "source": "user_statement",
    }
    client = FakeMindMemOSClient(
        [
            row(
                "structured",
                session_id=None,
                status="active",
                content="visible text",
                metadata={
                    "request_metadata": {
                        "record_metadata": [{"record_json": json.dumps(record)}]
                    }
                },
            ),
            row(
                "native",
                session_id=None,
                status="active",
                content="native structured text",
                structured_record={"memory_type": "fact"},
            ),
        ]
    )
    service = MemoryManagementService(client, FakeSessions({}))

    with pytest.raises(ValueError, match="structured record"):
        await service.update("structured", "edited", tenant_id="local")
    with pytest.raises(ValueError, match="structured record"):
        await service.update("native", "edited", tenant_id="local")

    # Fail-closed: neither a content patch nor any recreate ran.
    assert client.update_calls == []
    assert client.create_calls == []
    assert client.delete_calls == []


@pytest.mark.asyncio
async def test_update_fails_closed_on_invalid_structured_record() -> None:
    client = FakeMindMemOSClient(
        [
            row(
                "corrupt",
                session_id=None,
                status="active",
                content="visible text",
                metadata={"request_metadata": {"record_json": "not-json"}},
            )
        ]
    )
    service = MemoryManagementService(client, FakeSessions({}))

    with pytest.raises(ValueError, match="structured record"):
        await service.update("corrupt", "edited", tenant_id="local")

    assert client.update_calls == []


@pytest.mark.asyncio
async def test_update_structured_memory_with_fields_uses_native_fields() -> None:
    client = FakeMindMemOSClient(
        [
            row(
                "structured",
                session_id=None,
                status="active",
                content="visible text",
                structured_record={"memory_type": "fact", "predicate": "location"},
            )
        ]
    )
    service = MemoryManagementService(client, FakeSessions({}))

    updated = await service.update(
        "structured",
        "edited text",
        tenant_id="local",
        fields={"record.predicate": "room"},
    )

    (call,) = client.update_calls
    assert call["fields"] == {"record.predicate": "room"}
    assert call["content"] == "edited text"
    assert updated.memory_id == "structured"


@pytest.mark.asyncio
async def test_update_missing_memory_raises_not_found() -> None:
    service = MemoryManagementService(FakeMindMemOSClient([]), FakeSessions({}))

    with pytest.raises(MemoryNotFoundError):
        await service.update("missing", "edited", tenant_id="local")


@pytest.mark.asyncio
async def test_delete_archives_through_native_delete() -> None:
    client = FakeMindMemOSClient(
        [row("m1", session_id=None, status="active", content="bye")]
    )
    service = MemoryManagementService(client, FakeSessions({}))

    result = await service.delete("m1", tenant_id="local")

    assert result == {"memory_id": "m1", "deleted": True, "status": "archived"}
    assert client.delete_calls == ["m1"]


@pytest.mark.asyncio
async def test_delete_missing_memory_raises_not_found() -> None:
    service = MemoryManagementService(FakeMindMemOSClient([]), FakeSessions({}))

    with pytest.raises(MemoryNotFoundError):
        await service.delete("missing", tenant_id="local")


@pytest.mark.asyncio
async def test_delete_backend_not_found_maps_to_not_found() -> None:
    client = FakeMindMemOSClient(
        [row("m1", session_id=None, status="active", content="bye")]
    )
    client.delete_error = MindMemOSClientError("/v1/memory/delete: not_found")
    service = MemoryManagementService(client, FakeSessions({}))

    with pytest.raises(MemoryNotFoundError):
        await service.delete("m1", tenant_id="local")


@pytest.mark.asyncio
async def test_graph_joins_memory_nodes_with_reader_edges() -> None:
    client = FakeMindMemOSClient(
        [
            row(
                "m1",
                session_id="s1",
                status="active",
                content="alpha node",
                update_at="2026-08-24T08:30:00+00:00",
            ),
            row("m2", session_id="s1", status="active", content="beta node"),
        ]
    )
    edge_calls: list[int] = []

    async def graph_edges(limit: int) -> list[dict[str, Any]]:
        edge_calls.append(limit)
        return [
            {
                "src": "m1",
                "rel": "MENTIONS",
                "dst": "ent-001",
                "dst_label": "Entity",
                "entity_name": "厨房",
            },
            {
                "src": "m1",
                "rel": "RELATES_TO",
                "dst": "m2",
                "dst_label": "Memory",
                "entity_name": None,
            },
        ]

    sessions = FakeSessions({"s1": [UserMessage.from_text("graph session")]})
    service = MemoryManagementService(client, sessions, graph_reader=graph_edges)

    graph = await service.graph(tenant_id="local")

    assert edge_calls == [2000]
    nodes = {node.memory_id: node for node in graph.nodes}
    assert nodes["m1"].preview == "alpha node"
    assert nodes["m1"].memory_type == "fact"
    assert nodes["m1"].session_id == "s1"
    assert {edge.rel for edge in graph.edges} == {"MENTIONS", "RELATES_TO"}
    assert [(e.entity_id, e.name) for e in graph.entities] == [("ent-001", "厨房")]
    assert [(s.session_id, s.title) for s in graph.sessions] == [
        ("s1", "graph session")
    ]


@pytest.mark.asyncio
async def test_graph_requires_reader_and_fails_closed() -> None:
    client = FakeMindMemOSClient([])
    service = MemoryManagementService(client, FakeSessions({}))

    with pytest.raises(MemoryUnavailableError):
        await service.graph(tenant_id="local")

    assert client.scroll_calls == []


@pytest.mark.asyncio
async def test_graph_unconfigured_scope_maps_to_unavailable() -> None:
    """The verifier raises plain ``RuntimeError`` when its project scope is
    unconfigured — a backend-unavailable state, not a read failure."""
    client = FakeMindMemOSClient(
        [row("m1", session_id=None, status="active", content="node")]
    )

    async def graph_edges(limit: int) -> list[dict[str, Any]]:
        raise RuntimeError("graph edge reads require a project_id")

    service = MemoryManagementService(client, FakeSessions({}), graph_reader=graph_edges)

    with pytest.raises(MemoryUnavailableError):
        await service.graph(tenant_id="local")


@pytest.mark.asyncio
async def test_graph_marks_truncated_edges() -> None:
    client = FakeMindMemOSClient(
        [row("m1", session_id=None, status="active", content="node")]
    )

    async def graph_edges(limit: int) -> list[dict[str, Any]]:
        return [
            {"src": "m1", "rel": "RELATES_TO", "dst": f"dst-{i}",
             "dst_label": "Memory", "entity_name": None}
            for i in range(limit + 1)
        ]

    service = MemoryManagementService(client, FakeSessions({}), graph_reader=graph_edges)

    graph = await service.graph(tenant_id="local")

    assert graph.edges_truncated is True
    assert len(graph.edges) == 2000


@pytest.mark.asyncio
async def test_scroll_backend_failure_maps_to_unavailable() -> None:
    client = FakeMindMemOSClient([])
    client.scroll_error = MindMemOSClientError("connection refused")
    service = MemoryManagementService(client, FakeSessions({}))

    with pytest.raises(MemoryUnavailableError):
        await service.snapshot(tenant_id="local")
