"""Application-owned embedded MindMemOS resources."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from homemaster.config import HomeMasterConfig


def _litellm_model(api_format: str, model: str) -> str:
    prefix = f"{api_format}/"
    return model if model.startswith(prefix) else f"{prefix}{model}"


def _provider_api_key(provider: Any) -> str:
    if not provider.api_keys:
        raise ValueError(f"provider {provider.name!r} has no API key")
    return provider.api_keys[0]


def build_mindmemos_config(config: HomeMasterConfig) -> Any:
    """Translate HomeMaster providers into MindMemOS native configuration."""

    from mindmemos.config import MemoryConfig, build, validate_config

    chat = config.get_provider(
        config.runtime_defaults.default_provider_name,
        kind="chat",
    )
    embedding = config.get_provider(
        config.runtime_defaults.default_embedding_provider_name,
        kind="embedding",
    )
    timeout = int(config.provider_client.timeout_s)
    retries = config.provider_client.max_retries
    dimensions = config.memory.embedding_dimensions
    mapped = build(
        MemoryConfig,
        {
            "telemetry": {"enabled": False},
            "chat_model_router": {
                "endpoints": [
                    {
                        "model": _litellm_model(chat.api_format, chat.model),
                        "api_key": _provider_api_key(chat),
                        "api_base": chat.base_url,
                        "timeout": timeout,
                        "num_retries": retries,
                        "max_tokens": chat.max_output_tokens,
                    }
                ]
            },
            "embed_model_router": {
                "endpoints": [
                    {
                        "model": _litellm_model(embedding.api_format, embedding.model),
                        "api_key": _provider_api_key(embedding),
                        "api_base": embedding.base_url,
                        "timeout": timeout,
                        "num_retries": retries,
                        "dimensions": dimensions,
                    }
                ],
                "dimensions_supported_models": [embedding.model],
            },
            "database": {"qdrant": {"vector_size": dimensions}},
            "kafka": {"enabled": False},
        },
    )
    validate_config(mapped)
    return mapped


class EmbeddedMindMemOS:
    """Own the process-local resources used by MindMemOS pipelines."""

    def __init__(self, config: HomeMasterConfig) -> None:
        self._config = config
        self._mindmemos_config: Any | None = None
        self._qdrant: Any | None = None
        self._neo4j: Any | None = None
        self._recorder: Any | None = None
        self._add_pipeline: Any | None = None
        self._search_pipeline: Any | None = None

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
        if self._qdrant is not None:
            return

        self.qdrant_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.qdrant_path, 0o700)
        self.jieba_cache_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.jieba_cache_path, 0o700)

        import jieba

        jieba.dt.tmp_dir = str(self.jieba_cache_path)

        from mindmemos.config import init_config_value, reset_config
        from mindmemos.infra.db import Neo4jStore, QdrantStore, SkillVersionRepository
        from mindmemos.llm import get_embed_client, get_llm_client
        from mindmemos.llm.router import clear_router_cache
        from mindmemos.pipelines import create_pipeline
        from mindmemos.pipelines.memory_db import (
            AddRecordBuffer,
            AddRecordStore,
            MemoryDbReader,
            MemoryDbWriter,
            MemoryOperationRecorder,
        )
        from qdrant_client import AsyncQdrantClient

        mapped = build_mindmemos_config(self._config)
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
            embed_client = get_embed_client()
            reader = MemoryDbReader(clients=clients)
            writer = MemoryDbWriter(clients=clients, embed_client=embed_client)
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
                llm_client=get_llm_client(),
                embed_client=embed_client,
            )
            search_pipeline = create_pipeline(
                type="search",
                name="search_pipeline",
                db_reader=reader,
                db_writer=writer,
                recorder=recorder,
            )
        except BaseException:
            try:
                await neo4j.close()
            finally:
                await store.close()
                clear_router_cache()
                reset_config()
            raise
        self._mindmemos_config = mapped
        self._qdrant = store
        self._neo4j = neo4j
        self._recorder = recorder
        self._add_pipeline = add_pipeline
        self._search_pipeline = search_pipeline

    async def add(
        self,
        messages: list[Any],
        context: Any,
        *,
        force_generation: bool = True,
    ) -> Any:
        """Synchronously extract and persist memories from MindMemOS messages."""

        from mindmemos.pipelines.memory_db import suppress_recording_errors, utcnow
        from mindmemos.typing import AddPipelineInput

        if self._add_pipeline is None or self._recorder is None:
            raise RuntimeError("embedded MindMemOS is not started")
        payload = AddPipelineInput(
            messages=messages,
            mode="sync",
            force_generation=force_generation,
        )
        add_record_id = str(uuid4())
        submitted_at = utcnow()
        await suppress_recording_errors(
            self._recorder.record_add_input(
                payload,
                ctx=context,
                request_submitted_at=submitted_at,
                add_record_id=add_record_id,
                status="processing",
            ),
            operation="homemaster.mindmemos.add",
        )
        try:
            return await self._add_pipeline.add_sync(
                payload,
                context,
                add_record_id=add_record_id,
            )
        except Exception as exc:
            await suppress_recording_errors(
                self._recorder.mark_add_failed(context, add_record_id, str(exc)),
                operation="homemaster.mindmemos.add",
            )
            raise

    async def search(
        self,
        query: str,
        context: Any,
        *,
        top_k: int = 10,
        search_pipeline: str = "schema",
    ) -> Any:
        """Search memories and persist the search operation record."""

        from mindmemos.pipelines.memory_db import suppress_recording_errors, utcnow
        from mindmemos.typing import SearchPipelineInput

        if self._search_pipeline is None or self._recorder is None:
            raise RuntimeError("embedded MindMemOS is not started")
        payload = SearchPipelineInput(
            query=query,
            top_k=top_k,
            search_pipeline=search_pipeline,
        )
        submitted_at = utcnow()
        result = None
        try:
            result = await self._search_pipeline.search(payload, context)
            return result
        finally:
            await suppress_recording_errors(
                self._recorder.record_search(
                    payload,
                    result,
                    ctx=context,
                    request_submitted_at=submitted_at,
                    task_completed_at=utcnow(),
                ),
                operation="homemaster.mindmemos.search",
            )

    async def close(self) -> None:
        from mindmemos.config import reset_config
        from mindmemos.llm.router import clear_router_cache

        store = self._qdrant
        neo4j = self._neo4j
        self._mindmemos_config = None
        self._qdrant = None
        self._neo4j = None
        self._recorder = None
        self._add_pipeline = None
        self._search_pipeline = None
        try:
            if neo4j is not None:
                await neo4j.close()
        finally:
            if store is not None:
                await store.close()
            clear_router_cache()
            reset_config()


__all__ = ["EmbeddedMindMemOS", "build_mindmemos_config"]
