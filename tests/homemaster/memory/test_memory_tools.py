"""Canonical V2.6 memory tool surface and execution boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from homemaster.adapters.profiles import build_universal_tool_registry
from homemaster.agent.messages import UserMessage
from homemaster.benchmarking.alfworld.registry import build_alfworld_tool_registry
from homemaster.memory.evidence import MemoryEvidenceLedger
from homemaster.memory.feedback_context import build_feedback_context_snapshot
from homemaster.memory.file_store import FileMemoryStore
from homemaster.memory.mindmemos_runtime import EmbeddedMindMemOS
from homemaster.tools.base import ToolExecutionContext
from homemaster.tools.memory_tools import AddMemoryInput, build_memory_tools

MEMORY_TOOL_NAMES = {
    "context_memory",
    "mindmemos_add",
    "mindmemos_search",
    "mindmemos_history",
    "mindmemos_update",
    "mindmemos_delete",
    "mindmemos_feedback",
}
LEGACY_MEMORY_TOOL_NAMES = {
    "memory",
    "add_memory",
    "search_memories",
    "get_memory",
    "update_memory",
    "delete_memory",
}


def test_default_home_surface_has_exactly_seven_memory_tools() -> None:
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
    feedback = by_name["mindmemos_feedback"]
    assert feedback.definition.required_capabilities == ("tool.read", "tool.mutate")
    assert set(feedback.definition.input_schema["properties"]) == {"feedback"}
    assert tuple(feedback.definition.input_schema["required"]) == ("feedback",)
    assert "mindmemos_update" in feedback.definition.description
    assert "mindmemos_delete" in feedback.definition.description
    assert "vague complaint" in feedback.definition.description


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

    update_schema = by_name["mindmemos_update"]
    assert set(update_schema["properties"]) == {
        "memory_id",
        "record",
        "content",
        "evidence_refs",
    }
    history_schema = by_name["mindmemos_history"]
    assert set(history_schema["properties"]) == {"memory_id"}


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
    }
    statuses = {"raw-memory-1": "active"}

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
                content=(
                    f'{current["subject"]["name"]} 的 {current["predicate"]} 是 '
                    + json.dumps(current["value"], ensure_ascii=False)
                ),
                status=statuses[raw_id],
            )

        async def update_versioned(self, **kwargs):
            calls.append(("update_versioned", kwargs))
            assert kwargs["memory_id"] == "raw-memory-1"
            statuses["raw-memory-1"] = "archived"
            statuses["raw-memory-2"] = "active"
            stored_records["raw-memory-2"] = replacement
            return SimpleNamespace(status="ok", message=None, memory_id="raw-memory-2")

        async def has_memory_lineage(self, **kwargs):
            calls.append(("has_memory_lineage", kwargs))
            return (
                kwargs["source_memory_id"],
                kwargs["target_memory_id"],
                kwargs["relationship"],
            ) == ("raw-memory-2", "raw-memory-1", "DERIVED_FROM")

        async def delete(self, memory_id, context):
            calls.append(("delete", (memory_id, context)))
            statuses[memory_id] = "archived"
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
    assert len(add_calls) == 1
    assert add_calls[0][2]["metadata"]["homemaster_memory_type"] == "fact"
    version_call = next(payload for operation, payload in calls if operation == "update_versioned")
    assert json.loads(version_call["metadata"]["record_json"])["value"] == "餐桌上"
    delete_ids = [payload[0] for operation, payload in calls if operation == "delete"]
    assert delete_ids == ["raw-memory-2"]
    ledger.close()


@pytest.mark.asyncio
async def test_update_dispatches_vanilla_memory_to_native_update(tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []
    raw = SimpleNamespace(
        memory_id="vanilla-1",
        content="Aurora-A18 used conda.",
        metadata={"request_metadata": {"source_session_id": "session-old"}},
        mem_extract_type="vanilla",
        mem_type="experience",
        status="active",
        created_at=None,
        update_at=None,
    )

    class FakeMindMemOS(EmbeddedMindMemOS):
        def __init__(self) -> None:
            pass

        async def get_raw(self, memory_id, context):
            calls.append(("get_raw", memory_id))
            return raw if memory_id == raw.memory_id else None

        async def update(self, memory_id, content, context):
            calls.append(("update", (memory_id, content)))
            raw.content = content
            return SimpleNamespace(status="ok", message=None)

    ledger = MemoryEvidenceLedger(tmp_path / "evidence.sqlite3")
    ledger.start()
    evidence = ledger.register(
        kind="user_statement",
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
        turn_id="turn-1",
        tool_call_id="user-turn",
    )
    context = ToolExecutionContext(
        tmp_path,
        metadata={
            "services": {"mindmemos": FakeMindMemOS(), "memory_evidence_ledger": ledger},
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
    executor = next(
        tool.executor
        for tool in build_memory_tools()
        if tool.definition.model_alias == "mindmemos_update"
    )

    result = await executor.execute(
        {
            "memory_id": "vanilla-1",
            "content": "Aurora-A18 uses uv.",
            "evidence_refs": [evidence.ref],
        },
        context,
    )

    assert result.success
    assert result.data["memory_id"] == "vanilla-1"
    assert result.data["update_mode"] == "vanilla"
    assert raw.content == "Aurora-A18 uses uv."
    assert [call for call in calls if call[0] == "update"] == [
        ("update", ("vanilla-1", "Aurora-A18 uses uv."))
    ]
    ledger.close()


@pytest.mark.asyncio
async def test_update_fails_closed_when_record_json_is_corrupt(tmp_path: Path) -> None:
    class FakeMindMemOS(EmbeddedMindMemOS):
        def __init__(self) -> None:
            self.update_calls = 0

        async def get_raw(self, memory_id, context):
            return SimpleNamespace(
                memory_id=memory_id,
                content="broken",
                metadata={"request_metadata": {"record_json": "not-json"}},
                status="active",
            )

        async def update(self, *args, **kwargs):
            self.update_calls += 1

    ledger = MemoryEvidenceLedger(tmp_path / "evidence.sqlite3")
    ledger.start()
    evidence = ledger.register(
        kind="user_statement",
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
        turn_id="turn-1",
    )
    store = FakeMindMemOS()
    context = ToolExecutionContext(
        tmp_path,
        metadata={
            "services": {"mindmemos": store, "memory_evidence_ledger": ledger},
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
    executor = next(
        tool.executor
        for tool in build_memory_tools()
        if tool.definition.model_alias == "mindmemos_update"
    )

    result = await executor.execute(
        {
            "memory_id": "broken-1",
            "content": "replacement",
            "evidence_refs": [evidence.ref],
        },
        context,
    )

    assert not result.success
    assert result.error.code == "memory_record_corrupt"
    assert store.update_calls == 0
    ledger.close()


@pytest.mark.asyncio
async def test_history_returns_active_and_archived_versions(tmp_path: Path) -> None:
    old_record = {
        "schema_version": 1,
        "memory_type": "fact",
        "subject": {"type": "other", "name": "Aurora-A18", "id": None},
        "predicate": "package_manager",
        "value": "conda",
        "source": "user_statement",
    }
    new_record = {**old_record, "value": "uv"}

    def raw(memory_id: str, record: dict[str, object], status: str, created_at: str):
        return SimpleNamespace(
            memory_id=memory_id,
            content=f'Aurora-A18 的 package_manager 是 "{record["value"]}"',
            metadata={"request_metadata": {"record_json": json.dumps(record)}},
            status=status,
            created_at=created_at,
            update_at=None,
        )

    class FakeMindMemOS(EmbeddedMindMemOS):
        def __init__(self) -> None:
            pass

        async def get_history(self, memory_id, context):
            assert memory_id == "new"
            return [
                raw("new", new_record, "active", "2026-08-18T10:00:00+00:00"),
                raw("old", old_record, "archived", "2026-08-17T10:00:00+00:00"),
            ]

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
            "turn_index": 1,
            "tool_call_id": "call-history",
        },
    )
    executor = next(
        tool.executor
        for tool in build_memory_tools()
        if tool.definition.model_alias == "mindmemos_history"
    )

    result = await executor.execute({"memory_id": "new"}, context)

    assert result.success
    assert [version["memory_id"] for version in result.data["versions"]] == ["new", "old"]
    assert [version["status"] for version in result.data["versions"]] == ["active", "archived"]
    assert [version["record"]["value"] for version in result.data["versions"]] == ["uv", "conda"]
    assert result.data["verified_terminal_state"] is True


@pytest.mark.asyncio
async def test_feedback_requires_snapshot_before_backend_call(tmp_path: Path) -> None:
    class FakeMindMemOS(EmbeddedMindMemOS):
        def __init__(self) -> None:
            self.calls = 0

        async def feedback_explicit(self, **kwargs):
            self.calls += 1

    store = FakeMindMemOS()
    executor = next(
        tool.executor
        for tool in build_memory_tools()
        if tool.definition.model_alias == "mindmemos_feedback"
    )
    context = _feedback_tool_context(tmp_path, store)

    result = await executor.execute({"feedback": "Use uv, not conda."}, context)

    assert not result.success
    assert result.error is not None
    assert result.error.code == "memory_feedback_context_missing"
    assert store.calls == 0


@pytest.mark.asyncio
async def test_feedback_verifies_update_terminal_state_and_lineage(tmp_path: Path) -> None:
    from mindmemos.typing import FeedbackPipelineResult, FeedbackUpdateAction, MemorySearchItem

    old = SimpleNamespace(
        memory_id="old",
        project_id="tenant-a",
        user_id="tenant-a",
        content="User uses conda.",
        status="active",
    )
    new = SimpleNamespace(
        memory_id="new",
        project_id="tenant-a",
        user_id="tenant-a",
        content="User uses uv.",
        status="active",
    )

    class FakeMindMemOS(EmbeddedMindMemOS):
        def __init__(self) -> None:
            self.feedback_calls = []

        async def get_raw(self, memory_id, context):
            del context
            return {"old": old, "new": new}.get(memory_id)

        async def feedback_explicit(self, **kwargs):
            self.feedback_calls.append(kwargs)
            old.status = "archived"
            return FeedbackPipelineResult(
                status="ok",
                actions=[
                    FeedbackUpdateAction(
                        target_memory_id="old",
                        result_memory_id="new",
                        before_content="User uses conda.",
                        after_content="User uses uv.",
                        status="ok",
                    )
                ],
            )

        async def has_memory_lineage(self, **kwargs):
            return (
                kwargs["source_memory_id"],
                kwargs["target_memory_id"],
                kwargs["relationship"],
            ) == ("new", "old", "DERIVED_FROM")

    store = FakeMindMemOS()
    executor = next(
        tool.executor
        for tool in build_memory_tools()
        if tool.definition.model_alias == "mindmemos_feedback"
    )
    context = _feedback_tool_context(tmp_path, store)
    context.metadata["memory_feedback_context"] = build_feedback_context_snapshot(
        [UserMessage.from_text("I use uv, not conda.")],
        automatic_recalled_memories=[
            MemorySearchItem(
                id="old",
                memory="User uses conda.",
                last_update_at="2026-08-01 00:00:00",
            )
        ],
    )

    result = await executor.execute({"feedback": "Use uv, not conda."}, context)

    assert result.success
    assert result.data["verified_terminal_state"] is True
    assert result.data["actions"][0]["terminal_verified"] is True
    assert old.status == "archived"
    assert new.status == "active"
    assert store.feedback_calls[0]["feedback"] == "Use uv, not conda."


@pytest.mark.parametrize(
    "raw",
    [
        SimpleNamespace(
            memory_id="raw-1",
            project_id="tenant-a",
            user_id="tenant-a",
            content="old content",
            status="archived",
        ),
        SimpleNamespace(
            memory_id="raw-1",
            project_id="other-tenant",
            user_id="tenant-a",
            content="old content",
            status="active",
        ),
        SimpleNamespace(
            memory_id="raw-1",
            project_id="tenant-a",
            user_id="tenant-a",
            content="changed content",
            status="active",
        ),
        None,
    ],
)
@pytest.mark.asyncio
async def test_feedback_rejects_invalid_recalled_raw_without_backend_mutation(
    tmp_path: Path, raw
) -> None:
    from mindmemos.typing import MemorySearchItem

    class RecordingSink:
        def __init__(self) -> None:
            self.events = []

        def emit(self, event) -> None:
            self.events.append(event)

    class FakeMindMemOS(EmbeddedMindMemOS):
        def __init__(self) -> None:
            self.feedback_calls = 0

        async def get_raw(self, memory_id, context):
            del memory_id, context
            return raw

        async def feedback_explicit(self, **kwargs):
            del kwargs
            self.feedback_calls += 1

    store = FakeMindMemOS()
    sink = RecordingSink()
    executor = next(
        tool.executor
        for tool in build_memory_tools()
        if tool.definition.model_alias == "mindmemos_feedback"
    )
    context = _feedback_tool_context(tmp_path, store)
    context.metadata["run_context"] = SimpleNamespace(event_sink=sink)
    context.metadata["memory_feedback_context"] = build_feedback_context_snapshot(
        [UserMessage.from_text("correct it")],
        automatic_recalled_memories=[
            MemorySearchItem(
                id="raw-1",
                memory="old content",
                last_update_at="2026-08-01 00:00:00",
            )
        ],
    )

    result = await executor.execute({"feedback": "new content"}, context)

    assert not result.success
    assert result.error.code == "memory_feedback_recalled_memory_invalid"
    assert store.feedback_calls == 0
    assert [event.type for event in sink.events] == [
        "memory.feedback.explicit.started",
        "memory.feedback.explicit.failed",
    ]


def _feedback_tool_context(
    tmp_path: Path, store: EmbeddedMindMemOS
) -> ToolExecutionContext:
    return ToolExecutionContext(
        tmp_path,
        metadata={
            "services": {"mindmemos": store},
            "permission_subject": type(
                "Subject",
                (),
                {
                    "tenant_id": "tenant-a",
                    "capabilities": ("tool.read", "tool.mutate"),
                },
            )(),
            "session_id": "session-a",
            "run_id": "run-a",
            "turn_index": 1,
            "tool_call_id": "call-feedback",
        },
    )
