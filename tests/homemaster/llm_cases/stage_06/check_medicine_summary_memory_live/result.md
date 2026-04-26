# Stage 06 Summary And Memory Writeback - check_medicine_summary_memory_live

Status: PASS

## Expected Conditions

```json
{
  "expected_behavior": "药盒查看成功生成 summary、object memory confirm update、task record"
}
```

## Stage05 Input Summary

```json
{
  "task_id": "stage06-check-medicine",
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
  "orchestration_plan": {
    "goal": "确认药盒是否还在",
    "subtasks": [
      {
        "id": "check_medicine",
        "intent": "观察并确认药盒是否存在",
        "target_object": "药盒",
        "recipient": null,
        "room_hint": null,
        "anchor_hint": null,
        "success_criteria": [
          "确认药盒是否在视野中"
        ],
        "depends_on": []
      }
    ],
    "confidence": 0.9
  },
  "execution_state": {
    "task_status": "completed",
    "current_subtask_id": null,
    "subtasks": [
      {
        "subtask_id": "check_medicine",
        "status": "verified",
        "depends_on": [],
        "attempt_count": 0,
        "last_started_at": null,
        "last_completed_at": null,
        "last_skill": null,
        "last_observation": null,
        "last_verification_result": null,
        "failure_record_ids": []
      }
    ],
    "held_object": null,
    "target_object_visible": false,
    "target_object_location": null,
    "user_location": null,
    "current_location": null,
    "last_observation": null,
    "last_skill_call": null,
    "last_skill_result": null,
    "last_verification_result": null,
    "failure_record_ids": [],
    "negative_evidence": [],
    "retry_counts": {},
    "completed_subtask_ids": []
  },
  "skill_results": [
    {
      "skill": "navigation",
      "status": "success",
      "skill_output": {},
      "observation": {
        "target_object_visible": true,
        "target_object_location": "客厅边桌"
      },
      "runtime_state_delta": {},
      "evidence": {},
      "error": null,
      "image_input": {
        "enabled": false,
        "image_ref": null,
        "camera": null,
        "timestamp": null,
        "metadata": {}
      }
    }
  ],
  "verification_results": [
    {
      "scope": "subtask",
      "passed": true,
      "verified_facts": [
        "药盒在目标观察位置被看到"
      ],
      "missing_evidence": [],
      "failed_reason": null,
      "confidence": 0.91
    }
  ],
  "failure_records": [],
  "trace_events": [
    {
      "event_id": "stage06-check-medicine",
      "summary": "medicine visible"
    }
  ],
  "base_memory_path": "/Users/wylam/Documents/workspace/HomeMaster/data/scenarios/check_medicine_success/memory.json"
}
```

## Summary Prompt

```text
你是 HomeMaster V1.2 的 Stage06 任务总结组件。

目标：只根据输入证据生成 TaskSummary JSON，给用户和开发者阅读。
你不负责长期记忆写回，不能决定 object_memory 或 fact_memory 写什么。

必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。
不要编造没有证据的成功结果。
不要编造 evidence_refs，只能引用 EvidenceBundle 里已有的 evidence_id。

TaskSummary schema:
{
  "result": "success | failed | needs_user",
  "confirmed_facts": ["由验证证据支持的事实"],
  "unconfirmed_facts": ["没有被验证或失败的事实"],
  "recovery_attempts": ["发生过的恢复尝试，没有则 []"],
  "user_reply": "给用户看的简短回复或 null",
  "failure_summary": "失败摘要或 null",
  "evidence_refs": ["引用 EvidenceBundle.evidence_refs 中的 evidence_id"]
}

TaskCard:
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

ExecutionState:
{
  "task_status": "completed",
  "current_subtask_id": null,
  "subtasks": [
    {
      "subtask_id": "check_medicine",
      "status": "verified",
      "depends_on": [],
      "attempt_count": 0,
      "last_started_at": null,
      "last_completed_at": null,
      "last_skill": null,
      "last_observation": null,
      "last_verification_result": null,
      "failure_record_ids": []
    }
  ],
  "held_object": null,
  "target_object_visible": false,
  "target_object_location": null,
  "user_location": null,
  "current_location": null,
  "last_observation": null,
  "last_skill_call": null,
  "last_skill_result": null,
  "last_verification_result": null,
  "failure_record_ids": [],
  "negative_evidence": [],
  "retry_counts": {},
  "completed_subtask_ids": []
}

EvidenceBundle:
{
  "task_id": "stage06-check-medicine",
  "evidence_refs": [
    {
      "evidence_id": "verification:stage06-check-medicine:1",
      "evidence_type": "verification_result",
      "source_id": "verification-1",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "药盒在目标观察位置被看到"
    },
    {
      "evidence_id": "skill_result:stage06-check-medicine:1:navigation",
      "evidence_type": "skill_result",
      "source_id": "skill-1",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "navigation success"
    },
    {
      "evidence_id": "observation:stage06-check-medicine:1",
      "evidence_type": "observation",
      "source_id": "skill-1",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "observation: target visible at 客厅边桌"
    },
    {
      "evidence_id": "trace_event:stage06-check-medicine",
      "evidence_type": "trace_event",
      "source_id": "stage06-check-medicine",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "medicine visible"
    }
  ],
  "verified_facts": [
    "药盒在目标观察位置被看到"
  ],
  "failure_facts": [],
  "system_failures": [],
  "negative_evidence": []
}

只输出 JSON object:
```

