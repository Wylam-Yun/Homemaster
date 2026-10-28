"""Request-body contract tests for the real MindMemOSClient facade.

These exercise the actual ``_post``/``_scope_fields`` plumbing through a stub
transport so schema regressions (e.g. actor-identity fields sent to endpoints
whose server request model forbids extras) fail at test time, not in prod.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from homemaster.memory.mindmemos_client import MemoryScope, MindMemOSClient


class _CaptureTransport:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.gets: list[str] = []
        self._data = data or {}

    async def post_envelope(self, path: str, *, json: dict[str, Any]) -> Any:
        self.posts.append((path, json))
        return SimpleNamespace(data=self._data)

    async def get_envelope(self, path: str, *, params: Any = None) -> Any:
        self.gets.append(path)
        return SimpleNamespace(data=self._data)


def _client(transport: _CaptureTransport) -> MindMemOSClient:
    return MindMemOSClient(
        base_url="http://test",
        api_key="k",
        scope=MemoryScope(tenant_id="tenant-a"),
        transport=transport,  # type: ignore[arg-type]
    )


_SCOPE_KEYS = {"user_id", "app_id", "agent_id", "session_id"}


@pytest.mark.asyncio
async def test_get_memory_sends_no_actor_identity() -> None:
    """``GetRequest`` has no actor identity and forbids extra fields."""
    transport = _CaptureTransport()
    await _client(transport).get_memory(filters={"memory_id": "m-1"})
    path, body = transport.posts[-1]
    assert path == "/v1/memory/get"
    assert _SCOPE_KEYS.isdisjoint(body)


@pytest.mark.asyncio
async def test_delete_memory_sends_no_actor_identity() -> None:
    """``DeleteRequest`` has no actor identity and forbids extra fields."""
    transport = _CaptureTransport()
    await _client(transport).delete_memory(memory_id="m-1", operation_id="op-1")
    path, body = transport.posts[-1]
    assert path == "/v1/memory/delete"
    assert _SCOPE_KEYS.isdisjoint(body)


@pytest.mark.asyncio
async def test_identity_endpoints_keep_actor_fields() -> None:
    """Endpoints built on ``ActorIdentityRequest`` must keep scope fields."""
    transport = _CaptureTransport(data={"memory_ids": ["m-1"]})
    client = _client(transport)
    await client.add_turn(
        operation_id="op-1",
        session_id="s-1",
        turn_id="turn-1",
        dialogue=[{"role": "user", "content": "hi"}],
    )
    _, body = transport.posts[-1]
    assert body["user_id"] == "tenant-a"
    await client.update_memory(memory_id="m-1", operation_id="op-2", content="x")
    _, body = transport.posts[-1]
    assert body["user_id"] == "tenant-a"


@pytest.mark.asyncio
async def test_list_skill_versions_maps_lineage() -> None:
    """Versions list maps each entry's version/parent/status fields."""
    transport = _CaptureTransport(
        data={
            "versions": [
                {
                    "version_id": "v2",
                    "parent_version_id": "v1",
                    "status": "draft",
                    "version_label": "evo-1",
                    "content_hash": "h2",
                },
                {
                    "version_id": "v1",
                    "parent_version_id": None,
                    "status": "observed",
                    "version_label": None,
                    "content_hash": "h1",
                },
            ]
        }
    )
    versions = await _client(transport).list_skill_versions(cloud_skill_id="cs-1")
    assert transport.gets == ["/v1/skills/cs-1/versions"]
    assert [v.version_id for v in versions] == ["v2", "v1"]
    assert versions[0].parent_version_id == "v1"
    assert versions[0].status == "draft"
    assert versions[0].content_hash == "h2"
    assert versions[1].parent_version_id is None


@pytest.mark.asyncio
async def test_get_skill_version_content_requests_exact_path() -> None:
    """Content fetch hits the version-content route and returns full text."""
    transport = _CaptureTransport(
        data={
            "version": {
                "version_id": "v2",
                "parent_version_id": "v1",
                "status": "draft",
                "version_label": "evo-1",
                "content_hash": "h2",
            },
            "content": "---\nname: sk\n---\nbody",
        }
    )
    result = await _client(transport).get_skill_version_content(
        cloud_skill_id="cs-1", version_id="v2"
    )
    assert transport.gets == ["/v1/skills/cs-1/versions/v2/content"]
    assert result.version.version_id == "v2"
    assert result.version.parent_version_id == "v1"
    assert result.content == "---\nname: sk\n---\nbody"


__all__ = []
