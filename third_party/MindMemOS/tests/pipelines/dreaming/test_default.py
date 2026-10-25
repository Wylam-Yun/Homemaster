from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from mindmemos.components.dreaming.entity_merge import EntityMergeVerdict, entity_merge_parser
from mindmemos.config import DreamingConfig, TextProcessingConfig
from mindmemos.infra.db import QdrantRecord
from mindmemos.pipelines.dreaming.default import ConsolidationScope, DefaultDreamingPipeline
from mindmemos.typing.activity import ActivityScope, RecentActivityBundle, WrittenMemoryRef
from mindmemos.typing.algo import (
    ConsolidationAction,
    ConsolidationCreate,
    ConsolidationLink,
    ConsolidationMerge,
)
from mindmemos.typing.memory import GraphNeighborScope, MemoryRequestContext, MemoryView
from mindmemos.typing.service import DreamingActionReceipt, DreamingPipelineInput


class FakeReader:
    def __init__(self, memories: list[MemoryView]) -> None:
        self.memories = memories
        self.add_record_payloads: dict[str, dict] = {}
        self.neo4j_read_calls: list[tuple[str, dict]] = []
        self._clients = SimpleNamespace(neo4j=SimpleNamespace(run_read=self._run_neo4j_read))

    async def _run_neo4j_read(self, query: str, **params):
        self.neo4j_read_calls.append((query, params))
        return [
            {
                "entity_id": "entity-1",
                "entity_name": "Alice",
                "entity_type": "person",
                "memory_ids": [memory.memory_id for memory in self.memories],
            }
        ]

    async def get_memories(self, _ctx, memory_ids: list[str]) -> list[MemoryView]:
        requested = set(memory_ids)
        return [memory for memory in self.memories if memory.memory_id in requested]

    async def list_memories(self, _ctx, *, filters=None, limit=50, cursor=None):
        return self.memories[:limit], None

    async def list_add_records(self, _ctx, *, filters=None, limit=50, cursor=None):
        return [], None

    async def get_add_records_by_ids(self, _ctx, add_record_ids: list[str]):
        return [
            QdrantRecord(point_id=add_record_id, payload=self.add_record_payloads.get(add_record_id, {}))
            for add_record_id in add_record_ids
        ]

    async def list_memory_neighbor_scopes(
        self,
        _ctx,
        memory_ids: list[str],
        *,
        edge_filter=None,
        limit_per_entity: int = 50,
        limit_direct_per_memory: int = 20,
        attach_direct_neighbors_to_entity_scopes: bool = True,
    ) -> list[GraphNeighborScope]:
        all_ids = tuple(memory.memory_id for memory in self.memories[:limit_per_entity])
        return [
            GraphNeighborScope(
                seed_memory_id=memory_id,
                entity_id="entity-1",
                entity_name="Alice",
                entity_type="person",
                memory_ids=all_ids,
                source="shared_entity",
            )
            for memory_id in memory_ids
        ]

    async def search_sparse(self, _ctx, _req, *, indices, values):
        return SimpleNamespace(hits=[])


class FakeWriter:
    def __init__(self) -> None:
        self.plans: list[object] = []
        self.deleted: list[tuple[str, str]] = []
        self.updated: list[object] = []
        self.add_record_patches: list[tuple[str, dict]] = []

    async def apply_mutation_plan(self, _ctx, plan, *, consistency="fast"):
        write_plan = plan.to_write_plan()
        if write_plan.memories or write_plan.relationships:
            self.plans.append(write_plan)
        for command in plan.memory_updates:
            self.updated.append(command)
        for command in plan.memory_deletes:
            self.deleted.append((command.memory_id, command.reason))
        return SimpleNamespace(
            memory_ids=[m.memory_id for m in write_plan.memories],
            mutations=[SimpleNamespace(memory_id=command.memory_id, changed=True) for command in plan.memory_updates]
            + [SimpleNamespace(memory_id=command.memory_id, changed=True) for command in plan.memory_deletes],
            errors=[],
            graph_pending=False,
        )

    async def write(self, _ctx, plan, *, consistency="fast"):
        self.plans.append(plan)
        return SimpleNamespace(memory_ids=[m.memory_id for m in plan.memories])

    async def patch_add_record(self, _ctx, add_record_id, payload):
        self.add_record_patches.append((add_record_id, payload))
        return SimpleNamespace(changed=True)

    async def delete_memory(self, _ctx, req):
        self.deleted.append((req.memory_id, req.reason))
        return SimpleNamespace(changed=True)

    async def update_memory(self, _ctx, req):
        self.updated.append(req)
        return SimpleNamespace(changed=True)


