# Stage 03 Memory RAG - stage07_fetch_cup_retry-1778074786_memory_rag

Status: PASS

Provider: {'provider_name': 'deterministic', 'model': 'stage07-static'}

Embedding Provider: {'provider_name': 'deterministic-embedding', 'model': 'keyword-vector-v1'}

## Expected Conditions

```json
{
  "case_name": "stage07_fetch_cup_retry-1778074786_memory_rag"
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
    "后续观察或验证能确认水杯相关任务是否完成"
  ],
  "needs_clarification": false,
  "clarification_question": null,
  "confidence": 0.95
}

Runtime negative evidence:
{}

只输出 JSON object:

```

## Mimo Raw Response

```text
{"query_text":"水杯 厨房","target_category":"cup","target_aliases":["水杯"],"location_terms":["厨房"],"source_filter":["object_memory"],"top_k":5,"excluded_memory_ids":[],"excluded_location_keys":[],"reason":"deterministic Stage07 non-live query"}
```

## Query Attempts

```json
[
  {
    "attempt": 1,
    "prompt": "你是 HomeMaster V1.2 的 memory RAG query 构造组件。\n\n目标：根据 TaskCard 生成一个 MemoryRetrievalQuery JSON。\n你只负责构造检索 query，不读取 memory，不返回 memory hit，不选择目标地点。\n\n必须只输出一个 JSON object。\n不要输出 Markdown。\n不要输出解释。\n不要输出代码块。\n不要输出思考过程。\n不要编造 memory_id、anchor_id、viewpoint_id 或真实位置。\n\nMemoryRetrievalQuery schema:\n{\n  \"query_text\": \"非空字符串；包含目标物、别名、位置提示和稳定英文别名\",\n  \"target_category\": \"字符串或 null\",\n  \"target_aliases\": [\"目标物别名；可来自 TaskCard 或常识别名\"],\n  \"location_terms\": [\"位置词；只来自 TaskCard 明说的位置或常识位置别名\"],\n  \"source_filter\": [\"object_memory\"],\n  \"top_k\": 5,\n  \"excluded_memory_ids\": [\"只能来自 runtime negative evidence\"],\n  \"excluded_location_keys\": [\"只能来自 runtime negative evidence\"],\n  \"reason\": \"字符串或 null\"\n}\n\n边界:\n- source_filter 必须是 [\"object_memory\"]。\n- top_k 使用 5，除非任务明显需要更多候选；不要超过 10。\n- query_text 由你进行语义构造；程序不会替你补写语义别名。\n- excluded_memory_ids / excluded_location_keys 只能复制 runtime negative evidence 中已有值。\n- 不要编造 memory_id。\n\nTaskCard:\n{\n  \"task_type\": \"fetch_object\",\n  \"target\": \"水杯\",\n  \"delivery_target\": \"user\",\n  \"location_hint\": \"厨房\",\n  \"success_criteria\": [\n    \"后续观察或验证能确认水杯相关任务是否完成\"\n  ],\n  \"needs_clarification\": false,\n  \"clarification_question\": null,\n  \"confidence\": 0.95\n}\n\nRuntime negative evidence:\n{}\n\n只输出 JSON object:\n",
    "max_tokens": 4096,
    "passed": true,
    "raw_response": "{\"query_text\":\"水杯 厨房\",\"target_category\":\"cup\",\"target_aliases\":[\"水杯\"],\"location_terms\":[\"厨房\"],\"source_filter\":[\"object_memory\"],\"top_k\":5,\"excluded_memory_ids\":[],\"excluded_location_keys\":[],\"reason\":\"deterministic Stage07 non-live query\"}",
    "retrieval_query": {
      "query_text": "水杯 厨房",
      "target_category": "cup",
      "target_aliases": [
        "水杯"
      ],
      "location_terms": [
        "厨房"
      ],
      "source_filter": [
        "object_memory"
      ],
      "top_k": 5,
      "excluded_memory_ids": [],
      "excluded_location_keys": [],
      "reason": "deterministic Stage07 non-live query"
    },
    "query_provider": {
      "provider_name": "deterministic",
      "model": "stage07-static"
    }
  }
]
```

