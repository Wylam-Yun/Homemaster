# Stage 06 Summary And Memory Writeback - fetch_cup_success_fact_memory_live

Status: PASS

## Expected Conditions

```json
{
  "expected_behavior": "取水杯成功生成 object_seen 和 delivery_verified fact memory"
}
```

## Stage05 Input Summary

```json
{
  "task_id": "stage06-fetch-cup",
  "planning_context": {
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
      "query_text": "水杯，杯子，厨房，water cup",
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
        "query_text": "水杯，杯子，厨房，water cup",
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
          "document_id": "object_memory:mem-cup-1",
          "memory_id": "mem-cup-1",
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
    "rejected_hits": [],
    "runtime_state_summary": {
      "grounding_reason": "selected first reliable executable memory hit",
      "grounding_status": "grounded",
      "needs_exploratory_search": false
    },
    "world_summary": {
      "anchors": [
        {
          "anchor_id": "anchor_kitchen_table_1",
          "display_text": "厨房餐桌",
          "room_id": "kitchen",
          "viewpoint_id": "kitchen_table_viewpoint"
        },
        {
          "anchor_id": "anchor_pantry_shelf_1",
          "display_text": "储物间搁架",
          "room_id": "pantry",
          "viewpoint_id": "pantry_shelf_viewpoint"
        }
      ],
      "room_ids": [
        "kitchen",
        "pantry"
      ],
      "viewpoint_ids": [
        "kitchen_table_viewpoint",
        "pantry_shelf_viewpoint"
      ]
    },
    "planning_notes": [
      "grounded reliable memory target is available for Stage 05 planning"
    ]
  },
  "orchestration_plan": {
    "goal": "取水杯并交付给用户",
    "subtasks": [
      {
        "id": "find_cup",
        "intent": "找到水杯",
        "target_object": "水杯",
        "recipient": null,
        "room_hint": null,
        "anchor_hint": null,
        "success_criteria": [
          "看到水杯"
        ],
        "depends_on": []
      },
      {
        "id": "deliver_cup",
        "intent": "交付水杯给用户",
        "target_object": "水杯",
        "recipient": "user",
        "room_hint": null,
        "anchor_hint": null,
        "success_criteria": [
          "水杯已交付用户"
        ],
        "depends_on": [
          "find_cup"
        ]
      }
    ],
    "confidence": 0.9
  },
  "execution_state": {
    "task_status": "completed",
    "current_subtask_id": null,
    "subtasks": [
      {
        "subtask_id": "find_cup",
        "status": "verified",
        "depends_on": [],
        "attempt_count": 0,
        "last_started_at": null,
        "last_completed_at": null,
        "last_skill": null,
        "last_observation": null,
        "last_verification_result": null,
        "failure_record_ids": []
      },
      {
        "subtask_id": "deliver_cup",
        "status": "verified",
        "depends_on": [
          "find_cup"
        ],
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
        "target_object_location": "厨房餐桌"
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
    },
    {
      "skill": "operation",
      "status": "success",
      "skill_output": {},
      "observation": {
        "delivered_object": "水杯",
        "delivery_complete": true
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
        "水杯在厨房餐桌被观察到"
      ],
      "missing_evidence": [],
      "failed_reason": null,
      "confidence": 0.95
    },
    {
      "scope": "task",
      "passed": true,
      "verified_facts": [
        "水杯已交付给用户"
      ],
      "missing_evidence": [],
      "failed_reason": null,
      "confidence": 0.93
    }
  ],
  "failure_records": [],
  "trace_events": [
    {
      "event_id": "stage06-fetch-cup",
      "summary": "cup delivered"
    }
  ],
  "base_memory_path": "/Users/wylam/Documents/workspace/HomeMaster/data/scenarios/fetch_cup_retry/memory.json"
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

ExecutionState:
{
  "task_status": "completed",
  "current_subtask_id": null,
  "subtasks": [
    {
      "subtask_id": "find_cup",
      "status": "verified",
      "depends_on": [],
      "attempt_count": 0,
      "last_started_at": null,
      "last_completed_at": null,
      "last_skill": null,
      "last_observation": null,
      "last_verification_result": null,
      "failure_record_ids": []
    },
    {
      "subtask_id": "deliver_cup",
      "status": "verified",
      "depends_on": [
        "find_cup"
      ],
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
  "task_id": "stage06-fetch-cup",
  "evidence_refs": [
    {
      "evidence_id": "verification:stage06-fetch-cup:1",
      "evidence_type": "verification_result",
      "source_id": "verification-1",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "水杯在厨房餐桌被观察到"
    },
    {
      "evidence_id": "verification:stage06-fetch-cup:2",
      "evidence_type": "verification_result",
      "source_id": "verification-2",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "水杯已交付给用户"
    },
    {
      "evidence_id": "skill_result:stage06-fetch-cup:1:navigation",
      "evidence_type": "skill_result",
      "source_id": "skill-1",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "navigation success"
    },
    {
      "evidence_id": "observation:stage06-fetch-cup:1",
      "evidence_type": "observation",
      "source_id": "skill-1",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "observation: target visible at 厨房餐桌"
    },
    {
      "evidence_id": "skill_result:stage06-fetch-cup:2:operation",
      "evidence_type": "skill_result",
      "source_id": "skill-2",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "operation success"
    },
    {
      "evidence_id": "observation:stage06-fetch-cup:2",
      "evidence_type": "observation",
      "source_id": "skill-2",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "observation: delivered 水杯"
    },
    {
      "evidence_id": "trace_event:stage06-fetch-cup",
      "evidence_type": "trace_event",
      "source_id": "stage06-fetch-cup",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "cup delivered"
    }
  ],
  "verified_facts": [
    "水杯在厨房餐桌被观察到",
    "水杯已交付给用户"
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
  "confirmed_facts": ["水杯在厨房餐桌被观察到", "水杯已交付给用户"],
  "unconfirmed_facts": [],
  "recovery_attempts": [],
  "user_reply": "水杯已成功交付给您。",
  "failure_summary": null,
  "evidence_refs": ["verification:stage06-fetch-cup:1", "verification:stage06-fetch-cup:2", "skill_result:stage06-fetch-cup:1:navigation", "observation:stage06-fetch-cup:1", "skill_result:stage06-fetch-cup:2:operation", "observation:stage06-fetch-cup:2", "trace_event:stage06-fetch-cup"]
}
```