## Mimo Raw Response

```text
{
  "result": "success",
  "confirmed_facts": ["药盒在目标观察位置被看到"],
  "unconfirmed_facts": [],
  "recovery_attempts": [],
  "user_reply": "药盒在目标位置被确认可见。",
  "failure_summary": null,
  "evidence_refs": ["verification:stage06-check-medicine:1", "skill_result:stage06-check-medicine:1:navigation", "observation:stage06-check-medicine:1", "trace_event:stage06-check-medicine"]
}
```

## Parsed TaskSummary

```json
{
  "result": "success",
  "confirmed_facts": [
    "药盒在目标观察位置被看到"
  ],
  "unconfirmed_facts": [],
  "recovery_attempts": [],
  "user_reply": "药盒在目标位置被确认可见。",
  "failure_summary": null,
  "evidence_refs": [
    "verification:stage06-check-medicine:1",
    "skill_result:stage06-check-medicine:1:navigation",
    "observation:stage06-check-medicine:1",
    "trace_event:stage06-check-medicine"
  ]
}
```

## EvidenceBundle

```json
{
  "task_id": "stage06-check-medicine",
  "evidence_refs": [
    {
      "evidence_id": "verification:stage06-check-medicine:1",
      "evidence_type": "verification_result",
      "source_id": "verification-1",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "药盒在目标观察位置被看到"
    },
    {
      "evidence_id": "skill_result:stage06-check-medicine:1:navigation",
      "evidence_type": "skill_result",
      "source_id": "skill-1",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "navigation success"
    },
    {
      "evidence_id": "observation:stage06-check-medicine:1",
      "evidence_type": "observation",
      "source_id": "skill-1",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "observation: target visible at 客厅边桌"
    },
    {
      "evidence_id": "trace_event:stage06-check-medicine",
      "evidence_type": "trace_event",
      "source_id": "stage06-check-medicine",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "medicine visible"
    }
  ],
  "verified_facts": [
    "药盒在目标观察位置被看到"
  ],
  "failure_facts": [],
  "system_failures": [],
  "negative_evidence": []
}
```

## MemoryCommitPlan