class FakeEmbed:
    async def embed(self, *, task: str, text):
        texts = text if isinstance(text, list) else [text]
        return SimpleNamespace(embeddings=[[0.1, 0.2, 0.3] for _ in texts])


class FakeLLM:
    def __init__(self, action: ConsolidationAction) -> None:
        self.action = action
        self.calls = 0

    async def chat(self, **_kwargs):
        messages = _kwargs["messages"]
        if not any(message["role"] == "user" and message["content"].strip() for message in messages):
            raise RuntimeError("provider requires a non-empty user message")
        self.calls += 1
        if self.calls % 2 == 1:
            return SimpleNamespace(
                parsed=SimpleNamespace(
                    candidates=[
                        SimpleNamespace(
                            candidate_type="needs_consolidation",
                            primary_memory_id="m1",
                            neighbor_memory_id="m2",
                            primary_value_hint="",
                            neighbor_value_hint="",
                            confidence="high",
                            reason="test relation",
                        )
                    ]
                )
            )
        return SimpleNamespace(parsed=self.action)


class RejectingLLM:
    async def chat(self, **_kwargs):
        raise RuntimeError("provider rejected request")


class FakeActivityCollector:
    def __init__(self, memories: list[MemoryView]) -> None:
        self.memories = memories

    async def collect(self, scope: ActivityScope, **_kwargs) -> RecentActivityBundle:
        now = datetime.now(UTC)
        return RecentActivityBundle(
            window_start=now - timedelta(days=1),
            window_end=now,
            scope=scope,
            written_memories=[
                WrittenMemoryRef(
                    memory_id=memory.memory_id,
                    content=memory.content,
                    add_record_ids=[f"add-{memory.memory_id}"],
                    session_id="sess",
                    user_id="user",
                )
                for memory in self.memories
            ],
        )


def ctx() -> MemoryRequestContext:
    return MemoryRequestContext(
        request_id="00000000-0000-0000-0000-000000000001",
        account_id="acct",
        project_id="proj",
        api_key_uuid="key",
        user_id="user",
        session_id="sess",
        scopes=["memory:write"],
    )


def memory(
    memory_id: str,
    *,
    content: str,
    entity_id: str = "entity-1",
    property_name: str = "preference",
    content_hash: str | None = None,
    created_offset: int = 0,
) -> MemoryView:
    now = datetime.now(UTC)
    metadata = {}
    if content_hash:
        metadata["content_hash"] = content_hash
    return MemoryView(
        memory_id=memory_id,
        project_id="proj",
        content=content,
        mem_type="fact",
        status="active",
        metadata=metadata,
        root_id=["root-1"],
        entity_id=entity_id,
        entity_type="person",
        property_name=property_name,
        created_at=now - timedelta(minutes=created_offset),
        update_at=now - timedelta(minutes=created_offset),
    )


def pipeline(
    *, memories: list[MemoryView], action: ConsolidationAction
) -> tuple[DefaultDreamingPipeline, FakeReader, FakeWriter]:
    reader = FakeReader(memories)
    writer = FakeWriter()
    pipe = DefaultDreamingPipeline(
        dreaming_config=DreamingConfig(
            lookback_days=7,
            max_scopes_per_run=5,
            max_seed_memories=20,
            max_memories_per_scope=20,
            min_scope_updates=1,
            min_cluster_size=2,
        ),
        text_config=TextProcessingConfig(),
        llm_client=FakeLLM(action),
        embed_client=FakeEmbed(),
        activity_collector=FakeActivityCollector(memories),
        db_reader=reader,
        db_writer=writer,
        consistency="fast",
    )
    return pipe, reader, writer


def test_dreaming_input_round_trips_seed_add_record_ids():
    payload = DreamingPipelineInput(
        mode="sync",
        seed_add_record_ids=["add-m1", "add-m2"],
    )

    assert DreamingPipelineInput.model_validate(payload.model_dump()) == payload