## Parsed TaskSummary

```json
{
  "result": "success",
  "confirmed_facts": [
    "水杯在厨房餐桌被观察到",
    "水杯已交付给用户"
  ],
  "unconfirmed_facts": [],
  "recovery_attempts": [],
  "user_reply": "水杯已成功交付给您。",
  "failure_summary": null,
  "evidence_refs": [
    "verification:stage06-fetch-cup:1",
    "verification:stage06-fetch-cup:2",
    "skill_result:stage06-fetch-cup:1:navigation",
    "observation:stage06-fetch-cup:1",
    "skill_result:stage06-fetch-cup:2:operation",
    "observation:stage06-fetch-cup:2",
    "trace_event:stage06-fetch-cup"
  ]
}
```

## EvidenceBundle

```json
{
  "task_id": "stage06-fetch-cup",
  "evidence_refs": [
    {
      "evidence_id": "verification:stage06-fetch-cup:1",
      "evidence_type": "verification_result",
      "source_id": "verification-1",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "水杯在厨房餐桌被观察到"
    },
    {
      "evidence_id": "verification:stage06-fetch-cup:2",
      "evidence_type": "verification_result",
      "source_id": "verification-2",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "水杯已交付给用户"
    },
    {
      "evidence_id": "skill_result:stage06-fetch-cup:1:navigation",
      "evidence_type": "skill_result",
      "source_id": "skill-1",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "navigation success"
    },
    {
      "evidence_id": "observation:stage06-fetch-cup:1",
      "evidence_type": "observation",
      "source_id": "skill-1",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "observation: target visible at 厨房餐桌"
    },
    {
      "evidence_id": "skill_result:stage06-fetch-cup:2:operation",
      "evidence_type": "skill_result",
      "source_id": "skill-2",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "operation success"
    },
    {
      "evidence_id": "observation:stage06-fetch-cup:2",
      "evidence_type": "observation",
      "source_id": "skill-2",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "observation: delivered 水杯"
    },
    {
      "evidence_id": "trace_event:stage06-fetch-cup",
      "evidence_type": "trace_event",
      "source_id": "stage06-fetch-cup",
      "subtask_id": null,
      "memory_id": null,
      "location_key": null,
      "created_at": "2026-04-26T00:00:00Z",
      "summary": "cup delivered"
    }
  ],
  "verified_facts": [
    "水杯在厨房餐桌被观察到",
    "水杯已交付给用户"
  ],
  "failure_facts": [],
  "system_failures": [],
  "negative_evidence": []
}
```

