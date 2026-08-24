"""Application-owned embedded MindMemOS resources."""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from homemaster.config import HomeMasterConfig
from homemaster.events.third_party_logging import ThirdPartyLogCapture
from homemaster.memory.models import MEMORY_RECORD_ADAPTER, MemoryRecord
from homemaster.memory.serialization import serialize_record

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
_FEEDBACK_PROVENANCE_SEQ: ContextVar[int | None] = ContextVar(
    "homemaster_feedback_provenance_seq", default=None
)


@dataclass(frozen=True)
class RecordedAddResult:
    add_record_id: str
    result: Any


def _updated_schema_metadata(
    current: Mapping[str, Any] | None,
    replacement: Mapping[str, Any],
) -> dict[str, Any]:
    """Replace HomeMaster request metadata while preserving native schema bookkeeping."""

    merged = dict(current or {})
    request_metadata = merged.get("request_metadata")
    if not isinstance(request_metadata, Mapping):
        merged["request_metadata"] = dict(replacement)
        return merged
    request_copy = dict(request_metadata)
    record_metadata = request_copy.get("record_metadata")
    if isinstance(record_metadata, Sequence) and not isinstance(record_metadata, (str, bytes)):
        items = [dict(item) for item in record_metadata if isinstance(item, Mapping)]
        replaced = False
        for index, item in enumerate(items):
            if "record_json" in item:
                items[index] = {**item, **replacement}
                replaced = True
                break
        if not replaced:
            items.append(dict(replacement))
        request_copy["record_metadata"] = items
    else:
        request_copy.update(replacement)
    merged["request_metadata"] = request_copy
    return merged


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