@pytest.mark.asyncio
async def test_dreaming_archives_exact_duplicates_before_llm_actions():
    memories = [
        memory("m1", content="Alice likes tea", content_hash="same", created_offset=1),
        memory("m2", content="Alice likes tea", content_hash="same", created_offset=2),
    ]
    pipe, reader, writer = pipeline(memories=memories, action=ConsolidationAction())

    result = await pipe.dream_sync(DreamingPipelineInput(), ctx())

    assert result.status == "ok"
    assert writer.deleted == [("m2", "duplicate_of:m1")]
    assert writer.plans == []


@pytest.mark.asyncio
async def test_dreaming_provider_failure_does_not_complete_add_records():
    memories = [
        memory("m1", content="Alice likes tea", created_offset=1),
        memory("m2", content="Alice likes coffee", created_offset=2),
    ]
    pipe, _reader, writer = pipeline(memories=memories, action=ConsolidationAction())
    pipe._llm_client = RejectingLLM()

    result = await pipe.dream_sync(DreamingPipelineInput(mode="sync"), ctx())

    assert result.status == "error"
    assert result.outcome == "failed"
    assert result.errors
    assert writer.add_record_patches == []


@pytest.mark.asyncio
async def test_exact_duplicate_mutation_failure_returns_typed_failure(monkeypatch):
    memories = [
        memory("m1", content="Alice likes tea", content_hash="same", created_offset=1),
        memory("m2", content="Alice likes tea", content_hash="same", created_offset=2),
    ]
    pipe, _reader, writer = pipeline(memories=memories, action=ConsolidationAction())

    async def reject_mutation(*_args, **_kwargs):
        raise RuntimeError("qdrant rejected archive")

    monkeypatch.setattr(pipe, "_apply_memory_deletes", reject_mutation)

    result = await pipe.dream_sync(DreamingPipelineInput(), ctx())

    assert result.status == "error"
    assert result.outcome == "failed"
    assert result.completed_add_record_ids == []
    assert "qdrant rejected archive" in result.errors[0]
    assert writer.add_record_patches == []


@pytest.mark.asyncio
async def test_failed_scope_does_not_block_other_scope_completion(monkeypatch):
    memories = [
        memory("m1", content="Alice likes tea"),
        memory("m2", content="Alice likes coffee"),
        memory("m3", content="Bob likes tea", entity_id="entity-2"),
        memory("m4", content="Bob likes coffee", entity_id="entity-2"),
    ]
    pipe, _reader, writer = pipeline(memories=memories, action=ConsolidationAction())
    scopes = [
        (
            ConsolidationScope(
                entity_id="entity-1",
                property_name="preference",
                score=2,
                seed_memory_ids=("m1", "m2"),
                add_record_ids=("add-a",),
                primary_memory_id="m1",
            ),
            memories[:2],
        ),
        (
            ConsolidationScope(
                entity_id="entity-2",
                property_name="preference",
                score=2,
                seed_memory_ids=("m3", "m4"),
                add_record_ids=("add-b",),
                primary_memory_id="m3",
            ),
            memories[2:],
        ),
    ]

    async def fixed_clusters(*_args, **_kwargs):
        return scopes

    async def per_scope_receipts(_context, cluster_memories, _archived):
        if cluster_memories[0].memory_id == "m1":
            return [
                DreamingActionReceipt(
                    action="archive",
                    target_memory_ids=["m2"],
                    status="error",
                    error="archive rejected",
                )
            ]
        return []

    async def no_issues(_context):
        return []

    monkeypatch.setattr(pipe, "_cluster_hot_memories", fixed_clusters)
    monkeypatch.setattr(pipe, "_apply_exact_duplicate_archives", per_scope_receipts)
    monkeypatch.setattr(pipe, "_call_relation_detection_llm", no_issues)

    result = await pipe.dream_sync(DreamingPipelineInput(), ctx())

    assert result.status == "error"
    assert result.completed_add_record_ids == ["add-b"]
    assert [item[0] for item in writer.add_record_patches] == ["add-b"]


@pytest.mark.asyncio
async def test_dreaming_skips_done_add_records_when_clustering_hot_memories():
    memories = [
        memory("m1", content="Alice likes tea", created_offset=1),
        memory("m2", content="Alice likes coffee", created_offset=2),
    ]
    pipe, reader, _writer = pipeline(memories=memories, action=ConsolidationAction())
    reader.add_record_payloads = {
        "add-m1": {"consolidation_status": "done"},
        "add-m2": {"consolidation_status": "pending"},
    }

    clusters = await pipe._cluster_hot_memories(ctx())

    assert len(clusters) == 1
    scope, _memories = clusters[0]
    assert scope.primary_memory_id == "m2"
    assert scope.add_record_ids == ("add-m2",)
    query, params = reader.neo4j_read_calls[0]
    assert "ORDER BY coalesce(neighbor.update_at, neighbor.created_at) DESC" in query
    assert "LIMIT $entity_probe_limit" in query
    assert params["entity_probe_limit"] == pipe._cfg.max_entity_memory_count + 1