## Parsed MemoryRetrievalQuery

```json
{
  "query_text": "水杯 厨房",
  "target_category": "cup",
  "target_aliases": [
    "水杯"
  ],
  "location_terms": [
    "厨房"
  ],
  "source_filter": [
    "object_memory"
  ],
  "top_k": 5,
  "excluded_memory_ids": [],
  "excluded_location_keys": [],
  "reason": "deterministic Stage07 non-live query"
}
```

## Memory Documents

```json
[
  {
    "document_id": "object_memory:mem-cup-1",
    "text": "物体记忆。目标类别: cup。目标类别别名: 水杯、杯子、cup。别名: 水杯、杯子。历史位置: 厨房餐桌。房间: kitchen。房间别名: 厨房、kitchen。锚点类型: table。锚点别名: 桌子、table。可观察视角: anchor_kitchen_table_1_vp。置信度: high。记忆状态: confirmed。最近确认时间: 2026-04-30T10:00:00Z。",
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
      "viewpoint_id": "anchor_kitchen_table_1_vp",
      "confidence_level": "high",
      "belief_state": "confirmed",
      "last_confirmed_at": "2026-04-30T10:00:00Z",
      "document_text_hash": "3e1da7e6a7d575cbc9eac75849f2b93e4b9589e9475a363d6633afa01063adb8"
    },
    "executable": true,
    "invalid_reason": null
  },
  {
    "document_id": "object_memory:mem-cup-2",
    "text": "物体记忆。目标类别: cup。目标类别别名: 水杯、杯子、cup。别名: 水杯。历史位置: 厨房操作台。房间: kitchen。房间别名: 厨房、kitchen。锚点类型: counter。锚点别名: 台面、柜台、counter。可观察视角: anchor_kitchen_counter_1_vp。置信度: medium。记忆状态: confirmed。最近确认时间: 2026-04-25T10:00:00Z。",
    "metadata": {
      "source_type": "object_memory",
      "memory_id": "mem-cup-2",
      "object_category": "cup",
      "aliases": [
        "水杯"
      ],
      "room_id": "kitchen",
      "anchor_id": "anchor_kitchen_counter_1",
      "anchor_type": "counter",
      "display_text": "厨房操作台",
      "viewpoint_id": "anchor_kitchen_counter_1_vp",
      "confidence_level": "medium",
      "belief_state": "confirmed",
      "last_confirmed_at": "2026-04-25T10:00:00Z",
      "document_text_hash": "7ccd7e25307747f3561ed86052adf0dd239545dc540e052dcecc28e77c6afc1e"
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
  "厨房"
]
```

## BM25 Hits

```json
[
  {
    "document_id": "object_memory:mem-cup-2",
    "score": 0.1771123707294464,
    "rank": 1
  },
  {
    "document_id": "object_memory:mem-cup-1",
    "score": 0.1771123707294464,
    "rank": 2
  }
]
```

## BGE-M3 Dense Hits

