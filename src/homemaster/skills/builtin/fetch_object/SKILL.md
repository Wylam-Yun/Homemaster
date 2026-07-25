---
name: fetch_object
description: Navigate to a location, find a target object, pick it up, and deliver it to the user.
tool_names: ["task_interpreter", "memory_retriever", "target_grounder", "load_skill", "robot_go_to", "observe", "robot_manipulate", "robot_verify", "memory_writer", "task_summarizer"]
constraints: ["Must verify object identity before picking up", "Must check delivery target before placing"]
success_criteria: ["Object delivered to user's location", "Memory updated with new object location"]
---

## Capabilities

This skill enables the robot to fetch a requested object for the user. It combines
navigation, perception, and manipulation to locate, pick up, and deliver objects.

## Contributed Tools

- **task_interpreter**: Extracts target object from user request
- **memory_retriever**: Queries object memory for likely locations
- **target_grounder**: Selects best location candidate
- **robot_go_to**: Moves robot to candidate location
- **observe**: Confirms object presence
- **robot_manipulate**: Picks up and delivers the object
- **robot_verify**: Confirms successful delivery
- **memory_writer**: Updates object location after delivery
- **task_summarizer**: Records task completion

## Constraints

- Verify object identity before picking up
- Check delivery target before placing
- Update memory after moving objects

## Success Criteria

- Object delivered to user's location
- Memory updated with new object location
