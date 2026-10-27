"""Managed-runtime composition tests for the V3.8 memory tier.

The embedded MindMemOS runtime, the add/enrichment queues and the V3.6
SessionFinalizer are gone. The full tier composes a standalone
``MindMemOSClient`` plus a durable ``MemoryWorkerPool`` over
``SQLiteMemoryWorkStore``, with managed Neo4j/Qdrant starting *before* the
worker pool's client readiness probe and closing *after* it. These tests pin
that ordering, the resource-scope LIFO close, the standalone-client
construction surface, and the archive boundary that replaced finalization.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import homemaster.application.composition.base as composition
from homemaster.agent.messages import UserMessage
from homemaster.config import HomeMasterConfig
from homemaster.memory.memory_work_store import JOB_DREAMING, SQLiteMemoryWorkStore


def _config_payload(tmp_path: Path) -> dict:
    return {
        "memory": {
            # The managed-Neo4j composition under test only exists on the
            # full tier; "files" is now the built-in default.
            "mode": "full",
            "data_root": tmp_path / "memory",
            "neo4j": {
                "mode": "managed_local",
                "home": tmp_path / "neo4j-home",
                "java_home": tmp_path / "java-home",
                "password": "private-test-password",
            },
            "mindmemos": {
                "base_url": "https://mindmemos.test",
                "api_key": "test-api-key",
            },
            # Keep the worker loop deterministic: no scheduled feedback tick.
            "implicit_feedback": {"enabled": False},
        },
        "runtime": {"runtime_root": tmp_path / "runs"},
        "observability": {"session_dir": str(tmp_path / "sessions")},
        "providers": {
            "default": "Mimo",
            "items": [
                {
                    "name": "Mimo",
                    "kind": "chat",
                    "api_format": "anthropic",
                    "base_url": "https://chat.example/anthropic",
                    "model": "mimo-v2.5",
                    "api_keys": ["chat-key"],
                },
                {
                    "name": "MemoryEmbedding",
                    "kind": "embedding",
                    "api_format": "openai",
                    "base_url": "https://embedding.example/v1",
                    "embedding_url": "https://embedding.example/v1/embeddings",
                    "model": "Qwen/Qwen3-Embedding-8B",
                    "api_keys": ["embedding-key"],
                },
            ],
        },
    }


def _config(tmp_path: Path) -> HomeMasterConfig:
    return HomeMasterConfig.model_validate(_config_payload(tmp_path))


def _fake_managed_neo4j(calls: list[str], *, error: BaseException | None = None):
    class FakeManagedNeo4jRuntime:
        def __init__(self, _memory_config: object) -> None:
            pass

        async def start(self) -> None:
            calls.append("neo4j.start")
            if error is not None:
                raise error

        async def close(self) -> None:
            calls.append("neo4j.close")

    return FakeManagedNeo4jRuntime


def _fake_managed_qdrant(calls: list[str]):
    class FakeManagedQdrantRuntime:
        def __init__(self, _memory_config: object) -> None:
            pass

        async def start(self) -> None:
            calls.append("qdrant.start")

        async def close(self) -> None:
            calls.append("qdrant.close")

    return FakeManagedQdrantRuntime


def _fake_mindmemos_client(calls: list[str], clients: list, *, ready: bool = True):
    class FakeMindMemOSClient:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            clients.append(self)

        async def health(self) -> bool:
            calls.append("client.health")
            return ready

        async def aclose(self) -> None:
            calls.append("client.close")

    return FakeMindMemOSClient


def test_full_mode_requires_standalone_mindmemos_base_url(tmp_path: Path) -> None:
    """The embedded runtime is gone: full mode must point at a server."""
    payload = _config_payload(tmp_path)
    payload["memory"]["mindmemos"] = {}

    with pytest.raises(ValidationError, match="mindmemos"):
        HomeMasterConfig.model_validate(payload)


@pytest.mark.asyncio
async def test_managed_neo4j_starts_before_worker_pool_and_closes_after(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    clients: list = []
    monkeypatch.setattr(
        composition, "ManagedNeo4jRuntime", _fake_managed_neo4j(calls)
    )
    monkeypatch.setattr(
        composition, "MindMemOSClient", _fake_mindmemos_client(calls, clients)
    )
    bundle = composition.compose_application(config=_config(tmp_path), run_label="managed")

    services = bundle.application.settings.application_services
    assert services["managed_neo4j"] is not None
    assert services["mindmemos_client"] is clients[0]
    assert isinstance(bundle.memory_work_store, SQLiteMemoryWorkStore)
    assert bundle.session_trace is not None
    assert bundle.memory_worker_pool is not None
    assert bundle.archive_service is not None

    (client,) = clients
    assert client.kwargs["base_url"] == "https://mindmemos.test"
    assert client.kwargs["api_key"] == "test-api-key"
    assert client.kwargs["scope"].tenant_id == "local"

    await bundle.application.start()
    await bundle.application.aclose()

    # neo4j.start precedes the worker pool's client readiness probe; the LIFO
    # close runs worker pool (incl. store close) -> client -> neo4j.
    assert calls == [
        "neo4j.start",
        "client.health",
        "client.close",
        "neo4j.close",
    ]


@pytest.mark.asyncio
async def test_managed_qdrant_slots_between_neo4j_and_worker_pool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Managed qdrant slots between Neo4j and the worker pool so the vector
    store outlives every client that dials it."""
    calls: list[str] = []
    clients: list = []
    monkeypatch.setattr(
        composition, "ManagedNeo4jRuntime", _fake_managed_neo4j(calls)
    )
    monkeypatch.setattr(
        composition, "ManagedQdrantRuntime", _fake_managed_qdrant(calls)
    )
    monkeypatch.setattr(
        composition, "MindMemOSClient", _fake_mindmemos_client(calls, clients)
    )

    config = _config(tmp_path)
    config = config.model_copy(
        update={
            "memory": config.memory.model_copy(
                update={
                    "qdrant": config.memory.qdrant.model_copy(
                        update={
                            "mode": "managed_local",
                            "home": tmp_path / "qdrant-home",
                        }
                    )
                }
            )
        }
    )
    bundle = composition.compose_application(config=config, run_label="managed-qdrant")

    assert "managed_qdrant" in bundle.application.settings.application_services
    await bundle.application.start()
    await bundle.application.aclose()

    assert calls == [
        "neo4j.start",
        "qdrant.start",
        "client.health",
        "client.close",
        "qdrant.close",
        "neo4j.close",
    ]


