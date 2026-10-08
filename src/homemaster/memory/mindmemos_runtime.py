"""Application-owned embedded MindMemOS resources."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from homemaster.config import HomeMasterConfig
from homemaster.memory._mindmemos_shared import (
    # Re-exported: tests set/reset this contextvar via this module's attributes.
    _FEEDBACK_PROVENANCE_SEQ as _FEEDBACK_PROVENANCE_SEQ,
)
from homemaster.memory._mindmemos_shared import (
    _TYPED_RECORD,
    RecordedAddResult,
    _typed_entity_generation,
)
from homemaster.memory.mindmemos_feedback import MindMemOSFeedbackMixin
from homemaster.memory.mindmemos_lifecycle import MindMemOSLifecycleMixin
from homemaster.memory.mindmemos_queries import MindMemOSQueryMixin
from homemaster.memory.mindmemos_writes import MindMemOSWriteMixin

_ENTITY_MODELING_PATH = Path(__file__).with_name("mindmemos_entity_modeling.json")
_SCHEMA_EPISODE_PROMPTS_PATH = Path(__file__).with_name("schema_episode_prompts.json")
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


def load_schema_episode_prompts() -> dict[str, str]:
    """Load versioned fixed-schema prompts from package data."""

    payload = json.loads(_SCHEMA_EPISODE_PROMPTS_PATH.read_text(encoding="utf-8"))
    prompts = {
        key: value
        for key, value in payload.items()
        if key != "schema_version" and isinstance(value, str) and value.strip()
    }
    required = {"object_location", "search_observation", "task_procedure"}
    if set(prompts) != required:
        raise ValueError("schema episode prompt file must define exactly three domain prompts")
    return prompts


class EmbeddedMindMemOS(
    MindMemOSLifecycleMixin,
    MindMemOSWriteMixin,
    MindMemOSQueryMixin,
    MindMemOSFeedbackMixin,
):
    """Own the process-local resources used by MindMemOS pipelines.

    The facade API is unchanged; method groups live in focused mixins:
    lifecycle (``mindmemos_lifecycle``), writes (``mindmemos_writes``),
    queries (``mindmemos_queries``), and feedback/dreaming
    (``mindmemos_feedback``). Shared contextvars and record helpers live in
    ``_mindmemos_shared``.
    """


__all__ = [
    "EmbeddedMindMemOS",
    "RecordedAddResult",
    "build_mindmemos_add_prompts",
    "build_mindmemos_config",
]
