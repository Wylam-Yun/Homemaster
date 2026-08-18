from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from homemaster.config import HomeMasterConfig, ProviderProfileConfig


def _embedding_provider() -> ProviderProfileConfig:
    return ProviderProfileConfig(
        name="MemoryEmbedding",
        api_format="openai",
        base_url="https://embedding.example/v1",
        embedding_url="https://embedding.example/v1/embeddings",
        model="Qwen/Qwen3-Embedding-8B",
        api_keys=["test-key"],
        kind="embedding",
    )


def _chat_provider() -> ProviderProfileConfig:
    return ProviderProfileConfig(
        name="Mimo",
        api_format="anthropic",
        base_url="https://chat.example/anthropic",
        model="mimo-v2.5",
        api_keys=["chat-key"],
        kind="chat",
        max_output_tokens=2048,
    )


def test_build_mindmemos_config_reuses_homemaster_model_endpoints(tmp_path: Path) -> None:
    module = importlib.import_module("homemaster.memory.mindmemos_runtime")
    config = HomeMasterConfig(
        providers={"default": "Mimo", "items": [_chat_provider(), _embedding_provider()]},
        runtime_defaults={
            "default_provider_name": "Mimo",
            "default_embedding_provider_name": "MemoryEmbedding",
        },
        memory={
            "data_root": tmp_path / "memory",
            "embedding_dimensions": 4096,
            "neo4j": {
                "uri": "bolt://neo4j.internal:7687",
                "username": "homemaster",
                "password": "neo4j-test-password",
                "database": "memory",
            },
        },
        provider_client={"timeout_s": 90, "max_retries": 3},
    )

    mapped = module.build_mindmemos_config(config)

    chat = mapped.chat_model_router.endpoints[0]
    assert chat.model == "anthropic/mimo-v2.5"
    assert chat.api_base == "https://chat.example/anthropic"
    assert chat.api_key == "chat-key"
    assert chat.max_tokens == 2048
    assert chat.timeout == 90
    assert chat.num_retries == 3

    embedding = mapped.embed_model_router.endpoints[0]
    assert embedding.model == "openai/Qwen/Qwen3-Embedding-8B"
    assert embedding.api_base == "https://embedding.example/v1"
    assert embedding.api_key == "test-key"
    assert embedding.dimensions == 4096
    assert mapped.embed_model_router.dimensions_supported_models == ["Qwen/Qwen3-Embedding-8B"]
    assert mapped.database.qdrant.vector_size == 4096
    assert mapped.database.neo4j.uri == "bolt://neo4j.internal:7687"
    assert mapped.database.neo4j.username == "homemaster"
    assert mapped.database.neo4j.password == "neo4j-test-password"
    assert mapped.database.neo4j.database == "memory"
    assert mapped.algo_config.add.schema.merge.enable_entity_merge_decision is False
    schema_path = Path(mapped.algo_config.add.schema.entity_modeling_path)
    assert schema_path.is_file()
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert {item["entity_type"] for item in schema} == {
        "fact",
        "task_experience",
        "episodes",
    }

    uncapped = config.model_copy(
        update={
            "providers": config.providers.model_copy(
                update={
                    "items": [
                        _chat_provider().model_copy(update={"max_output_tokens": None}),
                        _embedding_provider(),
                    ]
                }
            )
        }
    )
    assert module.build_mindmemos_config(uncapped).chat_model_router.endpoints[0].max_tokens == 8192


def test_homemaster_schema_prompt_requires_native_entity_output() -> None:
    module = importlib.import_module("homemaster.memory.mindmemos_runtime")

    prompts = module.build_mindmemos_add_prompts("ZH")
    rendered = (
        prompts.entity_generation.replace("{entity_schema}", "[]")
        .replace("{dialogue_timestamp}", "2026-08-06 12:00:00")
        .replace("{chat_chunk}", "相机电池 的 location 是 书房抽屉")
    )

    assert '"entities"' in rendered
    assert '"edges"' in rendered
    assert '"message_mapping":' not in rendered
    assert len(rendered) < 3000
    assert "至少一个非 episodes 实体" in rendered


