# Memory-Grounded Embodied Task Brain MVP Design

Date: 2026-04-19

## Purpose

This project builds a first-month engineering demo for a household elder-care robot task brain. The demo focuses on the high-level agent brain rather than low-level robot control. It should show how a robot can interpret an elder's instruction, retrieve household memory, generate a memory-grounded high-level plan, coordinate navigation and embodied planning modules, verify each subgoal, recover from failures, and update memory after the task.

The MVP is designed as a stable CLI trace demo first. It should also leave clean extension points for later simulator integration, RoboBrain/VLA execution, training, and gateway-based interaction such as Feishu through Hermes.

## Scope

The MVP includes:

- LangGraph-based task orchestration.
- CLI instruction input and CLI trace output.
- Lightweight instruction parsing.
- Memory retrieval before planning.
- LLM-generated high-level plans with schema validation and fallback.
- JSON mock household world with BEHAVIOR-like predicates.
- Mock VLN, mock perception, and mock atomic executor adapters.
- RoboBrain server adapter for real atomic action-list planning.
- Verification after navigation, observation, embodied manipulation, and final task completion.
- Recovery policies for stale memory, failed manipulation, candidate exhaustion, and distractor rejection.
- Object memory and episodic memory updates.
- Scenario tests for full task traces.
- Optional Hermes/Feishu gateway milestone if the core CLI demo stabilizes early.

The MVP does not include:

- Real robot control.
- Full BEHAVIOR/OmniGibson integration.
- Full AI2-THOR, Habitat, or other simulator integration.
- Training a VLA or end-to-end robot policy.
- Product-grade safety, permissions, cancellation, or long-running task management.
- Complex container search such as opening cabinets or drawers as a primary path.
- Multi-robot coordination or full RoboOS-style distributed infrastructure.

## Design Principles

The task brain is not an end-to-end monolithic model. It is a modular, observable, memory-grounded orchestration system.

Key principles:

- Memory must be retrieved before executable high-level planning.
- LLM planning must be constrained by schemas, capabilities, memory evidence, and validation.
- RoboBrain is an embodied subgoal planner, not the final success judge.
- Execution is never treated as success until verification passes.
- Failure information is useful memory and must be recorded.
- CLI traces are first-class demo artifacts and future training data.
- Mock components should use realistic interfaces so they can later be replaced by simulator or real robot modules.

## High-Level Architecture

The system has three layers:

1. Task Brain / LangGraph

   The high-level task brain manages instruction parsing, memory retrieval, task context construction, LLM high-level planning, plan validation, subgoal execution, verification, recovery, memory updates, and trace logging.

2. RoboBrain Server

   RoboBrain receives a verified local embodied subgoal and current observation context. It returns a real atomic action list, expected state delta, confidence, and planner status.

3. Mock World and Mock Atomic Executor

   The mock world provides a controllable household environment for the first demo. The mock atomic executor consumes RoboBrain atomic actions and either applies state deltas to the mock world or injects controlled failures.

The first-month runtime path is:

```text
CLI instruction
  -> Task Brain
  -> mock VLN / mock perception / RoboBrain server
  -> mock atomic executor
  -> verification
  -> recovery or continue
  -> memory update
  -> CLI trace
```

## Main Task Flow

The graph order is:

```text
input_instruction
  -> parse_instruction
  -> retrieve_memory
  -> build_task_context
  -> llm_generate_high_level_plan
  -> validate_plan
  -> execute_subgoal_loop
  -> final_task_verification
  -> update_memory
  -> respond_with_trace
```

Two ordering rules are required:

- `retrieve_memory` must happen before `llm_generate_high_level_plan`.
- `final_task_verification` and subgoal verification must happen before marking any task or subgoal successful.

The subgoal loop is:

```text
select_next_subgoal
  -> execute_adapter_or_planner
  -> update_runtime_state
  -> verify_subgoal
  -> recover_or_continue
```

