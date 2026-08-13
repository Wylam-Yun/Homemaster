"""Canonical V2.1 memory tool surface and execution boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from homemaster.adapters.profiles import build_universal_tool_registry
from homemaster.benchmarking.alfworld.registry import build_alfworld_tool_registry
from homemaster.memory.evidence import MemoryEvidenceLedger
from homemaster.memory.file_store import FileMemoryStore
from homemaster.memory.mindmemos_runtime import EmbeddedMindMemOS
from homemaster.tools.base import ToolExecutionContext
from homemaster.tools.memory_tools import AddMemoryInput, build_memory_tools

MEMORY_TOOL_NAMES = {
    "context_memory",
    "mindmemos_add",
    "mindmemos_search",
    "mindmemos_update",
    "mindmemos_delete",
}
LEGACY_MEMORY_TOOL_NAMES = {
    "memory",
    "add_memory",
    "search_memories",
    "get_memory",
    "update_memory",
    "delete_memory",
}


def test_default_home_surface_has_exactly_five_memory_tools() -> None:
    names = set(build_universal_tool_registry().all_names())
    assert MEMORY_TOOL_NAMES <= names
    assert not (LEGACY_MEMORY_TOOL_NAMES & names)
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
    assert by_name["context_memory"].definition.required_capabilities == (
        "tool.read",
        "tool.mutate",
    )
    assert by_name["context_memory"].definition.state_effects == ("memory.write",)
    memory_description = by_name["context_memory"].definition.description
    assert "injected into future sessions" in memory_description
    action_schema = by_name["context_memory"].definition.input_schema["properties"]["action"]
    action_enum = next(item["enum"] for item in action_schema["anyOf"] if "enum" in item)
    assert set(action_enum) == {"add", "update", "delete"}
    for name in ("mindmemos_add", "mindmemos_update", "mindmemos_delete"):
        assert "tool.mutate" in by_name[name].definition.required_capabilities
    for tool in tools:
        schema = tool.definition.input_schema
        properties = schema.get("properties", {})
        forbidden = {"tenant_id", "session_id", "run_id", "metadata", "dedupe_key"}
        assert not (forbidden & set(properties))

    search_description = by_name["mindmemos_search"].definition.description
    assert "Search long-term memory by meaning" in search_description
    assert "experiences learned from past sessions" in search_description
    assert "Search again" in search_description


def test_memory_tool_schemas_expose_complete_pydantic_records() -> None:
    by_name = {
        tool.definition.model_alias: tool.definition.to_model_manifest()["input_schema"]
        for tool in build_memory_tools()
    }

    add_schema = by_name["mindmemos_add"]
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
    search_schema = by_name["mindmemos_search"]
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

    store = FileMemoryStore(MemoryConfig(data_root=tmp_path / "memory"))
    store.start()
    executor = next(
        tool.executor
        for tool in build_memory_tools()
        if tool.definition.model_alias == "context_memory"
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
    assert rows[-1]["payload"]["operation"] == "context_memory"
    encoded = json.dumps(rows, ensure_ascii=False)
    assert "偏好简洁回答" not in encoded
    assert "content" not in encoded


def test_memory_tool_services_are_application_owned_types() -> None:
    # Prevent accidental process-global fallbacks in the public executors.
    annotations = tuple(tool.executor for tool in build_memory_tools())
    assert annotations
    assert FileMemoryStore is not None
    assert MemoryEvidenceLedger is not None
    assert EmbeddedMindMemOS is not None


@pytest.mark.asyncio
async def test_search_memories_uses_embedded_mindmemos_and_returns_original_record(
    tmp_path: Path,
) -> None:
    from mindmemos.typing import MemorySearchItem

    calls: dict[str, object] = {}
    record = {
        "schema_version": 1,
        "memory_type": "fact",
        "subject": {"type": "object", "name": "苹果", "id": None},
        "predicate": "location",
        "value": "冰箱第二层",
        "source": "environment_observation",
    }

    class FakeMindMemOS(EmbeddedMindMemOS):
        def __init__(self) -> None:
            pass

        async def search(self, query, context, **kwargs):
            calls["search"] = (query, context, kwargs)
            return SimpleNamespace(
                status="ok",
                memories=[
                    MemorySearchItem(
                        id="raw-memory-1",
                        memory="苹果 的 location 是 冰箱第二层",
                        memory_type="fact",
                        last_update_at="2026-08-06 10:00:00",
                    )
                ],
            )

        async def get_raw(self, memory_id, context):
            calls["get_raw"] = (memory_id, context)
            return SimpleNamespace(
                memory_id=memory_id,
                metadata={
                    "request_metadata": {
                        "add_record_ids": ["add-1"],
                        "record_metadata": [
                            {"record_json": json.dumps(record, ensure_ascii=False)}
                        ],
                    }
                },
                created_at=None,
                update_at=None,
            )

    executor = next(
        tool.executor
        for tool in build_memory_tools()
        if tool.definition.model_alias == "mindmemos_search"
    )
    context = ToolExecutionContext(
        tmp_path,
        metadata={
            "services": {"mindmemos": FakeMindMemOS()},
            "permission_subject": type(
                "Subject",
                (),
                {"tenant_id": "tenant-a", "capabilities": ("tool.read",)},
            )(),
            "session_id": "session-a",
            "run_id": "run-a",
            "turn_index": 2,
            "tool_call_id": "call-search",
        },
    )

    result = await executor.execute(
        {"query": "苹果在哪里", "memory_type": "fact", "limit": 5},
        context,
    )

    assert result.success
    assert result.data["records"][0]["memory_id"] == "raw-memory-1"
    assert result.data["records"][0]["record"] == record
    query, memory_context, kwargs = calls["search"]
    assert query == "苹果在哪里"
    assert memory_context.project_id == "tenant-a"
    assert memory_context.session_id == "session-a"
    assert kwargs["search_pipeline"] == "vanilla"
    assert kwargs["filters"] == {"mem_type": "fact"}


@pytest.mark.asyncio
async def test_search_memories_returns_native_vanilla_experience(tmp_path: Path) -> None:
    from mindmemos.typing import MemorySearchItem

    content = "外部写操作超时后结果未知，不能自动重试，应先只读查询真实终态。"

    class FakeMindMemOS(EmbeddedMindMemOS):
        def __init__(self) -> None:
            pass

        async def search(self, query, context, **kwargs):
            return SimpleNamespace(
                status="ok",
                memories=[
                    MemorySearchItem(
                        id="experience-1",
                        memory=content,
                        memory_type="experience",
                        last_update_at="2026-08-13 00:00:00",
                    ),
                    MemorySearchItem(
                        id="malformed-schema-1",
                        memory="invalid",
                        memory_type="fact",
                        last_update_at="2026-08-13 00:00:00",
                    ),
                ],
            )

        async def get_raw(self, memory_id, context):
            if memory_id == "experience-1":
                return SimpleNamespace(
                    memory_id=memory_id,
                    mem_extract_type="vanilla",
                    mem_type="experience",
                    content=content,
                    status="active",
                    session_id="session-source",
                    metadata={
                        "request_metadata": {
                            "source_type": "homemaster_task_trace",
                            "source_session_id": "session-source",
                            "trace_schema_version": "homemaster-task-trace-v1",
                        }
                    },
                    created_at=None,
                    update_at=None,
                )
            return SimpleNamespace(
                memory_id=memory_id,
                mem_extract_type="schema",
                mem_type="fact",
                content="invalid",
                status="active",
                metadata={"request_metadata": {"record_json": "not-json"}},
                created_at=None,
                update_at=None,
            )

    executor = next(
        tool.executor
        for tool in build_memory_tools()
        if tool.definition.model_alias == "mindmemos_search"
    )
    context = ToolExecutionContext(
        tmp_path,
        metadata={
            "services": {"mindmemos": FakeMindMemOS()},
            "permission_subject": type(
                "Subject",
                (),
                {"tenant_id": "tenant-a", "capabilities": ("tool.read",)},
            )(),
            "session_id": "session-a",
            "run_id": "run-a",
            "turn_index": 2,
            "tool_call_id": "call-search",
        },
    )

    result = await executor.execute(
        {"query": "写操作超时", "memory_type": "procedure", "limit": 5}, context
    )

    assert result.success
    assert result.data["count"] == 1
    record = result.data["records"][0]
    assert record["memory_id"] == "experience-1"
    assert record["memory_type"] == "procedure"
    assert record["content"] == content
    assert dict(record["source"]) == {
        "source_type": "homemaster_task_trace",
        "source_session_id": "session-source",
        "trace_schema_version": "homemaster-task-trace-v1",
    }
    assert record["match_sources"] == ("semantic",)
    assert record["verified_terminal_state"] is True
    assert len(result.data["diagnostics"]) == 1
    assert result.data["diagnostics"][0]["code"] == "memory_record_corrupt"


@pytest.mark.asyncio
async def test_structured_memory_crud_uses_embedded_mindmemos(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, object]] = []
    record = {
        "schema_version": 1,
        "memory_type": "fact",
        "subject": {"type": "object", "name": "苹果", "id": None},
        "predicate": "location",
        "value": "冰箱第二层",
        "source": "environment_observation",
    }
    replacement = {**record, "value": "餐桌上"}
    stored_records = {
        "raw-memory-1": record,
        "raw-memory-2": replacement,
    }

    class FakeMindMemOS(EmbeddedMindMemOS):
        def __init__(self) -> None:
            self.next_id = 1

        async def add(self, messages, context, **kwargs):
            memory_id = f"raw-memory-{self.next_id}"
            episode_id = f"episode-memory-{self.next_id}"
            self.next_id += 1
            calls.append(("add", (messages, context, kwargs)))
            return SimpleNamespace(
                status="ok",
                memories=[
                    SimpleNamespace(
                        memory_id=memory_id,
                        related_memory_ids=[episode_id, memory_id],
                        memory_type="fact",
                        content=messages[0].text,
                    )
                ],
            )

        async def get_raw(self, memory_id, context):
            calls.append(("get_raw", (memory_id, context)))
            is_episode = memory_id.startswith("episode-memory-")
            raw_id = memory_id.replace("episode-memory-", "raw-memory-")
            current = stored_records.get(raw_id)
            if current is None:
                return None
            provenance_seq = 1 if memory_id == "raw-memory-1" else 2
            return SimpleNamespace(
                memory_id=memory_id,
                mem_type="episodic" if is_episode else "fact",
                metadata={
                    "request_metadata": {
                        "add_record_ids": ["add-1"],
                        "record_metadata": [
                            {
                                "record_json": json.dumps(current, ensure_ascii=False),
                                "provenance_seq": provenance_seq,
                            }
                        ],
                    }
                },
                created_at=None,
                update_at=None,
                status="active",
            )

        async def delete(self, memory_id, context):
            calls.append(("delete", (memory_id, context)))
            return SimpleNamespace(status="ok", message=None)

    ledger = MemoryEvidenceLedger(tmp_path / "evidence.sqlite3")
    ledger.start()
    first_evidence = ledger.register(
        kind="environment_observation",
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
        turn_id="turn-1",
        tool_call_id="environment-call-1",
    )
    second_evidence = ledger.register(
        kind="environment_observation",
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
        turn_id="turn-1",
        tool_call_id="environment-call-2",
    )
    runtime = FakeMindMemOS()
    context = ToolExecutionContext(
        tmp_path,
        metadata={
            "services": {"mindmemos": runtime, "memory_evidence_ledger": ledger},
            "permission_subject": type(
                "Subject",
                (),
                {"tenant_id": "tenant-a", "capabilities": ("tool.read", "tool.mutate")},
            )(),
            "session_id": "session-a",
            "run_id": "run-a",
            "turn_index": 1,
            "tool_call_id": "call-memory",
        },
    )
    executors = {tool.definition.model_alias: tool.executor for tool in build_memory_tools()}

    added = await executors["mindmemos_add"].execute(
        {
            "memory_type": "fact",
            "record": record,
            "evidence_refs": [first_evidence.ref],
        },
        context,
    )
    updated = await executors["mindmemos_update"].execute(
        {
            "memory_id": "raw-memory-1",
            "record": replacement,
            "evidence_refs": [second_evidence.ref],
        },
        context,
    )
    deleted = await executors["mindmemos_delete"].execute(
        {"memory_id": "raw-memory-2"},
        context,
    )

    assert added.success
    assert added.data["memory_id"] == "raw-memory-1"
    assert updated.success
    assert updated.data["memory_id"] == "raw-memory-2"
    assert deleted.success
    add_calls = [payload for operation, payload in calls if operation == "add"]
    assert len(add_calls) == 2
    assert add_calls[0][2]["metadata"]["homemaster_memory_type"] == "fact"
    assert json.loads(add_calls[1][2]["metadata"]["record_json"])["value"] == "餐桌上"
    delete_ids = [payload[0] for operation, payload in calls if operation == "delete"]
    assert delete_ids == ["raw-memory-1", "raw-memory-2"]
    ledger.close()
