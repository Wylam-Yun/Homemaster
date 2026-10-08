"""Lifecycle ownership for the embedded MindMemOS runtime."""

from __future__ import annotations

import asyncio
import os
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homemaster.config import HomeMasterConfig
from homemaster.events.third_party_logging import ThirdPartyLogCapture


class MindMemOSLifecycleMixin:
    """Own the process-local resources used by MindMemOS pipelines."""

    def __init__(self, config: HomeMasterConfig) -> None:
        self._config = config
        self._third_party_logs: ThirdPartyLogCapture | None = None
        self._mindmemos_config: Any | None = None
        self._qdrant: Any | None = None
        self._neo4j: Any | None = None
        self._recorder: Any | None = None
        self._reader: Any | None = None
        self._writer: Any | None = None
        self._flat_text_preprocessor: Any | None = None
        self._flat_sparse_encoder: Any | None = None
        self._flat_embed_client: Any | None = None
        self._flat_entity_extractor: Any | None = None
        self._flat_memory_vectorizer: Any | None = None
        self._schema_write_plan_builder: Any | None = None
        self._add_pipeline: Any | None = None
        self._vanilla_add_pipeline: Any | None = None
        self._search_pipeline: Any | None = None
        self._get_pipeline: Any | None = None
        self._update_pipeline: Any | None = None
        self._delete_pipeline: Any | None = None
        self._feedback_pipeline: Any | None = None
        self._dreaming_pipeline: Any | None = None
        self._schema_episode_locks: dict[str, asyncio.Lock] = {}
        self._unavailable_cause: str | None = None

    @property
    def available(self) -> bool:
        return self._qdrant is not None and self._unavailable_cause is None

    @property
    def unavailable_cause(self) -> str | None:
        return self._unavailable_cause

    @property
    def qdrant_path(self) -> Path:
        return self._config.memory.mindmemos_qdrant_path

    @property
    def jieba_cache_path(self) -> Path:
        return self._config.memory.data_root / "mindmemos" / "cache" / "jieba"

    @property
    def qdrant(self) -> Any:
        if self._qdrant is None:
            raise RuntimeError("embedded MindMemOS is not started")
        return self._qdrant

    @property
    def mindmemos_config(self) -> Any:
        if self._mindmemos_config is None:
            raise RuntimeError("embedded MindMemOS is not started")
        return self._mindmemos_config

    async def start(self) -> None:
        # Deferred import: the helpers live on the facade module, which imports
        # this mixin — a module-level import would be circular.
        from homemaster.memory.mindmemos_runtime import (
            _TypedSchemaLlmClient,
            build_mindmemos_add_prompts,
            build_mindmemos_config,
            load_schema_episode_prompts,
        )

        if self._qdrant is not None:
            return

        self.qdrant_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.qdrant_path, 0o700)
        self.jieba_cache_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.jieba_cache_path, 0o700)

        import_capture = (
            self._third_party_logs.capture_dependency_imports()
            if self._third_party_logs is not None
            else nullcontext()
        )
        with import_capture:
            import jieba
            from mindmemos.components.activity import RecentActivityCollector
            from mindmemos.components.extractor.schema._schema_write_plan import (
                SchemaWritePlanBuilder,
            )
            from mindmemos.components.extractor.vanilla import VanillaMemoryExtractor
            from mindmemos.components.feedback import (
                DefaultExplicitFeedbackPlanner,
                ImplicitFeedbackActionPlanner,
                ImplicitFeedbackQueryRewriter,
                ImplicitFeedbackSignalDetector,
            )
            from mindmemos.components.text import (
                MemoryVectorizer,
                SparseVectorEncoder,
                get_text_preprocessor,
            )
            from mindmemos.config import init_config_value, reset_config
            from mindmemos.config.algo.add.vanilla import VanillaAddConfig
            from mindmemos.infra.db import (
                Neo4jStore,
                QdrantStore,
                SkillVersionRepository,
            )
            from mindmemos.llm import get_embed_client, get_llm_client
            from mindmemos.llm.registry import close_llm_clients
            from mindmemos.pipelines import create_pipeline
            from mindmemos.pipelines.dreaming.default import DefaultDreamingPipeline
            from mindmemos.pipelines.feedback.executor import FeedbackActionExecutor
            from mindmemos.pipelines.feedback.explicit import ExplicitFeedbackHandler
            from mindmemos.pipelines.feedback.implicit import (
                ImplicitFeedbackHandler,
                ImplicitFeedbackRecordCollector,
            )
            from mindmemos.pipelines.memory_db import (
                AddRecordBuffer,
                AddRecordStore,
                MemoryDbReader,
                MemoryDbWriter,
                MemoryOperationRecorder,
            )
            from qdrant_client import AsyncQdrantClient

        jieba.dt.tmp_dir = str(self.jieba_cache_path)

        try:
            mapped = build_mindmemos_config(self._config)
        except Exception as exc:
            self._unavailable_cause = f"{type(exc).__name__}: {exc}"
            return
        init_config_value(mapped)
        client = AsyncQdrantClient(path=str(self.qdrant_path))
        store = QdrantStore(
            mapped.database.qdrant,
            client=client,
        )
        neo4j = Neo4jStore(mapped.database.neo4j)
        try:
            await store.ensure_schema()
            await neo4j.ensure_schema()
            skill = SkillVersionRepository(mapped.database.qdrant, engine=store.engine)
            await skill.ensure_schema()
            clients = SimpleNamespace(qdrant=store, neo4j=neo4j, skill=skill)
            llm_client = get_llm_client()
            embed_client = get_embed_client()
            reader = MemoryDbReader(clients=clients)
            writer = MemoryDbWriter(clients=clients, embed_client=embed_client)
            text_config = mapped.algo_config.text_processing

            async def embed_texts(task: str, texts: list[str]) -> list[list[float]]:
                response = await embed_client.embed(task=task, text=texts)
                return response.embeddings

            text_preprocessor = get_text_preprocessor(text_config)
            sparse_encoder = SparseVectorEncoder(text_config)
            flat_entity_extractor = VanillaMemoryExtractor(
                llm_client=llm_client,
                enable_entities=True,
            )
            flat_memory_vectorizer = MemoryVectorizer(
                sparse_encoder=sparse_encoder,
                embed_client=embed_client,
                text_preprocessor=text_preprocessor,
            )
            schema_write_plan_builder = SchemaWritePlanBuilder(
                text_preprocessor=text_preprocessor,
                sparse_encoder=sparse_encoder,
                embed_texts=embed_texts,
            )
            add_record_store = AddRecordStore(clients=clients)
            recorder = MemoryOperationRecorder(
                add_record_store=add_record_store,
                clients=clients,
            )
            add_pipeline = create_pipeline(
                type="add",
                name="schema_add",
                db_reader=reader,
                db_writer=writer,
                recorder=recorder,
                add_buffer=AddRecordBuffer(clients=clients),
                llm_client=_TypedSchemaLlmClient(llm_client),
                embed_client=embed_client,
                prompt_set=build_mindmemos_add_prompts(mapped.algo_config.common.prompt_language),
                fixed_episode_prompts=load_schema_episode_prompts(),
                fixed_episode_validator=(
                    __import__(
                        "homemaster.memory.schema_episode_validation",
                        fromlist=["validate_schema_episode_candidates"],
                    ).validate_schema_episode_candidates
                ),
            )
            vanilla_add_pipeline = create_pipeline(
                type="add",
                name="vanilla_add",
                db_reader=reader,
                db_writer=writer,
                recorder=recorder,
                llm_client=llm_client,
                embed_client=embed_client,
                memory_extractor=VanillaMemoryExtractor(
                    llm_client=llm_client,
                    enable_entities=True,
                ),
                vanilla_add_config=VanillaAddConfig(enable_entities=True),
            )
            search_pipeline = create_pipeline(
                type="search",
                name="search_pipeline",
                db_reader=reader,
                db_writer=writer,
                recorder=recorder,
            )
            get_pipeline = create_pipeline(
                type="get",
                name="default_get",
                db_reader=reader,
                db_writer=writer,
            )
            update_pipeline = create_pipeline(
                type="update",
                name="default_update",
                db_reader=reader,
                db_writer=writer,
            )
            delete_pipeline = create_pipeline(
                type="delete",
                name="default_delete",
                db_reader=reader,
                db_writer=writer,
            )
            feedback_executor = FeedbackActionExecutor(
                db_reader=reader,
                db_writer=writer,
                embed_client=embed_client,
                structured_update_handler=self._execute_structured_feedback_update,
            )
            activity_collector = RecentActivityCollector(store)
            feedback_pipeline = create_pipeline(
                type="feedback",
                name="default_feedback",
                explicit_handler=ExplicitFeedbackHandler(
                    planner=DefaultExplicitFeedbackPlanner(llm_client=llm_client),
                    executor=feedback_executor,
                    search_pipeline=search_pipeline,
                ),
                implicit_handler=ImplicitFeedbackHandler(
                    collector=ImplicitFeedbackRecordCollector(
                        memory_reader=reader,
                        memory_writer=writer,
                        activity_collector=activity_collector,
                        clients=clients,
                        query_rewriter=ImplicitFeedbackQueryRewriter(
                            llm_client=llm_client
                        ),
                        search_pipeline=search_pipeline,
                    ),
                    signal_detector=ImplicitFeedbackSignalDetector(
                        llm_client=llm_client
                    ),
                    action_planner=ImplicitFeedbackActionPlanner(
                        llm_client=llm_client
                    ),
                    executor=feedback_executor,
                ),
            )
            dreaming_pipeline = DefaultDreamingPipeline(
                llm_client=llm_client,
                embed_client=embed_client,
                activity_collector=activity_collector,
                db_reader=reader,
                db_writer=writer,
            )
        except Exception as exc:
            try:
                await neo4j.close()
            finally:
                await store.close()
                await close_llm_clients()
                reset_config()
            self._unavailable_cause = f"{type(exc).__name__}: {exc}"
            return
        self._mindmemos_config = mapped
        self._qdrant = store
        self._neo4j = neo4j
        self._recorder = recorder
        self._reader = reader
        self._writer = writer
        self._flat_text_preprocessor = text_preprocessor
        self._flat_sparse_encoder = sparse_encoder
        self._flat_embed_client = embed_client
        self._flat_entity_extractor = flat_entity_extractor
        self._flat_memory_vectorizer = flat_memory_vectorizer
        self._schema_write_plan_builder = schema_write_plan_builder
        self._add_pipeline = add_pipeline
        self._vanilla_add_pipeline = vanilla_add_pipeline
        self._search_pipeline = search_pipeline
        self._get_pipeline = get_pipeline
        self._update_pipeline = update_pipeline
        self._delete_pipeline = delete_pipeline
        self._feedback_pipeline = feedback_pipeline
        self._dreaming_pipeline = dreaming_pipeline
        self._unavailable_cause = None

    async def close(self) -> None:
        from mindmemos.config import reset_config
        from mindmemos.llm.registry import close_llm_clients

        store = self._qdrant
        neo4j = self._neo4j
        self._mindmemos_config = None
        self._qdrant = None
        self._neo4j = None
        self._recorder = None
        self._reader = None
        self._writer = None
        self._flat_text_preprocessor = None
        self._flat_sparse_encoder = None
        self._flat_embed_client = None
        self._flat_entity_extractor = None
        self._flat_memory_vectorizer = None
        self._schema_write_plan_builder = None
        self._add_pipeline = None
        self._vanilla_add_pipeline = None
        self._search_pipeline = None
        self._get_pipeline = None
        self._update_pipeline = None
        self._delete_pipeline = None
        self._feedback_pipeline = None
        self._dreaming_pipeline = None
        try:
            if neo4j is not None:
                await neo4j.close()
        finally:
            if store is not None:
                await store.close()
            await close_llm_clients()
            reset_config()