## MemoryCommitPlan

```json
{
  "commit_id": "commit:stage06-fetch-cup",
  "object_memory_updates": [
    {
      "memory_id": "mem-cup-1",
      "update_type": "confirm",
      "updated_fields": {
        "last_confirmed_at": "2026-04-26T00:01:00Z",
        "confidence_level": "high",
        "belief_state": "confirmed"
      },
      "evidence_refs": [
        {
          "evidence_id": "verification:stage06-fetch-cup:1",
          "evidence_type": "verification_result",
          "source_id": "verification-1",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "水杯在厨房餐桌被观察到"
        },
        {
          "evidence_id": "verification:stage06-fetch-cup:2",
          "evidence_type": "verification_result",
          "source_id": "verification-2",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "水杯已交付给用户"
        },
        {
          "evidence_id": "skill_result:stage06-fetch-cup:1:navigation",
          "evidence_type": "skill_result",
          "source_id": "skill-1",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "navigation success"
        },
        {
          "evidence_id": "observation:stage06-fetch-cup:1",
          "evidence_type": "observation",
          "source_id": "skill-1",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "observation: target visible at 厨房餐桌"
        },
        {
          "evidence_id": "skill_result:stage06-fetch-cup:2:operation",
          "evidence_type": "skill_result",
          "source_id": "skill-2",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "operation success"
        },
        {
          "evidence_id": "observation:stage06-fetch-cup:2",
          "evidence_type": "observation",
          "source_id": "skill-2",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "observation: delivered 水杯"
        },
        {
          "evidence_id": "trace_event:stage06-fetch-cup",
          "evidence_type": "trace_event",
          "source_id": "stage06-fetch-cup",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "cup delivered"
        },
        {
          "evidence_id": "selected_target:mem-cup-1",
          "evidence_type": "selected_target",
          "source_id": "mem-cup-1",
          "subtask_id": null,
          "memory_id": "mem-cup-1",
          "location_key": "kitchen:anchor_kitchen_table_1",
          "created_at": "2026-04-26T00:01:00Z",
          "summary": "Stage04 selected reliable target mem-cup-1"
        }
      ],
      "reason": "目标物已由本次任务证据确认"
    }
  ],
  "fact_memory_writes": [
    {
      "fact_id": "fact:stage06-fetch-cup:object-seen",
      "fact_type": "object_seen",
      "polarity": "positive",
      "target": "水杯",
      "location": {
        "memory_id": "mem-cup-1",
        "room_id": "kitchen",
        "anchor_id": "anchor_kitchen_table_1",
        "viewpoint_id": "kitchen_table_viewpoint",
        "display_text": "厨房餐桌"
      },
      "time_scope": "task_run",
      "confidence": 0.9,
      "text": "本次任务中，水杯在厨房餐桌被观察到。",
      "evidence_refs": [
        {
          "evidence_id": "verification:stage06-fetch-cup:1",
          "evidence_type": "verification_result",
          "source_id": "verification-1",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "水杯在厨房餐桌被观察到"
        },
        {
          "evidence_id": "verification:stage06-fetch-cup:2",
          "evidence_type": "verification_result",
          "source_id": "verification-2",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "水杯已交付给用户"
        },
        {
          "evidence_id": "skill_result:stage06-fetch-cup:1:navigation",
          "evidence_type": "skill_result",
          "source_id": "skill-1",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "navigation success"
        },
        {
          "evidence_id": "observation:stage06-fetch-cup:1",
          "evidence_type": "observation",
          "source_id": "skill-1",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "observation: target visible at 厨房餐桌"
        },
        {
          "evidence_id": "skill_result:stage06-fetch-cup:2:operation",
          "evidence_type": "skill_result",
          "source_id": "skill-2",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "operation success"
        },
        {
          "evidence_id": "observation:stage06-fetch-cup:2",
          "evidence_type": "observation",
          "source_id": "skill-2",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "observation: delivered 水杯"
        },
        {
          "evidence_id": "trace_event:stage06-fetch-cup",
          "evidence_type": "trace_event",
          "source_id": "stage06-fetch-cup",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "cup delivered"
        },
        {
          "evidence_id": "selected_target:mem-cup-1",
          "evidence_type": "selected_target",
          "source_id": "mem-cup-1",
          "subtask_id": null,
          "memory_id": "mem-cup-1",
          "location_key": "kitchen:anchor_kitchen_table_1",
          "created_at": "2026-04-26T00:01:00Z",
          "summary": "Stage04 selected reliable target mem-cup-1"
        }
      ],
      "expires_at": null,
      "stale_after": "2026-05-03T00:01:00Z",
      "searchable": false
    },
    {
      "fact_id": "fact:stage06-fetch-cup:delivery-verified",
      "fact_type": "delivery_verified",
      "polarity": "positive",
      "target": "水杯",
      "location": {},
      "time_scope": "task_run",
      "confidence": 0.85,
      "text": "本次任务中，水杯已交付给user。",
      "evidence_refs": [
        {
          "evidence_id": "verification:stage06-fetch-cup:1",
          "evidence_type": "verification_result",
          "source_id": "verification-1",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "水杯在厨房餐桌被观察到"
        },
        {
          "evidence_id": "verification:stage06-fetch-cup:2",
          "evidence_type": "verification_result",
          "source_id": "verification-2",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "水杯已交付给用户"
        },
        {
          "evidence_id": "skill_result:stage06-fetch-cup:1:navigation",
          "evidence_type": "skill_result",
          "source_id": "skill-1",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "navigation success"
        },
        {
          "evidence_id": "observation:stage06-fetch-cup:1",
          "evidence_type": "observation",
          "source_id": "skill-1",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "observation: target visible at 厨房餐桌"
        },
        {
          "evidence_id": "skill_result:stage06-fetch-cup:2:operation",
          "evidence_type": "skill_result",
          "source_id": "skill-2",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "operation success"
        },
        {
          "evidence_id": "observation:stage06-fetch-cup:2",
          "evidence_type": "observation",
          "source_id": "skill-2",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "observation: delivered 水杯"
        },
        {
          "evidence_id": "trace_event:stage06-fetch-cup",
          "evidence_type": "trace_event",
          "source_id": "stage06-fetch-cup",
          "subtask_id": null,
          "memory_id": null,
          "location_key": null,
          "created_at": "2026-04-26T00:00:00Z",
          "summary": "cup delivered"
        },
        {
          "evidence_id": "selected_target:mem-cup-1",
          "evidence_type": "selected_target",
          "source_id": "mem-cup-1",
          "subtask_id": null,
          "memory_id": "mem-cup-1",
          "location_key": "kitchen:anchor_kitchen_table_1",
          "created_at": "2026-04-26T00:01:00Z",
          "summary": "Stage04 selected reliable target mem-cup-1"
        }
      ],
      "expires_at": null,
      "stale_after": "2026-05-03T00:01:00Z",
      "searchable": false
    }
  ],
  "task_record": {
    "task_id": "stage06-fetch-cup",
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
    "summary": {
      "result": "success",
      "confirmed_facts": [
        "水杯在厨房餐桌被观察到",
        "水杯已交付给用户"
      ],
      "unconfirmed_facts": [],
      "recovery_attempts": [],
      "user_reply": "水杯已成功交付给您。",
      "failure_summary": null,
      "evidence_refs": [
        "verification:stage06-fetch-cup:1",
        "verification:stage06-fetch-cup:2",
        "skill_result:stage06-fetch-cup:1:navigation",
        "observation:stage06-fetch-cup:1",
        "skill_result:stage06-fetch-cup:2:operation",
        "observation:stage06-fetch-cup:2",
        "trace_event:stage06-fetch-cup"
      ]
    },
    "result": "success",
    "started_at": "2026-04-26T00:00:00Z",
    "completed_at": "2026-04-26T00:01:00Z",
    "evidence_refs": [
      {
        "evidence_id": "verification:stage06-fetch-cup:1",
        "evidence_type": "verification_result",
        "source_id": "verification-1",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-26T00:00:00Z",
        "summary": "水杯在厨房餐桌被观察到"
      },
      {
        "evidence_id": "verification:stage06-fetch-cup:2",
        "evidence_type": "verification_result",
        "source_id": "verification-2",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-26T00:00:00Z",
        "summary": "水杯已交付给用户"
      },
      {
        "evidence_id": "skill_result:stage06-fetch-cup:1:navigation",
        "evidence_type": "skill_result",
        "source_id": "skill-1",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-26T00:00:00Z",
        "summary": "navigation success"
      },
      {
        "evidence_id": "observation:stage06-fetch-cup:1",
        "evidence_type": "observation",
        "source_id": "skill-1",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-26T00:00:00Z",
        "summary": "observation: target visible at 厨房餐桌"
      },
      {
        "evidence_id": "skill_result:stage06-fetch-cup:2:operation",
        "evidence_type": "skill_result",
        "source_id": "skill-2",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-26T00:00:00Z",
        "summary": "operation success"
      },
      {
        "evidence_id": "observation:stage06-fetch-cup:2",
        "evidence_type": "observation",
        "source_id": "skill-2",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-26T00:00:00Z",
        "summary": "observation: delivered 水杯"
      },
      {
        "evidence_id": "trace_event:stage06-fetch-cup",
        "evidence_type": "trace_event",
        "source_id": "stage06-fetch-cup",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-26T00:00:00Z",
        "summary": "cup delivered"
      },
      {
        "evidence_id": "selected_target:mem-cup-1",
        "evidence_type": "selected_target",
        "source_id": "mem-cup-1",
        "subtask_id": null,
        "memory_id": "mem-cup-1",
        "location_key": "kitchen:anchor_kitchen_table_1",
        "created_at": "2026-04-26T00:01:00Z",
        "summary": "Stage04 selected reliable target mem-cup-1"
      }
    ],
    "failure_record_ids": []
  },
  "negative_evidence": [],
  "skipped_candidates": [],
  "index_stale_memory_ids": [
    "mem-cup-1"
  ],
  "skipped": false
}
```

## Persistence

```json
{
  "object_memory_path": "/private/var/folders/57/hbsp6s1s5y966v37y0xfm9ch0000gn/T/pytest-of-wylam/pytest-14/test_stage_06_live_summary_mem1/memory/object_memory.json",
  "fact_memory_write_count": 2,
  "task_record_write_count": 1,
  "commit_log": {
    "commit_id": "commit:stage06-fetch-cup",
    "task_id": "stage06-fetch-cup",
    "object_memory_update_count": 1,
    "fact_memory_write_count": 2,
    "task_record_written": true,
    "skipped_candidates": [],
    "index_stale_memory_ids": [
      "mem-cup-1"
    ],
    "object_memory_path": "/private/var/folders/57/hbsp6s1s5y966v37y0xfm9ch0000gn/T/pytest-of-wylam/pytest-14/test_stage_06_live_summary_mem1/memory/object_memory.json"
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
  "has_delivery_fact": true,
  "only_existing_object_memory_updated": true
}
```
