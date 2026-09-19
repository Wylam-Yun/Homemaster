import json
from datetime import UTC, datetime
from types import SimpleNamespace

import mindmemos.components.extractor._records as records
import pytest
from mindmemos.pipelines.add.schema.schema_add import _request_prompt_set
from mindmemos.prompts import get_add_prompts
from mindmemos.components.extractor.schema._schema_utils import (
    build_episode_entity,
    build_filtered_schema,
    dedupe_non_empty,
    entity_embedding_text,
    parse_json_object,
    schema_memory_type,
    strip_for_generation,
)
from mindmemos.components.extractor.schema.search_field import SchemaSearchFieldExtractor
from mindmemos.components.extractor.schema.schema_extractor import SchemaAddExtractor
from mindmemos.typing.memory import MemoryRequestContext


def make_context() -> MemoryRequestContext:
    return MemoryRequestContext(
        request_id="fallback-req",
        account_id="fallback-account",
        project_id="proj-1",
        api_key_uuid="fallback-key",
        user_id="fallback-user",
        session_id="fallback-session",
    )


def test_schema_add_request_prompt_honors_explicit_entity_generation() -> None:
    explicit = get_add_prompts("EN")
    explicit = type(explicit)(
        **{
            field: ("compact entity prompt" if field == "entity_generation" else getattr(explicit, field))
            for field in explicit.__dataclass_fields__
        }
    )

    selected = _request_prompt_set("ZH", explicit)

    assert selected.entity_generation == "compact entity prompt"
    assert selected.episode_description == get_add_prompts("ZH").episode_description


def test_record_operators_restore_context_and_conversation_text() -> None:
    record = SimpleNamespace(
        add_record_id="record-1",
        payload={
            "request_id": "req-1",
            "account_id": "acc-1",
            "project_id": "proj-1",
            "api_key_uuid": "key-1",
            "user_id": "user-1",
            "session_id": "session-1",
            "timestamp": 1770000000000,
            "messages": [{"role": "user", "content": "I like Qdrant."}],
            "metadata": {"buffer_message_index": 0},
            "force_generation": True,
        },
    )

    ctx = records.context([record], make_context())

    assert ctx.request_id == "req-1"
    assert ctx.user_id == "user-1"
    assert records.force_generation([record]) is True
    assert records.records_datetime([record]) == datetime(2026, 2, 2, 2, 40, tzinfo=UTC)
    assert records.metadata([record]) == {
        "add_record_ids": ["record-1"],
        "record_metadata": [{"buffer_message_index": 0}],
    }
    assert records.to_chunker_entries([record]) == [
        {
            "content": "I like Qdrant.",
            "speaker": "user",
            "timestamp": "2026-02-02 02:40:00",
            "add_record_id": "record-1",
        }
    ]
    assert records.to_conversation_text([record]) == "0. 2026-02-02 02:40:00 user: I like Qdrant."


def test_schema_add_conversation_text_preserves_named_speaker_identity() -> None:
    record = SimpleNamespace(
        add_record_id="record-rose",
        payload={
            "timestamp": 1770000000000,
            "messages": [{"role": "Rose", "content": "I moved to Boston."}],
        },
    )

    assert records.to_chunker_entries([record]) == [
        {
            "content": "I moved to Boston.",
            "speaker": "Rose",
            "timestamp": "2026-02-02 02:40:00",
            "add_record_id": "record-rose",
        }
    ]
    assert records.to_conversation_text([record]) == ("0. 2026-02-02 02:40:00 speaker=Rose: I moved to Boston.")


def test_schema_add_utils_filter_schema_and_build_episode_entity() -> None:
    schema = [
        {
            "entity_type": "user",
            "entity_description": "User profile",
            "dynamic_property": {
                "default_property": {"desc": "Fallback"},
                "preference": {"desc": "Preference"},
                "preference_summary": {"desc": "High order", "order": 2},
            },
        },
        {"entity_type": "episodes", "dynamic_property": {"input_messages": {"desc": "Episode"}}},
    ]

    generation_schema = strip_for_generation(schema)
    filtered_schema = build_filtered_schema(
        generation_schema,
        [{"entity_type": "user", "relevant_properties": ["preference"]}],
    )
    episode_entity = build_episode_entity(
        objectified_content="The user said they like Qdrant.",
        episode_description="Qdrant preference\nThe user likes Qdrant.",
        dialogue_date="2026-02-02",
        search_fields=["Qdrant preference"],
    )

    assert generation_schema == [
        {
            "entity_type": "user",
            "entity_description": "User profile",
            "dynamic_property": {
                "default_property": {"desc": "Fallback"},
                "preference": {"desc": "Preference"},
            },
        }
    ]
    assert filtered_schema[0]["dynamic_property"] == {
        "default_property": {"desc": "Fallback"},
        "preference": {"desc": "Preference"},
    }
    assert episode_entity["entity_type"] == "episodes"
    assert episode_entity["properties"][0]["property_name"] == "input_messages"
    assert "Qdrant" in entity_embedding_text(episode_entity)
    assert dedupe_non_empty([" a ", "", "a", "b"]) == ["a", "b"]


@pytest.mark.asyncio
async def test_schema_search_field_extractor_uses_properties_then_dedupes() -> None:
    extractor = SchemaSearchFieldExtractor()

    fields = await extractor.extract_search_fields(
        entities=[
            {
                "entity_type": "user",
                "description": "fallback description",
                "properties": [
                    {"property_name": "preference", "value": "Likes Qdrant"},
                    {"property_name": "preference", "value": "Likes Qdrant"},
                    {"property_name": "input_messages", "value": "ignored"},
                ],
            },
            {"entity_type": "episodes", "description": "ignored episode"},
            {"entity_type": "project", "description": "Uses Neo4j"},
        ],
        context_text="conversation",
        max_fields=3,
    )

    assert fields == ["Likes Qdrant", "Uses Neo4j"]


