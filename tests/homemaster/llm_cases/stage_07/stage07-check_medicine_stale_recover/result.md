# Stage 07 Run - stage07-check_medicine_stale_recover

Status: PASS

## Summary

- Scenario: check_medicine_stale_recover
- Utterance: 去桌子那边看看药盒是不是还在。
- Final status: completed

## Stage Statuses

```json
{
  "stage02": {
    "status": "PASS",
    "mode": "real_mimo"
  },
  "stage03": {
    "status": "PASS",
    "mode": "real_mimo",
    "embedding": "real_bge_m3"
  },
  "stage04": {
    "status": "PASS",
    "grounding_status": "grounded",
    "selected_target": "mem-medicine-2"
  },
  "stage05": {
    "status": "PASS",
    "mode": "real_mimo",
    "step_decision": {
      "mode": "real_mimo",
      "status": "PASS",
      "subtask_id": "subtask_1",
      "selected_skill": "navigation",
      "provider": {
        "provider_name": "Mimo",
        "model": "mimo-v2-pro",
        "protocol": "anthropic",
        "elapsed_ms": 37443.24629099992,
        "attempts": [
          {
            "key_index": 1,
            "status_code": 200,
            "elapsed_ms": 37443.24629099992
          }
        ]
      }
    },
    "final_task_status": "completed",
    "mock_skills": true
  },
  "stage06": {
    "status": "PASS",
    "mode": "real_mimo",
    "task_summary_result": "success",
    "object_memory_update_count": 1,
    "fact_memory_write_count": 1
  }
}
```

## Model And Skill Boundary

```json
{
  "stage02": "real_mimo",
  "stage03_query": "real_mimo",
  "stage03_embedding": "real_bge_m3",
  "stage04": "programmatic",
  "stage05_plan": "real_mimo",
  "stage05_step": "real_mimo",
  "stage05_navigation": "mock",
  "stage05_operation": "mock",
  "stage05_verification": "mock",
  "stage06_summary": "real_mimo",
  "stage06_memory_commit": "programmatic",
  "real_robot": "not_integrated",
  "real_vla": "not_integrated",
  "real_vlm": "not_integrated"
}
```

## Paths

```json
{
  "world_path": "/Users/wylam/Documents/workspace/HomeMaster/data/scenarios/check_medicine_stale_recover/world.json",
  "base_memory_path": "/Users/wylam/Documents/workspace/HomeMaster/data/scenarios/check_medicine_stale_recover/memory.json",
  "runtime_memory_root": "var/homemaster/runs/stage07-check_medicine_stale_recover/memory",
  "case_dir": "tests/homemaster/llm_cases/stage_07/stage07-check_medicine_stale_recover",
  "results_dir": "/Users/wylam/Documents/workspace/HomeMaster/plan/V1.2/test_results/stage_07"
}
```

## Full Actual

