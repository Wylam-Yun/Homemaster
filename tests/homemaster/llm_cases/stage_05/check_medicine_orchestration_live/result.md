# Stage 05 Skill Execution Loop - check_medicine_orchestration_live

Status: PASS

## Expected Conditions

```json
{
  "stage_04_case": "ground_medicine_target",
  "expected_behavior": "查看药盒，不生成拿取/交付动作"
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
      "document_id": "object_memory:mem-medicine-2",
      "final_score": 0.2827868852459016,
      "ranking_reasons": [
        "bm25_rank=1",
        "dense_rank=1",
        "metadata_target_alias_match",
        "metadata_medium_confidence"
      ],
      "reliability": {
        "document_id": "object_memory:mem-medicine-2",
        "memory_id": "mem-medicine-2",
        "needs_exploratory_search": false,
        "reasons": [
          "reliable_execution_memory"
        ],
        "status": "reliable",
        "suggested_search_hint": null
      },
      "source": "canonical_metadata"
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
    "grounding_reason": "selected first reliable executable memory hit",
    "grounding_status": "grounded",
    "needs_exploratory_search": false
  },
  "world_summary": {
    "anchors": [
      {
        "anchor_id": "anchor_kitchen_cabinet_1",
        "display_text": "厨房药柜",
        "room_id": "kitchen",
        "viewpoint_id": "kitchen_cabinet_viewpoint"
      },
      {
        "anchor_id": "anchor_living_side_table_1",
        "display_text": "客厅边桌",
        "room_id": "living_room",
        "viewpoint_id": "living_side_table_viewpoint"
      }
    ],
    "room_ids": [
      "kitchen",
      "living_room"
    ],
    "viewpoint_ids": [
      "kitchen_cabinet_viewpoint",
      "living_side_table_viewpoint"
    ]
  },
  "planning_notes": [
    "grounded reliable memory target is available for Stage 05 planning"
  ]
}
```

## Orchestration Prompt And Raw Response

```text
你是 HomeMaster V1.2 的 Stage05 高层子任务编排组件。

目标：根据 PlanningContext 生成一个 OrchestrationPlan JSON。
你只负责拆成高层子任务 intent，不负责选择 skill，不负责导航或操作执行。

必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。

OrchestrationPlan schema:
{
  "goal": "非空字符串，整体任务目标",
  "subtasks": [
    {
      "id": "稳定、唯一、非空字符串",
      "intent": "高层子任务意图，例如 找到水杯 / 拿起水杯 / 回到用户位置 / 放下水杯",
      "target_object": "字符串或 null",
      "recipient": "字符串或 null",
      "room_hint": "字符串或 null",
      "anchor_hint": "字符串或 null",
      "success_criteria": ["至少一个可验证完成条件"],
      "depends_on": ["依赖的 subtask id"]
    }
  ],
  "confidence": 0.0
}

边界:
- 高层 subtask 不写 selected_skill、skill、module、navigation、operation 或 verification。
- 不输出 selected_target、memory_id、candidate_id、selected_candidate_id、switch_candidate。
- 不伪造 PlanningContext 中没有的 memory target。
- 如果 PlanningContext.selected_target 为 null，不要假装有可靠记忆；先规划探索/寻找/观察或追问。
- 如果任务是取物交付给用户，通常需要覆盖：
  找到目标物、拿起目标物、回到用户位置、放下/交付目标物、确认完成。
- 找用户首版使用 runtime 里记录的 user_location，不新增 find_user skill。
- 可以用自然语言描述子任务，不要把原子动作当作独立执行接口。

PlanningContext:
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
      "document_id": "object_memory:mem-medicine-2",
      "final_score": 0.2827868852459016,
      "ranking_reasons": [
        "bm25_rank=1",
        "dense_rank=1",
        "metadata_target_alias_match",
        "metadata_medium_confidence"
      ],
      "reliability": {
        "document_id": "object_memory:mem-medicine-2",
        "memory_id": "mem-medicine-2",
        "needs_exploratory_search": false,
        "reasons": [
          "reliable_execution_memory"
        ],
        "status": "reliable",
        "suggested_search_hint": null
      },
      "source": "canonical_metadata"
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
    "grounding_reason": "selected first reliable executable memory hit",
    "grounding_status": "grounded",
    "needs_exploratory_search": false
  },
  "world_summary": {
    "anchors": [
      {
        "anchor_id": "anchor_kitchen_cabinet_1",
        "display_text": "厨房药柜",
        "room_id": "kitchen",
        "viewpoint_id": "kitchen_cabinet_viewpoint"
      },
      {
        "anchor_id": "anchor_living_side_table_1",
        "display_text": "客厅边桌",
        "room_id": "living_room",
        "viewpoint_id": "living_side_table_viewpoint"
      }
    ],
    "room_ids": [
      "kitchen",
      "living_room"
    ],
    "viewpoint_ids": [
      "kitchen_cabinet_viewpoint",
      "living_side_table_viewpoint"
    ]
  },
  "planning_notes": [
    "grounded reliable memory target is available for Stage 05 planning"
  ]
}

只输出 JSON object:
```

```text
{
  "goal": "检查药盒在客厅边桌的存在",
  "subtasks": [
    {
      "id": "subtask_1",
      "intent": "前往客厅边桌",
      "target_object": null,
      "recipient": null,
      "room_hint": "living_room",
      "anchor_hint": "anchor_living_side_table_1",
      "success_criteria": ["到达客厅边桌附近"],
      "depends_on": []
    },
    {
      "id": "subtask_2",
      "intent": "观察药盒以检查存在",
      "target_object": "药盒",
      "recipient": null,
      "room_hint": "living_room",
      "anchor_hint": "anchor_living_side_table_1",
      "success_criteria": ["确认药盒在客厅边桌上可见"],
      "depends_on": ["subtask_1"]
    }
  ],
  "confidence": 0.9
}
```

## StepDecision / Skill / Verification Trace

