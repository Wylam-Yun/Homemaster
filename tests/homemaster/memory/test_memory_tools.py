"""Canonical V2.6 memory tool surface and execution boundaries."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from homemaster.adapters.profiles import build_universal_tool_registry
from homemaster.agent.messages import UserMessage
from homemaster.benchmarking.alfworld.registry import build_alfworld_tool_registry
from homemaster.memory.add_queue import MemoryAddQueue
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
    assert "native MindMemOS types" in search_description
    assert "tool traces" in search_description
    assert "Search again" in search_description
    feedback = by_name["mindmemos_feedback"]
    assert feedback.definition.required_capabilities == ("tool.read", "tool.mutate")
    assert set(feedback.definition.input_schema["properties"]) == {"feedback"}
    assert tuple(feedback.definition.input_schema["required"]) == ("feedback",)
    assert "mindmemos_update" in feedback.definition.description
    assert "mindmemos_delete" in feedback.definition.description
    assert "vague complaint" in feedback.definition.description


def test_memory_tool_schemas_expose_direct_flat_add_and_legacy_structured_update() -> None:
    by_name = {
        tool.definition.model_alias: tool.definition.to_model_manifest()["input_schema"]
        for tool in build_memory_tools()
    }

    add_schema = by_name["mindmemos_add"]
    assert set(add_schema["properties"]) == {"content", "memory_type"}
    assert set(add_schema["required"]) == {"content", "memory_type"}
    assert add_schema["properties"]["memory_type"]["enum"] == ["fact", "procedure"]
    search_schema = by_name["mindmemos_search"]
    search_type_schema = search_schema["properties"]["memory_type"]
    search_type_enum = next(
        item["enum"] for item in search_type_schema["anyOf"] if "enum" in item
    )
    assert search_type_enum == [
        "profile",
        "fact",
        "experience",
        "episodic",
        "tool_trace",
        "skill_candidate",
        "file_knowledge",
    ]
    subject_schema = search_schema["properties"]["subject"]["anyOf"][0]
    assert set(subject_schema["properties"]) == {
        "type",
        "name",
        "id",
    }
    assert "$defs" not in add_schema
    assert "$ref" not in json.dumps(add_schema)

    update_schema = by_name["mindmemos_update"]
    assert set(update_schema["properties"]) == {
        "memory_id",
        "record",
        "content",
    }
    assert "evidence_refs" not in add_schema["properties"]
    history_schema = by_name["mindmemos_history"]
    assert set(history_schema["properties"]) == {"memory_id"}


def test_add_memory_accepts_exact_content_and_rejects_legacy_record() -> None:
    validated = AddMemoryInput.model_validate(
        {
            "memory_type": "fact",
            "content": "  probe is in cabinet  ",
        }
    )

    assert validated.content == "  probe is in cabinet  "
    with pytest.raises(ValidationError):
        AddMemoryInput.model_validate(
            {"memory_type": "fact", "record": {"memory_type": "fact"}}
        )


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
                        id="direct-fact-1",
                        memory="  Aurora-A18 uses uv.  ",
                        memory_type="fact",
                        last_update_at="2026-08-18 00:00:00",
                    ),
                    MemorySearchItem(
                        id="tool-trace-1",
                        memory="terminal returned exit code 7 for the deployment check",
                        memory_type="tool_trace",
                        last_update_at="2026-08-18 00:00:00",
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
            if memory_id == "tool-trace-1":
                return SimpleNamespace(
                    memory_id=memory_id,
                    mem_extract_type="vanilla",
                    mem_type="tool_trace",
                    content="terminal returned exit code 7 for the deployment check",
                    status="active",
                    metadata={
                        "request_metadata": {
                            "source_type": "homemaster_task_trace",
                            "source_session_id": "session-source",
                        }
                    },
                    created_at=None,
                    update_at=None,
                )
            if memory_id == "direct-fact-1":
                return SimpleNamespace(
                    memory_id=memory_id,
                    mem_extract_type="homemaster_direct_flat",
                    mem_type="fact",
                    content="  Aurora-A18 uses uv.  ",
                    status="active",
                    metadata={
                        "homemaster_add_mode": "direct_flat",
                        "provenance_seq": 7,
                        "evidence_kind": "user_statement",
                    },
                    created_at=None,
                    update_at=None,
                )
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
        {"query": "写操作超时", "memory_type": "experience", "limit": 5}, context
    )

    assert result.success
    assert result.data["count"] == 1
    record = result.data["records"][0]
    assert record["memory_id"] == "experience-1"
    assert record["memory_type"] == "experience"
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

    tool_trace_result = await executor.execute(
        {"query": "deployment check", "memory_type": "tool_trace", "limit": 5}, context
    )
    assert tool_trace_result.success
    assert tool_trace_result.data["count"] == 1
    tool_trace = tool_trace_result.data["records"][0]
    assert tool_trace["memory_id"] == "tool-trace-1"
    assert tool_trace["memory_type"] == "tool_trace"
    assert tool_trace["content"] == (
        "terminal returned exit code 7 for the deployment check"
    )
    assert all(
        diagnostic["memory_id_hash"]
        != hashlib.sha256(b"tool-trace-1").hexdigest()[:16]
        for diagnostic in tool_trace_result.data["diagnostics"]
    )

    fact_result = await executor.execute(
        {"query": "package manager", "memory_type": "fact", "limit": 5}, context
    )
    assert fact_result.success
    assert fact_result.data["count"] == 1
    fact = fact_result.data["records"][0]
    assert fact["memory_id"] == "direct-fact-1"
    assert fact["memory_type"] == "fact"
    assert fact["content"] == "  Aurora-A18 uses uv.  "
    assert fact["source"] == {
        "provenance_seq": 7,
        "evidence_kind": "user_statement",
        "homemaster_add_mode": "direct_flat",
    }


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

        async def add_flat(
            self, content, memory_type, *, provenance_seq, evidence_kind, context
        ):
            memory_id = f"raw-direct-{self.next_id}"
            self.next_id += 1
            calls.append(
                (
                    "add_flat",
                    {
                        "content": content,
                        "memory_type": memory_type,
                        "provenance_seq": provenance_seq,
                        "evidence_kind": evidence_kind,
                        "context": context,
                    },
                )
            )
            return {"memory_id": memory_id, "verified_terminal_state": True}

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
    ledger.register(
        kind="environment_observation",
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
        turn_id="turn-1",
        tool_call_id="environment-call-1",
    )
    ledger.register(
        kind="environment_observation",
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
        turn_id="turn-1",
        tool_call_id="environment-call-2",
    )
    runtime = FakeMindMemOS()
    add_queue = MemoryAddQueue(runtime, audit_path=tmp_path / "add_jobs.jsonl")
    await add_queue.start()
    context = ToolExecutionContext(
        tmp_path,
        metadata={
            "services": {
                "mindmemos": runtime,
                "memory_add_queue": add_queue,
                "memory_evidence_ledger": ledger,
            },
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
            "content": "苹果在冰箱第二层",
        },
        context,
    )
    await add_queue.wait_idle()
    updated = await executors["mindmemos_update"].execute(
        {
            "memory_id": "raw-memory-1",
            "record": replacement,
        },
        context,
    )
    deleted = await executors["mindmemos_delete"].execute(
        {"memory_id": "raw-memory-2"},
        context,
    )

    assert added.success
    assert added.data["status"] == "accepted"
    assert added.data["job_id"]
    assert added.data["verified_terminal_state"] is False
    assert "memory_id" not in added.data
    assert updated.success
    assert updated.data["memory_id"] == "raw-memory-2"
    assert deleted.success
    add_calls = [payload for operation, payload in calls if operation == "add_flat"]
    assert len(add_calls) == 1
    assert add_calls[0]["content"] == "苹果在冰箱第二层"
    assert add_calls[0]["memory_type"] == "fact"
    assert add_calls[0]["evidence_kind"] == "environment_observation"
    version_call = next(payload for operation, payload in calls if operation == "update_versioned")
    assert json.loads(version_call["metadata"]["record_json"])["value"] == "餐桌上"
    delete_ids = [payload[0] for operation, payload in calls if operation == "delete"]
    assert delete_ids == ["raw-memory-2"]
    await add_queue.aclose()
    ledger.close()


@pytest.mark.asyncio
async def test_mindmemos_add_returns_accepted_before_background_write_finishes(
    tmp_path: Path,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class Store:
        async def add_flat(
            self, content, memory_type, *, provenance_seq, evidence_kind, context
        ):
            assert content == "  Aurora-A18 uses uv.  "
            assert memory_type == "fact"
            assert evidence_kind == "user_statement"
            del provenance_seq, context
            entered.set()
            await release.wait()
            return {"memory_id": "raw-accepted"}

    ledger = MemoryEvidenceLedger(tmp_path / "evidence.sqlite3")
    ledger.start()
    ledger.register(
        kind="user_statement",
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
        turn_id="turn-1",
    )
    queue = MemoryAddQueue(Store(), audit_path=tmp_path / "add_jobs.jsonl")
    await queue.start()
    context = ToolExecutionContext(
        tmp_path,
        metadata={
            "services": {
                "memory_add_queue": queue,
                "memory_evidence_ledger": ledger,
            },
            "permission_subject": type(
                "Subject",
                (),
                {"tenant_id": "tenant-a", "capabilities": ("tool.read", "tool.mutate")},
            )(),
            "session_id": "session-a",
            "run_id": "run-a",
            "turn_index": 1,
            "tool_call_id": "call-add",
        },
    )
    executor = next(
        tool.executor
        for tool in build_memory_tools()
        if tool.definition.model_alias == "mindmemos_add"
    )

    result = await executor.execute(
        {
            "memory_type": "fact",
            "content": "  Aurora-A18 uses uv.  ",
        },
        context,
    )

    assert result.success
    assert result.data["status"] == "accepted"
    assert result.data["verified_terminal_state"] is False
    await asyncio.wait_for(entered.wait(), timeout=1)
    release.set()
    await queue.aclose()
    ledger.close()


@pytest.mark.asyncio
async def test_mindmemos_add_cannot_use_evidence_from_an_earlier_run(tmp_path: Path) -> None:
    calls = 0

    class Store:
        async def add_flat(
            self, content, memory_type, *, provenance_seq, evidence_kind, context
        ):
            nonlocal calls
            del content, memory_type, provenance_seq, evidence_kind, context
            calls += 1
            return {"memory_id": "must-not-exist"}

    ledger = MemoryEvidenceLedger(tmp_path / "evidence.sqlite3")
    ledger.start()
    ledger.register(
        kind="user_statement",
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-old",
        turn_id="turn-1",
    )
    queue = MemoryAddQueue(Store(), audit_path=tmp_path / "add_jobs.jsonl")
    await queue.start()
    context = ToolExecutionContext(
        tmp_path,
        metadata={
            "services": {
                "memory_add_queue": queue,
                "memory_evidence_ledger": ledger,
            },
            "permission_subject": type(
                "Subject",
                (),
                {"tenant_id": "tenant-a", "capabilities": ("tool.read", "tool.mutate")},
            )(),
            "session_id": "session-a",
            "run_id": "run-current",
            "turn_index": 1,
            "tool_call_id": "call-add",
        },
    )
    executor = next(
        tool.executor
        for tool in build_memory_tools()
        if tool.definition.model_alias == "mindmemos_add"
    )

    result = await executor.execute(
        {
            "memory_type": "fact",
            "content": "Aurora-A18 uses uv.",
        },
        context,
    )

    assert not result.success
    assert result.error is not None
    assert result.error.code == "memory_evidence_missing"
    await queue.wait_idle()
    assert calls == 0
    await queue.aclose()
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
    ledger.register(
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
    ledger.register(
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

    old_record = {
        "schema_version": 1,
        "memory_type": "fact",
        "subject": {"type": "device", "name": "Lumen-Q27", "id": None},
        "predicate": "package_manager",
        "value": "conda",
        "source": "user_statement",
    }
    new_record = {**old_record, "value": {"online": "uv", "offline": "Poetry"}}
    old = SimpleNamespace(
        memory_id="old",
        project_id="tenant-a",
        user_id="tenant-a",
        content="User uses conda.",
        metadata={"request_metadata": {"record_json": json.dumps(old_record)}},
        status="active",
    )
    new = SimpleNamespace(
        memory_id="new",
        project_id="tenant-a",
        user_id="tenant-a",
        content='Lumen-Q27 的 package_manager 是 {"offline":"Poetry","online":"uv"}',
        metadata={"request_metadata": {"record_json": json.dumps(new_record)}},
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
                        after_content=new.content,
                        replacement_record=new_record,
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


@pytest.mark.asyncio
async def test_feedback_rejects_schema_update_when_record_json_stays_stale(tmp_path: Path) -> None:
    from mindmemos.typing import FeedbackPipelineResult, FeedbackUpdateAction, MemorySearchItem

    old_record = {
        "schema_version": 1,
        "memory_type": "fact",
        "subject": {"type": "device", "name": "Lumen-Q27", "id": None},
        "predicate": "package_manager",
        "value": "uv",
        "source": "user_statement",
    }
    replacement = {**old_record, "value": {"online": "uv", "offline": "Poetry"}}
    old = SimpleNamespace(
        memory_id="old",
        project_id="tenant-a",
        user_id="tenant-a",
        content='Lumen-Q27 的 package_manager 是 "uv"',
        metadata={"request_metadata": {"record_json": json.dumps(old_record)}},
        status="active",
    )
    new = SimpleNamespace(
        memory_id="new",
        project_id="tenant-a",
        user_id="tenant-a",
        content="Lumen-Q27 在线开发使用 uv，离线交付使用 Poetry",
        metadata={"request_metadata": {"record_json": json.dumps(old_record)}},
        status="active",
    )

    class FakeMindMemOS(EmbeddedMindMemOS):
        def __init__(self) -> None:
            pass

        async def get_raw(self, memory_id, context):
            del context
            return {"old": old, "new": new}.get(memory_id)

        async def feedback_explicit(self, **kwargs):
            del kwargs
            old.status = "archived"
            return FeedbackPipelineResult(
                status="ok",
                actions=[
                    FeedbackUpdateAction(
                        target_memory_id="old",
                        result_memory_id="new",
                        before_content=old.content,
                        after_content=new.content,
                        replacement_record=replacement,
                        status="ok",
                    )
                ],
            )

        async def has_memory_lineage(self, **kwargs):
            del kwargs
            return True

    store = FakeMindMemOS()
    executor = next(
        tool.executor
        for tool in build_memory_tools()
        if tool.definition.model_alias == "mindmemos_feedback"
    )
    context = _feedback_tool_context(tmp_path, store)
    context.metadata["memory_feedback_context"] = build_feedback_context_snapshot(
        [UserMessage.from_text("Online uses uv; offline uses Poetry.")],
        automatic_recalled_memories=[
            MemorySearchItem(
                id="old",
                memory=old.content,
                last_update_at="2026-08-01 00:00:00",
                structured_record=old_record,
            )
        ],
    )

    result = await executor.execute(
        {"feedback": "Online uses uv; offline uses Poetry."}, context
    )

    assert not result.success
    assert result.error.code == "memory_feedback_failed"


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
    ledger = MemoryEvidenceLedger(tmp_path / "feedback-evidence.sqlite3")
    ledger.start()
    ledger.register(
        kind="user_statement",
        tenant_id="tenant-a",
        session_id="session-a",
        run_id="run-a",
        turn_id="turn-1",
    )
    return ToolExecutionContext(
        tmp_path,
        metadata={
            "services": {"mindmemos": store, "memory_evidence_ledger": ledger},
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