```json
{
  "run_id": "stage07-check_medicine_stale_recover",
  "scenario": "check_medicine_stale_recover",
  "utterance": "去桌子那边看看药盒是不是还在。",
  "final_status": "completed",
  "stage_statuses": {
    "stage02": {
      "status": "PASS",
      "mode": "real_mimo"
    },
    "stage03": {
      "status": "PASS",
      "mode": "real_mimo",
      "embedding": "real_bge_m3"
    },
    "stage04": {
      "status": "PASS",
      "grounding_status": "grounded",
      "selected_target": "mem-medicine-2"
    },
    "stage05": {
      "status": "PASS",
      "mode": "real_mimo",
      "step_decision": {
        "mode": "real_mimo",
        "status": "PASS",
        "subtask_id": "subtask_1",
        "selected_skill": "navigation",
        "provider": {
          "provider_name": "Mimo",
          "model": "mimo-v2-pro",
          "protocol": "anthropic",
          "elapsed_ms": 37443.24629099992,
          "attempts": [
            {
              "key_index": 1,
              "status_code": 200,
              "elapsed_ms": 37443.24629099992
            }
          ]
        }
      },
      "final_task_status": "completed",
      "mock_skills": true
    },
    "stage06": {
      "status": "PASS",
      "mode": "real_mimo",
      "task_summary_result": "success",
      "object_memory_update_count": 1,
      "fact_memory_write_count": 1
    }
  },
  "model_boundary": {
    "stage02": "real_mimo",
    "stage03_query": "real_mimo",
    "stage03_embedding": "real_bge_m3",
    "stage04": "programmatic",
    "stage05_plan": "real_mimo",
    "stage05_step": "real_mimo",
    "stage05_navigation": "mock",
    "stage05_operation": "mock",
    "stage05_verification": "mock",
    "stage06_summary": "real_mimo",
    "stage06_memory_commit": "programmatic",
    "real_robot": "not_integrated",
    "real_vla": "not_integrated",
    "real_vlm": "not_integrated"
  },
  "paths": {
    "world_path": "/Users/wylam/Documents/workspace/HomeMaster/data/scenarios/check_medicine_stale_recover/world.json",
    "base_memory_path": "/Users/wylam/Documents/workspace/HomeMaster/data/scenarios/check_medicine_stale_recover/memory.json",
    "runtime_memory_root": "var/homemaster/runs/stage07-check_medicine_stale_recover/memory",
    "case_dir": "tests/homemaster/llm_cases/stage_07/stage07-check_medicine_stale_recover",
    "results_dir": "/Users/wylam/Documents/workspace/HomeMaster/plan/V1.2/test_results/stage_07"
  },
  "task_card": {
    "task_type": "check_presence",
    "target": "药盒",
    "delivery_target": null,
    "location_hint": "桌子那边",
    "success_criteria": [
      "确认药盒在桌子那边"
    ],
    "needs_clarification": false,
    "clarification_question": null,
    "confidence": 0.95
  },
  "planning_context": {
    "task_card": {
      "task_type": "check_presence",
      "target": "药盒",
      "delivery_target": null,
      "location_hint": "桌子那边",
      "success_criteria": [
        "确认药盒在桌子那边"
      ],
      "needs_clarification": false,
      "clarification_question": null,
      "confidence": 0.95
    },
    "retrieval_query": {
      "query_text": "药盒 药箱 medicine box 桌子那边",
      "target_category": null,
      "target_aliases": [
        "药盒",
        "药箱",
        "药品盒"
      ],
      "location_terms": [
        "桌子那边",
        "桌子"
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
          "last_confirmed_at": "2026-04-15T09:00:00Z",
          "text_snippet": "物体记忆。目标类别: medicine_box。目标类别别名: 药盒、药箱、medicine_box、medicine。别名: 药盒、药箱。历史位置: 厨房药柜。房间: kitchen。房间别名: 厨房、kitchen。锚点类型: cabinet。锚点别名: 柜子、药柜、cabinet。可观察视角: kitchen_cabinet_viewpoint。置信度: high。记忆状态: confirmed。最近确认时间: 2026-04-15T09:00:00Z。",
          "bm25_score": 0.43699416518211365,
          "dense_score": 0.6803325487605275,
          "metadata_score": 0.30000000000000004,
          "final_score": 0.33225806451612905,
          "ranking_reasons": [
            "bm25_rank=2",
            "dense_rank=2",
            "metadata_target_alias_match",
            "metadata_high_confidence"
          ],
          "canonical_metadata": {
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
            "last_confirmed_at": "2026-04-15T09:00:00Z",
            "document_text_hash": "0afb5000847936f6217fc0e177af9b7d4407a09751d2df5ff94d91793bdf242f"
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
          "last_confirmed_at": "2026-04-19T08:30:00Z",
          "text_snippet": "物体记忆。目标类别: medicine_box。目标类别别名: 药盒、药箱、medicine_box、medicine。别名: 药盒。历史位置: 客厅边桌。房间: living_room。房间别名: 客厅、living room、living_room。锚点类型: table。锚点别名: 桌子、餐桌、table。可观察视角: living_side_table_viewpoint。置信度: medium。记忆状态: confirmed。最近确认时间: 2026-04-19T08:30:00Z。",
          "bm25_score": 0.6745474338531494,
          "dense_score": 0.6921792853832335,
          "metadata_score": 0.25,
          "final_score": 0.2827868852459016,
          "ranking_reasons": [
            "bm25_rank=1",
            "dense_rank=1",
            "metadata_target_alias_match",
            "metadata_medium_confidence"
          ],
          "canonical_metadata": {
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
            "last_confirmed_at": "2026-04-19T08:30:00Z",
            "document_text_hash": "a56d99546ce641d14c972a5323838f6fab4b1b864c97a7c26474c9d00c7be039"
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
        "query_text": "药盒 药箱 medicine box 桌子那边",
        "target_category": null,
        "target_aliases": [
          "药盒",
          "药箱",
          "药品盒"
        ],
        "location_terms": [
          "桌子那边",
          "桌子"
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
        "provider_name": "MemoryEmbedding",
        "model": "BAAI/bge-m3",
        "endpoint": "https://api.siliconflow.cn/v1/embeddings"
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
        "tokenized_query": "[REDACTED]",
        "ranking_stage": "bm25_dense_fusion"
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
          "last_confirmed_at": "2026-04-19T08:30:00Z",
          "document_text_hash": "a56d99546ce641d14c972a5323838f6fab4b1b864c97a7c26474c9d00c7be039"
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
        "last_confirmed_at": "2026-04-15T09:00:00Z",
        "text_snippet": "物体记忆。目标类别: medicine_box。目标类别别名: 药盒、药箱、medicine_box、medicine。别名: 药盒、药箱。历史位置: 厨房药柜。房间: kitchen。房间别名: 厨房、kitchen。锚点类型: cabinet。锚点别名: 柜子、药柜、cabinet。可观察视角: kitchen_cabinet_viewpoint。置信度: high。记忆状态: confirmed。最近确认时间: 2026-04-15T09:00:00Z。",
        "bm25_score": 0.43699416518211365,
        "dense_score": 0.6803325487605275,
        "metadata_score": 0.30000000000000004,
        "final_score": 0.33225806451612905,
        "ranking_reasons": [
          "bm25_rank=2",
          "dense_rank=2",
          "metadata_target_alias_match",
          "metadata_high_confidence"
        ],
        "canonical_metadata": {
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
          "last_confirmed_at": "2026-04-15T09:00:00Z",
          "document_text_hash": "0afb5000847936f6217fc0e177af9b7d4407a09751d2df5ff94d91793bdf242f"
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
  },
  "orchestration_plan": {
    "goal": "确认药盒在桌子那边",
    "subtasks": [
      {
        "id": "subtask_1",
        "intent": "导航到客厅边桌",
        "target_object": null,
        "recipient": null,
        "room_hint": "客厅",
        "anchor_hint": "边桌",
        "success_criteria": [
          "到达客厅边桌位置"
        ],
        "depends_on": []
      },
      {
        "id": "subtask_2",
        "intent": "确认药盒在桌子上",
        "target_object": "药盒",
        "recipient": null,
        "room_hint": "客厅",
        "anchor_hint": "边桌",
        "success_criteria": [
          "观察到药盒在客厅边桌上"
        ],
        "depends_on": [
          "subtask_1"
        ]
      }
    ],
    "confidence": 0.95
  },
  "execution_result": {
    "plan": {
      "goal": "确认药盒在桌子那边",
      "subtasks": [
        {
          "id": "subtask_1",
          "intent": "导航到客厅边桌",
          "target_object": null,
          "recipient": null,
          "room_hint": "客厅",
          "anchor_hint": "边桌",
          "success_criteria": [
            "到达客厅边桌位置"
          ],
          "depends_on": []
        },
        {
          "id": "subtask_2",
          "intent": "确认药盒在桌子上",
          "target_object": "药盒",
          "recipient": null,
          "room_hint": "客厅",
          "anchor_hint": "边桌",
          "success_criteria": [
            "观察到药盒在客厅边桌上"
          ],
          "depends_on": [
            "subtask_1"
          ]
        }
      ],
      "confidence": 0.95
    },
    "final_state": {
      "task_status": "completed",
      "current_subtask_id": "subtask_2",
      "subtasks": [
        {
          "subtask_id": "subtask_1",
          "status": "verified",
          "depends_on": [],
          "attempt_count": 0,
          "last_started_at": null,
          "last_completed_at": null,
          "last_skill": null,
          "last_observation": {},
          "last_verification_result": {
            "scope": "subtask",
            "passed": true,
            "verified_facts": [
              "结构化 observation 支持该子任务完成"
            ],
            "missing_evidence": [],
            "failed_reason": null,
            "confidence": 0.9
          },
          "failure_record_ids": []
        },
        {
          "subtask_id": "subtask_2",
          "status": "verified",
          "depends_on": [
            "subtask_1"
          ],
          "attempt_count": 0,
          "last_started_at": null,
          "last_completed_at": null,
          "last_skill": null,
          "last_observation": {
            "target_object_visible": true,
            "visible_objects": [
              "药盒"
            ],
            "target_object_location": "边桌",
            "current_location": "客厅"
          },
          "last_verification_result": {
            "scope": "subtask",
            "passed": true,
            "verified_facts": [
              "观察到药盒"
            ],
            "missing_evidence": [],
            "failed_reason": null,
            "confidence": 0.9
          },
          "failure_record_ids": []
        }
      ],
      "held_object": null,
      "target_object_visible": true,
      "target_object_location": "边桌",
      "user_location": "user_start",
      "current_location": "客厅",
      "last_observation": {
        "target_object_visible": true,
        "visible_objects": [
          "药盒"
        ],
        "target_object_location": "边桌",
        "current_location": "客厅"
      },
      "last_skill_call": {
        "subtask_id": "subtask_2",
        "selected_skill": "navigation",
        "skill_input": {
          "goal_type": "find_object",
          "target_object": "药盒",
          "room_hint": "客厅",
          "anchor_hint": "边桌",
          "subtask_id": "subtask_2",
          "subtask_intent": "确认药盒在桌子上"
        },
        "expected_result": "找到并观察目标物",
        "reason": "当前子任务需要先导航或观察目标物"
      },
      "last_skill_result": {
        "skill": "navigation",
        "status": "success",
        "skill_output": {
          "goal_type": "find_object",
          "navigated": true
        },
        "observation": {
          "target_object_visible": true,
          "visible_objects": [
            "药盒"
          ],
          "target_object_location": "边桌",
          "current_location": "客厅"
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
      "last_verification_result": {
        "scope": "subtask",
        "passed": true,
        "verified_facts": [
          "观察到药盒"
        ],
        "missing_evidence": [],
        "failed_reason": null,
        "confidence": 0.9
      },
      "failure_record_ids": [],
      "negative_evidence": [],
      "retry_counts": {},
      "completed_subtask_ids": [
        "subtask_1",
        "subtask_2"
      ]
    },
    "step_decisions": [
      {
        "subtask_id": "subtask_1",
        "selected_skill": "operation",
        "skill_input": {
          "subtask_id": "subtask_1",
          "subtask_intent": "导航到客厅边桌",
          "target_object": "药盒",
          "recipient": null,
          "observation": null
        },
        "expected_result": "完成操作子任务",
        "reason": "当前子任务需要操作 skill"
      },
      {
        "subtask_id": "subtask_2",
        "selected_skill": "navigation",
        "skill_input": {
          "goal_type": "find_object",
          "target_object": "药盒",
          "room_hint": "客厅",
          "anchor_hint": "边桌",
          "subtask_id": "subtask_2",
          "subtask_intent": "确认药盒在桌子上"
        },
        "expected_result": "找到并观察目标物",
        "reason": "当前子任务需要先导航或观察目标物"
      }
    ],
    "skill_results": [
      {
        "skill": "operation",
        "status": "success",
        "skill_output": {
          "vla_instruction": "根据当前观察执行：导航到客厅边桌",
          "planned_atomic_actions": [
            "operate"
          ]
        },
        "observation": {},
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
        "skill": "navigation",
        "status": "success",
        "skill_output": {
          "goal_type": "find_object",
          "navigated": true
        },
        "observation": {
          "target_object_visible": true,
          "visible_objects": [
            "药盒"
          ],
          "target_object_location": "边桌",
          "current_location": "客厅"
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
    "verification_inputs": [
      {
        "scope": "subtask",
        "subtask_id": "subtask_1",
        "subtask_intent": "导航到客厅边桌",
        "success_criteria": [
          "到达客厅边桌位置"
        ],
        "observation": {},
        "image_input": {
          "enabled": false,
          "image_ref": null,
          "camera": null,
          "timestamp": null,
          "metadata": {}
        }
      },
      {
        "scope": "subtask",
        "subtask_id": "subtask_2",
        "subtask_intent": "确认药盒在桌子上",
        "success_criteria": [
          "观察到药盒在客厅边桌上"
        ],
        "observation": {
          "target_object_visible": true,
          "visible_objects": [
            "药盒"
          ],
          "target_object_location": "边桌",
          "current_location": "客厅"
        },
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
          "结构化 observation 支持该子任务完成"
        ],
        "missing_evidence": [],
        "failed_reason": null,
        "confidence": 0.9
      },
      {
        "scope": "subtask",
        "passed": true,
        "verified_facts": [
          "观察到药盒"
        ],
        "missing_evidence": [],
        "failed_reason": null,
        "confidence": 0.9
      }
    ],
    "failure_records": []
  },
  "evidence_bundle": {
    "task_id": "stage07-check_medicine_stale_recover",
    "evidence_refs": [
      {
        "evidence_id": "verification:stage07-check_medicine_stale_recover:1",
        "evidence_type": "verification_result",
        "source_id": "verification-1",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-29T04:55:22Z",
        "summary": "结构化 observation 支持该子任务完成"
      },
      {
        "evidence_id": "verification:stage07-check_medicine_stale_recover:2",
        "evidence_type": "verification_result",
        "source_id": "verification-2",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-29T04:55:22Z",
        "summary": "观察到药盒"
      },
      {
        "evidence_id": "skill_result:stage07-check_medicine_stale_recover:1:operation",
        "evidence_type": "skill_result",
        "source_id": "skill-1",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-29T04:55:22Z",
        "summary": "operation success"
      },
      {
        "evidence_id": "skill_result:stage07-check_medicine_stale_recover:2:navigation",
        "evidence_type": "skill_result",
        "source_id": "skill-2",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-29T04:55:22Z",
        "summary": "navigation success"
      },
      {
        "evidence_id": "observation:stage07-check_medicine_stale_recover:2",
        "evidence_type": "observation",
        "source_id": "skill-2",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-29T04:55:22Z",
        "summary": "observation: target visible at 边桌"
      },
      {
        "evidence_id": "trace_event:stage07:stage07-check_medicine_stale_recover",
        "evidence_type": "trace_event",
        "source_id": "stage07:stage07-check_medicine_stale_recover",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-29T04:55:22Z",
        "summary": "stage07 task run"
      }
    ],
    "verified_facts": [
      "结构化 observation 支持该子任务完成",
      "观察到药盒"
    ],
    "failure_facts": [],
    "system_failures": [],
    "negative_evidence": []
  },
  "memory_commit": {
    "object_memory_path": "var/homemaster/runs/stage07-check_medicine_stale_recover/memory/object_memory.json",
    "fact_memory_write_count": 1,
    "task_record_write_count": 1,
    "commit_log": {
      "commit_id": "commit:stage07-check_medicine_stale_recover",
      "task_id": "stage07-check_medicine_stale_recover",
      "object_memory_update_count": 1,
      "fact_memory_write_count": 1,
      "task_record_written": true,
      "skipped_candidates": [],
      "index_stale_memory_ids": [
        "mem-medicine-2"
      ],
      "object_memory_path": "var/homemaster/runs/stage07-check_medicine_stale_recover/memory/object_memory.json"
    }
  },
  "case_dir": "tests/homemaster/llm_cases/stage_07/stage07-check_medicine_stale_recover",
  "results_dir": "/Users/wylam/Documents/workspace/HomeMaster/plan/V1.2/test_results/stage_07",
  "runtime_memory_root": "var/homemaster/runs/stage07-check_medicine_stale_recover/memory"
}
```
