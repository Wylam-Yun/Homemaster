---
name: fetch_object
description: Navigate to a location, find a target object, pick it up, and deliver it to the user.
allowed_tools: ["understand_task", "retrieve_memory", "ground_target", "get_skill", "navigate", "observe", "manipulate", "verify", "update_memory"]
constraints: ["Must verify object identity before picking up", "Must check delivery target before placing", "Recovery loop handles failed subtasks"]
success_criteria: ["Object delivered to user's location", "Memory updated with new object location"]
---

## Strategy
1. Understand the user's intent and extract target object
2. Retrieve memory for object location candidates
3. Ground the target by selecting the best memory anchor
4. Load this skill's full context via get_skill if needed
5. Navigate to the candidate location
6. Observe to confirm object presence
7. Manipulate to pick up the object
8. Verify the object is correctly held
9. Deliver to user and update memory

## Constraints
- Verify object identity before picking up
- Check delivery target before placing
- Failed subtasks produce evidence; Mimo decides recovery on next turn

## Success Criteria
- Object delivered to user's location
- Memory updated with new object location
