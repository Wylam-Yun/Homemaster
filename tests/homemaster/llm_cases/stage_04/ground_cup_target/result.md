# Stage 04 Grounding Context - ground_cup_target

Status: PASS

## Expected Conditions

```json
{
  "stage_03_case": "cup_object_memory_rag",
  "world_path": "data/scenarios/fetch_cup_retry/world.json",
  "expected_grounding_status": "grounded",
  "expected_selected_memory_id": "mem-cup-1"
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
    "bm25_score": 0.5871412754058838,
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
      "aliases": [
        "水杯",
        "杯子"
      ],
      "anchor_id": "anchor_kitchen_table_1",
      "anchor_type": "table",
      "belief_state": "confirmed",
      "confidence_level": "high",
      "display_text": "厨房餐桌",
      "document_text_hash": "4c3fb877f13a22c5a2e4924487333c7a484154120633c4fe536d668a5043e35b",
      "last_confirmed_at": "2026-04-19T11:00:00Z",
      "memory_id": "mem-cup-1",
      "object_category": "cup",
      "room_id": "kitchen",
      "source_type": "object_memory",
      "viewpoint_id": "kitchen_table_viewpoint"
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
    "bm25_score": 0.2824892997741699,
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
      "aliases": [
        "水杯"
      ],
      "anchor_id": "anchor_pantry_shelf_1",
      "anchor_type": "shelf",
      "belief_state": "confirmed",
      "confidence_level": "medium",
      "display_text": "储物间搁架",
      "document_text_hash": "0ce6d751065a83d42507903b864669875ac1077ccf68007227c057c650b11a6d",
      "last_confirmed_at": "2026-04-16T11:00:00Z",
      "memory_id": "mem-cup-2",
      "object_category": "cup",
      "room_id": "pantry",
      "source_type": "object_memory",
      "viewpoint_id": "pantry_shelf_viewpoint"
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
    "memory_id": "mem-cup-1",
    "document_id": "object_memory:mem-cup-1",
    "status": "reliable",
    "reasons": [
      "reliable_execution_memory"
    ],
    "needs_exploratory_search": false,
    "suggested_search_hint": null
  }
]
```

## Selected Target

```json
{
  "memory_id": "mem-cup-1",
  "room_id": "kitchen",
  "anchor_id": "anchor_kitchen_table_1",
  "viewpoint_id": "kitchen_table_viewpoint",
  "display_text": "厨房餐桌",
  "evidence": {
    "source": "canonical_metadata",
    "document_id": "object_memory:mem-cup-1",
    "final_score": 0.4827868852459016,
    "ranking_reasons": [
      "bm25_rank=1",
      "dense_rank=1",
      "metadata_target_alias_match",
      "metadata_location_match",
      "metadata_high_confidence"
    ],
    "reliability": {
      "memory_id": "mem-cup-1",
      "document_id": "object_memory:mem-cup-1",
      "status": "reliable",
      "reasons": [
        "reliable_execution_memory"
      ],
      "needs_exploratory_search": false,
      "suggested_search_hint": null
    },
    "canonical_metadata": {
      "aliases": [
        "水杯",
        "杯子"
      ],
      "anchor_id": "anchor_kitchen_table_1",
      "anchor_type": "table",
      "belief_state": "confirmed",
      "confidence_level": "high",
      "display_text": "厨房餐桌",
      "document_text_hash": "4c3fb877f13a22c5a2e4924487333c7a484154120633c4fe536d668a5043e35b",
      "last_confirmed_at": "2026-04-19T11:00:00Z",
      "memory_id": "mem-cup-1",
      "object_category": "cup",
      "room_id": "kitchen",
      "source_type": "object_memory",
      "viewpoint_id": "kitchen_table_viewpoint"
    }
  },
  "executable": true,
  "invalid_reason": null
}
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
        "bm25_score": 0.5871412754058838,
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
          "aliases": [
            "水杯",
            "杯子"
          ],
          "anchor_id": "anchor_kitchen_table_1",
          "anchor_type": "table",
          "belief_state": "confirmed",
          "confidence_level": "high",
          "display_text": "厨房餐桌",
          "document_text_hash": "4c3fb877f13a22c5a2e4924487333c7a484154120633c4fe536d668a5043e35b",
          "last_confirmed_at": "2026-04-19T11:00:00Z",
          "memory_id": "mem-cup-1",
          "object_category": "cup",
          "room_id": "kitchen",
          "source_type": "object_memory",
          "viewpoint_id": "kitchen_table_viewpoint"
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
        "bm25_score": 0.2824892997741699,
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
          "aliases": [
            "水杯"
          ],
          "anchor_id": "anchor_pantry_shelf_1",
          "anchor_type": "shelf",
          "belief_state": "confirmed",
          "confidence_level": "medium",
          "display_text": "储物间搁架",
          "document_text_hash": "0ce6d751065a83d42507903b864669875ac1077ccf68007227c057c650b11a6d",
          "last_confirmed_at": "2026-04-16T11:00:00Z",
          "memory_id": "mem-cup-2",
          "object_category": "cup",
          "room_id": "pantry",
          "source_type": "object_memory",
          "viewpoint_id": "pantry_shelf_viewpoint"
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
  "selected_target": {
    "memory_id": "mem-cup-1",
    "room_id": "kitchen",
    "anchor_id": "anchor_kitchen_table_1",
    "viewpoint_id": "kitchen_table_viewpoint",
    "display_text": "厨房餐桌",
    "evidence": {
      "source": "canonical_metadata",
      "document_id": "object_memory:mem-cup-1",
      "final_score": 0.4827868852459016,
      "ranking_reasons": [
        "bm25_rank=1",
        "dense_rank=1",
        "metadata_target_alias_match",
        "metadata_location_match",
        "metadata_high_confidence"
      ],
      "reliability": {
        "memory_id": "mem-cup-1",
        "document_id": "object_memory:mem-cup-1",
        "status": "reliable",
        "reasons": [
          "reliable_execution_memory"
        ],
        "needs_exploratory_search": false,
        "suggested_search_hint": null
      },
      "canonical_metadata": {
        "aliases": [
          "水杯",
          "杯子"
        ],
        "anchor_id": "anchor_kitchen_table_1",
        "anchor_type": "table",
        "belief_state": "confirmed",
        "confidence_level": "high",
        "display_text": "厨房餐桌",
        "document_text_hash": "4c3fb877f13a22c5a2e4924487333c7a484154120633c4fe536d668a5043e35b",
        "last_confirmed_at": "2026-04-19T11:00:00Z",
        "memory_id": "mem-cup-1",
        "object_category": "cup",
        "room_id": "kitchen",
        "source_type": "object_memory",
        "viewpoint_id": "kitchen_table_viewpoint"
      }
    },
    "executable": true,
    "invalid_reason": null
  },
  "rejected_hits": [],
  "runtime_state_summary": {
    "grounding_status": "grounded",
    "grounding_reason": "selected first reliable executable memory hit",
    "needs_exploratory_search": false
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
    "grounded reliable memory target is available for Stage 05 planning"
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