```json
[
  {
    "document_id": "object_memory:mem-cup-2",
    "score": 0.9999999999999998,
    "rank": 1
  },
  {
    "document_id": "object_memory:mem-cup-1",
    "score": 0.8164965809277259,
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
      "viewpoint_id": "anchor_kitchen_table_1_vp",
      "confidence_level": "high",
      "belief_state": "confirmed",
      "last_confirmed_at": "2026-04-30T10:00:00Z",
      "text_snippet": "物体记忆。目标类别: cup。目标类别别名: 水杯、杯子、cup。别名: 水杯、杯子。历史位置: 厨房餐桌。房间: kitchen。房间别名: 厨房、kitchen。锚点类型: table。锚点别名: 桌子、table。可观察视角: anchor_kitchen_table_1_vp。置信度: high。记忆状态: confirmed。最近确认时间: 2026-04-30T10:00:00Z。",
      "bm25_score": 0.1771123707294464,
      "dense_score": 0.8164965809277259,
      "metadata_score": 0.65,
      "final_score": 0.682258064516129,
      "ranking_reasons": [
        "bm25_rank=2",
        "dense_rank=2",
        "metadata_target_category_match",
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
        "viewpoint_id": "anchor_kitchen_table_1_vp",
        "confidence_level": "high",
        "belief_state": "confirmed",
        "last_confirmed_at": "2026-04-30T10:00:00Z",
        "document_text_hash": "3e1da7e6a7d575cbc9eac75849f2b93e4b9589e9475a363d6633afa01063adb8"
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
      "room_id": "kitchen",
      "anchor_id": "anchor_kitchen_counter_1",
      "anchor_type": "counter",
      "display_text": "厨房操作台",
      "viewpoint_id": "anchor_kitchen_counter_1_vp",
      "confidence_level": "medium",
      "belief_state": "confirmed",
      "last_confirmed_at": "2026-04-25T10:00:00Z",
      "text_snippet": "物体记忆。目标类别: cup。目标类别别名: 水杯、杯子、cup。别名: 水杯。历史位置: 厨房操作台。房间: kitchen。房间别名: 厨房、kitchen。锚点类型: counter。锚点别名: 台面、柜台、counter。可观察视角: anchor_kitchen_counter_1_vp。置信度: medium。记忆状态: confirmed。最近确认时间: 2026-04-25T10:00:00Z。",
      "bm25_score": 0.1771123707294464,
      "dense_score": 0.9999999999999998,
      "metadata_score": 0.6000000000000001,
      "final_score": 0.6327868852459018,
      "ranking_reasons": [
        "bm25_rank=1",
        "dense_rank=1",
        "metadata_target_category_match",
        "metadata_target_alias_match",
        "metadata_location_match",
        "metadata_medium_confidence"
      ],
      "canonical_metadata": {
        "source_type": "object_memory",
        "memory_id": "mem-cup-2",
        "object_category": "cup",
        "aliases": [
          "水杯"
        ],
        "room_id": "kitchen",
        "anchor_id": "anchor_kitchen_counter_1",
        "anchor_type": "counter",
        "display_text": "厨房操作台",
        "viewpoint_id": "anchor_kitchen_counter_1_vp",
        "confidence_level": "medium",
        "belief_state": "confirmed",
        "last_confirmed_at": "2026-04-25T10:00:00Z",
        "document_text_hash": "7ccd7e25307747f3561ed86052adf0dd239545dc540e052dcecc28e77c6afc1e"
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
    "query_text": "水杯 厨房",
    "target_category": "cup",
    "target_aliases": [
      "水杯"
    ],
    "location_terms": [
      "厨房"
    ],
    "source_filter": [
      "object_memory"
    ],
    "top_k": 5,
    "excluded_memory_ids": [],
    "excluded_location_keys": [],
    "reason": "deterministic Stage07 non-live query"
  },
  "ranking_reasons": [
    "bm25_dense_rrf_fusion",
    "metadata_guardrail"
  ],
  "retrieval_summary": "returned 2 hits and 0 excluded",
  "embedding_provider": {
    "provider_name": "deterministic-embedding",
    "model": "keyword-vector-v1"
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
      "anchor_kitchen_table_1_vp",
      "厨房餐桌",
      "厨房",
      "桌子",
      "anchor_kitchen_counter_1",
      "counter",
      "anchor_kitchen_counter_1_vp",
      "厨房操作台",
      "台面",
      "柜台"
    ],
    "tokenized_query": [
      "水杯",
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
  "has_score_breakdown": true
}
```
