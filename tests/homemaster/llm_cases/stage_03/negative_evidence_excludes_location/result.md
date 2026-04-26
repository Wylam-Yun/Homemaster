# Stage 03 Memory RAG - negative_evidence_excludes_location

Status: PASS

Provider: {'provider_name': 'Mimo', 'model': 'mimo-v2-pro', 'protocol': 'anthropic', 'elapsed_ms': 32266.782583000124, 'attempts': [{'key_index': 1, 'status_code': 200, 'elapsed_ms': 32266.782583000124}]}

Embedding Provider: {'provider_name': 'MemoryEmbedding', 'model': 'BAAI/bge-m3', 'endpoint': 'https://api.siliconflow.cn/v1/embeddings'}

## Expected Conditions

```json
{
  "case_name": "negative_evidence_excludes_location",
  "memory_path": "data/scenarios/object_not_found/memory.json",
  "task_card": {
    "task_type": "fetch_object",
    "target": "水杯",
    "delivery_target": "user",
    "location_hint": "厨房",
    "success_criteria": [
      "后续观察可以验证任务是否完成"
    ],
    "needs_clarification": false,
    "clarification_question": null,
    "confidence": 0.9
  },
  "negative_evidence": {
    "excluded_memory_ids": [
      "mem-cup-1"
    ]
  },
  "expected_query_keywords": [
    "水杯",
    "杯",
    "cup"
  ],
  "excluded_memory_ids": [
    "mem-cup-1"
  ]
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
    "后续观察可以验证任务是否完成"
  ],
  "needs_clarification": false,
  "clarification_question": null,
  "confidence": 0.9
}

Runtime negative evidence:
{
  "excluded_memory_ids": [
    "mem-cup-1"
  ]
}

只输出 JSON object:

```

## Mimo Raw Response

```text
{
  "query_text": "水杯 (杯子, cup) 在厨房",
  "target_category": null,
  "target_aliases": ["水杯", "杯子", "cup"],
  "location_terms": ["厨房"],
  "source_filter": ["object_memory"],
  "top_k": 5,
  "excluded_memory_ids": ["mem-cup-1"],
  "excluded_location_keys": [],
  "reason": null
}
```

## Query Attempts

```json
[
  {
    "attempt": 1,
    "prompt": "你是 HomeMaster V1.2 的 memory RAG query 构造组件。\n\n目标：根据 TaskCard 生成一个 MemoryRetrievalQuery JSON。\n你只负责构造检索 query，不读取 memory，不返回 memory hit，不选择目标地点。\n\n必须只输出一个 JSON object。\n不要输出 Markdown。\n不要输出解释。\n不要输出代码块。\n不要输出思考过程。\n不要编造 memory_id、anchor_id、viewpoint_id 或真实位置。\n\nMemoryRetrievalQuery schema:\n{\n  \"query_text\": \"非空字符串；包含目标物、别名、位置提示和稳定英文别名\",\n  \"target_category\": \"字符串或 null\",\n  \"target_aliases\": [\"目标物别名；可来自 TaskCard 或常识别名\"],\n  \"location_terms\": [\"位置词；只来自 TaskCard 明说的位置或常识位置别名\"],\n  \"source_filter\": [\"object_memory\"],\n  \"top_k\": 5,\n  \"excluded_memory_ids\": [\"只能来自 runtime negative evidence\"],\n  \"excluded_location_keys\": [\"只能来自 runtime negative evidence\"],\n  \"reason\": \"字符串或 null\"\n}\n\n边界:\n- source_filter 必须是 [\"object_memory\"]。\n- top_k 使用 5，除非任务明显需要更多候选；不要超过 10。\n- query_text 由你进行语义构造；程序不会替你补写语义别名。\n- excluded_memory_ids / excluded_location_keys 只能复制 runtime negative evidence 中已有值。\n- 不要编造 memory_id。\n\nTaskCard:\n{\n  \"task_type\": \"fetch_object\",\n  \"target\": \"水杯\",\n  \"delivery_target\": \"user\",\n  \"location_hint\": \"厨房\",\n  \"success_criteria\": [\n    \"后续观察可以验证任务是否完成\"\n  ],\n  \"needs_clarification\": false,\n  \"clarification_question\": null,\n  \"confidence\": 0.9\n}\n\nRuntime negative evidence:\n{\n  \"excluded_memory_ids\": [\n    \"mem-cup-1\"\n  ]\n}\n\n只输出 JSON object:\n",
    "passed": true,
    "raw_response": "{\n  \"query_text\": \"水杯 (杯子, cup) 在厨房\",\n  \"target_category\": null,\n  \"target_aliases\": [\"水杯\", \"杯子\", \"cup\"],\n  \"location_terms\": [\"厨房\"],\n  \"source_filter\": [\"object_memory\"],\n  \"top_k\": 5,\n  \"excluded_memory_ids\": [\"mem-cup-1\"],\n  \"excluded_location_keys\": [],\n  \"reason\": null\n}",
    "retrieval_query": {
      "query_text": "水杯 (杯子, cup) 在厨房",
      "target_category": null,
      "target_aliases": [
        "水杯",
        "杯子",
        "cup"
      ],
      "location_terms": [
        "厨房"
      ],
      "source_filter": [
        "object_memory"
      ],
      "top_k": 5,
      "excluded_memory_ids": [
        "mem-cup-1"
      ],
      "excluded_location_keys": [],
      "reason": null
    },
    "query_provider": {
      "provider_name": "Mimo",
      "model": "mimo-v2-pro",
      "protocol": "anthropic",
      "elapsed_ms": 32266.782583000124,
      "attempts": [
        {
          "key_index": 1,
          "status_code": 200,
          "elapsed_ms": 32266.782583000124
        }
      ]
    }
  }
]
```

