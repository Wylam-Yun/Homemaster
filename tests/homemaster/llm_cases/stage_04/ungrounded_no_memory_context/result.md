# Stage 04 Grounding Context - ungrounded_no_memory_context

Status: PASS

## Expected Conditions

```json
{
  "stage_03_case": "cup_object_memory_rag",
  "world_path": "data/scenarios/fetch_cup_retry/world.json",
  "force_empty_hits": true,
  "expected_grounding_status": "ungrounded",
  "expected_selected_memory_id": null
}
```

## TaskCard

```json
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
```

## Stage03 Memory Hits

```json
[]
```

## Hit Assessments

```json
[]
```

## Selected Target

```json
null
```

## PlanningContext

```json
{
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
  "retrieval_query": {
    "query_text": "水杯 杯子 厨房 water cup",
    "target_category": null,
    "target_aliases": [
      "水杯",
      "杯子"
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
    "reason": null
  },
  "memory_evidence": {
    "hits": [],
    "excluded": [],
    "retrieval_query": {
      "query_text": "水杯 杯子 厨房 water cup",
      "target_category": null,
      "target_aliases": [
        "水杯",
        "杯子"
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
      "reason": null
    },
    "ranking_reasons": [
      "bm25_dense_rrf_fusion",
      "metadata_guardrail"
    ],
    "retrieval_summary": "returned 2 hits and 0 excluded",
    "embedding_provider": {
      "endpoint": "https://api.siliconflow.cn/v1/embeddings",
      "model": "BAAI/bge-m3",
      "provider_name": "MemoryEmbedding"
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
      "ranking_stage": "bm25_dense_fusion",
      "tokenized_query": "[REDACTED]"
    }
  },
  "selected_target": null,
  "rejected_hits": [],
  "runtime_state_summary": {
    "grounding_status": "ungrounded",
    "grounding_reason": "no memory hits available; planner should explore/search",
    "needs_exploratory_search": true
  },
  "world_summary": {
    "room_ids": [
      "kitchen",
      "pantry"
    ],
    "viewpoint_ids": [
      "kitchen_table_viewpoint",
      "pantry_shelf_viewpoint"
    ],
    "anchors": [
      {
        "anchor_id": "anchor_kitchen_table_1",
        "room_id": "kitchen",
        "viewpoint_id": "kitchen_table_viewpoint",
        "display_text": "厨房餐桌"
      },
      {
        "anchor_id": "anchor_pantry_shelf_1",
        "room_id": "pantry",
        "viewpoint_id": "pantry_shelf_viewpoint",
        "display_text": "储物间搁架"
      }
    ]
  },
  "planning_notes": [
    "没有可靠执行记忆；Stage 05 should plan exploratory search/observe steps."
  ]
}
```

## Checks

```json
{
  "grounding_status_matches": true,
  "selected_memory_matches": true,
  "planning_context_serializes": true
}
```