@pytest.mark.asyncio
async def test_dreaming_locked_seeds_read_add_records_without_lookback_scan():
    memories = [
        memory("m1", content="Alice likes tea"),
        memory("m2", content="Alice likes coffee"),
    ]
    pipe, reader, _writer = pipeline(memories=memories, action=ConsolidationAction())
    reader.add_record_payloads = {
        "old-add-1": {
            "memories": [
                {"operation": "add", "memory_id": "m1", "content": "Alice likes tea"}
            ]
        },
        "old-add-2": {
            "memories": [
                {
                    "operation": "add",
                    "memory_id": "m2",
                    "content": "Alice likes coffee",
                }
            ]
        },
    }

    clusters = await pipe._cluster_hot_memories(
        ctx(), seed_add_record_ids=["old-add-1", "old-add-2"]
    )

    assert len(clusters) == 1
    assert clusters[0][0].add_record_ids == ("old-add-1", "old-add-2")


@pytest.mark.asyncio
async def test_dreaming_no_cluster_completes_locked_seed_add_records(monkeypatch):
    memories = [memory("m1", content="One standalone memory")]
    pipe, reader, writer = pipeline(memories=memories, action=ConsolidationAction())
    reader.add_record_payloads = {
        "add-m1": {
            "memories": [
                {"operation": "add", "memory_id": "m1", "content": memories[0].content}
            ]
        }
    }

    async def no_graph_clusters(_query: str, **_params):
        return []

    monkeypatch.setattr(reader, "_run_neo4j_read", no_graph_clusters)
    reader._clients.neo4j.run_read = no_graph_clusters

    result = await pipe.dream_sync(
        DreamingPipelineInput(seed_add_record_ids=["add-m1"]), ctx()
    )

    assert result.status == "ok"
    assert result.outcome == "no_action"
    assert result.reviewed_add_record_ids == ["add-m1"]
    assert result.completed_add_record_ids == ["add-m1"]
    assert [item[0] for item in writer.add_record_patches] == ["add-m1"]


@pytest.mark.asyncio
async def test_dreaming_skips_entity_clusters_over_memory_limit():
    memories = [
        memory("m1", content="Alice likes green tea"),
        memory("m2", content="Alice likes black tea"),
        memory("m3", content="Alice likes jasmine tea"),
    ]
    pipe, reader, _writer = pipeline(memories=memories, action=ConsolidationAction())
    pipe._cfg.max_entity_memory_count = 2

    clusters = await pipe._cluster_hot_memories(ctx())

    assert clusters == []
    _query, params = reader.neo4j_read_calls[0]
    assert params["entity_probe_limit"] == 3


@pytest.mark.asyncio
async def test_dreaming_dedupes_duplicate_clusters_before_llm_calls():
    memories = [
        memory("m1", content="Alice likes green tea", created_offset=2),
        memory("m2", content="Alice prefers jasmine tea", created_offset=1),
    ]
    action = ConsolidationAction()
    pipe, _reader, writer = pipeline(memories=memories, action=action)

    result = await pipe.dream_sync(DreamingPipelineInput(), ctx())

    assert result.status == "ok"
    assert pipe._llm_client.calls == 2
    patched_ids = {add_record_id for add_record_id, _payload in writer.add_record_patches}
    assert patched_ids == {"add-m1", "add-m2"}