## Parsed MemoryRetrievalQuery

```json
{
  "query_text": "水杯 (杯子, cup) 在厨房",
  "target_category": null,
  "target_aliases": [
    "水杯",
    "杯子",
    "cup"
  ],
  "location_terms": [
    "厨房"
  ],
  "source_filter": [
    "object_memory"
  ],
  "top_k": 5,
  "excluded_memory_ids": [
    "mem-cup-1"
  ],
  "excluded_location_keys": [],
  "reason": null
}
```

## Memory Documents

```json
[
  {
    "document_id": "object_memory:mem-cup-1",
    "text": "物体记忆。目标类别: cup。目标类别别名: 水杯、杯子、cup。别名: 水杯、杯子。历史位置: 厨房餐桌。房间: kitchen。房间别名: 厨房、kitchen。锚点类型: table。锚点别名: 桌子、餐桌、table。可观察视角: kitchen_table_viewpoint。置信度: medium。记忆状态: confirmed。最近确认时间: 2026-04-10T10:00:00Z。",
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
      "confidence_level": "medium",
      "belief_state": "confirmed",
      "last_confirmed_at": "2026-04-10T10:00:00Z",
      "document_text_hash": "66d36707b780d556dc37bd38c2e61a679166655db664c7838f4d4cfd23b96807"
    },
    "executable": true,
    "invalid_reason": null
  },
  {
    "document_id": "object_memory:mem-cup-2",
    "text": "物体记忆。目标类别: cup。目标类别别名: 水杯、杯子、cup。别名: 水杯。历史位置: 客厅边桌。房间: living_room。房间别名: 客厅、living room、living_room。锚点类型: table。锚点别名: 桌子、餐桌、table。可观察视角: living_side_table_viewpoint。置信度: low。记忆状态: stale。最近确认时间: 2026-04-01T10:00:00Z。",
    "metadata": {
      "source_type": "object_memory",
      "memory_id": "mem-cup-2",
      "object_category": "cup",
      "aliases": [
        "水杯"
      ],
      "room_id": "living_room",
      "anchor_id": "anchor_living_side_table_1",
      "anchor_type": "table",
      "display_text": "客厅边桌",
      "viewpoint_id": "living_side_table_viewpoint",
      "confidence_level": "low",
      "belief_state": "stale",
      "last_confirmed_at": "2026-04-01T10:00:00Z",
      "document_text_hash": "ae2e6e538fc84b173b4284ee8812981b933e5bad387b491d0e018b374da378e4"
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
  "(",
  "杯子",
  ",",
  "cup",
  ")",
  "在",
  "厨房"
]
```

## BM25 Hits

