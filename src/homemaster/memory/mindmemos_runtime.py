"""Application-owned embedded MindMemOS resources."""

from __future__ import annotations

import json
import os
import re
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from homemaster.config import HomeMasterConfig

_ENTITY_MODELING_PATH = Path(__file__).with_name("mindmemos_entity_modeling.json")
_HOMEMASTER_ENTITY_GENERATION_PROMPT = """
你负责把一条已经过 HomeMaster 校验的结构化记忆写入 MindMemOS schema。

允许的实体 schema：
{entity_schema}

输入时间：{dialogue_timestamp}
输入内容：{chat_chunk}

只返回一个 JSON 对象，顶层必须且只能包含 `entities` 和 `edges`：
{
  "entities": [
    {
      "name": "实体名",
      "entity_type": "fact 或 task_experience",
      "description": "简短描述",
      "properties": [
        {
          "property_name": "fact_value 或 task_experience",
          "value": "完整且自包含的输入事实或流程",
          "time": "YYYY-MM-DD"
        }
      ]
    }
  ],
  "edges": []
}

规则：
1. 必须输出至少一个非 episodes 实体，禁止输出 episodes。
2. 输入以“流程”开头时，entity_type 和 property_name 都使用 task_experience；
   否则分别使用 fact 和 fact_value。
3. fact 的 name 使用“<subject name>::<predicate>”；task_experience 的 name 使用准确流程名。
4. value 必须忠实保留输入的所有细节，不得补充输入中没有的地点、步骤、值或凭据。
5. time 使用输入时间的日期部分。edges 没有明确关系时返回空数组。
6. 不要输出 message_mapping、解释、Markdown 或代码围栏。
""".strip()

_TYPED_RECORD: ContextVar[dict[str, Any] | None] = ContextVar(
    "homemaster_mindmemos_typed_record",
    default=None,
)


