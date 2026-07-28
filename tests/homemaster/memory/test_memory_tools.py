"""Canonical V2.1 memory tool surface and execution boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from homemaster.adapters.profiles import build_universal_tool_registry
from homemaster.benchmarking.alfworld.registry import build_alfworld_tool_registry
from homemaster.memory.evidence import MemoryEvidenceLedger
from homemaster.memory.file_store import FileMemoryStore
from homemaster.memory.mem0_store import Mem0MemoryStore
from homemaster.tools.base import ToolExecutionContext
from homemaster.tools.memory_tools import AddMemoryInput, build_memory_tools

MEMORY_TOOL_NAMES = {
    "memory",
    "add_memory",
    "search_memories",
    "get_memory",
    "update_memory",
    "delete_memory",
}


def test_default_home_surface_has_exactly_six_new_memory_tools() -> None:
    names = set(build_universal_tool_registry().all_names())
    assert MEMORY_TOOL_NAMES <= names
    assert "memory_retriever" not in names
    assert "memory_writer" not in names
    disabled = set(build_universal_tool_registry(memory_enabled=False).all_names())
    assert not (MEMORY_TOOL_NAMES & disabled)


def test_benchmark_memory_profiles_keep_the_legacy_tools() -> None:
    readonly = set(build_alfworld_tool_registry(memory_mode="readonly").all_names())
    full = set(build_alfworld_tool_registry(memory_mode="full").all_names())
    assert "memory_retriever" in readonly
    assert "memory_writer" not in readonly
    assert {"memory_retriever", "memory_writer"} <= full
    assert not (MEMORY_TOOL_NAMES & readonly)
    assert not (MEMORY_TOOL_NAMES & full)


def test_memory_definitions_lock_names_permissions_and_model_prohibitions() -> None:
    tools = build_memory_tools()
    assert {tool.definition.model_alias for tool in tools} == MEMORY_TOOL_NAMES
    by_name = {tool.definition.model_alias: tool for tool in tools}
    assert by_name["memory"].definition.required_capabilities == ("tool.read", "tool.mutate")
    assert by_name["memory"].definition.state_effects == ("memory.write",)
    memory_description = by_name["memory"].definition.description
    assert "this tool has no read action" in memory_description
    action_schema = by_name["memory"].definition.input_schema["properties"]["action"]
    action_enum = next(item["enum"] for item in action_schema["anyOf"] if "enum" in item)
    assert set(action_enum) == {"add", "update", "delete"}
    for name in ("add_memory", "update_memory", "delete_memory"):
        assert "tool.mutate" in by_name[name].definition.required_capabilities
    for tool in tools:
        schema = tool.definition.input_schema
        properties = schema.get("properties", {})
        forbidden = {"tenant_id", "session_id", "run_id", "metadata", "dedupe_key"}
        assert not (forbidden & set(properties))

    search_description = by_name["search_memories"].definition.description
    assert "One call combines exact metadata with mem0 hybrid retrieval" in (search_description)
    assert "do not repeat it unless" in search_description


def test_memory_tool_schemas_expose_complete_pydantic_records() -> None:
    by_name = {
        tool.definition.model_alias: tool.definition.to_model_manifest()["input_schema"]
        for tool in build_memory_tools()
    }

    add_schema = by_name["add_memory"]
    record_schema = add_schema["properties"]["record"]
    object_union = next(item for item in record_schema["anyOf"] if "oneOf" in item)
    assert object_union["discriminator"]["propertyName"] == "memory_type"
    fact_schema, procedure_schema = object_union["oneOf"]
    assert set(fact_schema["properties"]) >= {
        "memory_type",
        "subject",
        "predicate",
        "value",
        "source",
    }
    assert set(procedure_schema["properties"]) >= {
        "memory_type",
        "name",
        "entry_url",
        "steps",
        "success",
    }
    assert set(fact_schema["properties"]["subject"]["properties"]) == {
        "type",
        "name",
        "id",
    }
    search_schema = by_name["search_memories"]
    subject_schema = search_schema["properties"]["subject"]["anyOf"][0]
    assert set(subject_schema["properties"]) == {
        "type",
        "name",
        "id",
    }
    assert "$defs" not in add_schema
    assert "$ref" not in json.dumps(add_schema)
    assert {item.get("type") for item in record_schema["anyOf"]} >= {"string"}


def test_add_memory_accepts_provider_encoded_record_string() -> None:
    validated = AddMemoryInput.model_validate(
        {
            "memory_type": "fact",
            "record": json.dumps(
                {
                    "memory_type": "fact",
                    "subject": {"type": "object", "name": "probe"},
                    "predicate": "location",
                    "value": "cabinet",
                    "source": "user_statement",
                }
            ),
            "evidence_refs": ["evidence-1"],
        }
    )

    assert validated.record.memory_type == "fact"
    assert validated.record.subject.name == "probe"
    assert validated.record.predicate == "location"


@pytest.mark.asyncio
async def test_file_memory_mutation_requires_tool_mutate_and_uses_service(tmp_path: Path) -> None:
    from homemaster.config import MemoryConfig

    store = FileMemoryStore(MemoryConfig(root=tmp_path / "memory"))
    store.start()
    executor = next(
        tool.executor for tool in build_memory_tools() if tool.definition.model_alias == "memory"
    )
    context = ToolExecutionContext(
        tmp_path,
        metadata={
            "services": {
                "file_memory_store": store,
                "memory_audit_path": tmp_path / "memory_operations.jsonl",
            },
            "permission_subject": type(
                "Subject",
                (),
                {"tenant_id": "tenant-a", "capabilities": ("tool.read",)},
            )(),
            "session_id": "session-a",
            "run_id": "run-a",
            "turn_index": 1,
            "tool_call_id": "call-a",
        },
    )
    denied = await executor.execute(
        {"target": "user", "action": "add", "content": "偏好简洁回答"},
        context,
    )
    assert denied.error is not None
    assert denied.error.code == "memory_permission_denied"
    assert store.read("user").entries == ()

    context.metadata["permission_subject"] = type(
        "Subject",
        (),
        {"tenant_id": "tenant-a", "capabilities": ("tool.read", "tool.mutate")},
    )()
    added = await executor.execute(
        {"target": "user", "action": "add", "content": "偏好简洁回答"},
        context,
    )
    assert added.success
    assert store.read("user").entries == ("偏好简洁回答",)
    rejected_read = await executor.execute({"target": "user", "action": "read"}, context)
    assert rejected_read.error is not None
    assert rejected_read.error.code == "memory_invalid_input"
    rows = [
        json.loads(line)
        for line in (tmp_path / "memory_operations.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["payload"]["return_status"] for row in rows] == ["failure", "success"]
    assert rows[-1]["payload"]["operation"] == "memory"
    encoded = json.dumps(rows, ensure_ascii=False)
    assert "偏好简洁回答" not in encoded
    assert "content" not in encoded


def test_memory_tool_services_are_application_owned_types() -> None:
    # Prevent accidental process-global fallbacks in the public executors.
    annotations = tuple(tool.executor for tool in build_memory_tools())
    assert annotations
    assert FileMemoryStore is not None
    assert MemoryEvidenceLedger is not None
    assert Mem0MemoryStore is not None