@pytest.mark.asyncio
async def test_dreaming_merge_creates_vectorized_memory_and_archives_sources():
    memories = [
        memory("m1", content="Alice likes green tea", created_offset=2),
        memory("m2", content="Alice prefers jasmine tea", created_offset=1),
    ]
    action = ConsolidationAction(
        merges=[
            ConsolidationMerge(
                source_memory_ids=["m1", "m2"],
                target_content="Alice prefers green or jasmine tea.",
                target_entity_id="entity-1",
                target_property_name="preference",
                merge_reason="fragments describe the same preference",
            )
        ]
    )
    pipe, reader, writer = pipeline(memories=memories, action=action)

    await pipe.dream_sync(DreamingPipelineInput(), ctx())

    assert [item[0] for item in writer.deleted] == ["m1", "m2"]
    assert len(writer.plans) == 1
    plan = writer.plans[0]
    assert len(plan.memories) == 1
    assert plan.memories[0].content == "Alice prefers green or jasmine tea."
    assert plan.memories[0].parent_ids == ["m1", "m2"]
    assert len(plan.vectors) == 1
    assert plan.vectors[0].semantic_vector == [0.1, 0.2, 0.3]
    assert plan.relationships


@pytest.mark.asyncio
async def test_dreaming_link_actions_cannot_reference_new_memories():
    memories = [
        memory("m1", content="Alice likes green tea", created_offset=2),
        memory("m2", content="Alice prefers jasmine tea", created_offset=1),
    ]
    action = ConsolidationAction(
        creates=[
            ConsolidationCreate(
                content="Alice has tea preferences.",
                evidence_memory_ids=["m1", "m2"],
                reason="generalized preference",
            )
        ],
        links=[
            ConsolidationLink(
                source_kind="Memory",
                source_id="new-memory",
                target_kind="Memory",
                target_id="m1",
                relation_type="generalizes",
            )
        ],
    )
    pipe, _reader, writer = pipeline(memories=memories, action=action)

    await pipe.dream_sync(DreamingPipelineInput(), ctx())

    plan = writer.plans[0]
    assert len(plan.memories) == 1
    assert all(rel.relation_type != "generalizes" for rel in plan.relationships)
    assert {rel.relation_type for rel in plan.relationships if rel.target.node_id in {"m1", "m2"}} == {
        "dreaming_evidence"
    }


# -- entity canonicalization (phase 0) ---------------------------------------


class MergeReader(FakeReader):
    """Fake reader whose entity expansion returns per-test rows and whose
    neo4j namespace also records write queries."""

    def __init__(self, memories, entity_rows):
        super().__init__(memories)
        self.entity_rows = entity_rows
        self.write_calls: list[tuple[str, dict]] = []
        self._clients.neo4j._run_write = self._run_neo4j_write

    async def _run_neo4j_read(self, query: str, **params):
        self.neo4j_read_calls.append((query, params))
        if "entity_ids" in params:
            return [
                {"entity_id": eid, "content": f"sample content for {eid}"}
                for eid in params["entity_ids"]
            ]
        if "count(*) AS remaining" in query:
            return [{"remaining": 0}]
        return list(self.entity_rows)

    async def _run_neo4j_write(self, query: str, **params):
        self.write_calls.append((query, params))


def merge_pipeline(
    *,
    memories: list[MemoryView],
    entity_rows: list[dict],
    verdicts: list[EntityMergeVerdict],
    action: ConsolidationAction | None = None,
    **cfg_overrides,
) -> tuple[DefaultDreamingPipeline, MergeReader, FakeWriter]:
    reader = MergeReader(memories, entity_rows)
    writer = FakeWriter()
    pipe = DefaultDreamingPipeline(
        dreaming_config=DreamingConfig(
            lookback_days=7,
            max_scopes_per_run=5,
            max_seed_memories=20,
            max_memories_per_scope=20,
            min_scope_updates=1,
            min_cluster_size=2,
            entity_canonicalization_enabled=True,
            **cfg_overrides,
        ),
        text_config=TextProcessingConfig(),
        llm_client=FakeLLM(action or ConsolidationAction()),
        embed_client=FakeEmbed(),
        activity_collector=FakeActivityCollector(memories),
        db_reader=reader,
        db_writer=writer,
        consistency="fast",
    )

    async def fixed_verdicts(_context_str):
        return list(verdicts)

    pipe._call_entity_merge_llm = fixed_verdicts
    return pipe, reader, writer


def dup_pair() -> tuple[list[MemoryView], list[dict]]:
    memories = [
        memory("m1", content="same fact", content_hash="h1", created_offset=2),
        memory("m2", content="same fact", content_hash="h1", created_offset=1),
    ]
    rows = [
        {"entity_id": "e-shelf", "entity_name": "shelf", "entity_type": "location",
         "memory_ids": ["m1"]},
        {"entity_id": "e-shelf1", "entity_name": "shelf 1", "entity_type": "location",
         "memory_ids": ["m2"]},
    ]
    return memories, rows


