import json
from datetime import UTC, datetime
from types import SimpleNamespace

import mindmemos.pipelines.add.schema.schema_add as schema_add_pipeline
import mindmemos.workers.schema_add_episode as schema_add_episode
import pytest
from mindmemos.infra.kafka import ConsumedMessage
from mindmemos.pipelines.add.schema.schema_add import SchemaAddPipeline
from mindmemos.pipelines.memory_db import BufferedAddRecord
from mindmemos.prompts import get_add_prompts
from mindmemos.typing import (
    MemoryAddEventItem,
    MemoryDbWritePlan,
    MemoryDbWriteResult,
    MemoryRequestContext,
)


def make_message(body: dict) -> ConsumedMessage:
    return ConsumedMessage(
        topic=schema_add_episode.TOPIC,
        partition=0,
        offset=1,
        key=None,
        value=json.dumps(body).encode("utf-8"),
    )


@pytest.mark.asyncio
async def test_schema_add_episode_worker_calls_schema_pipeline_generate(monkeypatch) -> None:
    calls = []
    pipeline_names = []

    class ConfiguredSchemaPipeline:
        async def generate_episode(
            self, context, add_record_ids, *, episode_id, consistency=None, trigger_record_id=None
        ):
            calls.append(
                SimpleNamespace(
                    context=context,
                    add_record_ids=add_record_ids,
                    episode_id=episode_id,
                    consistency=consistency,
                    trigger_record_id=trigger_record_id,
                )
            )

    monkeypatch.setattr(
        schema_add_episode,
        "create_pipeline",
        lambda *, type, name: pipeline_names.append(name) or ConfiguredSchemaPipeline(),
    )

    await schema_add_episode.handle_schema_add_episode(
        make_message(
            {
                "context": {
                    "request_id": "req-1",
                    "account_id": "acc-1",
                    "project_id": "proj-1",
                    "api_key_uuid": "key-1",
                    "memory_algorithm": "schema",
                    "user_id": "user-1",
                    "session_id": "session-1",
                },
                "add_record_ids": ["record-1"],
                "episode_id": "episode-1",
                "consistency": "strong",
                "trigger_record_id": "trace-1",
            }
        )
    )

    assert pipeline_names == ["schema_add"]
    assert len(calls) == 1
    assert calls[0].context.project_id == "proj-1"
    assert calls[0].add_record_ids == ["record-1"]
    assert calls[0].episode_id == "episode-1"
    assert calls[0].consistency == "strong"
    assert calls[0].trigger_record_id == "trace-1"


@pytest.mark.asyncio
async def test_schema_add_episode_worker_rejects_pipeline_without_generate(monkeypatch) -> None:
    class MissingGeneratePipeline:
        pass

    monkeypatch.setattr(schema_add_episode, "create_pipeline", lambda *, type, name: MissingGeneratePipeline())

    with pytest.raises(RuntimeError, match="support generate_episode"):
        await schema_add_episode.handle_schema_add_episode(
            make_message(
                {
                    "context": {
                        "request_id": "req-1",
                        "account_id": "acc-1",
                        "project_id": "proj-1",
                        "api_key_uuid": "key-1",
                        "memory_algorithm": "schema",
                        "user_id": "user-1",
                        "session_id": "session-1",
                    },
                    "add_record_ids": ["record-1"],
                    "episode_id": "episode-1",
                }
            )
        )


def _pipeline_context() -> MemoryRequestContext:
    return MemoryRequestContext(
        request_id="request-1",
        account_id="account-1",
        project_id="project-1",
        api_key_uuid="local",
        user_id="user-1",
        session_id="session-1",
    )


def _fixed_record() -> BufferedAddRecord:
    episode = {
        "schema_version": "homemaster.schema_episode.v1",
        "session_id": "session-1",
        "events": [],
        "tool_steps": [],
        "provenance": {"source_event_ids": []},
    }
    return BufferedAddRecord(
        add_record_id="buffer-1",
        payload={
            "request_id": "request-1",
            "account_id": "account-1",
            "project_id": "project-1",
            "api_key_uuid": "local",
            "user_id": "user-1",
            "session_id": "session-1",
            "timestamp": 1789639200000,
            "added_at": datetime(2026, 9, 17, 10, tzinfo=UTC),
            "messages": [{"text": json.dumps(episode)}],
            "metadata": {"ingress": "homemaster_schema_episode_v1"},
        },
    )


