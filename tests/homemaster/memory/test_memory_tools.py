"""Canonical V3.8 memory tool surface and execution boundaries."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from homemaster.adapters.profiles import build_tool_registry
from homemaster.agent.messages import ContentBlock, ToolResultMessage, UserMessage
from homemaster.memory.feedback_context import build_feedback_context_snapshot
from homemaster.memory.file_store import FileMemoryStore
from homemaster.memory.mindmemos_client import (
    FeedbackAction,
    FeedbackReceipt,
    MindMemOSClient,
    MindMemOSClientError,
    MutationReceipt,
    OutcomeUnknownError,
    SearchHit,
    SearchReceipt,
)
from homemaster.tools.contracts import PermissionSubject, ToolExecutionStatus
from homemaster.tools.memory_tools import UpdateMemoryInput, build_memory_tools
from tests.homemaster.tools.test_support import ToolExecutionContext

MEMORY_TOOL_NAMES = {
    "context_memory",
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


def test_default_home_surface_has_exactly_six_memory_tools() -> None:
    names = set(build_tool_registry(environment="local_robot").all_names())
    assert MEMORY_TOOL_NAMES <= names
    assert "mindmemos_add" not in names
    assert not (LEGACY_MEMORY_TOOL_NAMES & names)
    assert "memory_retriever" not in names
    assert "memory_writer" not in names
    disabled = set(build_tool_registry(environment="local_robot", memory_enabled=False).all_names())
    assert not (MEMORY_TOOL_NAMES & disabled)


def test_benchmark_memory_profiles_keep_legacy_and_canonical_memory_tools() -> None:
    readonly = set(build_tool_registry(environment="alfworld", memory_mode="readonly").all_names())
    full = set(build_tool_registry(environment="alfworld", memory_mode="full").all_names())
    assert "memory_retriever" in readonly
    assert "memory_writer" not in readonly
    assert {"memory_retriever", "memory_writer"} <= full
    assert MEMORY_TOOL_NAMES <= readonly
    assert MEMORY_TOOL_NAMES <= full


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
    for name in ("mindmemos_update", "mindmemos_delete", "mindmemos_feedback"):
        assert "tool.mutate" in by_name[name].definition.required_capabilities
    for tool in tools:
        assert "mindmemos_add" not in tool.definition.description
        schema = tool.definition.input_schema
        properties = schema.get("properties", {})
        forbidden = {"tenant_id", "session_id", "run_id", "metadata", "dedupe_key"}
        assert not (forbidden & set(properties))

    search_description = by_name["mindmemos_search"].definition.description
    assert "Search long-term memory by meaning" in search_description
    assert "memory types" in search_description
    assert "tool traces" in search_description
    assert "Search again" in search_description
    feedback = by_name["mindmemos_feedback"]
    assert feedback.definition.required_capabilities == ("tool.read", "tool.mutate")
    assert set(feedback.definition.input_schema["properties"]) == {"feedback"}
    assert tuple(feedback.definition.input_schema["required"]) == ("feedback",)
    assert "mindmemos_update" in feedback.definition.description
    assert "mindmemos_delete" in feedback.definition.description
    assert "vague complaint" in feedback.definition.description


def test_memory_tool_schemas_expose_native_lifecycle_surface() -> None:
    by_name = {
        tool.definition.model_alias: tool.definition.to_model_manifest()["input_schema"]
        for tool in build_memory_tools()
    }

    assert "mindmemos_add" not in by_name
    search_schema = by_name["mindmemos_search"]
    assert set(search_schema["properties"]) == {"query", "memory_type", "limit"}
    search_type_schema = search_schema["properties"]["memory_type"]
    search_type_enum = next(item["enum"] for item in search_type_schema["anyOf"] if "enum" in item)
    assert search_type_enum == [
        "profile",
        "fact",
        "experience",
        "episodic",
        "tool_trace",
        "skill_candidate",
        "file_knowledge",
    ]
    assert "$defs" not in search_schema
    assert "$ref" not in json.dumps(search_schema)

    update_schema = by_name["mindmemos_update"]
    assert set(update_schema["properties"]) == {
        "memory_id",
        "record",
        "content",
    }
    assert "evidence_refs" not in update_schema["properties"]
    history_schema = by_name["mindmemos_history"]
    assert set(history_schema["properties"]) == {"memory_id"}


def test_update_input_requires_exactly_one_replacement() -> None:
    with pytest.raises(ValidationError):
        UpdateMemoryInput.model_validate({"memory_id": "m-1"})
    with pytest.raises(ValidationError):
        UpdateMemoryInput.model_validate(
            {"memory_id": "m-1", "record": {"a": 1}, "content": "text"}
        )
    validated = UpdateMemoryInput.model_validate({"memory_id": "m-1", "record": {"a": 1}})
    assert validated.record == {"a": 1}
    decoded = UpdateMemoryInput.model_validate({"memory_id": "m-1", "record": '{"a": 1}'})
    assert decoded.record == {"a": 1}


class FakeMindMemOSClient(MindMemOSClient):
    """In-memory stub of the V3.8 SDK facade used by the MindMemOS tools."""

    def __init__(self) -> None:
        self.memories: dict[str, dict[str, Any]] = {}
        self.search_calls: list[dict[str, Any]] = []
        self.feedback_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []
        self.raise_next: Exception | None = None
        self._search_records = 0

    def _maybe_raise(self) -> None:
        if self.raise_next is not None:
            error = self.raise_next
            self.raise_next = None
            raise error

    def add(
        self,
        memory_id: str,
        memory: str,
        *,
        memory_type: str = "fact",
        score: float | None = 0.9,
        metadata: dict[str, Any] | None = None,
        history: list[dict[str, Any]] | None = None,
        version: int = 1,
        status: str = "active",
    ) -> None:
        self.memories[memory_id] = {
            "memory": memory,
            "memory_type": memory_type,
            "score": score,
            "metadata": dict(metadata or {}),
            "history": list(history) if history else None,
            "version": version,
            "status": status,
        }

    async def search(
        self,
        query: str,
        *,
        session_id: str | None = None,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> SearchReceipt:
        self.search_calls.append(
            {
                "query": query,
                "session_id": session_id,
                "top_k": top_k,
                "filters": filters,
            }
        )
        self._maybe_raise()
        mem_type = (filters or {}).get("mem_type")
        hits = []
        for memory_id, item in self.memories.items():
            if item["status"] != "active":
                continue
            if mem_type is not None and item["memory_type"] != mem_type:
                continue
            if query.strip().lower() not in item["memory"].lower():
                continue
            hits.append(
                SearchHit(
                    id=memory_id,
                    memory=item["memory"],
                    memory_type=item["memory_type"],
                    score=item["score"],
                    metadata=dict(item["metadata"]),
                )
            )
        self._search_records += 1
        return SearchReceipt(
            search_record_id=f"search-record-{self._search_records}",
            hits=tuple(hits[:top_k]),
            request_id=None,
        )

    async def feedback_explicit(
        self,
        *,
        operation_id: str,
        feedback: str,
        messages: list[dict[str, Any]],
        recalled_memories: list[dict[str, Any]],
        session_id: str | None = None,
    ) -> FeedbackReceipt:
        self.feedback_calls.append(
            {
                "operation_id": operation_id,
                "feedback": feedback,
                "messages": messages,
                "recalled_memories": recalled_memories,
                "session_id": session_id,
            }
        )
        self._maybe_raise()
        return FeedbackReceipt(
            operation_id=operation_id,
            feedback_record_id="fb-record-1",
            actions=(
                FeedbackAction(action_id="action-1", action="noop", memory_id=None),
            ),
            status="completed",
            request_id=None,
        )

    async def get_memory(self, *, filters: dict[str, Any]) -> dict[str, Any]:
        self.get_calls.append({"filters": filters})
        self._maybe_raise()
        memory_id = (filters or {}).get("memory_id")
        item = self.memories.get(memory_id)
        if item is None:
            return {"memories": []}
        data: dict[str, Any] = {
            "memories": [
                {
                    "id": memory_id,
                    "memory": item["memory"],
                    "memory_type": item["memory_type"],
                    "last_update_at": "2026-08-18 10:00:00",
                    "metadata": dict(item["metadata"]),
                    "status": item["status"],
                    "version": item["version"],
                }
            ]
        }
        if item["history"]:
            data["history"] = list(item["history"])
        return data

    async def update_memory(
        self,
        *,
        memory_id: str,
        operation_id: str,
        content: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> MutationReceipt:
        self.update_calls.append(
            {
                "memory_id": memory_id,
                "operation_id": operation_id,
                "content": content,
                "fields": fields,
            }
        )
        self._maybe_raise()
        item = self.memories.get(memory_id)
        if item is None:
            raise MindMemOSClientError(f"memory {memory_id} was not found")
        if content is not None:
            item["memory"] = content
        if fields:
            item["metadata"].setdefault("fields", {}).update(fields)
        item["version"] += 1
        return MutationReceipt(
            status="completed",
            memory_id=memory_id,
            request_id=None,
            version=str(item["version"]),
        )

    async def delete_memory(self, *, memory_id: str, operation_id: str) -> MutationReceipt:
        self.delete_calls.append({"memory_id": memory_id, "operation_id": operation_id})
        self._maybe_raise()
        item = self.memories.get(memory_id)
        if item is None:
            raise MindMemOSClientError(f"memory {memory_id} was not found")
        item["status"] = "archived"
        return MutationReceipt(status="completed", memory_id=memory_id, request_id=None)


def _executor(alias: str):
    return next(
        tool.executor for tool in build_memory_tools() if tool.definition.model_alias == alias
    )


def _mindmemos_context(
    tmp_path: Path,
    client: FakeMindMemOSClient | None,
    *,
    services: dict[str, Any] | None = None,
    tool_call_id: str = "call-memory",
    turn_index: int = 1,
) -> Any:
    merged: dict[str, Any] = dict(services or {})
    if client is not None:
        merged["mindmemos_client"] = client
    return ToolExecutionContext(
        tmp_path,
        metadata={
            "services": merged,
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
            "turn_index": turn_index,
            "tool_call_id": tool_call_id,
        },
    )


def _with_services(context, **services: object):
    return replace(context, services={**context.services, **services})


@pytest.mark.asyncio
async def test_file_memory_mutation_requires_tool_mutate_and_uses_service(tmp_path: Path) -> None:
    from homemaster.config import MemoryConfig

    store = FileMemoryStore(MemoryConfig(data_root=tmp_path / "memory"))
    store.start()
    executor = _executor("context_memory")
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

    context = replace(
        context,
        permission_subject=PermissionSubject(
            subject_id="test",
            channel="pytest",
            tenant_id="tenant-a",
            capabilities=("tool.read", "tool.mutate"),
        ),
    )
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


@pytest.mark.asyncio
async def test_search_memories_uses_client_and_returns_hit_payloads(tmp_path: Path) -> None:
    client = FakeMindMemOSClient()
    client.add(
        "m-1",
        "Aurora-A18 uses uv.",
        memory_type="fact",
        score=0.91,
        metadata={"source_session_id": "session-old"},
    )
    client.add("m-2", "Aurora-A18 uses uv.", memory_type="experience", score=0.5)
    client.add("m-3", "unrelated note", memory_type="fact")
    run_context = SimpleNamespace(deps={})
    context = _mindmemos_context(
        tmp_path,
        client,
        services={"run_context": run_context},
        tool_call_id="call-search",
        turn_index=2,
    )

    result = await _executor("mindmemos_search").execute(
        {"query": "aurora-a18", "memory_type": "fact", "limit": 5},
        context,
    )

    assert result.success
    call = client.search_calls[0]
    assert call == {
        "query": "aurora-a18",
        "session_id": "session-a",
        "top_k": 5,
        "filters": {"mem_type": "fact"},
    }
    assert result.data["count"] == 1
    assert result.data["search_record_id"] == "search-record-1"
    record = result.data["records"][0]
    assert record == {
        "memory_id": "m-1",
        "memory_type": "fact",
        "memory": "Aurora-A18 uses uv.",
        "score": 0.91,
        "metadata": {"source_session_id": "session-old"},
    }
    assert result.data["verified_terminal_state"] is True
    recalled = run_context.deps["recalled_memories_by_tool_call_id"]["call-search"]
    assert [hit.id for hit in recalled] == ["m-1"]


@pytest.mark.asyncio
async def test_search_memories_without_type_filter_sends_no_filters(tmp_path: Path) -> None:
    client = FakeMindMemOSClient()
    client.add("m-1", "probe is in cabinet", memory_type="fact")
    context = _mindmemos_context(tmp_path, client, tool_call_id="call-search")

    result = await _executor("mindmemos_search").execute({"query": "probe"}, context)

    assert result.success
    assert client.search_calls[0]["filters"] is None
    assert client.search_calls[0]["top_k"] == 5


@pytest.mark.asyncio
async def test_search_memories_service_unavailable_maps_backend_failure(tmp_path: Path) -> None:
    context = _mindmemos_context(tmp_path, None)

    result = await _executor("mindmemos_search").execute({"query": "probe"}, context)

    assert not result.success
    assert result.error is not None
    assert result.error.code == "memory_backend_unavailable"


@pytest.mark.asyncio
async def test_search_memories_client_error_maps_backend_failure(tmp_path: Path) -> None:
    client = FakeMindMemOSClient()
    client.raise_next = MindMemOSClientError("transport refused")
    context = _mindmemos_context(tmp_path, client)

    result = await _executor("mindmemos_search").execute({"query": "probe"}, context)

    assert not result.success
    assert result.error is not None
    assert result.error.code == "memory_backend_unavailable"


@pytest.mark.asyncio
async def test_history_returns_memory_detail_and_versions(tmp_path: Path) -> None:
    client = FakeMindMemOSClient()
    client.add(
        "m-new",
        "Aurora-A18 uses uv.",
        history=[
            {
                "id": "m-old",
                "memory": "Aurora-A18 used conda.",
                "memory_type": "fact",
                "status": "archived",
            },
            {
                "id": "m-new",
                "memory": "Aurora-A18 uses uv.",
                "memory_type": "fact",
                "status": "active",
            },
        ],
        version=2,
    )
    context = _mindmemos_context(tmp_path, client, tool_call_id="call-history")

    result = await _executor("mindmemos_history").execute({"memory_id": "m-new"}, context)

    assert result.success
    assert client.get_calls == [{"filters": {"memory_id": "m-new"}}]
    memory = result.data["memory"]
    assert memory["id"] == "m-new"
    assert memory["memory"] == "Aurora-A18 uses uv."
    assert memory["version"] == 2
    # The active version leads; the archived ancestor follows without duplicating it.
    assert [item["id"] for item in result.data["versions"]] == ["m-new", "m-old"]
    assert result.data["version_count"] == 2
    assert result.data["verified_terminal_state"] is True


@pytest.mark.asyncio
async def test_history_reports_not_found_for_unknown_memory(tmp_path: Path) -> None:
    client = FakeMindMemOSClient()
    context = _mindmemos_context(tmp_path, client, tool_call_id="call-history")

    result = await _executor("mindmemos_history").execute({"memory_id": "missing"}, context)

    assert not result.success
    assert result.error is not None
    assert result.error.code == "memory_not_found"


@pytest.mark.asyncio
async def test_update_sends_structured_fields_to_native_update(tmp_path: Path) -> None:
    client = FakeMindMemOSClient()
    client.add("m-1", "Aurora-A18 uses conda.", version=1)
    context = _mindmemos_context(tmp_path, client, tool_call_id="call-update")

    result = await _executor("mindmemos_update").execute(
        {"memory_id": "m-1", "record": {"predicate": "package_manager", "value": "uv"}},
        context,
    )

    assert result.success
    call = client.update_calls[0]
    assert call == {
        "memory_id": "m-1",
        "operation_id": "memupdate:session-a:call-update",
        "content": None,
        "fields": {"predicate": "package_manager", "value": "uv"},
    }
    assert result.data["update_mode"] == "structured"
    assert result.data["memory_id"] == "m-1"
    assert result.data["version"] == "2"
    assert result.data["operation_id"] == "memupdate:session-a:call-update"
    assert result.data["verified_terminal_state"] is True
    assert client.memories["m-1"]["metadata"]["fields"] == {
        "predicate": "package_manager",
        "value": "uv",
    }


@pytest.mark.asyncio
async def test_update_sends_plain_content_to_native_update(tmp_path: Path) -> None:
    client = FakeMindMemOSClient()
    client.add("m-1", "Aurora-A18 used conda.", version=3)
    context = _mindmemos_context(tmp_path, client, tool_call_id="call-update")

    result = await _executor("mindmemos_update").execute(
        {"memory_id": "m-1", "content": "Aurora-A18 uses uv."},
        context,
    )

    assert result.success
    call = client.update_calls[0]
    assert call["content"] == "Aurora-A18 uses uv."
    assert call["fields"] is None
    assert result.data["update_mode"] == "vanilla"
    assert result.data["version"] == "4"
    assert client.memories["m-1"]["memory"] == "Aurora-A18 uses uv."


@pytest.mark.asyncio
async def test_update_maps_outcome_unknown_to_typed_status(tmp_path: Path) -> None:
    client = FakeMindMemOSClient()
    client.add("m-1", "Aurora-A18 uses conda.")
    client.raise_next = OutcomeUnknownError("receipt lost after send")
    context = _mindmemos_context(tmp_path, client, tool_call_id="call-update")

    result = await _executor("mindmemos_update").execute(
        {"memory_id": "m-1", "content": "Aurora-A18 uses uv."},
        context,
    )

    assert not result.success
    assert result.status is ToolExecutionStatus.OUTCOME_UNKNOWN
    assert result.error is not None
    assert result.error.code == "memory_outcome_unknown"
    assert result.backend_attempted is True


@pytest.mark.asyncio
async def test_update_service_unavailable_maps_backend_failure(tmp_path: Path) -> None:
    context = _mindmemos_context(tmp_path, None, tool_call_id="call-update")

    result = await _executor("mindmemos_update").execute(
        {"memory_id": "m-1", "content": "replacement"},
        context,
    )

    assert not result.success
    assert result.error is not None
    assert result.error.code == "memory_backend_unavailable"


@pytest.mark.asyncio
async def test_delete_uses_native_delete_with_deterministic_operation_id(
    tmp_path: Path,
) -> None:
    client = FakeMindMemOSClient()
    client.add("m-1", "Aurora-A18 uses conda.")
    context = _mindmemos_context(tmp_path, client, tool_call_id="call-delete")

    result = await _executor("mindmemos_delete").execute({"memory_id": "m-1"}, context)

    assert result.success
    assert client.delete_calls == [
        {"memory_id": "m-1", "operation_id": "memdelete:session-a:call-delete"}
    ]
    assert result.data["memory_id"] == "m-1"
    assert result.data["operation_id"] == "memdelete:session-a:call-delete"
    assert result.data["verified_terminal_state"] is True
    assert client.memories["m-1"]["status"] == "archived"


@pytest.mark.asyncio
async def test_feedback_sends_snapshot_messages_recalled_memories_and_operation_id(
    tmp_path: Path,
) -> None:
    client = FakeMindMemOSClient()
    hit = SearchHit(
        id="m-1",
        memory="User uses conda.",
        memory_type="fact",
        score=0.88,
        metadata={"last_update_at": "2026-08-01 00:00:00"},
    )
    snapshot = build_feedback_context_snapshot(
        [
            UserMessage.from_text("Where is the probe?"),
            ToolResultMessage(
                tool_call_id="call-search",
                name="mindmemos_search",
                content=[ContentBlock(type="text", text='{"count": 1}')],
            ),
            UserMessage.from_text("I use uv, not conda."),
        ],
        recalled_memories_by_tool_call_id={"call-search": (hit,)},
    )
    context = _with_services(
        _mindmemos_context(tmp_path, client, tool_call_id="call-feedback"),
        memory_feedback_context=snapshot,
    )

    result = await _executor("mindmemos_feedback").execute(
        {"feedback": "I use uv, not conda."},
        context,
    )

    assert result.success
    call = client.feedback_calls[0]
    assert call["operation_id"] == "explicitfb:session-a:turn-1:call-feedback"
    assert call["feedback"] == "I use uv, not conda."
    assert call["session_id"] == "session-a"
    assert call["messages"] == [
        {"role": "user", "content": "Where is the probe?"},
        {"role": "tool", "content": '{"count": 1}'},
        {"role": "user", "content": "I use uv, not conda."},
    ]
    assert call["recalled_memories"] == [
        {
            "id": "m-1",
            "memory": "User uses conda.",
            "memory_type": "fact",
            "metadata": {"last_update_at": "2026-08-01 00:00:00"},
            "last_update_at": "2026-08-01 00:00:00",
        }
    ]
    assert result.data["feedback_record_id"] == "fb-record-1"
    assert result.data["operation_id"] == "explicitfb:session-a:turn-1:call-feedback"
    assert result.data["status"] == "completed"
    assert [dict(action) for action in result.data["actions"]] == [
        {"action_id": "action-1", "action": "noop", "memory_id": None}
    ]
    assert result.data["action_count"] == 1
    assert result.data["verified_terminal_state"] is True


@pytest.mark.asyncio
async def test_feedback_requires_snapshot_before_client_call(tmp_path: Path) -> None:
    client = FakeMindMemOSClient()
    context = _mindmemos_context(tmp_path, client, tool_call_id="call-feedback")

    result = await _executor("mindmemos_feedback").execute(
        {"feedback": "Use uv, not conda."},
        context,
    )

    assert not result.success
    assert result.error is not None
    assert result.error.code == "memory_feedback_context_missing"
    assert client.feedback_calls == []


@pytest.mark.asyncio
async def test_feedback_emits_runtime_events_around_client_call(tmp_path: Path) -> None:
    class RecordingSink:
        def __init__(self) -> None:
            self.events = []

        def emit(self, event) -> None:
            self.events.append(event)

    client = FakeMindMemOSClient()
    sink = RecordingSink()
    snapshot = build_feedback_context_snapshot(
        [UserMessage.from_text("I use uv, not conda.")],
    )
    context = _with_services(
        _mindmemos_context(tmp_path, client, tool_call_id="call-feedback"),
        run_context=SimpleNamespace(event_sink=sink, deps={}),
        memory_feedback_context=snapshot,
    )

    result = await _executor("mindmemos_feedback").execute(
        {"feedback": "I use uv, not conda."},
        context,
    )

    assert result.success
    assert [event.type for event in sink.events] == [
        "memory.feedback.explicit.started",
        "memory.feedback.explicit.completed",
    ]


@pytest.mark.asyncio
async def test_feedback_client_failure_maps_backend_error_and_fails_event(
    tmp_path: Path,
) -> None:
    class RecordingSink:
        def __init__(self) -> None:
            self.events = []

        def emit(self, event) -> None:
            self.events.append(event)

    client = FakeMindMemOSClient()
    client.raise_next = MindMemOSClientError("feedback rejected")
    sink = RecordingSink()
    snapshot = build_feedback_context_snapshot(
        [UserMessage.from_text("I use uv, not conda.")],
    )
    context = _with_services(
        _mindmemos_context(tmp_path, client, tool_call_id="call-feedback"),
        run_context=SimpleNamespace(event_sink=sink, deps={}),
        memory_feedback_context=snapshot,
    )

    result = await _executor("mindmemos_feedback").execute(
        {"feedback": "I use uv, not conda."},
        context,
    )

    assert not result.success
    assert result.error is not None
    assert result.error.code == "memory_backend_unavailable"
    assert result.backend_attempted is True
    assert [event.type for event in sink.events] == [
        "memory.feedback.explicit.started",
        "memory.feedback.explicit.failed",
    ]


@pytest.mark.asyncio
async def test_feedback_outcome_unknown_maps_typed_status(tmp_path: Path) -> None:
    client = FakeMindMemOSClient()
    client.raise_next = OutcomeUnknownError("response lost")
    snapshot = build_feedback_context_snapshot(
        [UserMessage.from_text("I use uv, not conda.")],
    )
    context = _with_services(
        _mindmemos_context(tmp_path, client, tool_call_id="call-feedback"),
        memory_feedback_context=snapshot,
    )

    result = await _executor("mindmemos_feedback").execute(
        {"feedback": "I use uv, not conda."},
        context,
    )

    assert not result.success
    assert result.status is ToolExecutionStatus.OUTCOME_UNKNOWN
    assert result.error is not None
    assert result.error.code == "memory_outcome_unknown"
