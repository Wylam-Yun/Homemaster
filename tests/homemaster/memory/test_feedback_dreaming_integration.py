from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from homemaster.application import RunPolicy, RunRequest, RunStatus
from homemaster.cli.composition import create_home_application
from homemaster.config import load_config
from homemaster.experience import DreamingCoordinator, DreamingStateStore
from homemaster.memory.models import FactRecord
from homemaster.memory.serialization import serialize_record


@pytest.fixture
def live_config(tmp_path: Path):
    config = load_config("config/homemaster.yaml")
    if not config.get_provider(kind="chat").api_keys:
        pytest.skip("a real chat provider API key is required")
    if not config.get_provider(
        config.memory.embedding_provider_name,
        kind="embedding",
    ).api_keys:
        pytest.skip("a real embedding provider API key is required")
    if config.memory.neo4j.mode != "managed_local":
        pytest.skip("the V2.6 live gate requires managed-local Neo4j")
    return config.model_copy(
        update={
            "memory": config.memory.model_copy(
                update={
                    "data_root": tmp_path / "memory",
                    "dreaming_memory_threshold": 8,
                }
            ),
            "runtime": config.runtime.model_copy(
                update={"runtime_root": tmp_path / "runtime"}
            ),
        }
    )


@pytest.mark.live_api
@pytest.mark.asyncio
async def test_real_structured_update_and_history_reach_qdrant_and_neo4j(live_config) -> None:
    nonce = "v26-direct-update-" + uuid.uuid4().hex[:10]
    bundle = create_home_application(
        config=live_config,
        run_label=nonce,
        progress=False,
        quiet=True,
        tool_environment=None,
    )
    application = bundle.application
    await application.start()
    active_ids: list[str] = []
    try:
        assert bundle.mindmemos is not None
        store = bundle.mindmemos
        context = _memory_context(nonce, session_id=nonce)
        old_id = await _add_fact(
            store,
            context,
            subject=nonce,
            predicate="package_manager",
            value="conda",
        )
        active_ids.append(old_id)
        old = await store.get_raw(old_id, context)
        assert old is not None and old.status == "active"

        record = FactRecord(
            memory_type="fact",
            subject={"type": "other", "name": nonce},
            predicate="package_manager",
            value="uv",
            source="user_statement",
        )
        serialized = serialize_record(record, provenance_seq=2)
        result = await store.update_versioned(
            memory_id=old_id,
            content=serialized.text,
            metadata={**serialized.metadata, "homemaster_memory_type": "fact"},
            context=context,
        )

        assert result.status == "ok"
        new_id = result.memory_id
        active_ids.append(new_id)
        old_after = await store.get_raw(old_id, context)
        new = await store.get_raw(new_id, context)
        assert old_after is not None and old_after.status == "archived"
        assert new is not None and new.status == "active"
        assert new.content == serialized.text
        request_metadata = _real_request_metadata(new.metadata)
        assert json.loads(request_metadata["record_json"])["value"] == "uv"
        assert request_metadata["provenance_seq"] == 2
        assert await store.has_memory_lineage(
            source_memory_id=new_id,
            target_memory_id=old_id,
            relationship="DERIVED_FROM",
            context=context,
        )
        assert [item.memory_id for item in await store.get_history(new_id, context)] == [
            new_id,
            old_id,
        ]
        assert [item.memory_id for item in await store.get_history(old_id, context)] == [
            new_id,
            old_id,
        ]
        assert store._neo4j is not None
        entity_rows = await store._neo4j.run_read(
            """
            MATCH (entity:Entity {project_id: $project_id, entity_id: $entity_id})
            RETURN entity.description AS description
            """,
            project_id=context.project_id,
            entity_id=new.entity_id,
        )
        assert len(entity_rows) == 1
        assert "uv" in entity_rows[0]["description"]
        assert "conda" not in entity_rows[0]["description"]
    finally:
        if bundle.mindmemos is not None:
            cleanup_context = _memory_context(nonce, session_id=nonce)
            for memory_id in dict.fromkeys(active_ids):
                raw = await bundle.mindmemos.get_raw(memory_id, cleanup_context)
                if raw is not None and raw.status == "active":
                    deleted = await bundle.mindmemos.delete(memory_id, cleanup_context)
                    assert deleted.status == "ok"
        await application.aclose()