def _typed_entity_generation(record: dict[str, Any], prompt: str) -> dict[str, Any]:
    """Project one validated HomeMaster record into the native schema deterministically."""

    date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", prompt)
    memory_date = date_match.group(1) if date_match else "1970-01-01"
    memory_type = record.get("memory_type")
    if memory_type == "fact":
        subject = record.get("subject")
        predicate = record.get("predicate")
        if not isinstance(subject, dict) or not isinstance(subject.get("name"), str):
            raise ValueError("typed fact is missing subject.name")
        if not isinstance(predicate, str) or not predicate:
            raise ValueError("typed fact is missing predicate")
        subject_name = subject["name"]
        value_json = json.dumps(
            record.get("value"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        description = f"{subject_name} 的 {predicate} 是 {value_json}"
        return {
            "entities": [
                {
                    "name": f"{subject_name}::{predicate}",
                    "entity_type": "fact",
                    "description": description,
                    "properties": [
                        {
                            "property_name": "fact_value",
                            "value": description,
                            "time": memory_date,
                        }
                    ],
                }
            ],
            "edges": [],
        }
    if memory_type == "procedure":
        name = record.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("typed procedure is missing name")
        value = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {
            "entities": [
                {
                    "name": name,
                    "entity_type": "task_experience",
                    "description": value,
                    "properties": [
                        {
                            "property_name": "task_experience",
                            "value": value,
                            "time": memory_date,
                        }
                    ],
                }
            ],
            "edges": [],
        }
    raise ValueError("typed record memory_type must be fact or procedure")


def _typed_record_from_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not metadata or metadata.get("homemaster_memory_type") not in {"fact", "procedure"}:
        return None
    value = metadata.get("record_json")
    if not isinstance(value, str):
        return None
    try:
        record = json.loads(value)
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


class _TypedSchemaLlmClient:
    """Keep native LLM stages while making typed entity output authoritative."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    async def chat(
        self,
        task: str,
        messages: list[dict[str, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        response = await self._delegate.chat(task, messages, *args, **kwargs)
        record = _TYPED_RECORD.get()
        if task != "memory.add.entity_generation" or record is None:
            return response
        prompt = "\n".join(
            str(message.get("content", "")) for message in messages if isinstance(message, dict)
        )
        parsed = _typed_entity_generation(record, prompt)
        return response.model_copy(
            update={
                "content": json.dumps(parsed, ensure_ascii=False, separators=(",", ":")),
                "parsed": parsed,
            }
        )


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
    neo4j = config.memory.neo4j
    neo4j_mapping: dict[str, Any] = {
        "uri": neo4j.uri,
        "username": neo4j.username,
        "database": neo4j.database,
    }
    password = neo4j.password.get_secret_value()
    if password:
        neo4j_mapping["password"] = password
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
                        "max_tokens": chat.max_output_tokens or 8192,
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
            "database": {
                "qdrant": {"vector_size": dimensions},
                "neo4j": neo4j_mapping,
            },
            "algo_config": {
                "add": {
                    "schema": {
                        "entity_modeling_path": str(_ENTITY_MODELING_PATH),
                        "extraction": {
                            "enable_schema_selection": False,
                            "episode_search_fields_augment": False,
                        },
                        "merge": {"enable_entity_merge_decision": False},
                    }
                }
            },
            "kafka": {"enabled": False},
        },
    )
    validate_config(mapped)
    return mapped


def build_mindmemos_add_prompts(language: str | None = None) -> Any:
    """Use MindMemOS prompts with a compact HomeMaster schema extractor contract."""

    from mindmemos.prompts import get_add_prompts

    return replace(
        get_add_prompts(language),
        entity_generation=_HOMEMASTER_ENTITY_GENERATION_PROMPT,
    )


class EmbeddedMindMemOS:
    """Own the process-local resources used by MindMemOS pipelines."""

    def __init__(self, config: HomeMasterConfig) -> None:
        self._config = config
        self._mindmemos_config: Any | None = None
        self._qdrant: Any | None = None
        self._neo4j: Any | None = None
        self._recorder: Any | None = None
        self._reader: Any | None = None
        self._add_pipeline: Any | None = None
        self._search_pipeline: Any | None = None
        self._get_pipeline: Any | None = None
        self._update_pipeline: Any | None = None
        self._delete_pipeline: Any | None = None
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
                llm_client=_TypedSchemaLlmClient(get_llm_client()),
                embed_client=embed_client,
                prompt_set=build_mindmemos_add_prompts(mapped.algo_config.common.prompt_language),
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
        except Exception as exc:
            try:
                await neo4j.close()
            finally:
                await store.close()
                clear_router_cache()
                reset_config()
            self._unavailable_cause = f"{type(exc).__name__}: {exc}"
            return
        self._mindmemos_config = mapped
        self._qdrant = store
        self._neo4j = neo4j
        self._recorder = recorder
        self._reader = reader
        self._add_pipeline = add_pipeline
        self._search_pipeline = search_pipeline
        self._get_pipeline = get_pipeline
        self._update_pipeline = update_pipeline
        self._delete_pipeline = delete_pipeline
        self._unavailable_cause = None

    async def add(
        self,
        messages: list[Any],
        context: Any,
        *,
        force_generation: bool = True,
        metadata: dict[str, Any] | None = None,
        event_timestamp_ms: int | None = None,
    ) -> Any:
        """Synchronously extract and persist memories from MindMemOS messages."""

        from mindmemos.pipelines.memory_db import suppress_recording_errors, utcnow
        from mindmemos.typing import AddPipelineInput

        if self._add_pipeline is None or self._recorder is None:
            raise RuntimeError("embedded MindMemOS is not started")
        payload_kwargs: dict[str, Any] = {
            "messages": messages,
            "mode": "sync",
            "force_generation": force_generation,
            "metadata": metadata or {},
        }
        if event_timestamp_ms is not None:
            payload_kwargs["event_timestamp_ms"] = event_timestamp_ms
        payload = AddPipelineInput(**payload_kwargs)
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
        typed_record = _typed_record_from_metadata(metadata)
        token = _TYPED_RECORD.set(typed_record)
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
        finally:
            _TYPED_RECORD.reset(token)

    async def search(
        self,
        query: str,
        context: Any,
        *,
        top_k: int = 10,
        search_pipeline: str = "schema",
        filters: dict[str, Any] | None = None,
        rerank: bool = False,
        score_threshold: float | None = None,
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
            filters=filters,
            rerank=rerank,
            score_threshold=score_threshold,
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

    async def get(
        self,
        context: Any,
        *,
        filters: dict[str, Any] | None = None,
        top_k: int | None = None,
    ) -> Any:
        """List active raw memories through the native get pipeline."""

        from mindmemos.typing import GetPipelineInput

        if self._get_pipeline is None:
            raise RuntimeError("embedded MindMemOS is not started")
        return await self._get_pipeline.get(
            GetPipelineInput(filters=filters, top_k=top_k),
            context,
        )

    async def update(self, memory_id: str, content: str, context: Any) -> Any:
        """Replace the content of one active raw memory."""

        from mindmemos.typing import UpdatePipelineInput

        if self._update_pipeline is None:
            raise RuntimeError("embedded MindMemOS is not started")
        return await self._update_pipeline.update(
            UpdatePipelineInput(memory_id=memory_id, content=content),
            context,
        )

    async def get_raw(self, memory_id: str, context: Any) -> Any:
        """Read one raw memory by its persistent memory ID."""

        if self._reader is None:
            raise RuntimeError("embedded MindMemOS is not started")
        return await self._reader.get_memory(context, memory_id)

    async def delete(self, memory_id: str, context: Any) -> Any:
        """Archive one raw memory through the native delete pipeline."""

        from mindmemos.typing import DeletePipelineInput

        if self._delete_pipeline is None:
            raise RuntimeError("embedded MindMemOS is not started")
        return await self._delete_pipeline.delete(
            DeletePipelineInput(memory_id=memory_id),
            context,
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
        self._reader = None
        self._add_pipeline = None
        self._search_pipeline = None
        self._get_pipeline = None
        self._update_pipeline = None
        self._delete_pipeline = None
        try:
            if neo4j is not None:
                await neo4j.close()
        finally:
            if store is not None:
                await store.close()
            clear_router_cache()
            reset_config()


__all__ = [
    "EmbeddedMindMemOS",
    "build_mindmemos_add_prompts",
    "build_mindmemos_config",
]
