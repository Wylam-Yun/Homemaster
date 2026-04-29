# Stage 03 Memory RAG - stage07_stage07-fetch_cup_retry_memory_rag

Status: PASS

Provider: {'provider_name': 'Mimo', 'model': 'mimo-v2-pro', 'protocol': 'anthropic', 'elapsed_ms': 15742.575209000279, 'attempts': [{'key_index': 1, 'status_code': 200, 'elapsed_ms': 15742.575209000279}]}

Embedding Provider: {'provider_name': 'MemoryEmbedding', 'model': 'BAAI/bge-m3', 'endpoint': 'https://api.siliconflow.cn/v1/embeddings'}

## Expected Conditions

```json
{
  "case_name": "stage07_stage07-fetch_cup_retry_memory_rag"
}
```

## Mimo Query Prompt

```text
你是 HomeMaster V1.2 的 memory RAG query 构造组件。

目标：根据 TaskCard 生成一个 MemoryRetrievalQuery JSON。
你只负责构造检索 query，不读取 memory，不返回 memory hit，不选择目标地点。

必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。
不要编造 memory_id、anchor_id、viewpoint_id 或真实位置。

MemoryRetrievalQuery schema:
{
  "query_text": "非空字符串；包含目标物、别名、位置提示和稳定英文别名",
  "target_category": "字符串或 null",
  "target_aliases": ["目标物别名；可来自 TaskCard 或常识别名"],
  "location_terms": ["位置词；只来自 TaskCard 明说的位置或常识位置别名"],
  "source_filter": ["object_memory"],
  "top_k": 5,
  "excluded_memory_ids": ["只能来自 runtime negative evidence"],
  "excluded_location_keys": ["只能来自 runtime negative evidence"],
  "reason": "字符串或 null"
}

边界:
- source_filter 必须是 ["object_memory"]。
- top_k 使用 5，除非任务明显需要更多候选；不要超过 10。
- query_text 由你进行语义构造；程序不会替你补写语义别名。
- excluded_memory_ids / excluded_location_keys 只能复制 runtime negative evidence 中已有值。
- 不要编造 memory_id。

TaskCard:
{
  "task_type": "fetch_object",
  "target": "水杯",
  "delivery_target": "user",
  "location_hint": "厨房",
  "success_criteria": [
    "水杯被成功交付给用户"
  ],
  "needs_clarification": false,
  "clarification_question": null,
  "confidence": 0.9
}

Runtime negative evidence:
{}

只输出 JSON object:

```

## Mimo Raw Response

```text
{
  "query_text": "水杯 杯子 茶杯 water cup cup 厨房 kitchen",
  "target_category": null,
  "target_aliases": ["杯子", "茶杯", "water cup", "cup"],
  "location_terms": ["厨房", "kitchen"],
  "source_filter": ["object_memory"],
  "top_k": 5,
  "excluded_memory_ids": [],
  "excluded_location_keys": [],
  "reason": "Fetch water cup from kitchen for user delivery."
}
```

## Query Attempts