@pytest.mark.live_api
@pytest.mark.asyncio
async def test_real_application_explicit_feedback_updates_only_recalled_memory(
    live_config,
) -> None:
    nonce = "v26-explicit-" + uuid.uuid4().hex[:10]
    bundle = create_home_application(
        config=live_config,
        run_label=nonce,
        progress=False,
        quiet=True,
        tool_environment=None,
    )
    application = bundle.application
    await application.start()
    created_ids: list[str] = []
    try:
        assert bundle.mindmemos is not None
        store = bundle.mindmemos
        context = _memory_context(nonce, session_id=nonce)
        old_id = await _add_fact(
            store,
            context,
            subject=nonce,
            predicate="package_manager",
            value="conda",
        )
        unrelated_id = await _add_fact(
            store,
            context,
            subject=f"{nonce}-unrelated",
            predicate="editor",
            value="vim",
        )
        created_ids.extend([old_id, unrelated_id])
        unrelated_before = await store.get_raw(unrelated_id, context)
        assert unrelated_before is not None
        unrelated_content = unrelated_before.content

        result = await application.run(
            RunRequest(
                text=(
                    f"The recalled memory about {nonce} is wrong. I use uv, not conda. "
                    "You must call mindmemos_feedback exactly once with this correction; "
                    "do not call mindmemos_update or any other memory mutation tool."
                ),
                session_id=nonce,
                profile="home",
                run_policy=RunPolicy(max_turns=1, max_tool_iterations=3, deadline_s=300),
            )
        )

        assert result.status is RunStatus.REPLIED
        explicit_events = [
            event
            for event in application.event_bus.events
            if event.type == "memory.feedback.explicit.completed"
        ]
        assert len(explicit_events) == 1
        actions = explicit_events[0].payload["actions"]
        assert len(actions) == 1
        action = actions[0]
        assert action["action"] == "update"
        assert action["status"] == "ok"
        assert action["terminal_verified"] is True
        assert action["target_memory_id"] == old_id
        new_id = action["result_memory_id"]
        created_ids.append(new_id)

        old = await store.get_raw(old_id, context)
        new = await store.get_raw(new_id, context)
        unrelated_after = await store.get_raw(unrelated_id, context)
        assert old is not None and old.status == "archived"
        assert new is not None and new.status == "active"
        assert "uv" in new.content.lower()
        assert "conda" not in new.content.lower()
        assert unrelated_after is not None
        assert unrelated_after.status == "active"
        assert unrelated_after.content == unrelated_content
        assert await store.has_memory_lineage(
            source_memory_id=new_id,
            target_memory_id=old_id,
            relationship="DERIVED_FROM",
            context=context,
        )
    finally:
        if bundle.mindmemos is not None:
            cleanup_context = _memory_context(nonce, session_id=nonce)
            for memory_id in dict.fromkeys(created_ids):
                raw = await bundle.mindmemos.get_raw(memory_id, cleanup_context)
                if raw is not None and raw.status == "active":
                    deleted = await bundle.mindmemos.delete(memory_id, cleanup_context)
                    assert deleted.status == "ok"
            await application.aclose()


@pytest.mark.live_api
@pytest.mark.asyncio
async def test_real_dreaming_no_action_consumes_verified_batch(live_config) -> None:
    nonce = "v26-dreaming-" + uuid.uuid4().hex[:10]
    bundle = create_home_application(
        config=live_config,
        run_label=nonce,
        progress=False,
        quiet=True,
        tool_environment=None,
    )
    application = bundle.application
    await application.start()
    try:
        assert bundle.mindmemos is not None
        from mindmemos.typing import DialogueMessage

        context = _memory_context(nonce, session_id=nonce)
        recorded = await bundle.mindmemos.add_vanilla(
            [
                DialogueMessage(role="user", content=f"Remember validation {nonce}."),
                DialogueMessage(
                    role="assistant",
                    content="The validation is complete and no correction is needed.",
                ),
            ],
            context,
            metadata={
                "source_type": "v26_dreaming_live",
                "source_session_id": nonce,
            },
        )
        assert recorded.result.status == "ok"
        memory_ids = [
            item.memory_id
            for item in recorded.result.memories
            if item.operation == "add" and item.memory_id
        ]
        assert memory_ids
        for memory_id in memory_ids:
            raw = await bundle.mindmemos.get_raw(memory_id, context)
            assert raw is not None and raw.status == "active"

        coordinator = DreamingCoordinator(
            store=DreamingStateStore(live_config.memory.data_root, threshold=1),
            mindmemos=bundle.mindmemos,
        )
        outcome = await coordinator.register_and_run(
            context=context,
            add_record_id=recorded.add_record_id,
            memory_ids=tuple(memory_ids),
        )
        assert outcome == "no_action"
        add_records = await bundle.mindmemos.get_add_records(
            [recorded.add_record_id], context
        )
        assert len(add_records) == 1
        assert add_records[0].payload["consolidation_status"] == "done"
        state = DreamingStateStore(live_config.memory.data_root, threshold=1).read(
            project_id="local", user_id="local"
        )
        assert state["pending"] is False
        assert state["last_successful_watermark"]["add_record_ids"] == [
            recorded.add_record_id
        ]
    finally:
        await application.aclose()


async def _add_fact(store, context, *, subject: str, predicate: str, value: str) -> str:
    from mindmemos.typing import TextMessage

    record = FactRecord(
        memory_type="fact",
        subject={"type": "other", "name": subject},
        predicate=predicate,
        value=value,
        source="user_statement",
    )
    serialized = serialize_record(record, provenance_seq=1)
    result = await store.add(
        [TextMessage(text=serialized.text)],
        context,
        force_generation=True,
        metadata={**serialized.metadata, "homemaster_memory_type": "fact"},
    )
    assert result.status == "ok"
    candidates = []
    for item in result.memories:
        candidates.extend(item.related_memory_ids)
        if item.memory_id:
            candidates.append(item.memory_id)
    for memory_id in dict.fromkeys(candidates):
        raw = await store.get_raw(memory_id, context)
        if raw is not None and raw.status == "active" and raw.mem_type == "fact":
            assert subject in raw.content
            return memory_id
    raise AssertionError("real add did not produce one active fact raw memory")


def _real_request_metadata(metadata):
    request = metadata["request_metadata"]
    records = request.get("record_metadata")
    if isinstance(records, list):
        return next(item for item in records if "record_json" in item)
    return request


def _memory_context(request_id: str, *, session_id: str):
    from mindmemos.typing import MemoryRequestContext

    return MemoryRequestContext(
        request_id=request_id,
        account_id="local",
        project_id="local",
        api_key_uuid="embedded-local",
        user_id="local",
        app_id="homemaster",
        session_id=session_id,
        agent_id="homemaster",
    )