@pytest.mark.asyncio
async def test_managed_neo4j_start_failure_prevents_worker_pool_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    clients: list = []
    monkeypatch.setattr(
        composition,
        "ManagedNeo4jRuntime",
        _fake_managed_neo4j(calls, error=RuntimeError("managed Neo4j unavailable")),
    )
    monkeypatch.setattr(
        composition, "MindMemOSClient", _fake_mindmemos_client(calls, clients)
    )
    bundle = composition.compose_application(config=_config(tmp_path), run_label="managed-fail")

    with pytest.raises(RuntimeError, match="managed Neo4j unavailable"):
        await bundle.application.start()
    await bundle.application.aclose()

    # The client readiness path is part of the worker pool start, which must
    # never run once the managed backend fails; startup rollback already
    # closed the bound resources in LIFO order.
    assert "client.health" not in calls
    assert calls.index("neo4j.start") < calls.index("client.close") < calls.index(
        "neo4j.close"
    )


@pytest.mark.asyncio
async def test_unreachable_mindmemos_does_not_fail_start_but_marks_not_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V3.8 contract: the durable outbox tolerates an unreachable backend —
    startup reports ``not ready`` instead of refusing the application."""
    calls: list[str] = []
    clients: list = []
    monkeypatch.setattr(
        composition, "ManagedNeo4jRuntime", _fake_managed_neo4j(calls)
    )
    monkeypatch.setattr(
        composition,
        "MindMemOSClient",
        _fake_mindmemos_client(calls, clients, ready=False),
    )
    bundle = composition.compose_application(
        config=_config(tmp_path), run_label="mindmemos-down"
    )

    await bundle.application.start()

    readiness = bundle.memory_worker_pool.readiness
    assert readiness is not None
    assert readiness.store_open is True
    assert readiness.client_ready is False
    assert readiness.ready is False
    await bundle.application.aclose()


@pytest.mark.asyncio
async def test_session_close_archives_terminal_boundary_and_enqueues_dreaming_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ApplicationSession.close`` replaces the removed SessionFinalizer: it
    freezes an immutable archive snapshot over the canonical trace and enqueues
    the bound Dreaming intent — while the work store is still open."""
    calls: list[str] = []
    clients: list = []
    monkeypatch.setattr(
        composition, "ManagedNeo4jRuntime", _fake_managed_neo4j(calls)
    )
    monkeypatch.setattr(
        composition, "MindMemOSClient", _fake_mindmemos_client(calls, clients)
    )
    bundle = composition.compose_application(
        config=_config(tmp_path),
        run_label="session-archive",
        memory_tenant_id="benchmark-tenant",
    )
    (client,) = clients
    assert client.kwargs["scope"].tenant_id == "benchmark-tenant"
    await bundle.application.start()
    # Stop the outbox loop so job assertions stay deterministic — the durable
    # intents are what this test owns, not worker timing.
    assert await bundle.memory_worker_pool.stop()
    store = bundle.memory_work_store
    trace = bundle.session_trace
    trace.begin_turn("episode-one", 1, "run-1", message_start_index=0)
    trace.commit_terminal(
        "episode-one",
        "turn-1",
        status="completed",
        messages=[UserMessage.from_text("告警在玄关抽屉里")],
        terminal_reason="replied",
        message_end_index=1,
    )

    session = bundle.application.session("episode-one", exit_reason="episode_end")
    receipt = session.close()

    assert receipt is not None
    assert receipt.status == "archived"
    (archive,) = store.archives_for_session("episode-one")
    assert receipt.job_id == archive.archive_id
    assert archive.add_operation_ids == ("turnadd:episode-one:turn-1",)
    dreaming_job = store.job(archive.dreaming_job_id)
    assert dreaming_job is not None
    assert dreaming_job.kind == JOB_DREAMING
    assert dreaming_job.archive_id == archive.archive_id
    assert dreaming_job.session_id == "episode-one"
    # Closing again is idempotent and never mints a second snapshot/job.
    assert session.close() is receipt
    await bundle.application.aclose()
    assert "neo4j.close" in calls