```json
[
  {
    "attempt": 1,
    "prompt": "你是 HomeMaster V1.2 的 memory RAG query 构造组件。\n\n目标：根据 TaskCard 生成一个 MemoryRetrievalQuery JSON。\n你只负责构造检索 query，不读取 memory，不返回 memory hit，不选择目标地点。\n\n必须只输出一个 JSON object。\n不要输出 Markdown。\n不要输出解释。\n不要输出代码块。\n不要输出思考过程。\n不要编造 memory_id、anchor_id、viewpoint_id 或真实位置。\n\nMemoryRetrievalQuery schema:\n{\n  \"query_text\": \"非空字符串；包含目标物、别名、位置提示和稳定英文别名\",\n  \"target_category\": \"字符串或 null\",\n  \"target_aliases\": [\"目标物别名；可来自 TaskCard 或常识别名\"],\n  \"location_terms\": [\"位置词；只来自 TaskCard 明说的位置或常识位置别名\"],\n  \"source_filter\": [\"object_memory\"],\n  \"top_k\": 5,\n  \"excluded_memory_ids\": [\"只能来自 runtime negative evidence\"],\n  \"excluded_location_keys\": [\"只能来自 runtime negative evidence\"],\n  \"reason\": \"字符串或 null\"\n}\n\n边界:\n- source_filter 必须是 [\"object_memory\"]。\n- top_k 使用 5，除非任务明显需要更多候选；不要超过 10。\n- query_text 由你进行语义构造；程序不会替你补写语义别名。\n- excluded_memory_ids / excluded_location_keys 只能复制 runtime negative evidence 中已有值。\n- 不要编造 memory_id。\n\nTaskCard:\n{\n  \"task_type\": \"fetch_object\",\n  \"target\": \"水杯\",\n  \"delivery_target\": \"user\",\n  \"location_hint\": \"厨房\",\n  \"success_criteria\": [\n    \"水杯被成功交付给用户\"\n  ],\n  \"needs_clarification\": false,\n  \"clarification_question\": null,\n  \"confidence\": 0.9\n}\n\nRuntime negative evidence:\n{}\n\n只输出 JSON object:\n",
    "max_tokens": 4096,
    "passed": true,
    "raw_response": "{\n  \"query_text\": \"水杯 杯子 茶杯 water cup cup 厨房 kitchen\",\n  \"target_category\": null,\n  \"target_aliases\": [\"杯子\", \"茶杯\", \"water cup\", \"cup\"],\n  \"location_terms\": [\"厨房\", \"kitchen\"],\n  \"source_filter\": [\"object_memory\"],\n  \"top_k\": 5,\n  \"excluded_memory_ids\": [],\n  \"excluded_location_keys\": [],\n  \"reason\": \"Fetch water cup from kitchen for user delivery.\"\n}",
    "retrieval_query": {
      "query_text": "水杯 杯子 茶杯 water cup cup 厨房 kitchen",
      "target_category": null,
      "target_aliases": [
        "杯子",
        "茶杯",
        "water cup",
        "cup"
      ],
      "location_terms": [
        "厨房",
        "kitchen"
      ],
      "source_filter": [
        "object_memory"
      ],
      "top_k": 5,
      "excluded_memory_ids": [],
      "excluded_location_keys": [],
      "reason": "Fetch water cup from kitchen for user delivery."
    },
    "query_provider": {
      "provider_name": "Mimo",
      "model": "mimo-v2-pro",
      "protocol": "anthropic",
      "elapsed_ms": 15742.575209000279,
      "attempts": [
        {
          "key_index": 1,
          "status_code": 200,
          "elapsed_ms": 15742.575209000279
        }
      ]
    }
  }
]
```

## Parsed MemoryRetrievalQuery

```json
{
  "query_text": "水杯 杯子 茶杯 water cup cup 厨房 kitchen",
  "target_category": null,
  "target_aliases": [
    "杯子",
    "茶杯",
    "water cup",
    "cup"
  ],
  "location_terms": [
    "厨房",
    "kitchen"
  ],
  "source_filter": [
    "object_memory"
  ],
  "top_k": 5,
  "excluded_memory_ids": [],
  "excluded_location_keys": [],
  "reason": "Fetch water cup from kitchen for user delivery."
}
```

## Memory Documents

