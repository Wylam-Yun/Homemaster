from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from typing import Any

import httpx
import pytest
from mindmemos.infra.db import MemoryPoint, SparseVectorData

from homemaster.application import SessionManager
from homemaster.config import HomeMasterConfig, ProviderProfileConfig
from homemaster.events.bus import EventBus
from homemaster.memory.management import MemoryManagementService
from homemaster.web.app import create_web_app
from homemaster.web.confirmations import WebConfirmationHandler


class _Application:
    def __init__(self, sessions: SessionManager) -> None:
        self.event_bus = EventBus()
        self.session_manager = sessions

    async def aclose(self) -> None:
        await self.event_bus.aclose()


def _provider(name: str, *, kind: str) -> ProviderProfileConfig:
    return ProviderProfileConfig(
        name=name,
        api_format="openai",
        base_url="https://provider.invalid/v1",
        model="test-model",
        api_keys=["test-key"],
        kind=kind,
        embedding_url=(
            "https://provider.invalid/v1/embeddings" if kind == "embedding" else None
        ),
    )


def _point(memory_id: str, *, status: str, session_id: str) -> MemoryPoint:
    return MemoryPoint(
        memory_id=memory_id,
        semantic_vector=[0.0] * 8,
        bm25_vector=SparseVectorData(indices=[1], values=[1.0]),
        payload={
            "memory_id": memory_id,
            "project_id": "local",
            "content": f"{status} fixture",
            "mem_type": "fact",
            "status": status,
            "session_id": session_id,
            "metadata": {"delete_reason": "user_request"} if status == "archived" else {},
            "status_changed_at": "2026-08-24T08:30:00+00:00" if status == "archived" else None,
        },
    )


def _fingerprint(root: Path) -> tuple[tuple[object, ...], ...]:
    rows: list[tuple[object, ...]] = []
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        kind = "dir" if path.is_dir() else "file" if path.is_file() else "other"
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        rows.append(
            (
                path.relative_to(root).as_posix(),
                kind,
                stat.st_mode,
                stat.st_size,
                stat.st_mtime_ns,
                digest,
            )
        )
    return tuple(rows)


@pytest.mark.asyncio
async def test_memory_get_routes_do_not_write_isolated_quiescent_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_module = importlib.import_module("homemaster.memory.mindmemos_runtime")
    infra_db = importlib.import_module("mindmemos.infra.db")
    active_id = "00000000-0000-0000-0000-000000000001"
    archived_id = "00000000-0000-0000-0000-000000000002"

    class FakeNeo4jStore:
        def __init__(self, _config: Any) -> None:
            pass

        async def ensure_schema(self) -> None:
            pass

        async def run_read(self, _query: str, **_params: Any) -> list[dict[str, str]]:
            return [{"memory_id": active_id}]

        async def close(self) -> None:
            pass

    monkeypatch.setattr(infra_db, "Neo4jStore", FakeNeo4jStore)
    data_root = tmp_path / "memory-root"
    config = HomeMasterConfig(
        providers={
            "items": [
                _provider("Mimo", kind="chat"),
                _provider("MemoryEmbedding", kind="embedding"),
            ]
        },
        memory={"data_root": data_root, "embedding_dimensions": 8},
    )
    runtime = runtime_module.EmbeddedMindMemOS(config)
    await runtime.start()
    assert runtime.available, runtime.unavailable_cause
    await runtime.qdrant.upsert_memories(
        [
            _point(active_id, status="active", session_id="session-01"),
            _point(archived_id, status="archived", session_id="session-01"),
        ]
    )

    sessions = SessionManager(session_root=tmp_path / "sessions")
    app = create_web_app(
        application=_Application(sessions),
        confirmation_handler=WebConfirmationHandler(timeout_s=None),
        memory_management_service=MemoryManagementService(runtime, sessions),
    )
    before = _fingerprint(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        snapshot = await client.get("/api/memories")
        history = await client.get(f"/api/memories/{active_id}/history")
    after = _fingerprint(tmp_path)

    assert snapshot.status_code == 200
    assert snapshot.json()["stats"] == {
        "active_count": 1,
        "archived_count": 1,
        "total_count": 2,
        "session_group_count": 1,
    }
    assert history.status_code == 200
    assert before == after

    await app.state.aclose()
    await runtime.close()