If a verification step fails, the task brain does not keep executing the old plan blindly. It enters recovery, which may retry the same subgoal, switch candidate locations, replan with updated context, ask for clarification, or stop safely.

## Module Responsibilities

### Instruction Adapter

The MVP supports CLI input. Hermes/Feishu is an optional milestone that wraps the same task request interface.

Input:

```json
{
  "source": "cli",
  "user_id": "demo_user",
  "utterance": "去厨房找水杯，然后拿给我"
}
```

Output:

```json
{
  "task_request_id": "req_001",
  "utterance": "去厨房找水杯，然后拿给我",
  "source": "cli"
}
```

### Task Parser

The parser performs lightweight semantic extraction only. It does not create the executable plan.

Output example:

```json
{
  "intent": "fetch_object",
  "target_object": {
    "category": "cup",
    "attributes": [],
    "quantity": 1
  },
  "explicit_location_hint": "kitchen",
  "delivery_target": "user",
  "requires_navigation": true,
  "requires_manipulation": true
}
```

### Memory Retriever

The memory retriever returns object memory, episodic memory, user habits, prior failures, and negative evidence relevant to the parsed task.

Output example:

```json
{
  "object_candidates": [
    {
      "location": "kitchen/kitchen_table",
      "confidence": 0.78,
      "reason": "prior_success_and_common_location",
      "memory_id": "objmem_cup_kitchen_table"
    },
    {
      "location": "kitchen/cabinet",
      "confidence": 0.52,
      "reason": "storage_location",
      "memory_id": "objmem_cup_cabinet"
    }
  ],
  "negative_evidence": [
    {
      "location": "living_room/coffee_table",
      "object_category": "cup",
      "status": "searched_not_found_recently"
    }
  ]
}
```

### Task Context Builder

The context builder combines parsed task data, memory hits, world capabilities, robot state, and adapter availability into a single planning context.

It must include:

- The original user instruction.
- Parsed task fields.
- Retrieved memory candidates.
- Negative evidence and excluded locations.
- Capability registry.
- Current robot location and holding state.
- Any known user location.
- Mock world constraints.

### LLM High-Level Planner

The LLM planner generates a structured high-level plan. It can choose subgoals but cannot emit low-level robot actions.

Allowed subgoal types:

- `navigate`
- `observe`
- `verify_object_presence`
- `embodied_manipulation`
- `return_to_user`
- `ask_clarification`
- `report_failure`

Disallowed planner outputs:

- `move_arm_to_pregrasp`
- `align_gripper`
- `close_gripper`
- `lift`
- Any other atomic robot action

Plan output example:

```json
{
  "plan_id": "fetch_cup_001",
  "memory_grounding": [
    {
      "used_memory_id": "objmem_cup_kitchen_table",
      "reason": "highest-confidence remembered cup location"
    }
  ],
  "subgoals": [
    {
      "id": "sg1",
      "type": "navigate",
      "target": "kitchen_table_viewpoint",
      "success_conditions": [
        ["inroom", "robot", "kitchen"],
        ["visible", "kitchen_table"]
      ]
    },
    {
      "id": "sg2",
      "type": "verify_object_presence",
      "target_category": "cup",
      "success_conditions": [
        ["visible_category", "cup"],
        ["reachable_category", "cup"]
      ]
    },
    {
      "id": "sg3",
      "type": "embodied_manipulation",
      "planner": "robobrain_server",
      "subgoal": "pick_up_object",
      "target_ref": "selected_object",
      "success_conditions": [
        ["holding_category", "robot", "cup"]
      ]
    },
    {
      "id": "sg4",
      "type": "navigate",
      "target": "user_location",
      "success_conditions": [
        ["near", "robot", "user"]
      ]
    }
  ]
}
```

### Plan Validator

The validator ensures the LLM output is executable and safe for the MVP.

Validation checks:

- The plan conforms to the JSON schema.
- The plan includes `memory_grounding`.
- Each subgoal type is registered in the capability registry.
- Each subgoal has success conditions.
- RoboBrain manipulation is not called before object presence verification.
- The plan does not contain atomic robot actions.
- The plan does not use unsupported mock-world capabilities.
- The plan does not skip final task verification.

If validation fails, the system falls back to a rule-based template plan for the supported task type.

### Capability Registry

The registry describes the modules the task brain can call.

Example:

```json
{
  "name": "robobrain_plan",
  "input_schema": "EmbodiedSubgoalRequest",
  "output_schema": "AtomicPlanResponse",
  "timeout_s": 30,
  "failure_modes": [
    "planner_unavailable",
    "no_valid_plan",
    "low_confidence"
  ]
}
```

Initial capabilities:

- `mock_vln.navigate`
- `mock_perception.observe`
- `robobrain.plan`
- `mock_atomic_executor.execute`
- `verification.evaluate`
- `memory.update`

### RoboBrain Adapter

The adapter sends embodied manipulation subgoals to the RoboBrain server.

Request example:

```json
{
  "subgoal": "pick_up_object",
  "target_object": {
    "id": "cup_1",
    "category": "cup",
    "states": {
      "visible": true,
      "reachable": true,
      "graspable": true
    }
  },
  "current_observation": {
    "viewpoint": "kitchen_table_viewpoint",
    "visible_objects": ["cup_1", "kettle_1", "bowl_1"]
  },
  "constraints": [
    "pick target object only",
    "avoid kettle"
  ],
  "success_conditions": [
    ["holding", "robot", "cup_1"]
  ]
}
```

Response example:

```json
{
  "status": "planned",
  "target_object_id": "cup_1",
  "atomic_action_list": [
    {"action": "move_arm_to_pregrasp", "target": "cup_1"},
    {"action": "align_gripper", "target": "cup_1"},
    {"action": "close_gripper", "target": "cup_1"},
    {"action": "lift", "target": "cup_1"}
  ],
  "expected_state_delta": [
    ["holding", "robot", "cup_1"]
  ],
  "confidence": 0.81
}
```

### Mock Atomic Executor

The executor consumes RoboBrain atomic action lists and applies state changes to the mock world.

Success example:

```json
{
  "status": "success",
  "applied_state_delta": [
    ["holding", "robot", "cup_1"],
    ["not", ["ontop", "cup_1", "kitchen_table"]]
  ],
  "evidence": {
    "robot_holding": "cup_1"
  }
}
```

Failure example:

```json
{
  "status": "failed",
  "failure_reason": "object_slipped",
  "applied_state_delta": [],
  "evidence": {
    "robot_holding": null
  }
}
```

### Verification Engine

The verification engine checks whether success conditions are satisfied by the current world state, observation state, and execution evidence.

Verification layers:

- Arrival verification: confirms target area or viewpoint was reached.
- Object presence verification: confirms the target object category or object id is visible and usable.
- Manipulation verification: confirms the expected object state changed, such as `holding(robot, cup_1)`.
- Final task verification: confirms the original user goal is complete.

The MVP focuses on task-level and subgoal-level verification. Atomic-action-level verification is summarized through the mock executor and RoboBrain trace.

### Recovery Policy

Recovery actions:

- Retry the same subgoal if the target remains visible and the retry budget allows it.
- Switch to the next candidate location if object presence verification fails.
- Mark a candidate as excluded for the current task after a failed search.
- Replan using updated context after stale memory is detected.
- Ask clarification or report failure when candidates are exhausted.

The task brain must not silently report success when verification fails.

### Memory Updater

Memory updates include:

- Successful object location updates.
- Stale memory confidence reductions.
- Negative evidence for searched locations.
- Episodic records for successful and failed task attempts.
- Recovery traces such as retry success or candidate switching.

Example update:

```json
{
  "object_category": "medicine_box",
  "old_location": {
    "room": "living_room",
    "support": "coffee_table",
    "confidence": 0.82
  },
  "new_location": {
    "room": "kitchen",
    "support": "kitchen_table",
    "confidence": 0.93
  },
  "negative_evidence": [
    {
      "location": "living_room/coffee_table",
      "status": "searched_not_found"
    }
  ]
}
```

### Trace Logger

The CLI trace should display:

- Input instruction.
- Parsed task.
- Retrieved memory candidates.
- High-level plan and memory grounding.
- Plan validation result.
- Each module call and response.
- RoboBrain atomic action list.
- Verification result after each subgoal.
- Recovery decisions.
- Memory updates.
- Final response.

Trace events should also be written as structured JSONL so the same data can later support dashboards, debugging, or training.

## Mock World Design

The MVP uses JSON files:

- `world.json`: current mock world truth.
- `memory.json`: what the robot believes or remembers.
- `failures.json`: failure injection rules.
- `tasks.json`: demo instructions and expected success conditions.
- `trace.jsonl`: emitted run trace events.

World and memory must be separate. This is necessary to demonstrate stale memory, verification failure, recovery, and memory update.

Initial BEHAVIOR-like predicate set:

```text
inroom(entity, room)
connected(room_a, room_b)
at(robot, viewpoint)
visible(entity)
visible_from(entity, viewpoint)
ontop(object, furniture)
inside(object, container)
near(entity_a, entity_b)
reachable(object)
graspable(object)
holding(robot, object)
open(container)
closed(container)
searched(location, object_category)
excluded_for_task(location, object_category)
```

Mainline demo uses open-surface search and manipulation. `inside`, `open`, and `closed` are reserved for later container-search extensions.

## Supported Demo Tasks

The MVP supports two task types:

1. Check object presence

   Example: "去桌子那边看看药盒是不是还在。"

   The system navigates to a memory-grounded candidate location, verifies arrival, observes the scene, checks object presence, updates memory, and reports the result.

2. Fetch object

   Example: "去厨房找水杯，然后拿给我。"

   The system retrieves memory, plans a candidate search path, navigates, verifies object presence, asks RoboBrain for an atomic pickup plan, mock-executes it, verifies the object is held, returns to the user, verifies final success, and updates memory.

Deferred task types:

- Opening cabinets or drawers to search.
- Closing drawers.
- Multi-object tasks.
- Long dialogue repair.
- Real simulator execution.

## Required Scenarios

The CLI demo should include three primary scenarios:

1. Medicine check succeeds

   Memory says the medicine box is on the living-room coffee table. The mock world agrees. The agent navigates, verifies the target, updates memory, and reports success.

2. Medicine memory is stale and recovery succeeds

   Memory says the medicine box is on the living-room coffee table, but the mock world has it on the kitchen table. The agent searches the remembered location, fails verification, marks the old candidate as excluded, switches to the next candidate, finds the box, and updates memory.

3. Cup fetch with manipulation retry

   The agent finds a cup on the kitchen table. RoboBrain returns an atomic pickup plan. The first mock execution fails with `object_slipped`. Verification fails, the agent retries, the second execution succeeds, and final task verification passes.

## Testing Strategy

The test suite should prioritize scenario tests because the main contribution is the end-to-end decision loop.

Required scenario tests:

- `check_medicine_success`: memory is correct and the task completes.
- `check_medicine_memory_stale_recover`: old memory is rejected and a later candidate succeeds.
- `fetch_cup_success_with_robobrain_plan`: RoboBrain returns an atomic action list and execution succeeds.
- `fetch_cup_grasp_retry_success`: first execution fails, verification catches it, and retry succeeds.
- `object_not_found_candidate_exhausted`: all candidates fail and the system reports or asks for clarification instead of fabricating success.
- `distractor_object_rejected`: distractor objects are visible but not accepted as the target.

Required planner tests:

- High-level planning occurs after memory retrieval.
- Plans include memory grounding.
- Plans do not contain atomic robot actions.
- Plans only call registered capabilities.
- Plans do not call RoboBrain manipulation before object presence verification.
- Invalid LLM plans fall back to supported rule templates.

Required verification tests:

- Execution result alone does not mark a subgoal successful.
- Missing target after navigation triggers candidate exclusion.
- Failed manipulation triggers retry or recovery.
- Final response cannot be success unless final task verification passes.

Optional gateway tests for the Hermes milestone:

- Feishu messages convert into `TaskRequest`.
- Non-allowlisted users cannot trigger runs.
- Task failures produce readable replies.
- Long traces can be summarized for Feishu.

## Hermes / Feishu Optional Milestone

Hermes/Feishu is an optional MVP milestone, not a blocker for the CLI demo.

Intended flow:

```text
Feishu message
  -> Hermes Gateway
  -> RobotTask Bridge
  -> Task Brain run
  -> trace summary
  -> Feishu reply
```

Hermes should only handle:

- Message intake.
- User/session identity.
- Optional allowlist.
- Task request forwarding.
- Result reply.

Hermes should not handle:

- High-level task planning.
- Memory updates.
- Recovery decisions.
- Robot execution.

Estimated effort:

- CLI MVP: required mainline.
- Basic Hermes text entry: 1-2 days if local gateway and Feishu credentials are already usable.
- Stable Feishu demo with trace summaries and errors: 2-4 days.
- Voice, cancellation, task progress, and product-grade permissions: out of scope for the first month.

## RoboOS-Inspired Elements

RoboOS is used as architectural inspiration rather than a runtime dependency.

Useful concepts to borrow:

- Brain-Cerebellum split: Task Brain delegates local embodied action planning to RoboBrain and execution to adapters.
- Skill library: MVP uses a capability registry with typed module interfaces.
- Shared memory: runtime state, world state, object memory, episodic memory, and trace events are centralized.
- Error correction: every subgoal is verified and can trigger recovery.

Concepts deferred:

- Multi-robot orchestration.
- Cross-embodiment dispatch.
- Distributed real-time memory infrastructure.
- Full RoboOS cloud-edge runtime.

## Future Simulation Path

The first-month MVP does not depend on a full simulator. After the CLI demo, likely integration paths are:

- AI2-THOR / ALFRED for language-conditioned household tasks and object interactions.
- BEHAVIOR / OmniGibson for richer long-horizon household activities and object-state complexity.
- Habitat / HomeRobot for navigation and mobile manipulation evaluation.
- VirtualHome for symbolic household planning and program-like action validation.
- ManiSkill, RLBench, or CALVIN for focused manipulation/VLA training rather than whole household task-brain evaluation.

The recommended next simulator after the MVP is AI2-THOR/ALFRED because it is lighter than BEHAVIOR/OmniGibson and closer to the first set of household tasks.

## Future Research Extensions

The MVP should produce trace data useful for later research:

- Memory retrieval and candidate ranking.
- Verification models.
- Recovery policy learning.
- High-level planning policy training.
- Harness-style self-improvement using failed traces.

The most natural next research direction is training plus verification/recovery, because the MVP directly records subgoal attempts, observations, failures, recovery choices, and outcomes.

## Acceptance Criteria

The MVP is complete when:

- The CLI can run the three required demo scenarios.
- The trace shows parse, memory retrieval, LLM plan, validation, module calls, verification, recovery, and memory update.
- At least one scenario demonstrates stale-memory recovery.
- At least one scenario demonstrates RoboBrain atomic action-list integration.
- At least one scenario demonstrates post-action verification catching a failed execution.
- Scenario tests cover the six required cases.
- Planner tests prove memory retrieval happens before high-level planning.
- The system never marks a subgoal or task successful before verification passes.
- Hermes/Feishu is either implemented as an optional milestone or documented as deferred without blocking the CLI demo.