```json
[
  {
    "document_id": "object_memory:mem-cup-1",
    "text": "物体记忆。目标类别: cup。目标类别别名: 水杯、杯子、cup。别名: 水杯、杯子。历史位置: 厨房餐桌。房间: kitchen。房间别名: 厨房、kitchen。锚点类型: table。锚点别名: 桌子、餐桌、table。可观察视角: kitchen_table_viewpoint。置信度: high。记忆状态: confirmed。最近确认时间: 2026-04-19T11:00:00Z。",
    "metadata": {
      "source_type": "object_memory",
      "memory_id": "mem-cup-1",
      "object_category": "cup",
      "aliases": [
        "水杯",
        "杯子"
      ],
      "room_id": "kitchen",
      "anchor_id": "anchor_kitchen_table_1",
      "anchor_type": "table",
      "display_text": "厨房餐桌",
      "viewpoint_id": "kitchen_table_viewpoint",
      "confidence_level": "high",
      "belief_state": "confirmed",
      "last_confirmed_at": "2026-04-19T11:00:00Z",
      "document_text_hash": "4c3fb877f13a22c5a2e4924487333c7a484154120633c4fe536d668a5043e35b"
    },
    "executable": true,
    "invalid_reason": null
  },
  {
    "document_id": "object_memory:mem-cup-2",
    "text": "物体记忆。目标类别: cup。目标类别别名: 水杯、杯子、cup。别名: 水杯。历史位置: 储物间搁架。房间: pantry。房间别名: 储物间、pantry。锚点类型: shelf。锚点别名: 搁架、架子、shelf。可观察视角: pantry_shelf_viewpoint。置信度: medium。记忆状态: confirmed。最近确认时间: 2026-04-16T11:00:00Z。",
    "metadata": {
      "source_type": "object_memory",
      "memory_id": "mem-cup-2",
      "object_category": "cup",
      "aliases": [
        "水杯"
      ],
      "room_id": "pantry",
      "anchor_id": "anchor_pantry_shelf_1",
      "anchor_type": "shelf",
      "display_text": "储物间搁架",
      "viewpoint_id": "pantry_shelf_viewpoint",
      "confidence_level": "medium",
      "belief_state": "confirmed",
      "last_confirmed_at": "2026-04-16T11:00:00Z",
      "document_text_hash": "0ce6d751065a83d42507903b864669875ac1077ccf68007227c057c650b11a6d"
    },
    "executable": true,
    "invalid_reason": null
  }
]
```

## Tokenized Query

```json
[
  "水杯",
  "杯子",
  "茶杯",
  "water",
  "cup",
  "cup",
  "厨房",
  "kitchen"
]
```

## BM25 Hits

```json
[
  {
    "document_id": "object_memory:mem-cup-1",
    "score": 1.151654839515686,
    "rank": 1
  },
  {
    "document_id": "object_memory:mem-cup-2",
    "score": 0.38707324862480164,
    "rank": 2
  }
]
```

## BGE-M3 Dense Hits

```json
[
  {
    "document_id": "object_memory:mem-cup-1",
    "score": 0.0,
    "rank": 1
  },
  {
    "document_id": "object_memory:mem-cup-2",
    "score": 0.0,
    "rank": 2
  }
]
```

## Fused MemoryRetrievalResult

