from __future__ import annotations

import json
import os
import statistics
import time

import pytest

from homemaster.config import load_config
from homemaster.memory.automatic_recall import build_mindmemos_request_context
from homemaster.memory.managed_neo4j import ManagedNeo4jRuntime
from homemaster.memory.mindmemos_runtime import EmbeddedMindMemOS


@pytest.mark.asyncio
async def test_real_automatic_recall_smoke_benchmark() -> None:
    if os.environ.get("HOMEMASTER_RUN_REAL_AUTOMATIC_RECALL") != "1":
        pytest.skip("set HOMEMASTER_RUN_REAL_AUTOMATIC_RECALL=1 for real external smoke")

    config = load_config("config/homemaster.yaml")
    neo4j = ManagedNeo4jRuntime(config.memory)
    store = EmbeddedMindMemOS(config)
    await neo4j.start()
    try:
        await store.start()
        try:
            assert store.available, store.unavailable_cause
            context = build_mindmemos_request_context(
                request_id="automatic-recall-integration",
                tenant_id="default",
                session_id="automatic-recall-integration",
            )
            measurements: list[float] = []
            counts: list[int] = []
            statuses: list[str] = []
            for _ in range(6):
                started = time.perf_counter()
                result = await store.search(
                    "网站操作步骤和历史告警处理经验",
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
                "default",
                "default",
                "default",
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
            await store.close()
    finally:
        await neo4j.close()