@pytest.mark.asyncio
async def test_entity_merge_unifies_cluster_and_exact_dedup_fires():
    memories, rows = dup_pair()
    verdicts = [
        EntityMergeVerdict(
            pair_index=1,
            same_entity=True,
            canonical_entity_id="e-shelf",
            confidence="high",
            reason="same shelf",
        )
    ]
    pipe, reader, writer = merge_pipeline(
        memories=memories, entity_rows=rows, verdicts=verdicts
    )

    result = await pipe.dream_sync(DreamingPipelineInput(), ctx())

    assert result.status == "ok"
    merge_queries = [q for q, _p in reader.write_calls]
    assert any("MERGE (m)-[r2:MENTIONS]->(winner)" in q for q in merge_queries)
    assert any("loser.status = 'archived'" in q for q in merge_queries)
    merge_receipts = [r for r in result.actions if r.relationship == "entity_merge"]
    assert len(merge_receipts) == 1
    assert merge_receipts[0].target_entity_id == "e-shelf1"
    assert merge_receipts[0].result_entity_id == "e-shelf"
    assert writer.deleted == [("m1", "duplicate_of:m2")]


@pytest.mark.asyncio
async def test_entity_merge_verdict_reject_leaves_topology_untouched():
    memories, rows = dup_pair()
    verdicts = [
        EntityMergeVerdict(pair_index=1, same_entity=False, confidence="high", reason="different")
    ]
    pipe, reader, writer = merge_pipeline(
        memories=memories, entity_rows=rows, verdicts=verdicts
    )

    result = await pipe.dream_sync(DreamingPipelineInput(), ctx())

    assert reader.write_calls == []
    assert not any(r.relationship == "entity_merge" for r in result.actions)
    assert writer.deleted == []


@pytest.mark.asyncio
async def test_entity_merge_dry_run_emits_receipt_without_mutation():
    memories, rows = dup_pair()
    verdicts = [
        EntityMergeVerdict(
            pair_index=1, same_entity=True, canonical_entity_id="e-shelf", reason="same shelf"
        )
    ]
    pipe, reader, writer = merge_pipeline(
        memories=memories,
        entity_rows=rows,
        verdicts=verdicts,
        entity_merge_dry_run=True,
    )

    result = await pipe.dream_sync(DreamingPipelineInput(), ctx())

    merge_receipts = [r for r in result.actions if r.relationship == "entity_merge"]
    assert len(merge_receipts) == 1
    assert merge_receipts[0].dry_run is True
    assert merge_receipts[0].status == "ok"
    assert reader.write_calls == []
    assert writer.deleted == []


@pytest.mark.asyncio
async def test_entity_canonicalization_disabled_by_default():
    memories, rows = dup_pair()
    reader = MergeReader(memories, rows)
    writer = FakeWriter()
    pipe = DefaultDreamingPipeline(
        dreaming_config=DreamingConfig(lookback_days=7, min_cluster_size=2),
        text_config=TextProcessingConfig(),
        llm_client=FakeLLM(ConsolidationAction()),
        embed_client=FakeEmbed(),
        activity_collector=FakeActivityCollector(memories),
        db_reader=reader,
        db_writer=writer,
        consistency="fast",
    )
    called = False

    async def spy(_context_str):
        nonlocal called
        called = True
        return []

    pipe._call_entity_merge_llm = spy
    await pipe.dream_sync(DreamingPipelineInput(), ctx())

    assert called is False


@pytest.mark.asyncio
async def test_entity_merge_pair_cap_limits_candidates():
    memories = [
        memory("m1", content="a", content_hash="h1"),
        memory("m2", content="b", content_hash="h2"),
        memory("m3", content="c", content_hash="h3"),
        memory("m4", content="d", content_hash="h4"),
    ]
    rows = [
        {"entity_id": "e1", "entity_name": "shelf", "entity_type": "location", "memory_ids": ["m1"]},
        {"entity_id": "e2", "entity_name": "shelf 1", "entity_type": "location", "memory_ids": ["m2"]},
        {"entity_id": "e3", "entity_name": "cabinet", "entity_type": "location", "memory_ids": ["m3"]},
        {"entity_id": "e4", "entity_name": "cabinet 1", "entity_type": "location", "memory_ids": ["m4"]},
    ]
    pipe, _reader, _writer = merge_pipeline(
        memories=memories, entity_rows=rows, verdicts=[], entity_merge_max_pairs=1
    )
    seen_context: list[str] = []

    async def capture(ctx_str):
        seen_context.append(ctx_str)
        return []

    pipe._call_entity_merge_llm = capture
    await pipe._cluster_hot_memories(ctx())

    assert len(seen_context) == 1
    assert seen_context[0].count("entity_type: location") == 1