```json
{
  "step_decision_prompt": null,
  "step_decision": null,
  "execution": null
}
```

## ExecutionState / Failure Records

```json
{
  "final_state": null,
  "failure_records": null
}
```

## Checks

```json
{
  "has_subtasks": true,
  "no_selected_target": true,
  "no_legacy_candidate": true,
  "subtasks_have_success_criteria": true,
  "no_fetch_or_delivery_operation": true,
  "mentions_medicine": true
}
```

## Full Actual

```json
{
  "case_name": "check_medicine_orchestration_live",
  "passed": true,
  "provider": {
    "provider_name": "Mimo",
    "model": "mimo-v2-pro",
    "protocol": "anthropic",
    "elapsed_ms": 50885.78675000099,
    "attempts": [
      {
        "key_index": 1,
        "status_code": 200,
        "elapsed_ms": 50885.78675000099
      }
    ]
  },
  "planning_context": {
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
        "document_id": "object_memory:mem-medicine-2",
        "final_score": 0.2827868852459016,
        "ranking_reasons": [
          "bm25_rank=1",
          "dense_rank=1",
          "metadata_target_alias_match",
          "metadata_medium_confidence"
        ],
        "reliability": {
          "document_id": "object_memory:mem-medicine-2",
          "memory_id": "mem-medicine-2",
          "needs_exploratory_search": false,
          "reasons": [
            "reliable_execution_memory"
          ],
          "status": "reliable",
          "suggested_search_hint": null
        },
        "source": "canonical_metadata"
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
      "grounding_reason": "selected first reliable executable memory hit",
      "grounding_status": "grounded",
      "needs_exploratory_search": false
    },
    "world_summary": {
      "anchors": [
        {
          "anchor_id": "anchor_kitchen_cabinet_1",
          "display_text": "厨房药柜",
          "room_id": "kitchen",
          "viewpoint_id": "kitchen_cabinet_viewpoint"
        },
        {
          "anchor_id": "anchor_living_side_table_1",
          "display_text": "客厅边桌",
          "room_id": "living_room",
          "viewpoint_id": "living_side_table_viewpoint"
        }
      ],
      "room_ids": [
        "kitchen",
        "living_room"
      ],
      "viewpoint_ids": [
        "kitchen_cabinet_viewpoint",
        "living_side_table_viewpoint"
      ]
    },
    "planning_notes": [
      "grounded reliable memory target is available for Stage 05 planning"
    ]
  },
  "orchestration_prompt": "你是 HomeMaster V1.2 的 Stage05 高层子任务编排组件。\n\n目标：根据 PlanningContext 生成一个 OrchestrationPlan JSON。\n你只负责拆成高层子任务 intent，不负责选择 skill，不负责导航或操作执行。\n\n必须只输出一个 JSON object。\n不要输出 Markdown。\n不要输出解释。\n不要输出代码块。\n不要输出思考过程。\n\nOrchestrationPlan schema:\n{\n  \"goal\": \"非空字符串，整体任务目标\",\n  \"subtasks\": [\n    {\n      \"id\": \"稳定、唯一、非空字符串\",\n      \"intent\": \"高层子任务意图，例如 找到水杯 / 拿起水杯 / 回到用户位置 / 放下水杯\",\n      \"target_object\": \"字符串或 null\",\n      \"recipient\": \"字符串或 null\",\n      \"room_hint\": \"字符串或 null\",\n      \"anchor_hint\": \"字符串或 null\",\n      \"success_criteria\": [\"至少一个可验证完成条件\"],\n      \"depends_on\": [\"依赖的 subtask id\"]\n    }\n  ],\n  \"confidence\": 0.0\n}\n\n边界:\n- 高层 subtask 不写 selected_skill、skill、module、navigation、operation 或 verification。\n- 不输出 selected_target、memory_id、candidate_id、selected_candidate_id、switch_candidate。\n- 不伪造 PlanningContext 中没有的 memory target。\n- 如果 PlanningContext.selected_target 为 null，不要假装有可靠记忆；先规划探索/寻找/观察或追问。\n- 如果任务是取物交付给用户，通常需要覆盖：\n  找到目标物、拿起目标物、回到用户位置、放下/交付目标物、确认完成。\n- 找用户首版使用 runtime 里记录的 user_location，不新增 find_user skill。\n- 可以用自然语言描述子任务，不要把原子动作当作独立执行接口。\n\nPlanningContext:\n{\n  \"task_card\": {\n    \"task_type\": \"check_presence\",\n    \"target\": \"药盒\",\n    \"delivery_target\": null,\n    \"location_hint\": \"桌子那边\",\n    \"success_criteria\": [\n      \"后续观察可以验证任务是否完成\"\n    ],\n    \"needs_clarification\": false,\n    \"clarification_question\": null,\n    \"confidence\": 0.9\n  },\n  \"retrieval_query\": {\n    \"query_text\": \"药盒，药物盒，药箱，pill box，桌子那边\",\n    \"target_category\": null,\n    \"target_aliases\": [\n      \"药盒\",\n      \"药物盒\",\n      \"药箱\"\n    ],\n    \"location_terms\": [\n      \"桌子那边\"\n    ],\n    \"source_filter\": [\n      \"object_memory\"\n    ],\n    \"top_k\": 5,\n    \"excluded_memory_ids\": [],\n    \"excluded_location_keys\": [],\n    \"reason\": null\n  },\n  \"memory_evidence\": {\n    \"hits\": [\n      {\n        \"document_id\": \"object_memory:mem-medicine-1\",\n        \"source_type\": \"object_memory\",\n        \"memory_id\": \"mem-medicine-1\",\n        \"object_category\": \"medicine_box\",\n        \"aliases\": [\n          \"药盒\",\n          \"药箱\"\n        ],\n        \"room_id\": \"kitchen\",\n        \"anchor_id\": \"anchor_kitchen_cabinet_1\",\n        \"anchor_type\": \"cabinet\",\n        \"display_text\": \"厨房药柜\",\n        \"viewpoint_id\": \"kitchen_cabinet_viewpoint\",\n        \"confidence_level\": \"high\",\n        \"belief_state\": \"confirmed\",\n        \"last_confirmed_at\": \"2026-04-19T09:00:00Z\",\n        \"text_snippet\": \"物体记忆。目标类别: medicine_box。目标类别别名: 药盒、药箱、medicine_box、medicine。别名: 药盒、药箱。历史位置: 厨房药柜。房间: kitchen。房间别名: 厨房、kitchen。锚点类型: cabinet。锚点别名: 柜子、药柜、cabinet。可观察视角: kitchen_cabinet_viewpoint。置信度: high。记忆状态: confirmed。最近确认时间: 2026-04-19T09:00:00Z。\",\n        \"bm25_score\": 0.31477493047714233,\n        \"dense_score\": 0.691463205593678,\n        \"metadata_score\": 0.30000000000000004,\n        \"final_score\": 0.33225806451612905,\n        \"ranking_reasons\": [\n          \"bm25_rank=2\",\n          \"dense_rank=2\",\n          \"metadata_target_alias_match\",\n          \"metadata_high_confidence\"\n        ],\n        \"canonical_metadata\": {\n          \"aliases\": [\n            \"药盒\",\n            \"药箱\"\n          ],\n          \"anchor_id\": \"anchor_kitchen_cabinet_1\",\n          \"anchor_type\": \"cabinet\",\n          \"belief_state\": \"confirmed\",\n          \"confidence_level\": \"high\",\n          \"display_text\": \"厨房药柜\",\n          \"document_text_hash\": \"d7f6ccb8e3d5a8f30955143466a7e652e071244ce067bad2b3aa441e4c3601e0\",\n          \"last_confirmed_at\": \"2026-04-19T09:00:00Z\",\n          \"memory_id\": \"mem-medicine-1\",\n          \"object_category\": \"medicine_box\",\n          \"room_id\": \"kitchen\",\n          \"source_type\": \"object_memory\",\n          \"viewpoint_id\": \"kitchen_cabinet_viewpoint\"\n        },\n        \"executable\": true,\n        \"invalid_reason\": null,\n        \"ranking_stage\": \"bm25_dense_fusion\",\n        \"rerank_score\": null,\n        \"reranker_model\": null\n      },\n      {\n        \"document_id\": \"object_memory:mem-medicine-2\",\n        \"source_type\": \"object_memory\",\n        \"memory_id\": \"mem-medicine-2\",\n        \"object_category\": \"medicine_box\",\n        \"aliases\": [\n          \"药盒\"\n        ],\n        \"room_id\": \"living_room\",\n        \"anchor_id\": \"anchor_living_side_table_1\",\n        \"anchor_type\": \"table\",\n        \"display_text\": \"客厅边桌\",\n        \"viewpoint_id\": \"living_side_table_viewpoint\",\n        \"confidence_level\": \"medium\",\n        \"belief_state\": \"confirmed\",\n        \"last_confirmed_at\": \"2026-04-17T09:00:00Z\",\n        \"text_snippet\": \"物体记忆。目标类别: medicine_box。目标类别别名: 药盒、药箱、medicine_box、medicine。别名: 药盒。历史位置: 客厅边桌。房间: living_room。房间别名: 客厅、living room、living_room。锚点类型: table。锚点别名: 桌子、餐桌、table。可观察视角: living_side_table_viewpoint。置信度: medium。记忆状态: confirmed。最近确认时间: 2026-04-17T09:00:00Z。\",\n        \"bm25_score\": 0.5536638498306274,\n        \"dense_score\": 0.7041529775162785,\n        \"metadata_score\": 0.25,\n        \"final_score\": 0.2827868852459016,\n        \"ranking_reasons\": [\n          \"bm25_rank=1\",\n          \"dense_rank=1\",\n          \"metadata_target_alias_match\",\n          \"metadata_medium_confidence\"\n        ],\n        \"canonical_metadata\": {\n          \"aliases\": [\n            \"药盒\"\n          ],\n          \"anchor_id\": \"anchor_living_side_table_1\",\n          \"anchor_type\": \"table\",\n          \"belief_state\": \"confirmed\",\n          \"confidence_level\": \"medium\",\n          \"display_text\": \"客厅边桌\",\n          \"document_text_hash\": \"38d0d78232705568b63372822d9e58966aa1e537a70e699fb34f03007fa2a45f\",\n          \"last_confirmed_at\": \"2026-04-17T09:00:00Z\",\n          \"memory_id\": \"mem-medicine-2\",\n          \"object_category\": \"medicine_box\",\n          \"room_id\": \"living_room\",\n          \"source_type\": \"object_memory\",\n          \"viewpoint_id\": \"living_side_table_viewpoint\"\n        },\n        \"executable\": true,\n        \"invalid_reason\": null,\n        \"ranking_stage\": \"bm25_dense_fusion\",\n        \"rerank_score\": null,\n        \"reranker_model\": null\n      }\n    ],\n    \"excluded\": [],\n    \"retrieval_query\": {\n      \"query_text\": \"药盒，药物盒，药箱，pill box，桌子那边\",\n      \"target_category\": null,\n      \"target_aliases\": [\n        \"药盒\",\n        \"药物盒\",\n        \"药箱\"\n      ],\n      \"location_terms\": [\n        \"桌子那边\"\n      ],\n      \"source_filter\": [\n        \"object_memory\"\n      ],\n      \"top_k\": 5,\n      \"excluded_memory_ids\": [],\n      \"excluded_location_keys\": [],\n      \"reason\": null\n    },\n    \"ranking_reasons\": [\n      \"bm25_dense_rrf_fusion\",\n      \"metadata_guardrail\"\n    ],\n    \"retrieval_summary\": \"returned 2 hits and 0 excluded\",\n    \"embedding_provider\": {\n      \"endpoint\": \"https://api.siliconflow.cn/v1/embeddings\",\n      \"model\": \"BAAI/bge-m3\",\n      \"provider_name\": \"MemoryEmbedding\"\n    },\n    \"index_snapshot\": {\n      \"document_count\": 2,\n      \"domain_terms\": [\n        \"medicine_box\",\n        \"药盒\",\n        \"药箱\",\n        \"medicine\",\n        \"kitchen\",\n        \"anchor_kitchen_cabinet_1\",\n        \"cabinet\",\n        \"kitchen_cabinet_viewpoint\",\n        \"厨房药柜\",\n        \"厨房\",\n        \"柜子\",\n        \"药柜\",\n        \"living_room\",\n        \"anchor_living_side_table_1\",\n        \"table\",\n        \"living_side_table_viewpoint\",\n        \"客厅边桌\",\n        \"客厅\",\n        \"living room\",\n        \"桌子\",\n        \"餐桌\"\n      ],\n      \"ranking_stage\": \"bm25_dense_fusion\",\n      \"tokenized_query\": \"[REDACTED]\"\n    }\n  },\n  \"selected_target\": {\n    \"memory_id\": \"mem-medicine-2\",\n    \"room_id\": \"living_room\",\n    \"anchor_id\": \"anchor_living_side_table_1\",\n    \"viewpoint_id\": \"living_side_table_viewpoint\",\n    \"display_text\": \"客厅边桌\",\n    \"evidence\": {\n      \"canonical_metadata\": {\n        \"aliases\": [\n          \"药盒\"\n        ],\n        \"anchor_id\": \"anchor_living_side_table_1\",\n        \"anchor_type\": \"table\",\n        \"belief_state\": \"confirmed\",\n        \"confidence_level\": \"medium\",\n        \"display_text\": \"客厅边桌\",\n        \"document_text_hash\": \"38d0d78232705568b63372822d9e58966aa1e537a70e699fb34f03007fa2a45f\",\n        \"last_confirmed_at\": \"2026-04-17T09:00:00Z\",\n        \"memory_id\": \"mem-medicine-2\",\n        \"object_category\": \"medicine_box\",\n        \"room_id\": \"living_room\",\n        \"source_type\": \"object_memory\",\n        \"viewpoint_id\": \"living_side_table_viewpoint\"\n      },\n      \"document_id\": \"object_memory:mem-medicine-2\",\n      \"final_score\": 0.2827868852459016,\n      \"ranking_reasons\": [\n        \"bm25_rank=1\",\n        \"dense_rank=1\",\n        \"metadata_target_alias_match\",\n        \"metadata_medium_confidence\"\n      ],\n      \"reliability\": {\n        \"document_id\": \"object_memory:mem-medicine-2\",\n        \"memory_id\": \"mem-medicine-2\",\n        \"needs_exploratory_search\": false,\n        \"reasons\": [\n          \"reliable_execution_memory\"\n        ],\n        \"status\": \"reliable\",\n        \"suggested_search_hint\": null\n      },\n      \"source\": \"canonical_metadata\"\n    },\n    \"executable\": true,\n    \"invalid_reason\": null\n  },\n  \"rejected_hits\": [\n    {\n      \"document_id\": \"object_memory:mem-medicine-1\",\n      \"source_type\": \"object_memory\",\n      \"memory_id\": \"mem-medicine-1\",\n      \"object_category\": \"medicine_box\",\n      \"aliases\": [\n        \"药盒\",\n        \"药箱\"\n      ],\n      \"room_id\": \"kitchen\",\n      \"anchor_id\": \"anchor_kitchen_cabinet_1\",\n      \"anchor_type\": \"cabinet\",\n      \"display_text\": \"厨房药柜\",\n      \"viewpoint_id\": \"kitchen_cabinet_viewpoint\",\n      \"confidence_level\": \"high\",\n      \"belief_state\": \"confirmed\",\n      \"last_confirmed_at\": \"2026-04-19T09:00:00Z\",\n      \"text_snippet\": \"物体记忆。目标类别: medicine_box。目标类别别名: 药盒、药箱、medicine_box、medicine。别名: 药盒、药箱。历史位置: 厨房药柜。房间: kitchen。房间别名: 厨房、kitchen。锚点类型: cabinet。锚点别名: 柜子、药柜、cabinet。可观察视角: kitchen_cabinet_viewpoint。置信度: high。记忆状态: confirmed。最近确认时间: 2026-04-19T09:00:00Z。\",\n      \"bm25_score\": 0.31477493047714233,\n      \"dense_score\": 0.691463205593678,\n      \"metadata_score\": 0.30000000000000004,\n      \"final_score\": 0.33225806451612905,\n      \"ranking_reasons\": [\n        \"bm25_rank=2\",\n        \"dense_rank=2\",\n        \"metadata_target_alias_match\",\n        \"metadata_high_confidence\"\n      ],\n      \"canonical_metadata\": {\n        \"aliases\": [\n          \"药盒\",\n          \"药箱\"\n        ],\n        \"anchor_id\": \"anchor_kitchen_cabinet_1\",\n        \"anchor_type\": \"cabinet\",\n        \"belief_state\": \"confirmed\",\n        \"confidence_level\": \"high\",\n        \"display_text\": \"厨房药柜\",\n        \"document_text_hash\": \"d7f6ccb8e3d5a8f30955143466a7e652e071244ce067bad2b3aa441e4c3601e0\",\n        \"last_confirmed_at\": \"2026-04-19T09:00:00Z\",\n        \"memory_id\": \"mem-medicine-1\",\n        \"object_category\": \"medicine_box\",\n        \"room_id\": \"kitchen\",\n        \"source_type\": \"object_memory\",\n        \"viewpoint_id\": \"kitchen_cabinet_viewpoint\"\n      },\n      \"executable\": true,\n      \"invalid_reason\": null,\n      \"ranking_stage\": \"bm25_dense_fusion\",\n      \"rerank_score\": null,\n      \"reranker_model\": null\n    }\n  ],\n  \"runtime_state_summary\": {\n    \"grounding_reason\": \"selected first reliable executable memory hit\",\n    \"grounding_status\": \"grounded\",\n    \"needs_exploratory_search\": false\n  },\n  \"world_summary\": {\n    \"anchors\": [\n      {\n        \"anchor_id\": \"anchor_kitchen_cabinet_1\",\n        \"display_text\": \"厨房药柜\",\n        \"room_id\": \"kitchen\",\n        \"viewpoint_id\": \"kitchen_cabinet_viewpoint\"\n      },\n      {\n        \"anchor_id\": \"anchor_living_side_table_1\",\n        \"display_text\": \"客厅边桌\",\n        \"room_id\": \"living_room\",\n        \"viewpoint_id\": \"living_side_table_viewpoint\"\n      }\n    ],\n    \"room_ids\": [\n      \"kitchen\",\n      \"living_room\"\n    ],\n    \"viewpoint_ids\": [\n      \"kitchen_cabinet_viewpoint\",\n      \"living_side_table_viewpoint\"\n    ]\n  },\n  \"planning_notes\": [\n    \"grounded reliable memory target is available for Stage 05 planning\"\n  ]\n}\n\n只输出 JSON object:",
  "raw_response": "{\n  \"goal\": \"检查药盒在客厅边桌的存在\",\n  \"subtasks\": [\n    {\n      \"id\": \"subtask_1\",\n      \"intent\": \"前往客厅边桌\",\n      \"target_object\": null,\n      \"recipient\": null,\n      \"room_hint\": \"living_room\",\n      \"anchor_hint\": \"anchor_living_side_table_1\",\n      \"success_criteria\": [\"到达客厅边桌附近\"],\n      \"depends_on\": []\n    },\n    {\n      \"id\": \"subtask_2\",\n      \"intent\": \"观察药盒以检查存在\",\n      \"target_object\": \"药盒\",\n      \"recipient\": null,\n      \"room_hint\": \"living_room\",\n      \"anchor_hint\": \"anchor_living_side_table_1\",\n      \"success_criteria\": [\"确认药盒在客厅边桌上可见\"],\n      \"depends_on\": [\"subtask_1\"]\n    }\n  ],\n  \"confidence\": 0.9\n}",
  "parsed_json": {
    "goal": "检查药盒在客厅边桌的存在",
    "subtasks": [
      {
        "id": "subtask_1",
        "intent": "前往客厅边桌",
        "target_object": null,
        "recipient": null,
        "room_hint": "living_room",
        "anchor_hint": "anchor_living_side_table_1",
        "success_criteria": [
          "到达客厅边桌附近"
        ],
        "depends_on": []
      },
      {
        "id": "subtask_2",
        "intent": "观察药盒以检查存在",
        "target_object": "药盒",
        "recipient": null,
        "room_hint": "living_room",
        "anchor_hint": "anchor_living_side_table_1",
        "success_criteria": [
          "确认药盒在客厅边桌上可见"
        ],
        "depends_on": [
          "subtask_1"
        ]
      }
    ],
    "confidence": 0.9
  },
  "orchestration_plan": {
    "goal": "检查药盒在客厅边桌的存在",
    "subtasks": [
      {
        "id": "subtask_1",
        "intent": "前往客厅边桌",
        "target_object": null,
        "recipient": null,
        "room_hint": "living_room",
        "anchor_hint": "anchor_living_side_table_1",
        "success_criteria": [
          "到达客厅边桌附近"
        ],
        "depends_on": []
      },
      {
        "id": "subtask_2",
        "intent": "观察药盒以检查存在",
        "target_object": "药盒",
        "recipient": null,
        "room_hint": "living_room",
        "anchor_hint": "anchor_living_side_table_1",
        "success_criteria": [
          "确认药盒在客厅边桌上可见"
        ],
        "depends_on": [
          "subtask_1"
        ]
      }
    ],
    "confidence": 0.9
  },
  "attempts": [
    {
      "attempt": 1,
      "prompt": "你是 HomeMaster V1.2 的 Stage05 高层子任务编排组件。\n\n目标：根据 PlanningContext 生成一个 OrchestrationPlan JSON。\n你只负责拆成高层子任务 intent，不负责选择 skill，不负责导航或操作执行。\n\n必须只输出一个 JSON object。\n不要输出 Markdown。\n不要输出解释。\n不要输出代码块。\n不要输出思考过程。\n\nOrchestrationPlan schema:\n{\n  \"goal\": \"非空字符串，整体任务目标\",\n  \"subtasks\": [\n    {\n      \"id\": \"稳定、唯一、非空字符串\",\n      \"intent\": \"高层子任务意图，例如 找到水杯 / 拿起水杯 / 回到用户位置 / 放下水杯\",\n      \"target_object\": \"字符串或 null\",\n      \"recipient\": \"字符串或 null\",\n      \"room_hint\": \"字符串或 null\",\n      \"anchor_hint\": \"字符串或 null\",\n      \"success_criteria\": [\"至少一个可验证完成条件\"],\n      \"depends_on\": [\"依赖的 subtask id\"]\n    }\n  ],\n  \"confidence\": 0.0\n}\n\n边界:\n- 高层 subtask 不写 selected_skill、skill、module、navigation、operation 或 verification。\n- 不输出 selected_target、memory_id、candidate_id、selected_candidate_id、switch_candidate。\n- 不伪造 PlanningContext 中没有的 memory target。\n- 如果 PlanningContext.selected_target 为 null，不要假装有可靠记忆；先规划探索/寻找/观察或追问。\n- 如果任务是取物交付给用户，通常需要覆盖：\n  找到目标物、拿起目标物、回到用户位置、放下/交付目标物、确认完成。\n- 找用户首版使用 runtime 里记录的 user_location，不新增 find_user skill。\n- 可以用自然语言描述子任务，不要把原子动作当作独立执行接口。\n\nPlanningContext:\n{\n  \"task_card\": {\n    \"task_type\": \"check_presence\",\n    \"target\": \"药盒\",\n    \"delivery_target\": null,\n    \"location_hint\": \"桌子那边\",\n    \"success_criteria\": [\n      \"后续观察可以验证任务是否完成\"\n    ],\n    \"needs_clarification\": false,\n    \"clarification_question\": null,\n    \"confidence\": 0.9\n  },\n  \"retrieval_query\": {\n    \"query_text\": \"药盒，药物盒，药箱，pill box，桌子那边\",\n    \"target_category\": null,\n    \"target_aliases\": [\n      \"药盒\",\n      \"药物盒\",\n      \"药箱\"\n    ],\n    \"location_terms\": [\n      \"桌子那边\"\n    ],\n    \"source_filter\": [\n      \"object_memory\"\n    ],\n    \"top_k\": 5,\n    \"excluded_memory_ids\": [],\n    \"excluded_location_keys\": [],\n    \"reason\": null\n  },\n  \"memory_evidence\": {\n    \"hits\": [\n      {\n        \"document_id\": \"object_memory:mem-medicine-1\",\n        \"source_type\": \"object_memory\",\n        \"memory_id\": \"mem-medicine-1\",\n        \"object_category\": \"medicine_box\",\n        \"aliases\": [\n          \"药盒\",\n          \"药箱\"\n        ],\n        \"room_id\": \"kitchen\",\n        \"anchor_id\": \"anchor_kitchen_cabinet_1\",\n        \"anchor_type\": \"cabinet\",\n        \"display_text\": \"厨房药柜\",\n        \"viewpoint_id\": \"kitchen_cabinet_viewpoint\",\n        \"confidence_level\": \"high\",\n        \"belief_state\": \"confirmed\",\n        \"last_confirmed_at\": \"2026-04-19T09:00:00Z\",\n        \"text_snippet\": \"物体记忆。目标类别: medicine_box。目标类别别名: 药盒、药箱、medicine_box、medicine。别名: 药盒、药箱。历史位置: 厨房药柜。房间: kitchen。房间别名: 厨房、kitchen。锚点类型: cabinet。锚点别名: 柜子、药柜、cabinet。可观察视角: kitchen_cabinet_viewpoint。置信度: high。记忆状态: confirmed。最近确认时间: 2026-04-19T09:00:00Z。\",\n        \"bm25_score\": 0.31477493047714233,\n        \"dense_score\": 0.691463205593678,\n        \"metadata_score\": 0.30000000000000004,\n        \"final_score\": 0.33225806451612905,\n        \"ranking_reasons\": [\n          \"bm25_rank=2\",\n          \"dense_rank=2\",\n          \"metadata_target_alias_match\",\n          \"metadata_high_confidence\"\n        ],\n        \"canonical_metadata\": {\n          \"aliases\": [\n            \"药盒\",\n            \"药箱\"\n          ],\n          \"anchor_id\": \"anchor_kitchen_cabinet_1\",\n          \"anchor_type\": \"cabinet\",\n          \"belief_state\": \"confirmed\",\n          \"confidence_level\": \"high\",\n          \"display_text\": \"厨房药柜\",\n          \"document_text_hash\": \"d7f6ccb8e3d5a8f30955143466a7e652e071244ce067bad2b3aa441e4c3601e0\",\n          \"last_confirmed_at\": \"2026-04-19T09:00:00Z\",\n          \"memory_id\": \"mem-medicine-1\",\n          \"object_category\": \"medicine_box\",\n          \"room_id\": \"kitchen\",\n          \"source_type\": \"object_memory\",\n          \"viewpoint_id\": \"kitchen_cabinet_viewpoint\"\n        },\n        \"executable\": true,\n        \"invalid_reason\": null,\n        \"ranking_stage\": \"bm25_dense_fusion\",\n        \"rerank_score\": null,\n        \"reranker_model\": null\n      },\n      {\n        \"document_id\": \"object_memory:mem-medicine-2\",\n        \"source_type\": \"object_memory\",\n        \"memory_id\": \"mem-medicine-2\",\n        \"object_category\": \"medicine_box\",\n        \"aliases\": [\n          \"药盒\"\n        ],\n        \"room_id\": \"living_room\",\n        \"anchor_id\": \"anchor_living_side_table_1\",\n        \"anchor_type\": \"table\",\n        \"display_text\": \"客厅边桌\",\n        \"viewpoint_id\": \"living_side_table_viewpoint\",\n        \"confidence_level\": \"medium\",\n        \"belief_state\": \"confirmed\",\n        \"last_confirmed_at\": \"2026-04-17T09:00:00Z\",\n        \"text_snippet\": \"物体记忆。目标类别: medicine_box。目标类别别名: 药盒、药箱、medicine_box、medicine。别名: 药盒。历史位置: 客厅边桌。房间: living_room。房间别名: 客厅、living room、living_room。锚点类型: table。锚点别名: 桌子、餐桌、table。可观察视角: living_side_table_viewpoint。置信度: medium。记忆状态: confirmed。最近确认时间: 2026-04-17T09:00:00Z。\",\n        \"bm25_score\": 0.5536638498306274,\n        \"dense_score\": 0.7041529775162785,\n        \"metadata_score\": 0.25,\n        \"final_score\": 0.2827868852459016,\n        \"ranking_reasons\": [\n          \"bm25_rank=1\",\n          \"dense_rank=1\",\n          \"metadata_target_alias_match\",\n          \"metadata_medium_confidence\"\n        ],\n        \"canonical_metadata\": {\n          \"aliases\": [\n            \"药盒\"\n          ],\n          \"anchor_id\": \"anchor_living_side_table_1\",\n          \"anchor_type\": \"table\",\n          \"belief_state\": \"confirmed\",\n          \"confidence_level\": \"medium\",\n          \"display_text\": \"客厅边桌\",\n          \"document_text_hash\": \"38d0d78232705568b63372822d9e58966aa1e537a70e699fb34f03007fa2a45f\",\n          \"last_confirmed_at\": \"2026-04-17T09:00:00Z\",\n          \"memory_id\": \"mem-medicine-2\",\n          \"object_category\": \"medicine_box\",\n          \"room_id\": \"living_room\",\n          \"source_type\": \"object_memory\",\n          \"viewpoint_id\": \"living_side_table_viewpoint\"\n        },\n        \"executable\": true,\n        \"invalid_reason\": null,\n        \"ranking_stage\": \"bm25_dense_fusion\",\n        \"rerank_score\": null,\n        \"reranker_model\": null\n      }\n    ],\n    \"excluded\": [],\n    \"retrieval_query\": {\n      \"query_text\": \"药盒，药物盒，药箱，pill box，桌子那边\",\n      \"target_category\": null,\n      \"target_aliases\": [\n        \"药盒\",\n        \"药物盒\",\n        \"药箱\"\n      ],\n      \"location_terms\": [\n        \"桌子那边\"\n      ],\n      \"source_filter\": [\n        \"object_memory\"\n      ],\n      \"top_k\": 5,\n      \"excluded_memory_ids\": [],\n      \"excluded_location_keys\": [],\n      \"reason\": null\n    },\n    \"ranking_reasons\": [\n      \"bm25_dense_rrf_fusion\",\n      \"metadata_guardrail\"\n    ],\n    \"retrieval_summary\": \"returned 2 hits and 0 excluded\",\n    \"embedding_provider\": {\n      \"endpoint\": \"https://api.siliconflow.cn/v1/embeddings\",\n      \"model\": \"BAAI/bge-m3\",\n      \"provider_name\": \"MemoryEmbedding\"\n    },\n    \"index_snapshot\": {\n      \"document_count\": 2,\n      \"domain_terms\": [\n        \"medicine_box\",\n        \"药盒\",\n        \"药箱\",\n        \"medicine\",\n        \"kitchen\",\n        \"anchor_kitchen_cabinet_1\",\n        \"cabinet\",\n        \"kitchen_cabinet_viewpoint\",\n        \"厨房药柜\",\n        \"厨房\",\n        \"柜子\",\n        \"药柜\",\n        \"living_room\",\n        \"anchor_living_side_table_1\",\n        \"table\",\n        \"living_side_table_viewpoint\",\n        \"客厅边桌\",\n        \"客厅\",\n        \"living room\",\n        \"桌子\",\n        \"餐桌\"\n      ],\n      \"ranking_stage\": \"bm25_dense_fusion\",\n      \"tokenized_query\": \"[REDACTED]\"\n    }\n  },\n  \"selected_target\": {\n    \"memory_id\": \"mem-medicine-2\",\n    \"room_id\": \"living_room\",\n    \"anchor_id\": \"anchor_living_side_table_1\",\n    \"viewpoint_id\": \"living_side_table_viewpoint\",\n    \"display_text\": \"客厅边桌\",\n    \"evidence\": {\n      \"canonical_metadata\": {\n        \"aliases\": [\n          \"药盒\"\n        ],\n        \"anchor_id\": \"anchor_living_side_table_1\",\n        \"anchor_type\": \"table\",\n        \"belief_state\": \"confirmed\",\n        \"confidence_level\": \"medium\",\n        \"display_text\": \"客厅边桌\",\n        \"document_text_hash\": \"38d0d78232705568b63372822d9e58966aa1e537a70e699fb34f03007fa2a45f\",\n        \"last_confirmed_at\": \"2026-04-17T09:00:00Z\",\n        \"memory_id\": \"mem-medicine-2\",\n        \"object_category\": \"medicine_box\",\n        \"room_id\": \"living_room\",\n        \"source_type\": \"object_memory\",\n        \"viewpoint_id\": \"living_side_table_viewpoint\"\n      },\n      \"document_id\": \"object_memory:mem-medicine-2\",\n      \"final_score\": 0.2827868852459016,\n      \"ranking_reasons\": [\n        \"bm25_rank=1\",\n        \"dense_rank=1\",\n        \"metadata_target_alias_match\",\n        \"metadata_medium_confidence\"\n      ],\n      \"reliability\": {\n        \"document_id\": \"object_memory:mem-medicine-2\",\n        \"memory_id\": \"mem-medicine-2\",\n        \"needs_exploratory_search\": false,\n        \"reasons\": [\n          \"reliable_execution_memory\"\n        ],\n        \"status\": \"reliable\",\n        \"suggested_search_hint\": null\n      },\n      \"source\": \"canonical_metadata\"\n    },\n    \"executable\": true,\n    \"invalid_reason\": null\n  },\n  \"rejected_hits\": [\n    {\n      \"document_id\": \"object_memory:mem-medicine-1\",\n      \"source_type\": \"object_memory\",\n      \"memory_id\": \"mem-medicine-1\",\n      \"object_category\": \"medicine_box\",\n      \"aliases\": [\n        \"药盒\",\n        \"药箱\"\n      ],\n      \"room_id\": \"kitchen\",\n      \"anchor_id\": \"anchor_kitchen_cabinet_1\",\n      \"anchor_type\": \"cabinet\",\n      \"display_text\": \"厨房药柜\",\n      \"viewpoint_id\": \"kitchen_cabinet_viewpoint\",\n      \"confidence_level\": \"high\",\n      \"belief_state\": \"confirmed\",\n      \"last_confirmed_at\": \"2026-04-19T09:00:00Z\",\n      \"text_snippet\": \"物体记忆。目标类别: medicine_box。目标类别别名: 药盒、药箱、medicine_box、medicine。别名: 药盒、药箱。历史位置: 厨房药柜。房间: kitchen。房间别名: 厨房、kitchen。锚点类型: cabinet。锚点别名: 柜子、药柜、cabinet。可观察视角: kitchen_cabinet_viewpoint。置信度: high。记忆状态: confirmed。最近确认时间: 2026-04-19T09:00:00Z。\",\n      \"bm25_score\": 0.31477493047714233,\n      \"dense_score\": 0.691463205593678,\n      \"metadata_score\": 0.30000000000000004,\n      \"final_score\": 0.33225806451612905,\n      \"ranking_reasons\": [\n        \"bm25_rank=2\",\n        \"dense_rank=2\",\n        \"metadata_target_alias_match\",\n        \"metadata_high_confidence\"\n      ],\n      \"canonical_metadata\": {\n        \"aliases\": [\n          \"药盒\",\n          \"药箱\"\n        ],\n        \"anchor_id\": \"anchor_kitchen_cabinet_1\",\n        \"anchor_type\": \"cabinet\",\n        \"belief_state\": \"confirmed\",\n        \"confidence_level\": \"high\",\n        \"display_text\": \"厨房药柜\",\n        \"document_text_hash\": \"d7f6ccb8e3d5a8f30955143466a7e652e071244ce067bad2b3aa441e4c3601e0\",\n        \"last_confirmed_at\": \"2026-04-19T09:00:00Z\",\n        \"memory_id\": \"mem-medicine-1\",\n        \"object_category\": \"medicine_box\",\n        \"room_id\": \"kitchen\",\n        \"source_type\": \"object_memory\",\n        \"viewpoint_id\": \"kitchen_cabinet_viewpoint\"\n      },\n      \"executable\": true,\n      \"invalid_reason\": null,\n      \"ranking_stage\": \"bm25_dense_fusion\",\n      \"rerank_score\": null,\n      \"reranker_model\": null\n    }\n  ],\n  \"runtime_state_summary\": {\n    \"grounding_reason\": \"selected first reliable executable memory hit\",\n    \"grounding_status\": \"grounded\",\n    \"needs_exploratory_search\": false\n  },\n  \"world_summary\": {\n    \"anchors\": [\n      {\n        \"anchor_id\": \"anchor_kitchen_cabinet_1\",\n        \"display_text\": \"厨房药柜\",\n        \"room_id\": \"kitchen\",\n        \"viewpoint_id\": \"kitchen_cabinet_viewpoint\"\n      },\n      {\n        \"anchor_id\": \"anchor_living_side_table_1\",\n        \"display_text\": \"客厅边桌\",\n        \"room_id\": \"living_room\",\n        \"viewpoint_id\": \"living_side_table_viewpoint\"\n      }\n    ],\n    \"room_ids\": [\n      \"kitchen\",\n      \"living_room\"\n    ],\n    \"viewpoint_ids\": [\n      \"kitchen_cabinet_viewpoint\",\n      \"living_side_table_viewpoint\"\n    ]\n  },\n  \"planning_notes\": [\n    \"grounded reliable memory target is available for Stage 05 planning\"\n  ]\n}\n\n只输出 JSON object:",
      "passed": true,
      "raw_response": "{\n  \"goal\": \"检查药盒在客厅边桌的存在\",\n  \"subtasks\": [\n    {\n      \"id\": \"subtask_1\",\n      \"intent\": \"前往客厅边桌\",\n      \"target_object\": null,\n      \"recipient\": null,\n      \"room_hint\": \"living_room\",\n      \"anchor_hint\": \"anchor_living_side_table_1\",\n      \"success_criteria\": [\"到达客厅边桌附近\"],\n      \"depends_on\": []\n    },\n    {\n      \"id\": \"subtask_2\",\n      \"intent\": \"观察药盒以检查存在\",\n      \"target_object\": \"药盒\",\n      \"recipient\": null,\n      \"room_hint\": \"living_room\",\n      \"anchor_hint\": \"anchor_living_side_table_1\",\n      \"success_criteria\": [\"确认药盒在客厅边桌上可见\"],\n      \"depends_on\": [\"subtask_1\"]\n    }\n  ],\n  \"confidence\": 0.9\n}",
      "json_payload": {
        "goal": "检查药盒在客厅边桌的存在",
        "subtasks": [
          {
            "id": "subtask_1",
            "intent": "前往客厅边桌",
            "target_object": null,
            "recipient": null,
            "room_hint": "living_room",
            "anchor_hint": "anchor_living_side_table_1",
            "success_criteria": [
              "到达客厅边桌附近"
            ],
            "depends_on": []
          },
          {
            "id": "subtask_2",
            "intent": "观察药盒以检查存在",
            "target_object": "药盒",
            "recipient": null,
            "room_hint": "living_room",
            "anchor_hint": "anchor_living_side_table_1",
            "success_criteria": [
              "确认药盒在客厅边桌上可见"
            ],
            "depends_on": [
              "subtask_1"
            ]
          }
        ],
        "confidence": 0.9
      },
      "plan": {
        "goal": "检查药盒在客厅边桌的存在",
        "subtasks": [
          {
            "id": "subtask_1",
            "intent": "前往客厅边桌",
            "target_object": null,
            "recipient": null,
            "room_hint": "living_room",
            "anchor_hint": "anchor_living_side_table_1",
            "success_criteria": [
              "到达客厅边桌附近"
            ],
            "depends_on": []
          },
          {
            "id": "subtask_2",
            "intent": "观察药盒以检查存在",
            "target_object": "药盒",
            "recipient": null,
            "room_hint": "living_room",
            "anchor_hint": "anchor_living_side_table_1",
            "success_criteria": [
              "确认药盒在客厅边桌上可见"
            ],
            "depends_on": [
              "subtask_1"
            ]
          }
        ],
        "confidence": 0.9
      },
      "provider": {
        "provider_name": "Mimo",
        "model": "mimo-v2-pro",
        "protocol": "anthropic",
        "elapsed_ms": 50885.78675000099,
        "attempts": [
          {
            "key_index": 1,
            "status_code": 200,
            "elapsed_ms": 50885.78675000099
          }
        ]
      }
    }
  ],
  "checks": {
    "has_subtasks": true,
    "no_selected_target": true,
    "no_legacy_candidate": true,
    "subtasks_have_success_criteria": true,
    "no_fetch_or_delivery_operation": true,
    "mentions_medicine": true
  }
}
```
