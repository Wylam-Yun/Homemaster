# Stage 07 Run - stage07-object_not_found

Status: PASS

## Summary

- Scenario: object_not_found
- Utterance: 去厨房找水杯，然后拿给我
- Final status: failed

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
    "selected_target": "mem-cup-1"
  },
  "stage05": {
    "status": "PASS",
    "mode": "real_mimo",
    "step_decision": {
      "mode": "real_mimo",
      "status": "PASS",
      "subtask_id": "find_cup",
      "selected_skill": "navigation",
      "provider": {
        "provider_name": "Mimo",
        "model": "mimo-v2-pro",
        "protocol": "anthropic",
        "elapsed_ms": 31100.154291999843,
        "attempts": [
          {
            "key_index": 1,
            "status_code": 200,
            "elapsed_ms": 31100.154291999843
          }
        ]
      }
    },
    "final_task_status": "failed",
    "mock_skills": true
  },
  "stage06": {
    "status": "PASS",
    "mode": "real_mimo",
    "task_summary_result": "failed",
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
  "world_path": "/Users/wylam/Documents/workspace/HomeMaster/data/scenarios/object_not_found/world.json",
  "base_memory_path": "/Users/wylam/Documents/workspace/HomeMaster/data/scenarios/object_not_found/memory.json",
  "runtime_memory_root": "var/homemaster/runs/stage07-object_not_found/memory",
  "case_dir": "tests/homemaster/llm_cases/stage_07/stage07-object_not_found",
  "results_dir": "/Users/wylam/Documents/workspace/HomeMaster/plan/V1.2/test_results/stage_07"
}
```

## Full Actual

```json
{
  "run_id": "stage07-object_not_found",
  "scenario": "object_not_found",
  "utterance": "去厨房找水杯，然后拿给我",
  "final_status": "failed",
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
      "selected_target": "mem-cup-1"
    },
    "stage05": {
      "status": "PASS",
      "mode": "real_mimo",
      "step_decision": {
        "mode": "real_mimo",
        "status": "PASS",
        "subtask_id": "find_cup",
        "selected_skill": "navigation",
        "provider": {
          "provider_name": "Mimo",
          "model": "mimo-v2-pro",
          "protocol": "anthropic",
          "elapsed_ms": 31100.154291999843,
          "attempts": [
            {
              "key_index": 1,
              "status_code": 200,
              "elapsed_ms": 31100.154291999843
            }
          ]
        }
      },
      "final_task_status": "failed",
      "mock_skills": true
    },
    "stage06": {
      "status": "PASS",
      "mode": "real_mimo",
      "task_summary_result": "failed",
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
    "world_path": "/Users/wylam/Documents/workspace/HomeMaster/data/scenarios/object_not_found/world.json",
    "base_memory_path": "/Users/wylam/Documents/workspace/HomeMaster/data/scenarios/object_not_found/memory.json",
    "runtime_memory_root": "var/homemaster/runs/stage07-object_not_found/memory",
    "case_dir": "tests/homemaster/llm_cases/stage_07/stage07-object_not_found",
    "results_dir": "/Users/wylam/Documents/workspace/HomeMaster/plan/V1.2/test_results/stage_07"
  },
  "task_card": {
    "task_type": "fetch_object",
    "target": "水杯",
    "delivery_target": "user",
    "location_hint": "厨房",
    "success_criteria": [
      "机器人成功拿到水杯并交付给用户"
    ],
    "needs_clarification": false,
    "clarification_question": null,
    "confidence": 0.95
  },
  "planning_context": {
    "task_card": {
      "task_type": "fetch_object",
      "target": "水杯",
      "delivery_target": "user",
      "location_hint": "厨房",
      "success_criteria": [
        "机器人成功拿到水杯并交付给用户"
      ],
      "needs_clarification": false,
      "clarification_question": null,
      "confidence": 0.95
    },
    "retrieval_query": {
      "query_text": "水杯 杯子 水壶 茶杯 water cup mug 厨房",
      "target_category": null,
      "target_aliases": [
        "水杯",
        "杯子",
        "水壶",
        "茶杯"
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
      "reason": "根据 TaskCard 检索水杯在厨房的位置以执行 fetch_object 任务"
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
      "excluded": [],
      "retrieval_query": {
        "query_text": "水杯 杯子 水壶 茶杯 water cup mug 厨房",
        "target_category": null,
        "target_aliases": [
          "水杯",
          "杯子",
          "水壶",
          "茶杯"
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
        "reason": "根据 TaskCard 检索水杯在厨房的位置以执行 fetch_object 任务"
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
        "tokenized_query": "[REDACTED]",
        "ranking_stage": "bm25_dense_fusion"
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
        "final_score": 0.4327868852459016,
        "ranking_reasons": [
          "bm25_rank=1",
          "dense_rank=1",
          "metadata_target_alias_match",
          "metadata_location_match",
          "metadata_medium_confidence"
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
      "grounded reliable memory target is available for Stage 05 planning"
    ]
  },
  "orchestration_plan": {
    "goal": "机器人成功拿到水杯并交付给用户",
    "subtasks": [
      {
        "id": "find_cup",
        "intent": "找到水杯",
        "target_object": "水杯",
        "recipient": null,
        "room_hint": "厨房",
        "anchor_hint": "厨房餐桌",
        "success_criteria": [
          "水杯被成功定位在厨房餐桌"
        ],
        "depends_on": []
      },
      {
        "id": "pick_cup",
        "intent": "拿起水杯",
        "target_object": "水杯",
        "recipient": null,
        "room_hint": null,
        "anchor_hint": null,
        "success_criteria": [
          "水杯被机器人成功抓取"
        ],
        "depends_on": [
          "find_cup"
        ]
      },
      {
        "id": "return_to_user",
        "intent": "回到用户位置",
        "target_object": null,
        "recipient": "user",
        "room_hint": null,
        "anchor_hint": null,
        "success_criteria": [
          "机器人到达用户所在位置"
        ],
        "depends_on": [
          "pick_cup"
        ]
      },
      {
        "id": "deliver_cup",
        "intent": "交付水杯给用户",
        "target_object": "水杯",
        "recipient": "user",
        "room_hint": null,
        "anchor_hint": null,
        "success_criteria": [
          "水杯被成功交付给用户"
        ],
        "depends_on": [
          "return_to_user"
        ]
      },
      {
        "id": "confirm_completion",
        "intent": "确认任务完成",
        "target_object": null,
        "recipient": null,
        "room_hint": null,
        "anchor_hint": null,
        "success_criteria": [
          "用户确认收到水杯"
        ],
        "depends_on": [
          "deliver_cup"
        ]
      }
    ],
    "confidence": 0.95
  },
  "execution_result": {
    "plan": {
      "goal": "机器人成功拿到水杯并交付给用户",
      "subtasks": [
        {
          "id": "find_cup",
          "intent": "找到水杯",
          "target_object": "水杯",
          "recipient": null,
          "room_hint": "厨房",
          "anchor_hint": "厨房餐桌",
          "success_criteria": [
            "水杯被成功定位在厨房餐桌"
          ],
          "depends_on": []
        },
        {
          "id": "pick_cup",
          "intent": "拿起水杯",
          "target_object": "水杯",
          "recipient": null,
          "room_hint": null,
          "anchor_hint": null,
          "success_criteria": [
            "水杯被机器人成功抓取"
          ],
          "depends_on": [
            "find_cup"
          ]
        },
        {
          "id": "return_to_user",
          "intent": "回到用户位置",
          "target_object": null,
          "recipient": "user",
          "room_hint": null,
          "anchor_hint": null,
          "success_criteria": [
            "机器人到达用户所在位置"
          ],
          "depends_on": [
            "pick_cup"
          ]
        },
        {
          "id": "deliver_cup",
          "intent": "交付水杯给用户",
          "target_object": "水杯",
          "recipient": "user",
          "room_hint": null,
          "anchor_hint": null,
          "success_criteria": [
            "水杯被成功交付给用户"
          ],
          "depends_on": [
            "return_to_user"
          ]
        },
        {
          "id": "confirm_completion",
          "intent": "确认任务完成",
          "target_object": null,
          "recipient": null,
          "room_hint": null,
          "anchor_hint": null,
          "success_criteria": [
            "用户确认收到水杯"
          ],
          "depends_on": [
            "deliver_cup"
          ]
        }
      ],
      "confidence": 0.95
    },
    "final_state": {
      "task_status": "failed",
      "current_subtask_id": "find_cup",
      "subtasks": [
        {
          "subtask_id": "find_cup",
          "status": "failed",
          "depends_on": [],
          "attempt_count": 1,
          "last_started_at": null,
          "last_completed_at": null,
          "last_skill": null,
          "last_observation": null,
          "last_verification_result": null,
          "failure_record_ids": [
            "failure-1"
          ]
        },
        {
          "subtask_id": "pick_cup",
          "status": "pending",
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
        },
        {
          "subtask_id": "return_to_user",
          "status": "pending",
          "depends_on": [
            "pick_cup"
          ],
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
          "status": "pending",
          "depends_on": [
            "return_to_user"
          ],
          "attempt_count": 0,
          "last_started_at": null,
          "last_completed_at": null,
          "last_skill": null,
          "last_observation": null,
          "last_verification_result": null,
          "failure_record_ids": []
        },
        {
          "subtask_id": "confirm_completion",
          "status": "pending",
          "depends_on": [
            "deliver_cup"
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
      "user_location": "user_start",
      "current_location": "robot_start",
      "last_observation": null,
      "last_skill_call": {
        "subtask_id": "find_cup",
        "selected_skill": "navigation",
        "skill_input": {
          "goal_type": "find_object",
          "target_object": "水杯",
          "room_hint": "厨房",
          "anchor_hint": "厨房餐桌",
          "subtask_id": "find_cup",
          "subtask_intent": "找到水杯",
          "force_no_object": true
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
          "target_object_visible": false,
          "visible_objects": [],
          "current_location": "robot_start"
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
        "passed": false,
        "verified_facts": [],
        "missing_evidence": [
          "没有观察到水杯"
        ],
        "failed_reason": "没有观察到水杯",
        "confidence": 0.8
      },
      "failure_record_ids": [
        "failure-1"
      ],
      "negative_evidence": [
        {
          "subtask_id": "find_cup",
          "reason": "verification_failed",
          "memory_id": "mem-cup-1",
          "location_key": "kitchen:anchor_kitchen_table_1",
          "observation": {
            "target_object_visible": false,
            "visible_objects": [],
            "current_location": "robot_start"
          }
        }
      ],
      "retry_counts": {
        "find_cup": 1
      },
      "completed_subtask_ids": []
    },
    "step_decisions": [
      {
        "subtask_id": "find_cup",
        "selected_skill": "navigation",
        "skill_input": {
          "goal_type": "find_object",
          "target_object": "水杯",
          "room_hint": "厨房",
          "anchor_hint": "厨房餐桌",
          "subtask_id": "find_cup",
          "subtask_intent": "找到水杯",
          "force_no_object": true
        },
        "expected_result": "找到并观察目标物",
        "reason": "当前子任务需要先导航或观察目标物"
      }
    ],
    "skill_results": [
      {
        "skill": "navigation",
        "status": "success",
        "skill_output": {
          "goal_type": "find_object",
          "navigated": true
        },
        "observation": {
          "target_object_visible": false,
          "visible_objects": [],
          "current_location": "robot_start"
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
        "subtask_id": "find_cup",
        "subtask_intent": "找到水杯",
        "success_criteria": [
          "水杯被成功定位在厨房餐桌"
        ],
        "observation": {
          "target_object_visible": false,
          "visible_objects": [],
          "current_location": "robot_start"
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
        "passed": false,
        "verified_facts": [],
        "missing_evidence": [
          "没有观察到水杯"
        ],
        "failed_reason": "没有观察到水杯",
        "confidence": 0.8
      }
    ],
    "failure_records": [
      {
        "failure_id": "failure-1",
        "subtask_id": "find_cup",
        "subtask_intent": "找到水杯",
        "skill": "navigation",
        "failure_type": "verification_failed",
        "failed_reason": "没有观察到水杯",
        "skill_input": {
          "goal_type": "find_object",
          "target_object": "水杯",
          "room_hint": "厨房",
          "anchor_hint": "厨房餐桌",
          "subtask_id": "find_cup",
          "subtask_intent": "找到水杯",
          "force_no_object": true
        },
        "skill_output": {
          "goal_type": "find_object",
          "navigated": true
        },
        "verification_result": {
          "scope": "subtask",
          "passed": false,
          "verified_facts": [],
          "missing_evidence": [
            "没有观察到水杯"
          ],
          "failed_reason": "没有观察到水杯",
          "confidence": 0.8
        },
        "observation": {
          "target_object_visible": false,
          "visible_objects": [],
          "current_location": "robot_start"
        },
        "negative_evidence": [
          {
            "subtask_id": "find_cup",
            "reason": "verification_failed",
            "memory_id": "mem-cup-1",
            "location_key": "kitchen:anchor_kitchen_table_1",
            "observation": {
              "target_object_visible": false,
              "visible_objects": [],
              "current_location": "robot_start"
            }
          }
        ],
        "retry_count": 0,
        "created_at": "2026-04-29T04:59:11Z",
        "event_memory_candidate": {
          "type": "verification_failed",
          "subtask_id": "find_cup",
          "reason": "没有观察到水杯",
          "negative_evidence": [
            {
              "subtask_id": "find_cup",
              "reason": "verification_failed",
              "memory_id": "mem-cup-1",
              "location_key": "kitchen:anchor_kitchen_table_1",
              "observation": {
                "target_object_visible": false,
                "visible_objects": [],
                "current_location": "robot_start"
              }
            }
          ]
        }
      }
    ]
  },
  "evidence_bundle": {
    "task_id": "stage07-object_not_found",
    "evidence_refs": [
      {
        "evidence_id": "verification:stage07-object_not_found:1",
        "evidence_type": "verification_result",
        "source_id": "verification-1",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-29T04:59:11Z",
        "summary": "没有观察到水杯"
      },
      {
        "evidence_id": "skill_result:stage07-object_not_found:1:navigation",
        "evidence_type": "skill_result",
        "source_id": "skill-1",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-29T04:59:11Z",
        "summary": "navigation success"
      },
      {
        "evidence_id": "observation:stage07-object_not_found:1",
        "evidence_type": "observation",
        "source_id": "skill-1",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-29T04:59:11Z",
        "summary": "observation: target not visible"
      },
      {
        "evidence_id": "failure:failure-1",
        "evidence_type": "failure_record",
        "source_id": "failure-1",
        "subtask_id": "find_cup",
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-29T04:59:11Z",
        "summary": "没有观察到水杯"
      },
      {
        "evidence_id": "trace_event:stage07:stage07-object_not_found",
        "evidence_type": "trace_event",
        "source_id": "stage07:stage07-object_not_found",
        "subtask_id": null,
        "memory_id": null,
        "location_key": null,
        "created_at": "2026-04-29T04:59:11Z",
        "summary": "stage07 task run"
      }
    ],
    "verified_facts": [],
    "failure_facts": [
      "没有观察到水杯",
      "没有观察到水杯"
    ],
    "system_failures": [],
    "negative_evidence": [
      {
        "subtask_id": "find_cup",
        "reason": "verification_failed",
        "memory_id": "mem-cup-1",
        "location_key": "kitchen:anchor_kitchen_table_1",
        "observation": {
          "target_object_visible": false,
          "visible_objects": [],
          "current_location": "robot_start"
        },
        "failure_record_id": "failure-1",
        "created_at": "2026-04-29T04:59:11Z",
        "stale_after": "2026-05-06T04:59:11Z"
      }
    ]
  },
  "memory_commit": {
    "object_memory_path": "var/homemaster/runs/stage07-object_not_found/memory/object_memory.json",
    "fact_memory_write_count": 1,
    "task_record_write_count": 1,
    "commit_log": {
      "commit_id": "commit:stage07-object_not_found",
      "task_id": "stage07-object_not_found",
      "object_memory_update_count": 1,
      "fact_memory_write_count": 1,
      "task_record_written": true,
      "skipped_candidates": [],
      "index_stale_memory_ids": [
        "mem-cup-1"
      ],
      "object_memory_path": "var/homemaster/runs/stage07-object_not_found/memory/object_memory.json"
    }
  },
  "case_dir": "tests/homemaster/llm_cases/stage_07/stage07-object_not_found",
  "results_dir": "/Users/wylam/Documents/workspace/HomeMaster/plan/V1.2/test_results/stage_07",
  "runtime_memory_root": "var/homemaster/runs/stage07-object_not_found/memory"
}
```