def test_typed_fact_generation_overrides_llm_type_and_identity() -> None:
    module = importlib.import_module("homemaster.memory.mindmemos_runtime")
    record = {
        "schema_version": 1,
        "memory_type": "fact",
        "subject": {"type": "object", "name": "HM100::0002::筛选待发货订单"},
        "predicate": "web_operation_steps",
        "value": {"steps": ["打开订单", "点击筛选"]},
        "source": "user_statement",
    }

    generated = module._typed_entity_generation(
        record,
        "输入时间：2026-08-10 23:00:00",
    )

    assert generated["edges"] == []
    assert generated["entities"] == [
        {
            "name": "HM100::0002::筛选待发货订单::web_operation_steps",
            "entity_type": "fact",
            "description": (
                "HM100::0002::筛选待发货订单 的 web_operation_steps 是 "
                '{"steps":["打开订单","点击筛选"]}'
            ),
            "properties": [
                {
                    "property_name": "fact_value",
                    "value": (
                        "HM100::0002::筛选待发货订单 的 web_operation_steps 是 "
                        '{"steps":["打开订单","点击筛选"]}'
                    ),
                    "time": "2026-08-10",
                }
            ],
        }
    ]


def test_typed_procedure_generation_preserves_procedure_type() -> None:
    module = importlib.import_module("homemaster.memory.mindmemos_runtime")
    record = {
        "schema_version": 1,
        "memory_type": "procedure",
        "name": "导出订单",
        "entry_url": "https://example.test/orders",
        "steps": [{"order": 1, "action": "click", "target": {"name": "导出"}}],
        "success_condition": "看到下载完成",
        "source": "environment_observation",
    }

    generated = module._typed_entity_generation(record, "2026-08-10 23:00:00")

    entity = generated["entities"][0]
    assert entity["name"] == "导出订单"
    assert entity["entity_type"] == "task_experience"
    assert entity["properties"][0]["property_name"] == "task_experience"
    assert entity["properties"][0]["time"] == "2026-08-10"