def _record_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Find HomeMaster record metadata inside native request metadata shapes."""

    if not isinstance(metadata, Mapping):
        return {}
    if isinstance(metadata.get("record_json"), str):
        return dict(metadata)
    for value in metadata.values():
        if isinstance(value, Mapping):
            if found := _record_metadata(value):
                return found
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                if isinstance(item, Mapping) and (found := _record_metadata(item)):
                    return found
    return {}


def _record_from_raw_memory(raw: Any) -> MemoryRecord | None:
    metadata = getattr(raw, "metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    nested = metadata.get("request_metadata")
    if isinstance(nested, Mapping):
        record_metadata = nested.get("record_metadata")
        if isinstance(record_metadata, Sequence) and not isinstance(
            record_metadata, (str, bytes)
        ):
            metadata = next(
                (
                    item
                    for item in record_metadata
                    if isinstance(item, Mapping) and "record_json" in item
                ),
                nested,
            )
        else:
            metadata = nested
    value = metadata.get("record_json")
    if not isinstance(value, str):
        return None
    try:
        return MEMORY_RECORD_ADAPTER.validate_json(value)
    except ValueError:
        return None


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
            from mindmemos.llm.router import clear_router_cache
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
                clear_router_cache()
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

    async def add_record(
        self,
        record: MemoryRecord,
        *,
        provenance_seq: int,
        context: Any,
    ) -> dict[str, object]:
        """Persist one validated HomeMaster record and verify its raw terminal state."""

        from mindmemos.typing import TextMessage

        serialized = serialize_record(record, provenance_seq=provenance_seq)
        result = await self.add(
            [TextMessage(text=serialized.text)],
            context,
            force_generation=True,
            metadata={
                **serialized.metadata,
                "homemaster_memory_type": record.memory_type,
            },
        )
        candidate_ids: list[str] = []
        for event in result.memories:
            candidate_ids.extend(
                item
                for item in getattr(event, "related_memory_ids", [])
                if isinstance(item, str) and item
            )
            event_id = getattr(event, "memory_id", None)
            if isinstance(event_id, str) and event_id:
                candidate_ids.append(event_id)
        expected_type = "experience" if record.memory_type == "procedure" else record.memory_type
        for memory_id in dict.fromkeys(candidate_ids):
            raw = await self.get_raw(memory_id, context)
            if getattr(raw, "mem_type", None) != expected_type:
                continue
            parsed = _record_from_raw_memory(raw)
            if parsed == record:
                created_at = getattr(raw, "created_at", None)
                updated_at = getattr(raw, "update_at", None)
                return {
                    "memory_id": raw.memory_id,
                    "memory_type": record.memory_type,
                    "record": record.model_dump(mode="json"),
                    "created_at": (
                        created_at.isoformat() if hasattr(created_at, "isoformat") else None
                    ),
                    "updated_at": (
                        updated_at.isoformat() if hasattr(updated_at, "isoformat") else None
                    ),
                    "score": None,
                    "match_sources": [],
                    "verified_terminal_state": True,
                }
        raise RuntimeError("MindMemOS Add returned no verified raw memory")

    async def add_flat(
        self,
        content: str,
        memory_type: str,
        *,
        provenance_seq: int,
        evidence_kind: str,
        context: Any,
    ) -> dict[str, object]:
        """Persist exact caller-authored content without extraction or entity modeling."""

        from datetime import UTC, datetime

        from mindmemos.components.id import generate_memory_id, generate_source_id
        from mindmemos.pipelines.memory_db import suppress_recording_errors, utcnow
        from mindmemos.pipelines.utils.dto_factory import build_source_write
        from mindmemos.typing import (
            AddPipelineInput,
            AddPipelineSyncResult,
            GraphNodeRef,
            GraphRelationship,
            MemoryAddEventItem,
            MemoryDbWritePlan,
            MemoryWrite,
            SourceRef,
            TextMessage,
            VectorWrite,
        )

        if (
            self._writer is None
            or self._reader is None
            or self._recorder is None
            or self._flat_text_preprocessor is None
            or self._flat_sparse_encoder is None
            or self._qdrant is None
            or self._neo4j is None
        ):
            raise RuntimeError("embedded MindMemOS is not started")
        if memory_type not in {"fact", "procedure"}:
            raise ValueError("memory_type must be fact or procedure")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must not be empty")
        if evidence_kind not in {"user_statement", "environment_observation"}:
            raise ValueError("unsupported evidence kind")

        native_type = "experience" if memory_type == "procedure" else "fact"
        preprocessed = self._flat_text_preprocessor.preprocess_text(
            content,
            segment_id="homemaster-direct-flat-add",
            include_entities=False,
        )
        sparse = self._flat_sparse_encoder.encode_document(list(preprocessed.tokens))
        now = datetime.now(UTC)
        memory_id = generate_memory_id(
            context.project_id,
            context.request_id,
            f"{preprocessed.content_hash}:{native_type}",
        )
        source_ref = generate_source_id(
            SourceRef(
                source_type="message",
                message_id=(
                    f"homemaster-direct-flat-{context.request_id}-evidence-{provenance_seq}"
                ),
                is_parsed=True,
                content_hash=preprocessed.content_hash,
                metadata={
                    "producer": "homemaster_explicit_add",
                    "provenance_seq": provenance_seq,
                    "evidence_kind": evidence_kind,
                },
            ),
            context,
        )
        source = build_source_write(source_ref, context, now)
        add_record_id = str(uuid4())
        metadata = {
            "homemaster_add_mode": "direct_flat",
            "homemaster_memory_type": memory_type,
            "provenance_seq": provenance_seq,
            "evidence_kind": evidence_kind,
            "content_hash": preprocessed.content_hash,
            "bm25_text": preprocessed.bm25_text,
            "tokens": list(preprocessed.tokens),
            "lang": preprocessed.lang,
            "source_id": source.source_id,
            "source_type": source.source_type,
            "source_session_id": context.session_id,
            "add_record_id": add_record_id,
            "entity_count": 0,
            "entities": [],
            "extractor": "homemaster_direct_flat_v1",
            "vector_pending": True,
            "entity_enrichment_pending": True,
        }
        memory = MemoryWrite(
            memory_id=memory_id,
            account_id=context.account_id,
            project_id=context.project_id,
            api_key_uuid=context.api_key_uuid,
            user_id=context.user_id,
            app_id=context.app_id,
            session_id=context.session_id,
            agent_id=context.agent_id,
            request_id=context.request_id,
            content=content,
            mem_type=native_type,
            mem_extract_type="homemaster_direct_flat",
            mem_extract_version="homemaster_direct_flat_v1",
            metadata=metadata,
            validate_from=now,
            created_at=now,
            root_id=[memory_id],
        )
        relationship = GraphRelationship(
            source=GraphNodeRef(
                kind="Memory", project_id=context.project_id, node_id=memory_id
            ),
            target=GraphNodeRef(
                kind="Source", project_id=context.project_id, node_id=source.source_id
            ),
            rel_type="EXTRACTED_FROM",
            project_id=context.project_id,
            metadata={
                "source_type": "message",
                "producer": "homemaster_explicit_add",
            },
        )
        plan = MemoryDbWritePlan(
            memories=[memory],
            sources=[source],
            vectors=[
                VectorWrite(
                    memory_id=memory_id,
                    bm25_indices=list(sparse.indices),
                    bm25_values=list(sparse.values),
                )
            ],
            relationships=[relationship],
        )
        payload = AddPipelineInput(
            messages=[TextMessage(text=content)],
            mode="sync",
            force_generation=False,
            metadata=metadata,
        )
        await suppress_recording_errors(
            self._recorder.record_add_input(
                payload,
                ctx=context,
                request_submitted_at=utcnow(),
                add_record_id=add_record_id,
                status="processing",
            ),
            operation="homemaster.mindmemos.direct_flat_add",
        )
        try:
            write_result = await self._writer.write(context, plan, consistency="strong")
            if (
                memory_id not in write_result.memory_ids
                or bool(write_result.graph_pending)
                or bool(write_result.errors)
            ):
                raise RuntimeError(
                    "direct flat Add database write was incomplete: "
                    f"graph_pending={write_result.graph_pending}, errors={write_result.errors}"
                )
            result = AddPipelineSyncResult(
                status="ok",
                memories=[
                    MemoryAddEventItem(
                        operation="add",
                        content=content,
                        memory_id=memory_id,
                        mem_type=native_type,
                        graph_edge_count=1,
                    )
                ],
            )
            await suppress_recording_errors(
                self._recorder.mark_add_completed(context, add_record_id, result),
                operation="homemaster.mindmemos.direct_flat_add",
            )
            raw = await self.get_raw(memory_id, context)
            if not (
                raw is not None
                and getattr(raw, "status", None) == "active"
                and getattr(raw, "content", None) == content
                and getattr(raw, "mem_type", None) == native_type
                and getattr(raw, "mem_extract_type", None) == "homemaster_direct_flat"
                and (getattr(raw, "metadata", {}) or {}).get("vector_pending") is True
            ):
                raise RuntimeError("direct flat Add raw terminal state could not be verified")
            stored_points = await self._qdrant.get_memories(
                context.project_id,
                [memory_id],
                with_vectors=True,
            )
            semantic_name = self.mindmemos_config.database.qdrant.semantic_vector_name
            bm25_name = self.mindmemos_config.database.qdrant.bm25_vector_name
            stored_vectors = stored_points[0].vectors if len(stored_points) == 1 else None
            dense = stored_vectors.get(semantic_name) if stored_vectors else None
            stored_sparse = stored_vectors.get(bm25_name) if stored_vectors else None
            if (
                not isinstance(dense, list)
                or any(dense)
                or list(getattr(stored_sparse, "indices", ())) != list(sparse.indices)
                or list(getattr(stored_sparse, "values", ())) != list(sparse.values)
            ):
                raise RuntimeError("direct flat Add vector terminal state could not be verified")
            graph_rows = await self._neo4j.run_read(
                """
                MATCH (m:Memory {project_id: $project_id, memory_id: $memory_id})
                      -[:EXTRACTED_FROM]->
                      (s:Source {project_id: $project_id, source_id: $source_id})
                RETURN m.memory_id AS memory_id, s.source_id AS source_id
                """,
                project_id=context.project_id,
                memory_id=memory_id,
                source_id=source.source_id,
            )
            if graph_rows != [{"memory_id": memory_id, "source_id": source.source_id}]:
                raise RuntimeError("direct flat Add graph terminal state could not be verified")
        except Exception as exc:
            await suppress_recording_errors(
                self._recorder.mark_add_failed(context, add_record_id, str(exc)),
                operation="homemaster.mindmemos.direct_flat_add",
            )
            raise

        created_at = getattr(raw, "created_at", None)
        updated_at = getattr(raw, "update_at", None)
        return {
            "memory_id": memory_id,
            "memory_type": memory_type,
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else None,
            "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else None,
            "score": None,
            "match_sources": [],
            "verified_terminal_state": True,
        }

    async def enrich_flat_memory(
        self,
        *,
        memory_id: str,
        content: str,
        context: Any,
    ) -> dict[str, object]:
        """Complete dense and Entity enrichment for an already stored flat memory."""
        if (
            self._reader is None
            or self._writer is None
            or self._flat_embed_client is None
            or self._flat_text_preprocessor is None
            or self._flat_entity_extractor is None
            or self._flat_memory_vectorizer is None
            or self._qdrant is None
            or self._neo4j is None
        ):
            raise RuntimeError("embedded MindMemOS is not started")
        current = await self._reader.get_memory(context, memory_id)
        if current is None or getattr(current, "status", None) != "active":
            raise RuntimeError(f"stored memory is not active: {memory_id}")
        metadata = dict(getattr(current, "metadata", {}) or {})
        semantic_name = self.mindmemos_config.database.qdrant.semantic_vector_name
        if metadata.get("vector_pending"):
            response = await self._flat_embed_client.embed(task="memory.add.embed", text=content)
            vectors = list(response.embeddings)
            if not vectors or not vectors[0]:
                raise RuntimeError("enrichment embedding returned no vector")
            await self._qdrant.patch_memory(
                context.project_id,
                memory_id,
                {"metadata": metadata},
                dense_vector=list(vectors[0]),
            )
            verified = await self._qdrant.get_memories(
                context.project_id, [memory_id], with_vectors=True
            )
            dense = (
                verified[0].vectors.get(semantic_name)
                if verified and verified[0].vectors
                else None
            )
            expected_dense = list(vectors[0])
            if (
                not isinstance(dense, list)
                or len(dense) != len(expected_dense)
                or not any(dense)
                or not all(
                    math.isclose(
                        float(stored),
                        float(expected),
                        rel_tol=1e-6,
                        abs_tol=1e-7,
                    )
                    for stored, expected in zip(dense, expected_dense, strict=True)
                )
            ):
                raise RuntimeError("enrichment vector readback failed")
            metadata["vector_pending"] = False
            await self._qdrant.patch_memory(
                context.project_id,
                memory_id,
                {"metadata": metadata},
            )

        entity_ids: list[str] = []
        if metadata.get("entity_enrichment_pending"):
            from datetime import UTC, datetime

            from mindmemos.components.extractor.vanilla._entity import (
                deduplicate_entities,
                resolve_candidate_entities,
            )
            from mindmemos.components.id import generate_entity_id
            from mindmemos.components.memory_modeling.vanilla import build_mentions_edge
            from mindmemos.pipelines.utils import build_entity_write
            from mindmemos.typing import ExtractionEnvelope, MemoryDbWritePlan, TurnMessageRef

            preprocessed = self._flat_text_preprocessor.preprocess_text(
                content,
                segment_id=f"homemaster-flat-enrichment:{memory_id}",
                include_entities=True,
            )
            message = TurnMessageRef(
                text=content,
                role="user",
                raw_role="user",
                timestamp=None,
                message_index=0,
                is_extractable=True,
            )
            extracted = await self._flat_entity_extractor.extract_from_envelope(
                ExtractionEnvelope(
                    extractable_messages=[message],
                    boundary="complete",
                    chunk_index=0,
                ),
                [preprocessed],
                context,
            )
            resolved = []
            for candidate in extracted.memories:
                resolved.extend(
                    resolve_candidate_entities(
                        candidate,
                        extracted.entities,
                        preprocessed.entities,
                    )
                )
            entities = deduplicate_entities(resolved)
            entity_writes = []
            relationships = []
            for entity in entities:
                entity_id = generate_entity_id(context.project_id, entity)
                entity_write = build_entity_write(entity, entity_id, context, datetime.now(UTC))
                entity_write.metadata = {
                    **dict(entity_write.metadata or {}),
                    "search_fields": [content.strip()],
                }
                entity_writes.append(entity_write)
                relationships.append(build_mentions_edge(memory_id, entity_id, entity, context))
                entity_ids.append(entity_id)
            entity_vectors, vector_pending = await self._flat_memory_vectorizer.vectorize_entities(
                entity_writes,
                consistency="strong",
            )
            if vector_pending:
                raise RuntimeError("entity enrichment vectorization was incomplete")
            if entity_writes:
                write_result = await self._writer.write(
                    context,
                    MemoryDbWritePlan(
                        entities=entity_writes,
                        entity_vectors=entity_vectors,
                        relationships=relationships,
                    ),
                    consistency="strong",
                )
                if write_result.graph_pending or write_result.errors:
                    raise RuntimeError("entity enrichment database write was incomplete")
                for entity_id in entity_ids:
                    entity_record = await self._qdrant.get_entity(
                        context.project_id,
                        entity_id,
                        with_vectors=True,
                    )
                    stored_entity_vectors = entity_record.vectors if entity_record else None
                    entity_dense = (
                        stored_entity_vectors.get(semantic_name)
                        if stored_entity_vectors
                        else None
                    )
                    if (
                        entity_record is None
                        or not isinstance(entity_dense, list)
                        or not any(entity_dense)
                    ):
                        raise RuntimeError(f"entity enrichment readback failed: {entity_id}")
                    graph_rows = await self._neo4j.run_read(
                        """
                        MATCH (m:Memory {project_id: $project_id, memory_id: $memory_id})
                              -[:MENTIONS]->
                              (e:Entity {project_id: $project_id, entity_id: $entity_id})
                        RETURN e.entity_id AS entity_id
                        """,
                        project_id=context.project_id,
                        memory_id=memory_id,
                        entity_id=entity_id,
                    )
                    if graph_rows != [{"entity_id": entity_id}]:
                        raise RuntimeError(
                            f"entity enrichment graph readback failed: {entity_id}"
                        )
            metadata["entity_enrichment_pending"] = False
            metadata["entity_count"] = len(entity_ids)
            metadata["entities"] = [
                entity.canonical_name or entity.name for entity in entities
            ]
        await self._qdrant.patch_memory(
            context.project_id,
            memory_id,
            {"metadata": metadata},
        )
        final = await self._reader.get_memory(context, memory_id)
        final_metadata = dict(getattr(final, "metadata", {}) or {})
        if final_metadata.get("vector_pending") or final_metadata.get(
            "entity_enrichment_pending"
        ):
            raise RuntimeError("enrichment completion metadata readback failed")
        return {"memory_id": memory_id, "entity_ids": entity_ids}

    async def add_vanilla(
        self,
        messages: list[Any],
        context: Any,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Extract free-form experience memories with native Vanilla Add."""

        from mindmemos.pipelines.memory_db import suppress_recording_errors, utcnow
        from mindmemos.typing import AddPipelineInput

        if self._vanilla_add_pipeline is None or self._recorder is None:
            raise RuntimeError("embedded MindMemOS is not started")
        payload = AddPipelineInput(
            messages=messages,
            mode="sync",
            force_generation=True,
            metadata=metadata or {},
        )
        add_record_id = str(uuid4())
        await suppress_recording_errors(
            self._recorder.record_add_input(
                payload,
                ctx=context,
                request_submitted_at=utcnow(),
                add_record_id=add_record_id,
                status="processing",
            ),
            operation="homemaster.mindmemos.vanilla_add",
        )
        try:
            result = await self._vanilla_add_pipeline.add_sync(
                payload,
                context,
                add_record_id=add_record_id,
            )
            return RecordedAddResult(add_record_id=add_record_id, result=result)
        except Exception as exc:
            await suppress_recording_errors(
                self._recorder.mark_add_failed(context, add_record_id, str(exc)),
                operation="homemaster.mindmemos.vanilla_add",
            )
            raise

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

    async def update_versioned(
        self,
        *,
        memory_id: str,
        content: str,
        metadata: dict[str, Any],
        context: Any,
    ) -> Any:
        """Create one deterministic schema version without re-running Schema Add."""

        from datetime import UTC, datetime

        from mindmemos.components.extractor.schema import property_relationships
        from mindmemos.typing import (
            REL_DERIVED_FROM,
            EntityWrite,
            GraphNodeRef,
            GraphRelationship,
            MemoryDbMutationPlan,
            MemoryDbUpdateCommand,
            MemoryWrite,
        )

        if self._reader is None or self._writer is None or self._schema_write_plan_builder is None:
            raise RuntimeError("embedded MindMemOS is not started")
        current = await self._reader.get_memory(context, memory_id)
        if current is None:
            return SimpleNamespace(
                status="error",
                message=f"memory not found: {memory_id}",
                memory_id=None,
            )
        if current.status != "active":
            return SimpleNamespace(
                status="error",
                message=f"memory is not active (status={current.status}): {memory_id}",
                memory_id=None,
            )
        if not current.entity_id or not current.property_name:
            return SimpleNamespace(
                status="error",
                message="structured memory is missing entity linkage",
                memory_id=None,
            )
        entity_state = await self._reader.get_entity_with_memories(context, current.entity_id)
        if entity_state is None:
            return SimpleNamespace(
                status="error",
                message=f"entity not found: {current.entity_id}",
                memory_id=None,
            )

        record_value = metadata.get("record_json")
        try:
            record = json.loads(record_value) if isinstance(record_value, str) else None
            projected = _typed_entity_generation(record, "") if isinstance(record, dict) else None
            entity_description = projected["entities"][0]["description"]
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return SimpleNamespace(
                status="error",
                message=f"invalid structured replacement metadata: {exc}",
                memory_id=None,
            )

        now = datetime.now(UTC)
        new_memory_id = str(uuid4())
        new_memory = MemoryWrite(
            memory_id=new_memory_id,
            account_id=context.account_id,
            project_id=context.project_id,
            api_key_uuid=context.api_key_uuid,
            user_id=context.user_id,
            app_id=context.app_id,
            session_id=context.session_id,
            agent_id=context.agent_id,
            request_id=context.request_id,
            content=content,
            mem_type=current.mem_type,
            mem_extract_type=current.mem_extract_type or "schema",
            mem_extract_version="homemaster_versioned_update_v1",
            metadata=_updated_schema_metadata(current.metadata, metadata),
            validate_from=current.validate_from,
            validate_to=current.validate_to,
            created_at=now,
            parent_ids=[current.memory_id],
            root_id=current.root_id or [current.memory_id],
            property_name=current.property_name,
            entity_id=current.entity_id,
            entity_type=current.entity_type,
        )
        entity_view = entity_state.to_entity_view(project_id=context.project_id)
        entity = EntityWrite(
            entity_id=current.entity_id,
            account_id=context.account_id,
            project_id=context.project_id,
            api_key_uuid=context.api_key_uuid,
            user_id=context.user_id,
            app_id=context.app_id,
            session_id=context.session_id,
            agent_id=context.agent_id,
            request_id=context.request_id,
            entity_name=entity_view.entity_name,
            entity_type=entity_view.entity_type,
            description=str(entity_description),
            created_at=entity_view.created_at or current.created_at or now,
            update_at=now,
            metadata=dict(entity_view.metadata),
        )
        relationships = [
            *property_relationships(context.project_id, current.entity_id, new_memory),
            GraphRelationship(
                source=GraphNodeRef(
                    kind="Memory", project_id=context.project_id, node_id=new_memory_id
                ),
                target=GraphNodeRef(
                    kind="Memory", project_id=context.project_id, node_id=current.memory_id
                ),
                rel_type=REL_DERIVED_FROM,
                project_id=context.project_id,
                metadata={"reason": "direct_structured_update", "created_at": now.isoformat()},
            ),
        ]
        write_plan = await self._schema_write_plan_builder.build(
            memories=[new_memory],
            entities=[entity],
            relationships=relationships,
            project_id=context.project_id,
            entity_context_memories=[new_memory],
        )
        mutation_plan = MemoryDbMutationPlan.from_write_plan(write_plan)
        mutation_plan.memory_updates = [
            MemoryDbUpdateCommand(
                memory_id=current.memory_id,
                status="archived",
                reason="direct_structured_update",
                metadata_patch={"derived_to": new_memory_id},
            )
        ]
        result = await self._writer.apply_mutation_plan(
            context,
            mutation_plan,
            consistency="strong",
        )
        mutation = result.mutations[0] if result.mutations else None
        changed = new_memory_id in result.memory_ids and bool(mutation and mutation.changed)
        return SimpleNamespace(
            status="ok" if changed and not result.errors else "error",
            message="; ".join(result.errors) if result.errors else None,
            memory_id=new_memory_id if changed else None,
        )

    async def _execute_structured_feedback_update(
        self,
        action: Any,
        current: Any,
        context: Any,
    ) -> Any:
        """Apply a feedback replacement through the canonical Schema writer."""

        current_metadata = _record_metadata(getattr(current, "metadata", None))
        try:
            current_record = MEMORY_RECORD_ADAPTER.validate_json(current_metadata["record_json"])
            replacement_record = MEMORY_RECORD_ADAPTER.validate_python(action.replacement_record)
        except (KeyError, TypeError, ValueError):
            return action.model_copy(
                update={"result_memory_id": action.target_memory_id, "status": "error"}
            )
        current_seq = int(current_metadata.get("provenance_seq", 0))
        provenance_seq = _FEEDBACK_PROVENANCE_SEQ.get()
        if provenance_seq is None:
            provenance_seq = current_seq + 1
        current_serialized = serialize_record(current_record, provenance_seq=current_seq)
        replacement = serialize_record(replacement_record, provenance_seq=provenance_seq)
        if (
            current_record.memory_type != replacement_record.memory_type
            or current_serialized.dedupe_key != replacement.dedupe_key
            or replacement_record.source != current_record.source
            or provenance_seq <= current_seq
        ):
            return action.model_copy(
                update={"result_memory_id": action.target_memory_id, "status": "error"}
            )
        result = await self.update_versioned(
            memory_id=action.target_memory_id,
            content=replacement.text,
            metadata={
                **replacement.metadata,
                "homemaster_memory_type": replacement_record.memory_type,
            },
            context=context,
        )
        if result.status != "ok" or not isinstance(result.memory_id, str):
            return action.model_copy(
                update={"result_memory_id": action.target_memory_id, "status": "error"}
            )
        return action.model_copy(
            update={
                "result_memory_id": result.memory_id,
                "after_content": replacement.text,
                "replacement_record": replacement_record.model_dump(mode="json"),
                "status": "ok",
            }
        )

    async def list_raw_memories(
        self,
        context: Any,
        *,
        statuses: frozenset[str] = frozenset({"active", "archived"}),
        page_size: int = 50,
    ) -> list[Any]:
        """Return every matching memory from the project-scoped cursor stream."""

        if self._reader is None:
            raise RuntimeError("embedded MindMemOS is not started")
        cursor: Any | None = None
        seen_cursors: set[str] = set()
        seen_ids: set[str] = set()
        rows: list[Any] = []
        while True:
            page, next_cursor = await self._reader.list_memories(
                context,
                limit=page_size,
                cursor=cursor,
            )
            for memory in page:
                if memory.status in statuses and memory.memory_id not in seen_ids:
                    seen_ids.add(memory.memory_id)
                    rows.append(memory)
            if next_cursor is None:
                return rows
            cursor_key = repr(next_cursor)
            if cursor_key in seen_cursors:
                raise RuntimeError("MindMemOS list cursor repeated")
            seen_cursors.add(cursor_key)
            cursor = next_cursor

    async def get_history(self, memory_id: str, context: Any) -> list[Any]:
        """Return every Qdrant version connected to one memory through DERIVED_FROM."""

        if self._reader is None or self._neo4j is None:
            raise RuntimeError("embedded MindMemOS is not started")
        seed = await self._reader.get_memory(context, memory_id)
        if seed is None:
            return []
        rows = await self._neo4j.run_read(
            """
            MATCH (seed:Memory {project_id: $project_id, memory_id: $memory_id})
            OPTIONAL MATCH (seed)-[:DERIVED_FROM*0..]-(version:Memory {project_id: $project_id})
            RETURN DISTINCT coalesce(version.memory_id, seed.memory_id) AS memory_id
            """,
            project_id=context.project_id,
            memory_id=memory_id,
        )
        ids = list(
            dict.fromkeys(
                str(row.get("memory_id"))
                for row in rows
                if isinstance(row.get("memory_id"), str) and row.get("memory_id")
            )
        )
        if memory_id not in ids:
            ids.append(memory_id)
        versions = []
        for version_id in ids:
            raw = await self._reader.get_memory(context, version_id)
            if raw is not None:
                versions.append(raw)
        return sorted(
            versions,
            key=lambda item: str(item.created_at or item.update_at or ""),
            reverse=True,
        )

    async def get_raw(self, memory_id: str, context: Any) -> Any:
        """Read one raw memory by its persistent memory ID."""

        if self._reader is None:
            raise RuntimeError("embedded MindMemOS is not started")
        return await self._reader.get_memory(context, memory_id)

    async def has_memory_lineage(
        self,
        *,
        source_memory_id: str,
        target_memory_id: str,
        relationship: str,
        context: Any,
    ) -> bool:
        if self._neo4j is None:
            raise RuntimeError("embedded MindMemOS is not started")
        rows = await self._neo4j.run_read(
            """
            MATCH (source:Memory {project_id: $project_id, memory_id: $source_memory_id})
                  -[relation]->
                  (target:Memory {project_id: $project_id, memory_id: $target_memory_id})
            WHERE type(relation) = $relationship
               OR relation.relation_type = $relationship
            RETURN count(relation) AS relation_count
            """,
            project_id=context.project_id,
            source_memory_id=source_memory_id,
            target_memory_id=target_memory_id,
            relationship=relationship,
        )
        return bool(rows and int(rows[0].get("relation_count", 0)) > 0)

    async def get_add_records(
        self, add_record_ids: list[str], context: Any
    ) -> list[Any]:
        if self._reader is None:
            raise RuntimeError("embedded MindMemOS is not started")
        return await self._reader.get_add_records_by_ids(context, add_record_ids)

    async def delete(self, memory_id: str, context: Any) -> Any:
        """Archive one raw memory through the native delete pipeline."""

        from mindmemos.typing import DeletePipelineInput

        if self._delete_pipeline is None:
            raise RuntimeError("embedded MindMemOS is not started")
        return await self._delete_pipeline.delete(
            DeletePipelineInput(memory_id=memory_id),
            context,
        )

    async def feedback_explicit(
        self,
        *,
        feedback: str,
        messages: list[Any],
        recalled_memories: list[Any],
        provenance_seq: int,
        context: Any,
    ) -> Any:
        from mindmemos.typing import FeedbackPipelineInput

        if self._feedback_pipeline is None:
            raise RuntimeError("embedded MindMemOS is not started")
        token = _FEEDBACK_PROVENANCE_SEQ.set(provenance_seq)
        try:
            return await self._feedback_pipeline.feedback_sync(
                FeedbackPipelineInput(
                    feedback=feedback,
                    messages=messages,
                    recalled_memories=recalled_memories,
                    mode="sync",
                ),
                context,
            )
        finally:
            _FEEDBACK_PROVENANCE_SEQ.reset(token)

    async def feedback_implicit(self, context: Any) -> Any:
        from mindmemos.typing import FeedbackPipelineInput

        if self._feedback_pipeline is None:
            raise RuntimeError("embedded MindMemOS is not started")
        return await self._feedback_pipeline.feedback_sync(
            FeedbackPipelineInput(mode="sync"), context
        )

    async def dream(
        self,
        *,
        seed_add_record_ids: list[str],
        context: Any,
    ) -> Any:
        from mindmemos.typing import DreamingPipelineInput

        if self._dreaming_pipeline is None:
            raise RuntimeError("embedded MindMemOS is not started")
        return await self._dreaming_pipeline.dream_sync(
            DreamingPipelineInput(
                mode="sync",
                seed_add_record_ids=seed_add_record_ids,
            ),
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
            clear_router_cache()
            reset_config()


__all__ = [
    "EmbeddedMindMemOS",
    "RecordedAddResult",
    "build_mindmemos_add_prompts",
    "build_mindmemos_config",
]