```json
{
  "hits": [
    {
      "document_id": "object_memory:mem-cup-1",
      "source_type": "object_memory",
      "memory_id": "mem-cup-1",
      "object_category": "cup",
      "aliases": [
        "水杯",
        "杯子"
      ],
      "room_id": "kitchen",
      "anchor_id": "anchor_kitchen_table_1",
      "anchor_type": "table",
      "display_text": "厨房餐桌",
      "viewpoint_id": "kitchen_table_viewpoint",
      "confidence_level": "high",
      "belief_state": "confirmed",
      "last_confirmed_at": "2026-04-19T11:00:00Z",
      "text_snippet": "物体记忆。目标类别: cup。目标类别别名: 水杯、杯子、cup。别名: 水杯、杯子。历史位置: 厨房餐桌。房间: kitchen。房间别名: 厨房、kitchen。锚点类型: table。锚点别名: 桌子、餐桌、table。可观察视角: kitchen_table_viewpoint。置信度: high。记忆状态: confirmed。最近确认时间: 2026-04-19T11:00:00Z。",
      "bm25_score": 1.151654839515686,
      "dense_score": 0.0,
      "metadata_score": 0.44999999999999996,
      "final_score": 0.4827868852459016,
      "ranking_reasons": [
        "bm25_rank=1",
        "dense_rank=1",
        "metadata_target_alias_match",
        "metadata_location_match",
        "metadata_high_confidence"
      ],
      "canonical_metadata": {
        "source_type": "object_memory",
        "memory_id": "mem-cup-1",
        "object_category": "cup",
        "aliases": [
          "水杯",
          "杯子"
        ],
        "room_id": "kitchen",
        "anchor_id": "anchor_kitchen_table_1",
        "anchor_type": "table",
        "display_text": "厨房餐桌",
        "viewpoint_id": "kitchen_table_viewpoint",
        "confidence_level": "high",
        "belief_state": "confirmed",
        "last_confirmed_at": "2026-04-19T11:00:00Z",
        "document_text_hash": "4c3fb877f13a22c5a2e4924487333c7a484154120633c4fe536d668a5043e35b"
      },
      "executable": true,
      "invalid_reason": null,
      "ranking_stage": "bm25_dense_fusion",
      "rerank_score": null,
      "reranker_model": null
    },
    {
      "document_id": "object_memory:mem-cup-2",
      "source_type": "object_memory",
      "memory_id": "mem-cup-2",
      "object_category": "cup",
      "aliases": [
        "水杯"
      ],
      "room_id": "pantry",
      "anchor_id": "anchor_pantry_shelf_1",
      "anchor_type": "shelf",
      "display_text": "储物间搁架",
      "viewpoint_id": "pantry_shelf_viewpoint",
      "confidence_level": "medium",
      "belief_state": "confirmed",
      "last_confirmed_at": "2026-04-16T11:00:00Z",
      "text_snippet": "物体记忆。目标类别: cup。目标类别别名: 水杯、杯子、cup。别名: 水杯。历史位置: 储物间搁架。房间: pantry。房间别名: 储物间、pantry。锚点类型: shelf。锚点别名: 搁架、架子、shelf。可观察视角: pantry_shelf_viewpoint。置信度: medium。记忆状态: confirmed。最近确认时间: 2026-04-16T11:00:00Z。",
      "bm25_score": 0.38707324862480164,
      "dense_score": 0.0,
      "metadata_score": 0.25,
      "final_score": 0.282258064516129,
      "ranking_reasons": [
        "bm25_rank=2",
        "dense_rank=2",
        "metadata_target_alias_match",
        "metadata_medium_confidence"
      ],
      "canonical_metadata": {
        "source_type": "object_memory",
        "memory_id": "mem-cup-2",
        "object_category": "cup",
        "aliases": [
          "水杯"
        ],
        "room_id": "pantry",
        "anchor_id": "anchor_pantry_shelf_1",
        "anchor_type": "shelf",
        "display_text": "储物间搁架",
        "viewpoint_id": "pantry_shelf_viewpoint",
        "confidence_level": "medium",
        "belief_state": "confirmed",
        "last_confirmed_at": "2026-04-16T11:00:00Z",
        "document_text_hash": "0ce6d751065a83d42507903b864669875ac1077ccf68007227c057c650b11a6d"
      },
      "executable": true,
      "invalid_reason": null,
      "ranking_stage": "bm25_dense_fusion",
      "rerank_score": null,
      "reranker_model": null
    }
  ],
  "excluded": [],
  "retrieval_query": {
    "query_text": "水杯 杯子 茶杯 water cup cup 厨房 kitchen",
    "target_category": null,
    "target_aliases": [
      "杯子",
      "茶杯",
      "water cup",
      "cup"
    ],
    "location_terms": [
      "厨房",
      "kitchen"
    ],
    "source_filter": [
      "object_memory"
    ],
    "top_k": 5,
    "excluded_memory_ids": [],
    "excluded_location_keys": [],
    "reason": "Fetch water cup from kitchen for user delivery."
  },
  "ranking_reasons": [
    "bm25_dense_rrf_fusion",
    "metadata_guardrail"
  ],
  "retrieval_summary": "returned 2 hits and 0 excluded",
  "embedding_provider": {
    "provider_name": "MemoryEmbedding",
    "model": "BAAI/bge-m3",
    "endpoint": "https://api.siliconflow.cn/v1/embeddings"
  },
  "index_snapshot": {
    "document_count": 2,
    "domain_terms": [
      "cup",
      "水杯",
      "杯子",
      "kitchen",
      "anchor_kitchen_table_1",
      "table",
      "kitchen_table_viewpoint",
      "厨房餐桌",
      "厨房",
      "桌子",
      "餐桌",
      "pantry",
      "anchor_pantry_shelf_1",
      "shelf",
      "pantry_shelf_viewpoint",
      "储物间搁架",
      "储物间",
      "搁架",
      "架子"
    ],
    "tokenized_query": [
      "水杯",
      "杯子",
      "茶杯",
      "water",
      "cup",
      "cup",
      "厨房",
      "kitchen"
    ],
    "ranking_stage": "bm25_dense_fusion"
  }
}
```

## Checks

```json
{
  "schema_valid": true,
  "has_score_breakdown": true
}
```