@pytest.mark.asyncio
async def test_typed_llm_client_replaces_wrong_entity_generation() -> None:
    module = importlib.import_module("homemaster.memory.mindmemos_runtime")
    record = {
        "schema_version": 1,
        "memory_type": "fact",
        "subject": {"type": "object", "name": "HM100::0002"},
        "predicate": "web_operation_steps",
        "value": {"steps": ["点击筛选"]},
        "source": "user_statement",
    }

    class FakeResponse:
        def __init__(self) -> None:
            self.parsed = {
                "entities": [{"name": "wrong", "entity_type": "task_experience"}],
                "edges": [{"source": "wrong", "target": "other"}],
            }

        def model_copy(self, *, update: dict[str, Any]) -> Any:
            return SimpleNamespace(**update)

    class FakeDelegate:
        async def chat(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
            return FakeResponse()

    token = module._TYPED_RECORD.set(record)
    try:
        response = await module._TypedSchemaLlmClient(FakeDelegate()).chat(
            "memory.add.entity_generation",
            [{"role": "user", "content": "输入时间：2026-08-10 23:00:00"}],
        )
    finally:
        module._TYPED_RECORD.reset(token)

    assert response.parsed["edges"] == []
    assert response.parsed["entities"][0]["name"] == "HM100::0002::web_operation_steps"
    assert response.parsed["entities"][0]["entity_type"] == "fact"


@pytest.mark.asyncio
async def test_embedded_mindmemos_reports_unavailable_without_configured_providers(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("homemaster.memory.mindmemos_runtime")
    runtime = module.EmbeddedMindMemOS(HomeMasterConfig(memory={"data_root": tmp_path / "memory"}))

    await runtime.start()

    assert runtime.available is False
    assert "provider" in runtime.unavailable_cause
    await runtime.close()


@pytest.mark.asyncio
async def test_embedded_mindmemos_owns_persistent_local_qdrant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("homemaster.memory.mindmemos_runtime")
    infra_db = importlib.import_module("mindmemos.infra.db")

    class FakeNeo4jStore:
        def __init__(self, _config: Any) -> None:
            self.closed = False

        async def ensure_schema(self) -> None:
            return None

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(infra_db, "Neo4jStore", FakeNeo4jStore)
    runtime_type = module.EmbeddedMindMemOS
    config = HomeMasterConfig(
        providers={"items": [_chat_provider(), _embedding_provider()]},
        memory={"data_root": tmp_path / "memory", "embedding_dimensions": 8},
    )

    runtime = runtime_type(config)
    await runtime.start()
    assert runtime.qdrant_path == config.memory.mindmemos_qdrant_path
    assert runtime.jieba_cache_path == config.memory.data_root / "mindmemos" / "cache" / "jieba"
    assert runtime.jieba_cache_path.is_dir()
    assert importlib.import_module("jieba").dt.tmp_dir == str(runtime.jieba_cache_path)
    collections = await runtime.qdrant.client.get_collections()
    assert runtime.qdrant.memory_collection in {
        collection.name for collection in collections.collections
    }
    await runtime.close()

    reopened = runtime_type(config)
    await reopened.start()
    collections = await reopened.qdrant.client.get_collections()
    assert reopened.qdrant.memory_collection in {
        collection.name for collection in collections.collections
    }
    await reopened.close()


@pytest.mark.asyncio
async def test_embedded_mindmemos_add_and_search_record_pipeline_calls(tmp_path: Path) -> None:
    from mindmemos.typing import DialogueMessage, MemoryRequestContext

    module = importlib.import_module("homemaster.memory.mindmemos_runtime")
    config = HomeMasterConfig(
        providers={"items": [_chat_provider(), _embedding_provider()]},
        memory={"data_root": tmp_path / "memory", "embedding_dimensions": 8},
    )
    runtime = module.EmbeddedMindMemOS(config)
    calls: dict[str, Any] = {}

    class FakeRecorder:
        async def record_add_input(self, payload: Any, **kwargs: Any) -> str:
            calls["record_add"] = (payload, kwargs)
            return kwargs["add_record_id"]

        async def mark_add_failed(self, *_args: Any, **_kwargs: Any) -> None:
            calls["add_failed"] = True

        async def record_search(self, payload: Any, result: Any, **kwargs: Any) -> None:
            calls["record_search"] = (payload, result, kwargs)

    class FakeAddPipeline:
        async def add_sync(
            self,
            payload: Any,
            context: Any,
            *,
            add_record_id: str,
        ) -> Any:
            calls["pipeline_add"] = (payload, context, add_record_id)
            return SimpleNamespace(status="ok", memories=[])

    class FakeSearchPipeline:
        async def search(self, payload: Any, context: Any) -> Any:
            calls["pipeline_search"] = (payload, context)
            return SimpleNamespace(status="ok", memories=[])

    class FakeGetPipeline:
        async def get(self, payload: Any, context: Any) -> Any:
            calls["pipeline_get"] = (payload, context)
            return SimpleNamespace(status="ok", memories=[])

    class FakeUpdatePipeline:
        async def update(self, payload: Any, context: Any) -> Any:
            calls["pipeline_update"] = (payload, context)
            return SimpleNamespace(status="ok", message=None)

    class FakeDeletePipeline:
        async def delete(self, payload: Any, context: Any) -> Any:
            calls["pipeline_delete"] = (payload, context)
            return SimpleNamespace(status="ok", message=None)

    class FakeReader:
        async def get_memory(self, context: Any, memory_id: str) -> Any:
            calls["reader_get"] = (context, memory_id)
            return SimpleNamespace(memory_id=memory_id, metadata={"record_json": "{}"})

    runtime._recorder = FakeRecorder()
    runtime._add_pipeline = FakeAddPipeline()
    runtime._search_pipeline = FakeSearchPipeline()
    runtime._get_pipeline = FakeGetPipeline()
    runtime._update_pipeline = FakeUpdatePipeline()
    runtime._delete_pipeline = FakeDeletePipeline()
    runtime._reader = FakeReader()
    context = MemoryRequestContext(
        request_id="request-1",
        account_id="account-1",
        project_id="project-1",
        api_key_uuid="local",
        user_id="user-1",
    )

    add_result = await runtime.add(
        [DialogueMessage(role="user", content="remember this")],
        context,
        metadata={"homemaster_memory_type": "fact"},
        event_timestamp_ms=1234,
    )
    search_result = await runtime.search(
        "what should you remember?",
        context,
        top_k=3,
        filters={"homemaster_memory_type": "fact"},
        rerank=True,
        score_threshold=0.25,
    )
    get_result = await runtime.get(
        context,
        filters={"homemaster_memory_type": "fact"},
        top_k=4,
    )
    update_result = await runtime.update("raw-memory-1", "replacement", context)
    delete_result = await runtime.delete("raw-memory-1", context)
    raw_memory = await runtime.get_raw("raw-memory-1", context)

    recorded_id = calls["record_add"][1]["add_record_id"]
    assert calls["pipeline_add"][2] == recorded_id
    assert calls["pipeline_add"][0].force_generation is True
    assert calls["pipeline_add"][0].metadata == {"homemaster_memory_type": "fact"}
    assert calls["pipeline_add"][0].event_timestamp_ms == 1234
    assert add_result.status == "ok"
    assert calls["pipeline_search"][0].query == "what should you remember?"
    assert calls["pipeline_search"][0].top_k == 3
    assert calls["pipeline_search"][0].search_pipeline == "schema"
    assert calls["pipeline_search"][0].filters == {"homemaster_memory_type": "fact"}
    assert calls["pipeline_search"][0].rerank is True
    assert calls["pipeline_search"][0].score_threshold == 0.25
    assert calls["record_search"][1] is search_result
    assert calls["pipeline_get"][0].filters == {"homemaster_memory_type": "fact"}
    assert calls["pipeline_get"][0].top_k == 4
    assert get_result.status == "ok"
    assert calls["pipeline_update"][0].id == "raw-memory-1"
    assert calls["pipeline_update"][0].content == "replacement"
    assert update_result.status == "ok"
    assert calls["pipeline_delete"][0].id == "raw-memory-1"
    assert delete_result.status == "ok"
    assert calls["reader_get"] == (context, "raw-memory-1")
    assert raw_memory.memory_id == "raw-memory-1"


@pytest.mark.asyncio
async def test_versioned_update_builds_native_memory_entity_and_lineage_plan(
    tmp_path: Path,
) -> None:
    from mindmemos.typing import MemoryDbWritePlan, MemoryRequestContext

    module = importlib.import_module("homemaster.memory.mindmemos_runtime")
    runtime = module.EmbeddedMindMemOS(
        HomeMasterConfig(memory={"data_root": tmp_path / "memory", "embedding_dimensions": 8})
    )
    now = datetime(2026, 8, 17, tzinfo=UTC)
    old_record = {
        "schema_version": 1,
        "memory_type": "fact",
        "subject": {"type": "other", "name": "Aurora-A18", "id": None},
        "predicate": "package_manager",
        "value": "conda",
        "source": "user_statement",
    }
    current = SimpleNamespace(
        memory_id="old",
        content='Aurora-A18 的 package_manager 是 "conda"',
        mem_type="fact",
        mem_extract_type="schema",
        status="active",
        metadata={
            "request_metadata": {
                "add_record_ids": ["add-old"],
                "record_metadata": [{"record_json": json.dumps(old_record)}],
            },
            "entity_name": "Aurora-A18::package_manager",
        },
        validate_from=now,
        validate_to=None,
        root_id=["old"],
        property_name="fact_value",
        entity_id="entity-1",
        entity_type="fact",
        created_at=now,
    )
    calls: dict[str, Any] = {}

    class FakeEntityState:
        def to_entity_view(self, *, project_id: str):
            return SimpleNamespace(
                entity_name="Aurora-A18::package_manager",
                entity_type="fact",
                created_at=now,
                metadata={"search_fields": ["Aurora package manager"]},
            )

    class FakeReader:
        async def get_memory(self, context: Any, memory_id: str):
            return current if memory_id == "old" else None

        async def get_entity_with_memories(self, context: Any, entity_id: str):
            assert entity_id == "entity-1"
            return FakeEntityState()

    class FakeBuilder:
        async def build(self, **kwargs: Any):
            calls["build"] = kwargs
            return MemoryDbWritePlan(
                memories=kwargs["memories"],
                entities=kwargs["entities"],
                relationships=kwargs["relationships"],
            )

    class FakeWriter:
        async def apply_mutation_plan(self, context: Any, plan: Any, *, consistency: str):
            calls["plan"] = plan
            calls["consistency"] = consistency
            return SimpleNamespace(
                memory_ids=[plan.memory_writes[0].memory.memory_id],
                mutations=[SimpleNamespace(changed=True)],
                errors=[],
            )

    runtime._reader = FakeReader()
    runtime._writer = FakeWriter()
    runtime._schema_write_plan_builder = FakeBuilder()
    context = MemoryRequestContext(
        request_id="request-1",
        account_id="account-1",
        project_id="project-1",
        api_key_uuid="local",
        user_id="user-1",
    )
    new_record = {**old_record, "value": "uv"}

    result = await runtime.update_versioned(
        memory_id="old",
        content='Aurora-A18 的 package_manager 是 "uv"',
        metadata={
            "record_json": json.dumps(new_record),
            "memory_type": "fact",
            "provenance_seq": 2,
        },
        context=context,
    )

    assert result.status == "ok"
    assert result.memory_id != "old"
    build = calls["build"]
    new_memory = build["memories"][0]
    assert new_memory.parent_ids == ["old"]
    assert new_memory.root_id == ["old"]
    assert new_memory.content.endswith('"uv"')
    record_metadata = new_memory.metadata["request_metadata"]["record_metadata"][0]
    assert json.loads(record_metadata["record_json"])["value"] == "uv"
    assert record_metadata["provenance_seq"] == 2
    assert build["entities"][0].description.endswith('"uv"')
    assert {relationship.rel_type for relationship in build["relationships"]} == {
        "HAS_PROPERTY_MEMORY",
        "MENTIONS",
        "DERIVED_FROM",
    }
    plan = calls["plan"]
    assert plan.memory_updates[0].memory_id == "old"
    assert plan.memory_updates[0].status == "archived"
    assert plan.memory_updates[0].metadata_patch["derived_to"] == result.memory_id
    assert calls["consistency"] == "strong"


@pytest.mark.asyncio
async def test_history_reads_entire_lineage_component_and_orders_newest_first(
    tmp_path: Path,
) -> None:
    from mindmemos.typing import MemoryRequestContext

    module = importlib.import_module("homemaster.memory.mindmemos_runtime")
    runtime = module.EmbeddedMindMemOS(
        HomeMasterConfig(memory={"data_root": tmp_path / "memory", "embedding_dimensions": 8})
    )
    old = SimpleNamespace(
        memory_id="old",
        created_at=datetime(2026, 8, 17, tzinfo=UTC),
        update_at=None,
    )
    new = SimpleNamespace(
        memory_id="new",
        created_at=datetime(2026, 8, 18, tzinfo=UTC),
        update_at=None,
    )

    class FakeReader:
        async def get_memory(self, context: Any, memory_id: str):
            return {"old": old, "new": new}.get(memory_id)

    class FakeNeo4j:
        async def run_read(self, query: str, **kwargs: Any):
            assert "DERIVED_FROM*0.." in query
            assert kwargs == {"project_id": "project-1", "memory_id": "old"}
            return [{"memory_id": "old"}, {"memory_id": "new"}]

    runtime._reader = FakeReader()
    runtime._neo4j = FakeNeo4j()
    context = MemoryRequestContext(
        request_id="request-1",
        account_id="account-1",
        project_id="project-1",
        api_key_uuid="local",
        user_id="user-1",
    )

    versions = await runtime.get_history("old", context)

    assert [version.memory_id for version in versions] == ["new", "old"]
