# Stage 04 Grounding Context - ground_medicine_target

Status: PASS

## Expected Conditions

```json
{
  "stage_03_case": "medicine_object_memory_rag",
  "world_path": "data/scenarios/check_medicine_success/world.json",
  "expected_grounding_status": "grounded",
  "expected_selected_memory_id": "mem-medicine-2"
}
```

## TaskCard

```json
{
  "task_type": "check_presence",
  "target": "药盒",
  "delivery_target": null,
  "location_hint": "桌子那边",
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
    "document_id": "object_memory:mem-medicine-1",
    "source_type": "object_memory",
    "memory_id": "mem-medicine-1",
    "object_category": "medicine_box",
    "aliases": [
      "药盒",
      "药箱"
    ],
    "room_id": "kitchen",
    "anchor_id": "anchor_kitchen_cabinet_1",
    "anchor_type": "cabinet",
    "display_text": "厨房药柜",
    "viewpoint_id": "kitchen_cabinet_viewpoint",
    "confidence_level": "high",
    "belief_state": "confirmed",
    "last_confirmed_at": "2026-04-19T09:00:00Z",
    "text_snippet": "物体记忆。目标类别: medicine_box。目标类别别名: 药盒、药箱、medicine_box、medicine。别名: 药盒、药箱。历史位置: 厨房药柜。房间: kitchen。房间别名: 厨房、kitchen。锚点类型: cabinet。锚点别名: 柜子、药柜、cabinet。可观察视角: kitchen_cabinet_viewpoint。置信度: high。记忆状态: confirmed。最近确认时间: 2026-04-19T09:00:00Z。",
    "bm25_score": 0.31477493047714233,
    "dense_score": 0.691463205593678,
    "metadata_score": 0.30000000000000004,
    "final_score": 0.33225806451612905,
    "ranking_reasons": [
      "bm25_rank=2",
      "dense_rank=2",
      "metadata_target_alias_match",
      "metadata_high_confidence"
    ],
    "canonical_metadata": {
      "aliases": [
        "药盒",
        "药箱"
      ],
      "anchor_id": "anchor_kitchen_cabinet_1",
      "anchor_type": "cabinet",
      "belief_state": "confirmed",
      "confidence_level": "high",
      "display_text": "厨房药柜",
      "document_text_hash": "d7f6ccb8e3d5a8f30955143466a7e652e071244ce067bad2b3aa441e4c3601e0",
      "last_confirmed_at": "2026-04-19T09:00:00Z",
      "memory_id": "mem-medicine-1",
      "object_category": "medicine_box",
      "room_id": "kitchen",
      "source_type": "object_memory",
      "viewpoint_id": "kitchen_cabinet_viewpoint"
    },
    "executable": true,
    "invalid_reason": null,
    "ranking_stage": "bm25_dense_fusion",
    "rerank_score": null,
    "reranker_model": null
  },
  {
    "document_id": "object_memory:mem-medicine-2",
    "source_type": "object_memory",
    "memory_id": "mem-medicine-2",
    "object_category": "medicine_box",
    "aliases": [
      "药盒"
    ],
    "room_id": "living_room",
    "anchor_id": "anchor_living_side_table_1",
    "anchor_type": "table",
    "display_text": "客厅边桌",
    "viewpoint_id": "living_side_table_viewpoint",
    "confidence_level": "medium",
    "belief_state": "confirmed",
    "last_confirmed_at": "2026-04-17T09:00:00Z",
    "text_snippet": "物体记忆。目标类别: medicine_box。目标类别别名: 药盒、药箱、medicine_box、medicine。别名: 药盒。历史位置: 客厅边桌。房间: living_room。房间别名: 客厅、living room、living_room。锚点类型: table。锚点别名: 桌子、餐桌、table。可观察视角: living_side_table_viewpoint。置信度: medium。记忆状态: confirmed。最近确认时间: 2026-04-17T09:00:00Z。",
    "bm25_score": 0.5536638498306274,
    "dense_score": 0.7041529775162785,
    "metadata_score": 0.25,
    "final_score": 0.2827868852459016,
    "ranking_reasons": [
      "bm25_rank=1",
      "dense_rank=1",
      "metadata_target_alias_match",
      "metadata_medium_confidence"
    ],
    "canonical_metadata": {
      "aliases": [
        "药盒"
      ],
      "anchor_id": "anchor_living_side_table_1",
      "anchor_type": "table",
      "belief_state": "confirmed",
      "confidence_level": "medium",
      "display_text": "客厅边桌",
      "document_text_hash": "38d0d78232705568b63372822d9e58966aa1e537a70e699fb34f03007fa2a45f",
      "last_confirmed_at": "2026-04-17T09:00:00Z",
      "memory_id": "mem-medicine-2",
      "object_category": "medicine_box",
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
    "memory_id": "mem-medicine-1",
    "document_id": "object_memory:mem-medicine-1",
    "status": "weak_lead",
    "reasons": [
      "anchor_hint_conflict"
    ],
    "needs_exploratory_search": true,
    "suggested_search_hint": "药盒 桌子那边"
  },
  {
    "memory_id": "mem-medicine-2",
    "document_id": "object_memory:mem-medicine-2",
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
  "memory_id": "mem-medicine-2",
  "room_id": "living_room",
  "anchor_id": "anchor_living_side_table_1",
  "viewpoint_id": "living_side_table_viewpoint",
  "display_text": "客厅边桌",
  "evidence": {
    "source": "canonical_metadata",
    "document_id": "object_memory:mem-medicine-2",
    "final_score": 0.2827868852459016,
    "ranking_reasons": [
      "bm25_rank=1",
      "dense_rank=1",
      "metadata_target_alias_match",
      "metadata_medium_confidence"
    ],
    "reliability": {
      "memory_id": "mem-medicine-2",
      "document_id": "object_memory:mem-medicine-2",
      "status": "reliable",
      "reasons": [
        "reliable_execution_memory"
      ],
      "needs_exploratory_search": false,
      "suggested_search_hint": null
    },
    "canonical_metadata": {
      "aliases": [
        "药盒"
      ],
      "anchor_id": "anchor_living_side_table_1",
      "anchor_type": "table",
      "belief_state": "confirmed",
      "confidence_level": "medium",
      "display_text": "客厅边桌",
      "document_text_hash": "38d0d78232705568b63372822d9e58966aa1e537a70e699fb34f03007fa2a45f",
      "last_confirmed_at": "2026-04-17T09:00:00Z",
      "memory_id": "mem-medicine-2",
      "object_category": "medicine_box",
      "room_id": "living_room",
      "source_type": "object_memory",
      "viewpoint_id": "living_side_table_viewpoint"
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
    "task_type": "check_presence",
    "target": "药盒",
    "delivery_target": null,
    "location_hint": "桌子那边",
    "success_criteria": [
      "后续观察可以验证任务是否完成"
    ],
    "needs_clarification": false,
    "clarification_question": null,
    "confidence": 0.9
  },
  "retrieval_query": {
    "query_text": "药盒，药物盒，药箱，pill box，桌子那边",
    "target_category": null,
    "target_aliases": [
      "药盒",
      "药物盒",
      "药箱"
    ],
    "location_terms": [
      "桌子那边"
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
        "document_id": "object_memory:mem-medicine-1",
        "source_type": "object_memory",
        "memory_id": "mem-medicine-1",
        "object_category": "medicine_box",
        "aliases": [
          "药盒",
          "药箱"
        ],
        "room_id": "kitchen",
        "anchor_id": "anchor_kitchen_cabinet_1",
        "anchor_type": "cabinet",
        "display_text": "厨房药柜",
        "viewpoint_id": "kitchen_cabinet_viewpoint",
        "confidence_level": "high",
        "belief_state": "confirmed",
        "last_confirmed_at": "2026-04-19T09:00:00Z",
        "text_snippet": "物体记忆。目标类别: medicine_box。目标类别别名: 药盒、药箱、medicine_box、medicine。别名: 药盒、药箱。历史位置: 厨房药柜。房间: kitchen。房间别名: 厨房、kitchen。锚点类型: cabinet。锚点别名: 柜子、药柜、cabinet。可观察视角: kitchen_cabinet_viewpoint。置信度: high。记忆状态: confirmed。最近确认时间: 2026-04-19T09:00:00Z。",
        "bm25_score": 0.31477493047714233,
        "dense_score": 0.691463205593678,
        "metadata_score": 0.30000000000000004,
        "final_score": 0.33225806451612905,
        "ranking_reasons": [
          "bm25_rank=2",
          "dense_rank=2",
          "metadata_target_alias_match",
          "metadata_high_confidence"
        ],
        "canonical_metadata": {
          "aliases": [
            "药盒",
            "药箱"
          ],
          "anchor_id": "anchor_kitchen_cabinet_1",
          "anchor_type": "cabinet",
          "belief_state": "confirmed",
          "confidence_level": "high",
          "display_text": "厨房药柜",
          "document_text_hash": "d7f6ccb8e3d5a8f30955143466a7e652e071244ce067bad2b3aa441e4c3601e0",
          "last_confirmed_at": "2026-04-19T09:00:00Z",
          "memory_id": "mem-medicine-1",
          "object_category": "medicine_box",
          "room_id": "kitchen",
          "source_type": "object_memory",
          "viewpoint_id": "kitchen_cabinet_viewpoint"
        },
        "executable": true,
        "invalid_reason": null,
        "ranking_stage": "bm25_dense_fusion",
        "rerank_score": null,
        "reranker_model": null
      },
      {
        "document_id": "object_memory:mem-medicine-2",
        "source_type": "object_memory",
        "memory_id": "mem-medicine-2",
        "object_category": "medicine_box",
        "aliases": [
          "药盒"
        ],
        "room_id": "living_room",
        "anchor_id": "anchor_living_side_table_1",
        "anchor_type": "table",
        "display_text": "客厅边桌",
        "viewpoint_id": "living_side_table_viewpoint",
        "confidence_level": "medium",
        "belief_state": "confirmed",
        "last_confirmed_at": "2026-04-17T09:00:00Z",
        "text_snippet": "物体记忆。目标类别: medicine_box。目标类别别名: 药盒、药箱、medicine_box、medicine。别名: 药盒。历史位置: 客厅边桌。房间: living_room。房间别名: 客厅、living room、living_room。锚点类型: table。锚点别名: 桌子、餐桌、table。可观察视角: living_side_table_viewpoint。置信度: medium。记忆状态: confirmed。最近确认时间: 2026-04-17T09:00:00Z。",
        "bm25_score": 0.5536638498306274,
        "dense_score": 0.7041529775162785,
        "metadata_score": 0.25,
        "final_score": 0.2827868852459016,
        "ranking_reasons": [
          "bm25_rank=1",
          "dense_rank=1",
          "metadata_target_alias_match",
          "metadata_medium_confidence"
        ],
        "canonical_metadata": {
          "aliases": [
            "药盒"
          ],
          "anchor_id": "anchor_living_side_table_1",
          "anchor_type": "table",
          "belief_state": "confirmed",
          "confidence_level": "medium",
          "display_text": "客厅边桌",
          "document_text_hash": "38d0d78232705568b63372822d9e58966aa1e537a70e699fb34f03007fa2a45f",
          "last_confirmed_at": "2026-04-17T09:00:00Z",
          "memory_id": "mem-medicine-2",
          "object_category": "medicine_box",
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
    "excluded": [],
    "retrieval_query": {
      "query_text": "药盒，药物盒，药箱，pill box，桌子那边",
      "target_category": null,
      "target_aliases": [
        "药盒",
        "药物盒",
        "药箱"
      ],
      "location_terms": [
        "桌子那边"
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
        "medicine_box",
        "药盒",
        "药箱",
        "medicine",
        "kitchen",
        "anchor_kitchen_cabinet_1",
        "cabinet",
        "kitchen_cabinet_viewpoint",
        "厨房药柜",
        "厨房",
        "柜子",
        "药柜",
        "living_room",
        "anchor_living_side_table_1",
        "table",
        "living_side_table_viewpoint",
        "客厅边桌",
        "客厅",
        "living room",
        "桌子",
        "餐桌"
      ],
      "ranking_stage": "bm25_dense_fusion",
      "tokenized_query": "[REDACTED]"
    }
  },
  "selected_target": {
    "memory_id": "mem-medicine-2",
    "room_id": "living_room",
    "anchor_id": "anchor_living_side_table_1",
    "viewpoint_id": "living_side_table_viewpoint",
    "display_text": "客厅边桌",
    "evidence": {
      "source": "canonical_metadata",
      "document_id": "object_memory:mem-medicine-2",
      "final_score": 0.2827868852459016,
      "ranking_reasons": [
        "bm25_rank=1",
        "dense_rank=1",
        "metadata_target_alias_match",
        "metadata_medium_confidence"
      ],
      "reliability": {
        "memory_id": "mem-medicine-2",
        "document_id": "object_memory:mem-medicine-2",
        "status": "reliable",
        "reasons": [
          "reliable_execution_memory"
        ],
        "needs_exploratory_search": false,
        "suggested_search_hint": null
      },
      "canonical_metadata": {
        "aliases": [
          "药盒"
        ],
        "anchor_id": "anchor_living_side_table_1",
        "anchor_type": "table",
        "belief_state": "confirmed",
        "confidence_level": "medium",
        "display_text": "客厅边桌",
        "document_text_hash": "38d0d78232705568b63372822d9e58966aa1e537a70e699fb34f03007fa2a45f",
        "last_confirmed_at": "2026-04-17T09:00:00Z",
        "memory_id": "mem-medicine-2",
        "object_category": "medicine_box",
        "room_id": "living_room",
        "source_type": "object_memory",
        "viewpoint_id": "living_side_table_viewpoint"
      }
    },
    "executable": true,
    "invalid_reason": null
  },
  "rejected_hits": [
    {
      "document_id": "object_memory:mem-medicine-1",
      "source_type": "object_memory",
      "memory_id": "mem-medicine-1",
      "object_category": "medicine_box",
      "aliases": [
        "药盒",
        "药箱"
      ],
      "room_id": "kitchen",
      "anchor_id": "anchor_kitchen_cabinet_1",
      "anchor_type": "cabinet",
      "display_text": "厨房药柜",
      "viewpoint_id": "kitchen_cabinet_viewpoint",
      "confidence_level": "high",
      "belief_state": "confirmed",
      "last_confirmed_at": "2026-04-19T09:00:00Z",
      "text_snippet": "物体记忆。目标类别: medicine_box。目标类别别名: 药盒、药箱、medicine_box、medicine。别名: 药盒、药箱。历史位置: 厨房药柜。房间: kitchen。房间别名: 厨房、kitchen。锚点类型: cabinet。锚点别名: 柜子、药柜、cabinet。可观察视角: kitchen_cabinet_viewpoint。置信度: high。记忆状态: confirmed。最近确认时间: 2026-04-19T09:00:00Z。",
      "bm25_score": 0.31477493047714233,
      "dense_score": 0.691463205593678,
      "metadata_score": 0.30000000000000004,
      "final_score": 0.33225806451612905,
      "ranking_reasons": [
        "bm25_rank=2",
        "dense_rank=2",
        "metadata_target_alias_match",
        "metadata_high_confidence"
      ],
      "canonical_metadata": {
        "aliases": [
          "药盒",
          "药箱"
        ],
        "anchor_id": "anchor_kitchen_cabinet_1",
        "anchor_type": "cabinet",
        "belief_state": "confirmed",
        "confidence_level": "high",
        "display_text": "厨房药柜",
        "document_text_hash": "d7f6ccb8e3d5a8f30955143466a7e652e071244ce067bad2b3aa441e4c3601e0",
        "last_confirmed_at": "2026-04-19T09:00:00Z",
        "memory_id": "mem-medicine-1",
        "object_category": "medicine_box",
        "room_id": "kitchen",
        "source_type": "object_memory",
        "viewpoint_id": "kitchen_cabinet_viewpoint"
      },
      "executable": true,
      "invalid_reason": null,
      "ranking_stage": "bm25_dense_fusion",
      "rerank_score": null,
      "reranker_model": null
    }
  ],
  "runtime_state_summary": {
    "grounding_status": "grounded",
    "grounding_reason": "selected first reliable executable memory hit",
    "needs_exploratory_search": false
  },
  "world_summary": {
    "room_ids": [
      "kitchen",
      "living_room"
    ],
    "viewpoint_ids": [
      "kitchen_cabinet_viewpoint",
      "living_side_table_viewpoint"
    ],
    "anchors": [
      {
        "anchor_id": "anchor_kitchen_cabinet_1",
        "room_id": "kitchen",
        "viewpoint_id": "kitchen_cabinet_viewpoint",
        "display_text": "厨房药柜"
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