```json
[
  {
    "document_id": "object_memory:mem-cup-1",
    "score": 0.5950349569320679,
    "rank": 1
  },
  {
    "document_id": "object_memory:mem-cup-2",
    "score": 0.2790210545063019,
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
      "document_id": "object_memory:mem-cup-2",
      "source_type": "object_memory",
      "memory_id": "mem-cup-2",
      "object_category": "cup",
      "aliases": [
        "水杯"
      ],
      "room_id": "living_room",
      "anchor_id": "anchor_living_side_table_1",
      "anchor_type": "table",
      "display_text": "客厅边桌",
      "viewpoint_id": "living_side_table_viewpoint",
      "confidence_level": "low",
      "belief_state": "stale",
      "last_confirmed_at": "2026-04-01T10:00:00Z",
      "text_snippet": "物体记忆。目标类别: cup。目标类别别名: 水杯、杯子、cup。别名: 水杯。历史位置: 客厅边桌。房间: living_room。房间别名: 客厅、living room、living_room。锚点类型: table。锚点别名: 桌子、餐桌、table。可观察视角: living_side_table_viewpoint。置信度: low。记忆状态: stale。最近确认时间: 2026-04-01T10:00:00Z。",
      "bm25_score": 0.2790210545063019,
      "dense_score": 0.0,
      "metadata_score": 0.1,
      "final_score": 0.13225806451612904,
      "ranking_reasons": [
        "bm25_rank=2",
        "dense_rank=2",
        "metadata_target_alias_match",
        "metadata_stale_penalty"
      ],
      "canonical_metadata": {
        "source_type": "object_memory",
        "memory_id": "mem-cup-2",
        "object_category": "cup",
        "aliases": [
          "水杯"
        ],
        "room_id": "living_room",
        "anchor_id": "anchor_living_side_table_1",
        "anchor_type": "table",
        "display_text": "客厅边桌",
        "viewpoint_id": "living_side_table_viewpoint",
        "confidence_level": "low",
        "belief_state": "stale",
        "last_confirmed_at": "2026-04-01T10:00:00Z",
        "document_text_hash": "ae2e6e538fc84b173b4284ee8812981b933e5bad387b491d0e018b374da378e4"
      },
      "executable": true,
      "invalid_reason": null,
      "ranking_stage": "bm25_dense_fusion",
      "rerank_score": null,
      "reranker_model": null
    }
  ],
  "excluded": [
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
      "confidence_level": "medium",
      "belief_state": "confirmed",
      "last_confirmed_at": "2026-04-10T10:00:00Z",
      "text_snippet": "物体记忆。目标类别: cup。目标类别别名: 水杯、杯子、cup。别名: 水杯、杯子。历史位置: 厨房餐桌。房间: kitchen。房间别名: 厨房、kitchen。锚点类型: table。锚点别名: 桌子、餐桌、table。可观察视角: kitchen_table_viewpoint。置信度: medium。记忆状态: confirmed。最近确认时间: 2026-04-10T10:00:00Z。",
      "bm25_score": 0.5950349569320679,
      "dense_score": 0.0,
      "metadata_score": 0.39999999999999997,
      "final_score": 0.4327868852459016,
      "ranking_reasons": [
        "bm25_rank=1",
        "dense_rank=1",
        "metadata_target_alias_match",
        "metadata_location_match",
        "metadata_medium_confidence"
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
        "confidence_level": "medium",
        "belief_state": "confirmed",
        "last_confirmed_at": "2026-04-10T10:00:00Z",
        "document_text_hash": "66d36707b780d556dc37bd38c2e61a679166655db664c7838f4d4cfd23b96807"
      },
      "executable": false,
      "invalid_reason": "excluded by runtime negative evidence memory_id",
      "ranking_stage": "bm25_dense_fusion",
      "rerank_score": null,
      "reranker_model": null
    }
  ],
  "retrieval_query": {
    "query_text": "水杯 (杯子, cup) 在厨房",
    "target_category": null,
    "target_aliases": [
      "水杯",
      "杯子",
      "cup"
    ],
    "location_terms": [
      "厨房"
    ],
    "source_filter": [
      "object_memory"
    ],
    "top_k": 5,
    "excluded_memory_ids": [
      "mem-cup-1"
    ],
    "excluded_location_keys": [],
    "reason": null
  },
  "ranking_reasons": [
    "bm25_dense_rrf_fusion",
    "metadata_guardrail"
  ],
  "retrieval_summary": "returned 1 hits and 1 excluded",
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
      "living_room",
      "anchor_living_side_table_1",
      "living_side_table_viewpoint",
      "客厅边桌",
      "客厅",
      "living room"
    ],
    "tokenized_query": [
      "水杯",
      "(",
      "杯子",
      ",",
      "cup",
      ")",
      "在",
      "厨房"
    ],
    "ranking_stage": "bm25_dense_fusion"
  }
}
```

## Checks

```json
{
  "schema_valid": true,
  "has_score_breakdown": true,
  "query_mentions_target": true,
  "excluded_memory_matches": true
}
```