@pytest.mark.asyncio
async def test_entity_merge_cross_type_pair_reaches_arbitration_and_audits():
    # Extraction types are noisy ("cabinet-42" typed as "date"); a high
    # name-similarity pair must still reach the LLM across types.
    memories, _rows = dup_pair()
    rows = [
        {"entity_id": "e-c42", "entity_name": "cabinet-42", "entity_type": "date",
         "memory_ids": ["m1"]},
        {"entity_id": "e-pc42", "entity_name": "probe-cabinet-42", "entity_type": "other",
         "memory_ids": ["m2"]},
    ]
    verdicts = [
        EntityMergeVerdict(
            pair_index=1,
            same_entity=True,
            canonical_entity_id="e-c42",
            confidence="medium",
            reason="same cabinet",
        )
    ]
    pipe, reader, _writer = merge_pipeline(
        memories=memories,
        entity_rows=rows,
        verdicts=verdicts,
        entity_merge_dry_run=True,
    )

    result = await pipe.dream_sync(DreamingPipelineInput(), ctx())

    merge_receipts = [r for r in result.actions if r.relationship == "entity_merge"]
    assert len(merge_receipts) == 1
    assert merge_receipts[0].dry_run is True
    assert merge_receipts[0].status == "ok"
    assert "name_sim_xtype" in merge_receipts[0].reason
    assert reader.write_calls == []


@pytest.mark.asyncio
async def test_entity_merge_dry_run_reports_distinct_and_missing_verdicts():
    memories = [
        memory("m1", content="a", content_hash="h1"),
        memory("m2", content="b", content_hash="h2"),
        memory("m3", content="c", content_hash="h3"),
        memory("m4", content="d", content_hash="h4"),
    ]
    rows = [
        {"entity_id": "e1", "entity_name": "shelf", "entity_type": "location", "memory_ids": ["m1"]},
        {"entity_id": "e2", "entity_name": "shelf 1", "entity_type": "location", "memory_ids": ["m2"]},
        {"entity_id": "e3", "entity_name": "cabinet", "entity_type": "location", "memory_ids": ["m3"]},
        {"entity_id": "e4", "entity_name": "cabinet 9", "entity_type": "date", "memory_ids": ["m4"]},
    ]
    # Two candidates: (shelf,shelf1) same-type and (cabinet,cabinet9) cross-type.
    # LLM only answers pair 1 with a "distinct" verdict; pair 2 gets none.
    verdicts = [
        EntityMergeVerdict(pair_index=1, same_entity=False, confidence="high", reason="numbered")
    ]
    pipe, _reader, _writer = merge_pipeline(
        memories=memories,
        entity_rows=rows,
        verdicts=verdicts,
        entity_merge_dry_run=True,
    )

    result = await pipe.dream_sync(DreamingPipelineInput(), ctx())

    merge_receipts = [r for r in result.actions if r.relationship == "entity_merge"]
    prefixes = sorted((r.reason or "").split(":")[0] for r in merge_receipts)
    assert prefixes == ["distinct", "no_verdict"]


@pytest.mark.asyncio
async def test_entity_merge_skips_different_types_and_instances():
    from mindmemos.pipelines.dreaming.default import DefaultDreamingPipeline as _P

    assert _P._entity_name_similarity("shelf", "shelf 1") == 0.5
    assert _P._entity_name_similarity("counter top 1", "countertop 1") == 1.0
    assert _P._entity_name_similarity("observation-1", "observation-2") < 0.5
    assert _P._entity_name_similarity("cabinet-42", "probe-cabinet-42") >= 0.5


def test_entity_merge_parser_round_trip():
    parsed = entity_merge_parser(
        '{"verdicts":[{"pair_index":2,"same_entity":true,'
        '"canonical_entity_id":"e1","confidence":"high","reason":"same"}]}'
    )
    assert parsed.verdicts[0].pair_index == 2
    assert parsed.verdicts[0].canonical_entity_id == "e1"
