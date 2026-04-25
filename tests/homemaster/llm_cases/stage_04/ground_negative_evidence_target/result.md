# Stage 04 Grounding Context - ground_negative_evidence_target

Status: PASS

## Expected Conditions

```json
{
  "stage_03_case": "negative_evidence_excludes_location",
  "world_path": "data/scenarios/object_not_found/world.json",
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
[
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
    "bm25_score": 0.20683912932872772,
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
      "aliases": [
        "水杯"
      ],
      "anchor_id": "anchor_living_side_table_1",
      "anchor_type": "table",
      "belief_state": "stale",
      "confidence_level": "low",
      "display_text": "客厅边桌",
      "document_text_hash": "ae2e6e538fc84b173b4284ee8812981b933e5bad387b491d0e018b374da378e4",
      "last_confirmed_at": "2026-04-01T10:00:00Z",
      "memory_id": "mem-cup-2",
      "object_category": "cup",
      "room_id": "living_room",
      "source_type": "object_memory",
      "viewpoint_id": "living_side_table_viewpoint"
    },
    "executable": true,
    "invalid_reason": null,
    "ranking_stage": "bm25_dense_fusion",
    "rerank_score": null,
    "reranker_model": null
  }
]
```

## Hit Assessments

```json
[
  {
    "memory_id": "mem-cup-2",
    "document_id": "object_memory:mem-cup-2",
    "status": "weak_lead",
    "reasons": [
      "location_conflict",
      "low_confidence",
      "stale_belief"
    ],
    "needs_exploratory_search": true,
    "suggested_search_hint": "水杯 厨房"
  }
]
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
    "query_text": "水杯 (cup) 在厨房",
    "target_category": null,
    "target_aliases": [
      "水杯",
      "杯子",
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
    "excluded_memory_ids": [
      "mem-cup-1"
    ],
    "excluded_location_keys": [],
    "reason": null
  },
  "memory_evidence": {
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
        "bm25_score": 0.20683912932872772,
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
          "aliases": [
            "水杯"
          ],
          "anchor_id": "anchor_living_side_table_1",
          "anchor_type": "table",
          "belief_state": "stale",
          "confidence_level": "low",
          "display_text": "客厅边桌",
          "document_text_hash": "ae2e6e538fc84b173b4284ee8812981b933e5bad387b491d0e018b374da378e4",
          "last_confirmed_at": "2026-04-01T10:00:00Z",
          "memory_id": "mem-cup-2",
          "object_category": "cup",
          "room_id": "living_room",
          "source_type": "object_memory",
          "viewpoint_id": "living_side_table_viewpoint"
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
        "bm25_score": 0.4900756776332855,
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
          "aliases": [
            "水杯",
            "杯子"
          ],
          "anchor_id": "anchor_kitchen_table_1",
          "anchor_type": "table",
          "belief_state": "confirmed",
          "confidence_level": "medium",
          "display_text": "厨房餐桌",
          "document_text_hash": "66d36707b780d556dc37bd38c2e61a679166655db664c7838f4d4cfd23b96807",
          "last_confirmed_at": "2026-04-10T10:00:00Z",
          "memory_id": "mem-cup-1",
          "object_category": "cup",
          "room_id": "kitchen",
          "source_type": "object_memory",
          "viewpoint_id": "kitchen_table_viewpoint"
        },
        "executable": false,
        "invalid_reason": "excluded by runtime negative evidence memory_id",
        "ranking_stage": "bm25_dense_fusion",
        "rerank_score": null,
        "reranker_model": null
      }
    ],
    "retrieval_query": {
      "query_text": "水杯 (cup) 在厨房",
      "target_category": null,
      "target_aliases": [
        "水杯",
        "杯子",
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
        "living_room",
        "anchor_living_side_table_1",
        "living_side_table_viewpoint",
        "客厅边桌",
        "客厅",
        "living room"
      ],
      "ranking_stage": "bm25_dense_fusion",
      "tokenized_query": "[REDACTED]"
    }
  },
  "selected_target": null,
  "rejected_hits": [
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
      "bm25_score": 0.20683912932872772,
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
        "aliases": [
          "水杯"
        ],
        "anchor_id": "anchor_living_side_table_1",
        "anchor_type": "table",
        "belief_state": "stale",
        "confidence_level": "low",
        "display_text": "客厅边桌",
        "document_text_hash": "ae2e6e538fc84b173b4284ee8812981b933e5bad387b491d0e018b374da378e4",
        "last_confirmed_at": "2026-04-01T10:00:00Z",
        "memory_id": "mem-cup-2",
        "object_category": "cup",
        "room_id": "living_room",
        "source_type": "object_memory",
        "viewpoint_id": "living_side_table_viewpoint"
      },
      "executable": true,
      "invalid_reason": null,
      "ranking_stage": "bm25_dense_fusion",
      "rerank_score": null,
      "reranker_model": null
    }
  ],
  "runtime_state_summary": {
    "grounding_status": "ungrounded",
    "grounding_reason": "only weak memory leads available; planner should explore/search",
    "needs_exploratory_search": true
  },
  "world_summary": {
    "room_ids": [
      "kitchen",
      "living_room"
    ],
    "viewpoint_ids": [
      "kitchen_table_viewpoint",
      "living_side_table_viewpoint"
    ],
    "anchors": [
      {
        "anchor_id": "anchor_kitchen_table_1",
        "room_id": "kitchen",
        "viewpoint_id": "kitchen_table_viewpoint",
        "display_text": "厨房餐桌"
      },
      {
        "anchor_id": "anchor_living_side_table_1",
        "room_id": "living_room",
        "viewpoint_id": "living_side_table_viewpoint",
        "display_text": "客厅边桌"
      }
    ]
  },
  "planning_notes": [
    "没有可靠执行记忆；Stage 05 should plan exploratory search/observe steps.",
    "weak memory leads for exploration: 水杯 厨房"
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
