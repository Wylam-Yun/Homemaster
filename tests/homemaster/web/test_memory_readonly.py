"""HTTP-level read surface tests for the MindMemOS-native memory routes.

V3.8: ``EmbeddedMindMemOS`` and its ``mindmemos.infra`` fixtures are gone;
these tests drive ``GET /api/memories*`` through a duck-typed
``MindMemOSClient`` and assert the read paths never reach the mutating
client surface (create/update/delete).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from homemaster.application import SessionManager
from homemaster.events.bus import EventBus
from homemaster.memory.mindmemos_client import (
    MutationReceipt,
    SearchHit,
    SearchReceipt,
)
from homemaster.web.app import create_web_app
from homemaster.web.confirmations import WebConfirmationHandler
from homemaster.web.memory_views import MemoryManagementService


class _Application:
    def __init__(self, sessions: SessionManager) -> None:
        self.event_bus = EventBus()
        self.session_manager = sessions

    async def aclose(self) -> None:
        await self.event_bus.aclose()


def _row(
    memory_id: str,
    *,
    status: str,
    session_id: str,
    content: str = "fixture",
) -> dict[str, Any]:
    return {
        "memory_id": memory_id,
        "session_id": session_id,
        "status": status,
        "content": content or f"{status} fixture",
        "mem_type": "fact",
        "metadata": {"delete_reason": "user_request"} if status == "archived" else {},
        "status_changed_at": "2026-08-24T08:30:00+00:00" if status == "archived" else None,
        "updated_at": "2026-08-24T08:30:00+00:00",
        "created_at": "2026-08-24T08:00:00+00:00",
    }


class FakeMindMemOSClient:
    """Duck-typed ``MindMemOSClient``: GETs read ``rows``; mutations record
    calls so tests can prove the read-only routes never touch them."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = {row["memory_id"]: row for row in rows}
        self.search_hits: list[SearchHit] = []
        self.mutation_calls: list[str] = []

    async def scroll_memories(self, **_kwargs: Any) -> dict[str, Any]:
        return {"memories": list(self.rows.values()), "next_cursor": None}

    async def get_memory(self, *, filters: dict[str, Any]) -> dict[str, Any]:
        memory_id = (filters or {}).get("memory_id")
        row = self.rows.get(memory_id)
        return {"memories": [row] if row is not None else []}

    async def search(
        self,
        query: str,
        *,
        session_id: str | None = None,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> SearchReceipt:
        return SearchReceipt(
            search_record_id="sr-1",
            hits=tuple(self.search_hits),
            request_id=None,
        )

    async def create_memory(self, **kwargs: Any) -> MutationReceipt:
        self.mutation_calls.append("create")
        return MutationReceipt(status="completed", memory_id=None, request_id=None)

    async def update_memory(self, **kwargs: Any) -> MutationReceipt:
        self.mutation_calls.append("update")
        return MutationReceipt(status="completed", memory_id=None, request_id=None)

    async def delete_memory(self, **kwargs: Any) -> MutationReceipt:
        self.mutation_calls.append("delete")
        return MutationReceipt(status="completed", memory_id=None, request_id=None)


def _app(sessions: SessionManager, service: MemoryManagementService | None) -> Any:
    return create_web_app(
        application=_Application(sessions),
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
        memory_management_service=service,
    )


@pytest.mark.asyncio
async def test_memory_get_routes_read_without_mutating_backend(tmp_path) -> None:
    active_id = "00000000-0000-0000-0000-000000000001"
    archived_id = "00000000-0000-0000-0000-000000000002"
    client = FakeMindMemOSClient(
        [
            _row(active_id, status="active", session_id="session-01"),
            _row(archived_id, status="archived", session_id="session-01"),
        ]
    )
    sessions = SessionManager(session_root=tmp_path / "sessions")
    app = _app(sessions, MemoryManagementService(client, sessions))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        snapshot = await http.get("/api/memories")
        detail = await http.get(f"/api/memories/{active_id}")
        missing = await http.get("/api/memories/not-there")
        history = await http.get(f"/api/memories/{active_id}/history")

    assert snapshot.status_code == 200
    assert snapshot.json()["stats"] == {
        "active_count": 1,
        "archived_count": 1,
        "total_count": 2,
        "session_group_count": 1,
    }
    assert detail.status_code == 200
    assert detail.json()["memory_id"] == active_id
    assert missing.status_code == 404
    assert missing.json()["code"] == "memory_not_found"
    assert history.status_code == 200
    assert history.json()["memory_id"] == active_id
    assert len(history.json()["versions"]) == 1
    # Read-only guarantee: no GET route reached a mutating client method.
    assert client.mutation_calls == []

    await app.state.aclose()


@pytest.mark.asyncio
async def test_memory_graph_joins_listed_nodes_with_verifier_edges(tmp_path) -> None:
    memory_id = "00000000-0000-0000-0000-0000000000aa"
    related_id = "00000000-0000-0000-0000-0000000000bb"
    entity_id = "ent-001"
    client = FakeMindMemOSClient(
        [
            _row(memory_id, status="active", session_id="session-01", content="active fixture"),
            _row(related_id, status="active", session_id="session-01"),
        ]
    )
    queries: list[int] = []

    async def graph_edges(limit: int) -> list[dict[str, Any]]:
        queries.append(limit)
        return [
            {
                "src": memory_id,
                "rel": "MENTIONS",
                "dst": entity_id,
                "dst_label": "Entity",
                "entity_name": "厨房",
            },
            {
                "src": memory_id,
                "rel": "RELATES_TO",
                "dst": related_id,
                "dst_label": "Memory",
                "entity_name": None,
            },
        ]

    sessions = SessionManager(session_root=tmp_path / "sessions")
    app = _app(
        sessions,
        MemoryManagementService(client, sessions, graph_reader=graph_edges),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get("/api/memories/graph")

    assert response.status_code == 200
    body = response.json()
    # Node fields come from the memory payload, never from Neo4j properties.
    nodes = {node["memory_id"]: node for node in body["nodes"]}
    assert nodes[memory_id]["preview"] == "active fixture"
    assert nodes[memory_id]["memory_type"] == "fact"
    assert nodes[memory_id]["session_id"] == "session-01"
    assert {edge["rel"] for edge in body["edges"]} == {"MENTIONS", "RELATES_TO"}
    assert body["entities"] == [{"entity_id": entity_id, "name": "厨房"}]
    # No session file on disk → the service falls back to the truncated-id label.
    assert body["sessions"] == [{"session_id": "session-01", "title": "会话 session-"}]
    assert queries == [2000]

    await app.state.aclose()


@pytest.mark.asyncio
async def test_memory_graph_reports_unavailable_without_graph_reader(tmp_path) -> None:
    client = FakeMindMemOSClient([])
    sessions = SessionManager(session_root=tmp_path / "sessions")
    app = _app(sessions, MemoryManagementService(client, sessions))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get("/api/memories/graph")

    assert response.status_code == 503
    assert response.json()["code"] == "memory_unavailable"

    await app.state.aclose()


@pytest.mark.asyncio
async def test_memory_search_route_returns_hits(tmp_path) -> None:
    client = FakeMindMemOSClient([])
    client.search_hits.append(
        SearchHit(
            id="m1",
            memory="hit one",
            memory_type="fact",
            score=0.9,
            metadata={"session_id": "s1"},
        )
    )
    sessions = SessionManager(session_root=tmp_path / "sessions")
    app = _app(sessions, MemoryManagementService(client, sessions))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        found = await http.get("/api/memories/search", params={"q": "hit"})
        empty = await http.get("/api/memories/search", params={"q": "  "})

    assert found.status_code == 200
    body = found.json()
    assert body["query"] == "hit"
    assert body["search_record_id"] == "sr-1"
    assert body["hits"][0]["memory_id"] == "m1"
    assert body["hits"][0]["memory_type_label"] == "事实"
    assert empty.status_code == 422
    assert empty.json()["code"] == "invalid_memory_input"

    await app.state.aclose()


@pytest.mark.asyncio
async def test_memory_search_route_503_without_service(tmp_path) -> None:
    sessions = SessionManager(session_root=tmp_path / "sessions")
    app = _app(sessions, None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        for path in (
            "/api/memories",
            "/api/memories/search?q=x",
            "/api/memories/graph",
            "/api/memories/m1",
            "/api/memories/m1/history",
        ):
            response = await http.get(path)
            assert response.status_code == 503, path
            assert response.json()["code"] == "memory_unavailable", path

    await app.state.aclose()