```json
{
  "commit_id": "commit:stage06-check-medicine",
  "object_memory_updates": [
    {
      "memory_id": "mem-medicine-2",
      "update_type": "confirm",
      "updated_fields": {
        "last_confirmed_at": "2026-04-26T00:01:00Z",
        "confidence_level": "high",
        "belief_state": "confirmed"
      },
      "evidence_refs": [
        {
          "evidence_id": "verification:stage06-check-medicine:1",
          "evidence_type": "verification_result",
          "source_id": "verification-1",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "药盒在目标观察位置被看到"
        },
        {
          "evidence_id": "skill_result:stage06-check-medicine:1:navigation",
          "evidence_type": "skill_result",
          "source_id": "skill-1",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "navigation success"
        },
        {
          "evidence_id": "observation:stage06-check-medicine:1",
          "evidence_type": "observation",
          "source_id": "skill-1",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "observation: target visible at 客厅边桌"
        },
        {
          "evidence_id": "trace_event:stage06-check-medicine",
          "evidence_type": "trace_event",
          "source_id": "stage06-check-medicine",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "medicine visible"
        },
        {
          "evidence_id": "selected_target:mem-medicine-2",
          "evidence_type": "selected_target",
          "source_id": "mem-medicine-2",
          "subtask_id": null,
          "memory_id": "mem-medicine-2",
          "location_key": "living_room:anchor_living_side_table_1",
          "created_at": "2026-04-26T00:01:00Z",
          "summary": "Stage04 selected reliable target mem-medicine-2"
        }
      ],
      "reason": "目标物已由本次任务证据确认"
    }
  ],
  "fact_memory_writes": [
    {
      "fact_id": "fact:stage06-check-medicine:object-seen",
      "fact_type": "object_seen",
      "polarity": "positive",
      "target": "药盒",
      "location": {
        "memory_id": "mem-medicine-2",
        "room_id": "living_room",
        "anchor_id": "anchor_living_side_table_1",
        "viewpoint_id": "living_side_table_viewpoint",
        "display_text": "客厅边桌"
      },
      "time_scope": "task_run",
      "confidence": 0.9,
      "text": "本次任务中，药盒在客厅边桌被观察到。",
      "evidence_refs": [
        {
          "evidence_id": "verification:stage06-check-medicine:1",
          "evidence_type": "verification_result",
          "source_id": "verification-1",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "药盒在目标观察位置被看到"
        },
        {
          "evidence_id": "skill_result:stage06-check-medicine:1:navigation",
          "evidence_type": "skill_result",
          "source_id": "skill-1",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "navigation success"
        },
        {
          "evidence_id": "observation:stage06-check-medicine:1",
          "evidence_type": "observation",
          "source_id": "skill-1",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "observation: target visible at 客厅边桌"
        },
        {
          "evidence_id": "trace_event:stage06-check-medicine",
          "evidence_type": "trace_event",
          "source_id": "stage06-check-medicine",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "medicine visible"
        },
        {
          "evidence_id": "selected_target:mem-medicine-2",
          "evidence_type": "selected_target",
          "source_id": "mem-medicine-2",
          "subtask_id": null,
          "memory_id": "mem-medicine-2",
          "location_key": "living_room:anchor_living_side_table_1",
          "created_at": "2026-04-26T00:01:00Z",
          "summary": "Stage04 selected reliable target mem-medicine-2"
        }
      ],
      "expires_at": null,
      "stale_after": "2026-05-03T00:01:00Z",
      "searchable": false
    }
  ],
  "task_record": {
    "task_id": "stage06-check-medicine",
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
    "summary": {
      "result": "success",
      "confirmed_facts": [
        "药盒在目标观察位置被看到"
      ],
      "unconfirmed_facts": [],
      "recovery_attempts": [],
      "user_reply": "药盒在目标位置被确认可见。",
      "failure_summary": null,
      "evidence_refs": [
        "verification:stage06-check-medicine:1",
        "skill_result:stage06-check-medicine:1:navigation",
        "observation:stage06-check-medicine:1",
        "trace_event:stage06-check-medicine"
      ]
    },
    "result": "success",
    "started_at": "2026-04-26T00:00:00Z",
    "completed_at": "2026-04-26T00:01:00Z",
    "evidence_refs": [
      {
        "evidence_id": "verification:stage06-check-medicine:1",
        "evidence_type": "verification_result",
        "source_id": "verification-1",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-26T00:00:00Z",
        "summary": "药盒在目标观察位置被看到"
      },
      {
        "evidence_id": "skill_result:stage06-check-medicine:1:navigation",
        "evidence_type": "skill_result",
        "source_id": "skill-1",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-26T00:00:00Z",
        "summary": "navigation success"
      },
      {
        "evidence_id": "observation:stage06-check-medicine:1",
        "evidence_type": "observation",
        "source_id": "skill-1",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-26T00:00:00Z",
        "summary": "observation: target visible at 客厅边桌"
      },
      {
        "evidence_id": "trace_event:stage06-check-medicine",
        "evidence_type": "trace_event",
        "source_id": "stage06-check-medicine",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-26T00:00:00Z",
        "summary": "medicine visible"
      },
      {
        "evidence_id": "selected_target:mem-medicine-2",
        "evidence_type": "selected_target",
        "source_id": "mem-medicine-2",
        "subtask_id": null,
        "memory_id": "mem-medicine-2",
        "location_key": "living_room:anchor_living_side_table_1",
        "created_at": "2026-04-26T00:01:00Z",
        "summary": "Stage04 selected reliable target mem-medicine-2"
      }
    ],
    "failure_record_ids": []
  },
  "negative_evidence": [],
  "skipped_candidates": [],
  "index_stale_memory_ids": [
    "mem-medicine-2"
  ],
  "skipped": false
}
```

## Persistence

```json
{
  "object_memory_path": "/private/var/folders/57/hbsp6s1s5y966v37y0xfm9ch0000gn/T/pytest-of-wylam/pytest-14/test_stage_06_live_summary_mem0/memory/object_memory.json",
  "fact_memory_write_count": 1,
  "task_record_write_count": 1,
  "commit_log": {
    "commit_id": "commit:stage06-check-medicine",
    "task_id": "stage06-check-medicine",
    "object_memory_update_count": 1,
    "fact_memory_write_count": 1,
    "task_record_written": true,
    "skipped_candidates": [],
    "index_stale_memory_ids": [
      "mem-medicine-2"
    ],
    "object_memory_path": "/private/var/folders/57/hbsp6s1s5y966v37y0xfm9ch0000gn/T/pytest-of-wylam/pytest-14/test_stage_06_live_summary_mem0/memory/object_memory.json"
  }
}
```

## Checks

```json
{
  "summary_has_evidence_refs": true,
  "task_record_written": true,
  "commit_has_id": true,
  "all_fact_memory_non_searchable": true,
  "summary_success": true,
  "object_memory_updated": true,
  "has_object_seen_fact": true
}
```