def _candidate(domain: str) -> dict:
    return {
        "entities": [
            {
                "name": domain,
                "entity_type": domain,
                "description": domain,
                "properties": [
                    {
                        "property_name": "episode_record",
                        "value": json.dumps({"type": domain, "summary": domain}),
                    }
                ],
            }
        ],
        "edges": [],
    }


@pytest.mark.asyncio
async def test_fixed_episode_calls_three_extractors_one_planner_and_one_writer(monkeypatch) -> None:
    calls = {"extract": [], "planner": 0, "writer": 0}

    class Extractor:
        async def objectify_conversation(self, *args, **kwargs):
            return "objectified"

        async def generate_episode_description(self, *args, **kwargs):
            return "Episode\nDescription"

        async def extract_fixed_episode(self, *, entity_type, **kwargs):
            calls["extract"].append((entity_type, kwargs["conversation_text"]))
            return {
                "status": "completed",
                "candidate_count": 1,
                "raw_memory": _candidate(entity_type),
            }

    class Planner:
        async def build_write_plan(self, **kwargs):
            calls["planner"] += 1
            assert [entity["entity_type"] for entity in kwargs["raw_entities"]] == [
                "object_location",
                "search_observation",
                "task_procedure",
            ]
            events = [
                MemoryAddEventItem(
                    operation="add",
                    content=f"Entity: {domain} (Type: {domain})",
                    memory_id=f"entity-{domain}",
                    mem_type="experience" if domain == "task_procedure" else "fact",
                    related_memory_ids=[f"memory-{domain}"],
                )
                for domain in (
                    "object_location",
                    "search_observation",
                    "task_procedure",
                )
            ]
            return MemoryDbWritePlan(), events, [], []

        async def build_memory_update_commands(self, *args, **kwargs):
            return []

        def build_archive_memory_commands(self, *args, **kwargs):
            return []

        def memory_update_events(self, *args, **kwargs):
            return []

    class Writer:
        async def apply_mutation_plan(self, *args, **kwargs):
            calls["writer"] += 1
            return MemoryDbWriteResult()

    pipeline = SchemaAddPipeline.__new__(SchemaAddPipeline)
    pipeline._explicit_prompts = get_add_prompts("EN")
    pipeline._fixed_episode_prompts = {
        domain: "{entity_schema}{dialogue_timestamp}{provenance}{chat_chunk}"
        for domain in (
            "object_location",
            "search_observation",
            "task_procedure",
        )
    }
    pipeline._fixed_episode_validator = lambda domain, raw, episode: raw
    pipeline.db_writer = Writer()
    rt = SimpleNamespace(
        extractor=Extractor(),
        planner=Planner(),
        project_em=object(),
        use_search_fields=False,
    )
    monkeypatch.setattr(
        schema_add_pipeline,
        "get_config",
        lambda: SimpleNamespace(
            algo_config=SimpleNamespace(
                common=SimpleNamespace(prompt_language="EN")
            )
        ),
    )
    monkeypatch.setattr(schema_add_pipeline, "detect_prompt_language", lambda *args, **kwargs: "EN")

    result = await pipeline._generate_episode_memory(
        [_fixed_record()],
        context=_pipeline_context(),
        consistency="strong",
        episode_id="episode-1",
        rt=rt,
    )

    assert [domain for domain, _ in calls["extract"]] == [
        "object_location",
        "search_observation",
        "task_procedure",
    ]
    assert len({text for _, text in calls["extract"]}) == 1
    assert calls["planner"] == 1
    assert calls["writer"] == 1
    assert result.schema_episode.episode_id == "episode-1"
    assert result.schema_episode.write_status == "completed"
    for domain in ("object_location", "search_observation", "task_procedure"):
        assert result.schema_episode.types[domain].memory_ids == [f"memory-{domain}"]
        assert result.schema_episode.types[domain].memory_count == 1


