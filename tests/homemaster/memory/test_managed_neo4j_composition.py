from __future__ import annotations

from pathlib import Path

import pytest

from homemaster.cli import composition
from homemaster.config import HomeMasterConfig
from homemaster.experience import FinalizeResult


def _config(tmp_path: Path) -> HomeMasterConfig:
    return HomeMasterConfig.model_validate(
        {
            "memory": {
                "data_root": tmp_path / "memory",
                "neo4j": {
                    "mode": "managed_local",
                    "home": tmp_path / "neo4j-home",
                    "java_home": tmp_path / "java-home",
                    "password": "private-test-password",
                },
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
    )


@pytest.mark.asyncio
async def test_managed_neo4j_wraps_embedded_mindmemos_application_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeManagedNeo4jRuntime:
        def __init__(self, _memory_config: object) -> None:
            pass

        async def start(self) -> None:
            calls.append("neo4j.start")

        async def close(self) -> None:
            calls.append("neo4j.close")

    class FakeEmbeddedMindMemOS:
        available = True
        unavailable_cause = None

        def __init__(self, _config: object) -> None:
            pass

        async def start(self) -> None:
            calls.append("mindmemos.start")

        async def close(self) -> None:
            calls.append("mindmemos.close")

    class FakeMemoryAddQueue:
        def __init__(self, _mindmemos: object, *, audit_path: Path) -> None:
            assert audit_path.name == "add_jobs.jsonl"

        async def start(self) -> None:
            calls.append("queue.start")

        async def aclose(self) -> None:
            calls.append("queue.close")

    monkeypatch.setattr(composition, "ManagedNeo4jRuntime", FakeManagedNeo4jRuntime)
    monkeypatch.setattr(composition, "EmbeddedMindMemOS", FakeEmbeddedMindMemOS)
    monkeypatch.setattr(composition, "MemoryAddQueue", FakeMemoryAddQueue)
    bundle = composition.create_home_application(config=_config(tmp_path), run_label="managed")

    assert "managed_neo4j" in bundle.application.settings.application_services
    await bundle.application.start()
    await bundle.application.aclose()

    assert calls == [
        "neo4j.start",
        "mindmemos.start",
        "queue.start",
        "queue.close",
        "mindmemos.close",
        "neo4j.close",
    ]


@pytest.mark.asyncio
async def test_session_finalization_drains_before_memory_resources_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeManagedNeo4jRuntime:
        def __init__(self, _memory_config: object) -> None:
            pass

        async def start(self) -> None:
            calls.append("neo4j.start")

        async def close(self) -> None:
            calls.append("neo4j.close")

    class FakeEmbeddedMindMemOS:
        available = True
        unavailable_cause = None

        def __init__(self, _config: object) -> None:
            pass

        async def start(self) -> None:
            calls.append("mindmemos.start")

        async def close(self) -> None:
            calls.append("mindmemos.close")

    class FakeSessionFinalizer:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["memory_tenant_id"] == "benchmark-tenant"

        async def finalize(self, session_id: str, exit_reason: str) -> FinalizeResult:
            calls.append(f"finalize:{session_id}:{exit_reason}")
            return FinalizeResult(session_id=session_id, status="completed")

    monkeypatch.setattr(composition, "ManagedNeo4jRuntime", FakeManagedNeo4jRuntime)
    monkeypatch.setattr(composition, "EmbeddedMindMemOS", FakeEmbeddedMindMemOS)
    monkeypatch.setattr(composition, "SessionFinalizer", FakeSessionFinalizer)
    bundle = composition.create_home_application(
        config=_config(tmp_path),
        run_label="session-finalization-order",
        memory_tenant_id="benchmark-tenant",
    )

    await bundle.application.start()
    scope = bundle.application.session("episode-one", exit_reason="episode_end")
    receipt = scope.close()
    assert receipt is not None
    assert receipt.status == "accepted"
    await bundle.application.aclose()

    assert calls.index("finalize:episode-one:episode_end") < calls.index("mindmemos.close")
    assert calls.index("mindmemos.close") < calls.index("neo4j.close")


@pytest.mark.asyncio
async def test_managed_neo4j_start_failure_prevents_mindmemos_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FailingManagedNeo4jRuntime:
        def __init__(self, _memory_config: object) -> None:
            pass

        async def start(self) -> None:
            calls.append("neo4j.start")
            raise RuntimeError("managed Neo4j unavailable")

        async def close(self) -> None:
            calls.append("neo4j.close")

    class FakeEmbeddedMindMemOS:
        available = True
        unavailable_cause = None

        def __init__(self, _config: object) -> None:
            pass

        async def start(self) -> None:
            calls.append("mindmemos.start")

        async def close(self) -> None:
            calls.append("mindmemos.close")

    monkeypatch.setattr(composition, "ManagedNeo4jRuntime", FailingManagedNeo4jRuntime)
    monkeypatch.setattr(composition, "EmbeddedMindMemOS", FakeEmbeddedMindMemOS)
    bundle = composition.create_home_application(config=_config(tmp_path), run_label="managed-fail")

    with pytest.raises(RuntimeError, match="managed Neo4j unavailable"):
        await bundle.application.start()
    await bundle.application.aclose()

    assert "mindmemos.start" not in calls


@pytest.mark.asyncio
async def test_managed_mode_rejects_unavailable_mindmemos_before_first_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeManagedNeo4jRuntime:
        def __init__(self, _memory_config: object) -> None:
            pass

        async def start(self) -> None:
            return None

        async def close(self) -> None:
            return None

    class UnavailableMindMemOS:
        available = False
        unavailable_cause = "Neo4j schema initialization failed"

        def __init__(self, _config: object) -> None:
            pass

        async def start(self) -> None:
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr(composition, "ManagedNeo4jRuntime", FakeManagedNeo4jRuntime)
    monkeypatch.setattr(composition, "EmbeddedMindMemOS", UnavailableMindMemOS)
    bundle = composition.create_home_application(
        config=_config(tmp_path), run_label="mindmemos-fail"
    )

    with pytest.raises(RuntimeError, match="Neo4j schema initialization failed"):
        await bundle.application.start()
    await bundle.application.aclose()
