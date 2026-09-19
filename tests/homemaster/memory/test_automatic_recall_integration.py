from __future__ import annotations

import json
import os
import statistics
import time
import uuid

import pytest

from homemaster.config import load_config
from homemaster.memory.automatic_recall import (
    build_automatic_recall_context,
    build_mindmemos_request_context,
)
from homemaster.memory.managed_neo4j import ManagedNeo4jRuntime
from homemaster.memory.mindmemos_runtime import EmbeddedMindMemOS
from homemaster.memory.models import FactRecord
from homemaster.memory.serialization import serialize_record


@pytest.mark.asyncio
async def test_real_automatic_recall_smoke_benchmark() -> None:
    if os.environ.get("HOMEMASTER_RUN_REAL_AUTOMATIC_RECALL") != "1":
        pytest.skip("set HOMEMASTER_RUN_REAL_AUTOMATIC_RECALL=1 for real external smoke")

    config = load_config(
        os.environ.get("HOMEMASTER_REAL_TEST_CONFIG", "config/homemaster.yaml")
    )
    neo4j = ManagedNeo4jRuntime(config.memory)
    store = EmbeddedMindMemOS(config)
    nonce = "v25-auto-recall-" + uuid.uuid4().hex[:12]
    created_ids: list[str] = []
    context = None
    await neo4j.start()
    try:
        await store.start()
        try:
            assert store.available, store.unavailable_cause
            context = build_mindmemos_request_context(
                request_id=nonce,
                tenant_id="v25-auto-recall-integration",
                session_id=nonce,
            )
            from mindmemos.typing import TextMessage

            fact = FactRecord(
                memory_type="fact",
                subject={"type": "object", "name": nonce},
                predicate="validation_marker",
                value={"marker": nonce},
                source="user_statement",
            )
            serialized = serialize_record(fact, provenance_seq=1)
            fact_result = await store.add(
                [TextMessage(text=serialized.text)],
                context,
                force_generation=True,
                metadata={**serialized.metadata, "homemaster_memory_type": "fact"},
            )
            created_ids.extend(_result_ids(fact_result))
            fact_ids = await _active_ids(store, context, created_ids, "fact")
            assert fact_ids

            experience_text = f"Complete validation task {nonce}: find medicine and place it on the table."
            episode = {
                "schema_version": "homemaster.schema_episode.v1",
                "session_id": nonce,
                "exit_reason": "task_completed",
                "extractor_slots": [
                    "object_location",
                    "search_observation",
                    "task_procedure",
                ],
                "events": [
                    {
                        "event_id": f"{nonce}-user",
                        "session_id": nonce,
                        "run_id": nonce,
                        "timestamp": "2026-09-17T10:00:00Z",
                        "type": "runtime.turn_started",
                        "payload": {"user_text": experience_text},
                        "trace_offset": 0,
                    },
                    {
                        "event_id": f"{nonce}-call-1",
                        "session_id": nonce,
                        "run_id": nonce,
                        "timestamp": "2026-09-17T10:00:01Z",
                        "type": "tool.call_started",
                        "name": "inspect",
                        "tool_call_id": "call-1",
                        "payload": {"arguments": {"location": "living_room_table"}},
                        "trace_offset": 1,
                    },
                    {
                        "event_id": f"{nonce}-result-1",
                        "session_id": nonce,
                        "run_id": nonce,
                        "timestamp": "2026-09-17T10:00:02Z",
                        "type": "tool.call_completed",
                        "name": "inspect",
                        "tool_call_id": "call-1",
                        "payload": {
                            "args": {"location": "living_room_table"},
                            "result": "not_found",
                            "data": {"status": "success"},
                        },
                        "trace_offset": 2,
                    },
                    {
                        "event_id": f"{nonce}-call-2",
                        "session_id": nonce,
                        "run_id": nonce,
                        "timestamp": "2026-09-17T10:00:03Z",
                        "type": "tool.call_started",
                        "name": "inspect",
                        "tool_call_id": "call-2",
                        "payload": {"arguments": {"location": "bedroom_drawer"}},
                        "trace_offset": 3,
                    },
                    {
                        "event_id": f"{nonce}-result-2",
                        "session_id": nonce,
                        "run_id": nonce,
                        "timestamp": "2026-09-17T10:00:04Z",
                        "type": "tool.call_completed",
                        "name": "inspect",
                        "tool_call_id": "call-2",
                        "payload": {
                            "args": {"location": "bedroom_drawer"},
                            "result": "found medicine",
                            "data": {"status": "success"},
                        },
                        "trace_offset": 4,
                    },
                    {
                        "event_id": f"{nonce}-call-3",
                        "session_id": nonce,
                        "run_id": nonce,
                        "timestamp": "2026-09-17T10:00:05Z",
                        "type": "tool.call_started",
                        "name": "put",
                        "tool_call_id": "call-3",
                        "payload": {
                            "arguments": {
                                "object": "medicine",
                                "target": "living_room_table",
                            }
                        },
                        "trace_offset": 5,
                    },
                    {
                        "event_id": f"{nonce}-result-3",
                        "session_id": nonce,
                        "run_id": nonce,
                        "timestamp": "2026-09-17T10:00:06Z",
                        "type": "tool.call_completed",
                        "name": "put",
                        "tool_call_id": "call-3",
                        "payload": {
                            "args": {
                                "object": "medicine",
                                "target": "living_room_table",
                            },
                            "result": "success",
                            "data": {"status": "success"},
                        },
                        "trace_offset": 6,
                    },
                    {
                        "event_id": f"{nonce}-goal",
                        "session_id": nonce,
                        "run_id": nonce,
                        "timestamp": "2026-09-17T10:00:07Z",
                        "type": "runtime.goal_evaluated",
                        "payload": {"won": True, "success": True},
                        "trace_offset": 7,
                    },
                ],
                "tool_steps": [
                    {
                        "session_id": nonce,
                        "run_id": nonce,
                        "tool_call_id": "call-1",
                        "tool": "inspect",
                        "arguments": {"location": "living_room_table"},
                        "result": "not_found",
                        "status": "success",
                        "timestamp": "2026-09-17T10:00:02Z",
                        "source_event_ids": [f"{nonce}-call-1", f"{nonce}-result-1"],
                    },
                    {
                        "session_id": nonce,
                        "run_id": nonce,
                        "tool_call_id": "call-2",
                        "tool": "inspect",
                        "arguments": {"location": "bedroom_drawer"},
                        "result": "found medicine",
                        "status": "success",
                        "timestamp": "2026-09-17T10:00:04Z",
                        "source_event_ids": [f"{nonce}-call-2", f"{nonce}-result-2"],
                    },
                    {
                        "session_id": nonce,
                        "run_id": nonce,
                        "tool_call_id": "call-3",
                        "tool": "put",
                        "arguments": {"object": "medicine", "target": "living_room_table"},
                        "result": "success",
                        "status": "success",
                        "timestamp": "2026-09-17T10:00:06Z",
                        "source_event_ids": [f"{nonce}-call-3", f"{nonce}-result-3"],
                    },
                ],
                "diagnostics": [],
                "provenance": {
                    "source": "homemaster_runtime_trace",
                    "source_event_ids": [
                        f"{nonce}-user",
                        f"{nonce}-call-1",
                        f"{nonce}-result-1",
                        f"{nonce}-call-2",
                        f"{nonce}-result-2",
                        f"{nonce}-call-3",
                        f"{nonce}-result-3",
                        f"{nonce}-goal",
                    ],
                    "authoritative_goal_event_ids": [f"{nonce}-goal"],
                },
            }
            experience_result = await store.add_schema_episode(
                episode,
                context,
                metadata={
                    "source_type": "automatic_recall_validation",
                    "source_session_id": nonce,
                    "input_hash": "sha256:" + nonce,
                    "extractor_version": "schema-episode-v1:prompts-v1",
                },
            )
            experience_result_ids = _result_ids(experience_result.result)
            created_ids.extend(experience_result_ids)
            experience_ids = await _active_ids(
                store,
                context,
                experience_result_ids,
                "experience",
            )
            assert experience_ids

            fact_search = await store.search(
                nonce,
                context,
                top_k=3,
                search_pipeline="vanilla",
                rerank=False,
                filters=None,
            )
            assert fact_search.status == "ok"
            assert fact_ids[0] in {item.id for item in fact_search.memories}
            fact_context = build_automatic_recall_context(fact_search.memories)
            assert fact_context is not None and fact_ids[0] in fact_context

            experience_search = await store.search(
                experience_text,
                context,
                top_k=3,
                search_pipeline="vanilla",
                rerank=False,
                filters=None,
            )
            assert experience_search.status == "ok"
            assert experience_ids[0] in {item.id for item in experience_search.memories}
            experience_context = build_automatic_recall_context(experience_search.memories)
            assert experience_context is not None and experience_ids[0] in experience_context

            measurements: list[float] = []
            counts: list[int] = []
            statuses: list[str] = []
            for _ in range(6):
                started = time.perf_counter()
                result = await store.search(
                    experience_text,
                    context,
                    top_k=3,
                    search_pipeline="vanilla",
                    rerank=False,
                    filters=None,
                )
                measurements.append((time.perf_counter() - started) * 1000)
                counts.append(len(result.memories))
                statuses.append(result.status)
            assert statuses == ["ok"] * 6
            assert all(count <= 3 for count in counts)
            assert (context.account_id, context.project_id, context.user_id) == (
                "v25-auto-recall-integration",
                "v25-auto-recall-integration",
                "v25-auto-recall-integration",
            )
            print(
                json.dumps(
                    {
                        "cold_ms": measurements[0],
                        "warm_ms": measurements[1:],
                        "warm_p50_ms": statistics.median(measurements[1:]),
                        "warm_max_ms": max(measurements[1:]),
                        "result_counts": counts,
                    },
                    sort_keys=True,
                )
            )
        finally:
            if context is not None:
                for memory_id in dict.fromkeys(created_ids):
                    deleted = await store.delete(memory_id, context)
                    assert deleted.status in {"ok", "error"}
                    raw = await store.get_raw(memory_id, context)
                    if raw is not None:
                        assert raw.status == "archived"
            await store.close()
    finally:
        await neo4j.close()


def _result_ids(result: object) -> list[str]:
    memory_ids: list[str] = []
    for item in getattr(result, "memories", ()):
        memory_ids.extend(
            value
            for value in getattr(item, "related_memory_ids", ())
            if isinstance(value, str) and value
        )
        memory_id = getattr(item, "memory_id", None)
        if isinstance(memory_id, str) and memory_id:
            memory_ids.append(memory_id)
    return list(dict.fromkeys(memory_ids))


async def _active_ids(store, context, memory_ids: list[str], memory_type: str) -> list[str]:
    matches: list[str] = []
    for memory_id in memory_ids:
        raw = await store.get_raw(memory_id, context)
        if (
            raw is not None
            and raw.mem_type == memory_type
            and raw.status == "active"
        ):
            matches.append(memory_id)
    return matches