@pytest.mark.asyncio
async def test_session_close_without_trace_is_a_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    clients: list = []
    monkeypatch.setattr(
        composition, "ManagedNeo4jRuntime", _fake_managed_neo4j(calls)
    )
    monkeypatch.setattr(
        composition, "MindMemOSClient", _fake_mindmemos_client(calls, clients)
    )
    bundle = composition.compose_application(
        config=_config(tmp_path), run_label="archive-empty"
    )
    await bundle.application.start()
    assert await bundle.memory_worker_pool.stop()

    session = bundle.application.session("empty-session", exit_reason="rotate")
    assert session.close() is None
    assert bundle.memory_work_store.archives_for_session("empty-session") == []
    await bundle.application.aclose()


def test_compose_full_tier_exposes_v38_bundle_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bundle advertises the V3.8 surface only — no embedded runtime,
    queues, dreaming coordinator, or session finalization fields."""
    calls: list[str] = []
    clients: list = []
    monkeypatch.setattr(
        composition, "ManagedNeo4jRuntime", _fake_managed_neo4j(calls)
    )
    monkeypatch.setattr(
        composition, "MindMemOSClient", _fake_mindmemos_client(calls, clients)
    )
    bundle = composition.compose_application(
        config=_config(tmp_path), run_label="surface"
    )

    assert bundle.mindmemos_client is clients[0]
    assert isinstance(bundle.memory_work_store, SQLiteMemoryWorkStore)
    assert bundle.session_trace is not None
    assert bundle.memory_worker_pool is not None
    assert bundle.archive_service is not None
    assert bundle.skill_tracker is not None
    for removed in (
        "mindmemos",
        "memory_add_queue",
        "memory_enrichment_queue",
        "dreaming_coordinator",
        "session_finalization",
    ):
        assert not hasattr(bundle, removed), removed


def test_files_tier_composes_no_archive_handler_or_worker_surface(
    tmp_path: Path,
) -> None:
    """Files tier keeps the local store only — no session-end archive hook,
    no work store/client/worker/archive surface at all."""
    payload = _config_payload(tmp_path)
    payload["memory"]["mode"] = "files"
    bundle = composition.compose_application(
        config=HomeMasterConfig.model_validate(payload),
        run_label="files-tier",
    )

    assert bundle.application.session_end_handler is None
    assert bundle.mindmemos_client is None
    assert bundle.memory_work_store is None
    assert bundle.session_trace is None
    assert bundle.memory_worker_pool is None
    assert bundle.archive_service is None
    services = bundle.application.settings.application_services
    assert "file_memory_store" in services
    assert "mindmemos_client" not in services
    assert "memory_worker_pool" not in services