@pytest.mark.asyncio
async def test_fixed_episode_all_empty_is_not_detected_and_still_uses_one_write(monkeypatch) -> None:
    calls = {"planner": 0, "writer": 0}

    class Extractor:
        async def objectify_conversation(self, *args, **kwargs):
            return "objectified"

        async def generate_episode_description(self, *args, **kwargs):
            return "Episode\nDescription"

        async def extract_fixed_episode(self, **kwargs):
            return {
                "status": "not_detected",
                "candidate_count": 0,
                "raw_memory": {"entities": [], "edges": []},
            }

    class Planner:
        async def build_write_plan(self, **kwargs):
            calls["planner"] += 1
            assert kwargs["raw_entities"] == []
            return MemoryDbWritePlan(), [], [], []

        async def build_memory_update_commands(self, *args, **kwargs):
            return []

        def build_archive_memory_commands(self, *args, **kwargs):
            return []

        def memory_update_events(self, *args, **kwargs):
            return []

    class Writer:
        async def apply_mutation_plan(self, *args, **kwargs):
            calls["writer"] += 1
            return MemoryDbWriteResult()

    pipeline = SchemaAddPipeline.__new__(SchemaAddPipeline)
    pipeline._explicit_prompts = get_add_prompts("EN")
    pipeline._fixed_episode_prompts = {
        domain: "prompt"
        for domain in (
            "object_location",
            "search_observation",
            "task_procedure",
        )
    }
    pipeline._fixed_episode_validator = None
    pipeline.db_writer = Writer()
    rt = SimpleNamespace(
        extractor=Extractor(),
        planner=Planner(),
        project_em=object(),
        use_search_fields=False,
    )
    monkeypatch.setattr(
        schema_add_pipeline,
        "get_config",
        lambda: SimpleNamespace(
            algo_config=SimpleNamespace(
                common=SimpleNamespace(prompt_language="EN")
            )
        ),
    )
    monkeypatch.setattr(schema_add_pipeline, "detect_prompt_language", lambda *args, **kwargs: "EN")

    result = await pipeline._generate_episode_memory(
        [_fixed_record()],
        context=_pipeline_context(),
        consistency="strong",
        rt=rt,
    )

    assert calls == {"planner": 1, "writer": 1}
    assert result.events == []
    assert result.schema_episode.write_status == "not_detected"
    assert all(
        item.status == "not_detected"
        for item in result.schema_episode.types.values()
    )


@pytest.mark.asyncio
async def test_fixed_episode_add_record_persistence_failure_is_propagated(monkeypatch) -> None:
    from mindmemos.typing import AddPipelineInput, AddPipelineSyncResult, TextMessage

    class Buffer:
        async def append(self, *args, **kwargs):
            return ["buffer-1"]

        async def get_by_ids(self, *args, **kwargs):
            return [object()]

        async def mark_processing(self, *args, **kwargs):
            return None

    class Recorder:
        def __init__(self):
            self.failed = None

        async def mark_add_completed(self, *args, **kwargs):
            raise RuntimeError("add record persistence failed")

        async def mark_add_failed(self, *args, **kwargs):
            self.failed = (args, kwargs)

    pipeline = SchemaAddPipeline.__new__(SchemaAddPipeline)
    pipeline.add_buffer = Buffer()
    pipeline.recorder = Recorder()
    pipeline._get_consistency = lambda: "strong"
    pipeline._resolve_add_runtime = lambda _context: object()

    async def execute(*args, **kwargs):
        del args, kwargs
        return schema_add_pipeline._EpisodeExecutionResult(events=[], schema_episode=None)

    pipeline._execute_episode_task = execute
    inp = AddPipelineInput(
        messages=[TextMessage(text=json.dumps({"schema_version": "homemaster.schema_episode.v1"}))],
        mode="sync",
        force_generation=True,
        metadata={"ingress": "homemaster_schema_episode_v1"},
    )
    with pytest.raises(RuntimeError, match="add record persistence failed"):
        await pipeline.add_sync(inp, _pipeline_context(), add_record_id="record-1")
    assert pipeline.recorder.failed[0][1] == "record-1"
