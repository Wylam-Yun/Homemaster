---
name: check_object_state
description: Navigate to a location, observe a target object, and report its state to the user.
tool_names: ["task_interpreter", "memory_retriever", "target_grounder", "skill_view", "robot_go_to", "observe", "robot_verify", "memory_writer", "task_summarizer"]
constraints: ["Must ground target before navigating", "Must verify observation before reporting"]
success_criteria: ["Object state reported to user", "Memory updated with findings"]
---

## Capabilities

This skill enables the robot to check the state of a requested object. It combines
navigation and perception to locate and observe objects, then reports findings to the user.

## Contributed Tools

- **task_interpreter**: Extracts target object from user request
- **memory_retriever**: Queries object memory for likely locations
- **target_grounder**: Selects best location candidate
- **robot_go_to**: Moves robot to candidate location
- **observe**: Observes object state
- **robot_verify**: Confirms observation matches expectations
- **memory_writer**: Updates memory with observations
- **task_summarizer**: Records task completion

## Constraints

- Observe and verify are primary actions; do not default to manipulate
- Update memory with observations

## Success Criteria

- Object state reported to user
- Memory updated with findings
