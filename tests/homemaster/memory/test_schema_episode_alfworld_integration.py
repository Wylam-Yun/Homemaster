from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from homemaster.benchmarking.alfworld.env_adapter import (
    AlfworldEnvAdapter,
    build_alfworld_batch_env,
)
from homemaster.benchmarking.alfworld.types import AlfworldBenchmarkConfig
from homemaster.config import load_config
from homemaster.memory.automatic_recall import build_mindmemos_request_context
from homemaster.memory.managed_neo4j import ManagedNeo4jRuntime
from homemaster.memory.mindmemos_runtime import EmbeddedMindMemOS


@pytest.mark.asyncio
async def test_real_schema_episode_alfworld_single_episode() -> None:
    if os.environ.get("HOMEMASTER_RUN_REAL_SCHEMA_EPISODE_ALFWORLD") != "1":
        pytest.skip("set HOMEMASTER_RUN_REAL_SCHEMA_EPISODE_ALFWORLD=1 for real ALFWorld gate")

    alfworld_root = Path(
        os.environ.get("ALFWORLD_ROOT", "/home/haodong2/weilin/red_bird/alfworld")
    )
    alfworld_config = Path(
        os.environ.get("ALFWORLD_CONFIG", str(alfworld_root / "configs" / "base_config.yaml"))
    )
    provider_config = Path(
        os.environ.get("HOMEMASTER_ALFWORLD_PROVIDER_CONFIG", "config/homemaster.yaml")
    )
    env_type = os.environ.get("HOMEMASTER_ALFWORLD_ENV_TYPE", "AlfredTWEnv")
    split = os.environ.get("HOMEMASTER_ALFWORLD_SPLIT", "valid_seen")
    assert (alfworld_root / "data" / "json_2.1.1" / split).is_dir()
    assert alfworld_config.is_file()
    assert provider_config.is_file()

    benchmark_config = AlfworldBenchmarkConfig(
        alfworld_root=alfworld_root,
        alfworld_config=alfworld_config,
        data_root=alfworld_root / "data",
        trace_root=Path(
            os.environ.get("HOMEMASTER_ALFWORLD_TRACE_ROOT", "var/alfworld-schema-episode-gate")
        ),
        env_type=env_type,  # type: ignore[arg-type]
        split=split,  # type: ignore[arg-type]
        episodes=1,
        memory_mode="disabled",
        observation_mode="textual_debug",
        provider_config=provider_config,
        provider_name=os.environ.get("HOMEMASTER_ALFWORLD_PROVIDER", "Mimo"),
    )
    env = build_alfworld_batch_env(benchmark_config)
    adapter = AlfworldEnvAdapter(
        env=env,
        episode_prefix=split,
        seed=benchmark_config.seed,
    )
    reset = adapter.reset()
    assert reset.ready, reset.setup_failure
    assert reset.state is not None
    state = reset.state

    config = load_config(
        os.environ.get("HOMEMASTER_REAL_TEST_CONFIG", str(provider_config))
    )
    neo4j = ManagedNeo4jRuntime(config.memory)
    store = EmbeddedMindMemOS(config)
    nonce = "alfworld-schema-" + uuid.uuid4().hex[:12]
    context = build_mindmemos_request_context(
        request_id=nonce,
        tenant_id=nonce,
        session_id=state.episode_id,
    )
    try:
        await neo4j.start()
        await store.start()
        assert store.available, store.unavailable_cause
        task_text = state.task or state.observation
        episode = {
            "schema_version": "homemaster.schema_episode.v1",
            "session_id": state.episode_id,
            "exit_reason": "alfworld_data_gate",
            "extractor_slots": [
                "object_location",
                "search_observation",
                "task_procedure",
            ],
            "events": [
                {
                    "event_id": f"{nonce}-task",
                    "session_id": state.episode_id,
                    "run_id": nonce,
                    "timestamp": "2026-09-18T00:00:00Z",
                    "type": "runtime.turn_started",
                    "payload": {"user_text": task_text},
                    "trace_offset": 0,
                },
                {
                    "event_id": f"{nonce}-observation",
                    "session_id": state.episode_id,
                    "run_id": nonce,
                    "timestamp": "2026-09-18T00:00:01Z",
                    "type": "tool.call_completed",
                    "name": "alfworld_observation",
                    "tool_call_id": f"{nonce}-observation",
                    "payload": {
                        "args": {"episode_id": state.episode_id},
                        "result": state.observation,
                        "data": {"status": "success", "source": "alfworld_reset"},
                    },
                    "trace_offset": 1,
                },
            ],
            "tool_steps": [
                {
                    "session_id": state.episode_id,
                    "run_id": nonce,
                    "tool_call_id": f"{nonce}-observation",
                    "tool": "alfworld_observation",
                    "arguments": {"episode_id": state.episode_id},
                    "result": state.observation,
                    "status": "success",
                    "timestamp": "2026-09-18T00:00:01Z",
                    "source_event_ids": [f"{nonce}-observation"],
                }
            ],
            "diagnostics": [],
            "provenance": {
                "source": "alfworld_reset",
                "source_event_ids": [f"{nonce}-task", f"{nonce}-observation"],
            },
        }
        result = await store.add_schema_episode(
            episode,
            context,
            metadata={
                "source_type": "alfworld_schema_episode_gate",
                "source_session_id": state.episode_id,
                "input_hash": "sha256:" + nonce,
                "extractor_version": "schema-episode-v1:prompts-v1",
            },
        )
        assert result.result.status == "ok"
        receipt = result.result.schema_episode
        assert receipt is not None
        assert uuid.UUID(receipt.episode_id)
        assert receipt.write_status in {"completed", "not_detected"}
        completed_domains = {
            domain
            for domain, type_result in receipt.types.items()
            if type_result.status == "completed" and type_result.memory_ids
        }
        assert completed_domains, "real ALFWorld episode produced no domain memory"
        for domain, type_result in receipt.types.items():
            assert type_result.status in {"completed", "not_detected"}
            for memory_id in type_result.memory_ids:
                raw = await store.get_raw(memory_id, context)
                assert raw is not None
                assert raw.status == "active"
                assert raw.mem_type in {"fact", "experience"}
                record = json.loads(raw.content)
                assert record["type"] == domain
    finally:
        await store.close()
        await neo4j.close()
        close = getattr(env, "close", None)
        if callable(close):
            close()