@pytest.mark.asyncio
async def test_schema_search_field_extractor_can_augment_fields() -> None:
    class FakeLLM:
        async def chat(self, **kwargs):
            return SimpleNamespace(parsed=["Augmented Qdrant query"])

    prompt_set = SimpleNamespace(
        episode_search_field_augment="{episode_text}\n{existing_fields}\n{augment_count}",
    )
    extractor = SchemaSearchFieldExtractor(llm_client=FakeLLM(), prompt_set=prompt_set)

    fields = await extractor.extract_search_fields(
        entities=[{"entity_type": "project", "description": "Uses Qdrant"}],
        context_text="conversation",
        max_fields=3,
        augment=True,
        augment_count=1,
    )

    assert fields == ["Uses Qdrant", "Augmented Qdrant query"]


def test_parse_json_object_handles_fenced_json() -> None:
    assert parse_json_object('```json\n{"ok": true}\n```') == {"ok": True}


@pytest.mark.parametrize(
    ("entity_type", "property_name", "expected"),
    [
        ("episodes", "default_property", "episodic"),
        ("episode", "default_property", "episodic"),
        ("user", "preference", "profile"),
        ("person", "preference", "profile"),
        ("person", "task_experience", "experience"),
        ("task_experience", "default_property", "experience"),
        ("task", "task_experience", "experience"),
        ("object_location", "episode_record", "fact"),
        ("search_observation", "episode_record", "fact"),
        ("task_procedure", "episode_record", "experience"),
        ("episodes", "episode_record", "episodic"),
        ("organization", "service_info", "fact"),
        (None, None, "fact"),
    ],
)
def test_schema_memory_type_maps_schema_labels_to_display_types(entity_type, property_name, expected) -> None:
    assert schema_memory_type(entity_type, property_name) == expected


@pytest.mark.parametrize("content", ["not-json", "{bad"])
def test_parse_json_object_raises_for_invalid_json(content: str) -> None:
    with pytest.raises(ValueError):
        parse_json_object(content)


class _FixedEntityManager:
    def get_all_dicts(self):
        return [
            {
                "entity_type": entity_type,
                "dynamic_property": {"episode_record": {"type": "string"}},
            }
            for entity_type in (
                "object_location",
                "search_observation",
                "task_procedure",
                "episodes",
            )
        ]

    def list_types(self):
        return [item["entity_type"] for item in self.get_all_dicts()]


class _FixedLlm:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.tasks = []

    async def chat(self, *, task, messages, format_parser):
        self.tasks.append(task)
        parsed = self.outputs.pop(0)
        return SimpleNamespace(parsed=parsed)


def _fixed_raw(entity_type: str) -> dict:
    return {
        "entities": [
            {
                "name": f"candidate-{entity_type}",
                "entity_type": entity_type,
                "description": "summary",
                "properties": [
                    {
                        "property_name": "episode_record",
                        "value": json.dumps(
                            {"type": entity_type, "summary": "summary"}
                        ),
                    }
                ],
            }
        ],
        "edges": [],
    }


@pytest.mark.asyncio
async def test_fixed_schema_extractor_skips_router_and_reports_empty() -> None:
    llm = _FixedLlm([{"entities": [], "edges": []}])
    extractor = SchemaAddExtractor(
        llm_client=llm,
        prompt_set=SimpleNamespace(),
        entity_manager=_FixedEntityManager(),
        enable_schema_selection=True,
    )

    result = await extractor.extract_fixed_episode(
        entity_type="object_location",
        conversation_text="episode",
        dialogue_timestamp="2026-09-17 10:00:00",
        provenance={"source_event_ids": ["evt-1"]},
        prompt_template="{entity_schema}|{dialogue_timestamp}|{provenance}|{chat_chunk}",
    )

    assert result["status"] == "not_detected"
    assert result["candidate_count"] == 0
    assert llm.tasks == ["memory.add.fixed_episode.object_location"]


@pytest.mark.asyncio
async def test_fixed_schema_extractor_accepts_only_its_own_type() -> None:
    llm = _FixedLlm([_fixed_raw("search_observation")])
    extractor = SchemaAddExtractor(
        llm_client=llm,
        prompt_set=SimpleNamespace(),
        entity_manager=_FixedEntityManager(),
        enable_schema_selection=True,
    )

    result = await extractor.extract_fixed_episode(
        entity_type="search_observation",
        conversation_text="episode",
        dialogue_timestamp="2026-09-17 10:00:00",
        provenance={},
        prompt_template="{entity_schema}{dialogue_timestamp}{provenance}{chat_chunk}",
    )

    assert result["status"] == "completed"
    assert result["candidate_count"] == 1
    assert result["raw_memory"]["entities"][0]["entity_type"] == "search_observation"


@pytest.mark.asyncio
async def test_fixed_schema_extractor_rejects_cross_type_output_after_retries() -> None:
    wrong = _fixed_raw("task_procedure")
    llm = _FixedLlm([wrong, wrong, wrong])
    extractor = SchemaAddExtractor(
        llm_client=llm,
        prompt_set=SimpleNamespace(),
        entity_manager=_FixedEntityManager(),
        enable_schema_selection=False,
    )

    with pytest.raises(ValueError, match="object_location emitted entity type task_procedure"):
        await extractor.extract_fixed_episode(
            entity_type="object_location",
            conversation_text="episode",
            dialogue_timestamp="2026-09-17 10:00:00",
            provenance={},
            prompt_template="{entity_schema}{dialogue_timestamp}{provenance}{chat_chunk}",
        )

    assert len(llm.tasks) == 3
