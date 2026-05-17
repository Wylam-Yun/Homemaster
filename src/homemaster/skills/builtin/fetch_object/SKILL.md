---
name: fetch_object
description: Navigate to a location, find a target object, pick it up, and deliver it to the user.
allowed_tools: ["understand_task", "retrieve_memory", "ground_target", "get_skill", "navigate", "observe", "manipulate", "verify", "update_memory"]
constraints: ["Must verify object identity before picking up", "Must check delivery target before placing", "Recovery loop handles failed subtasks"]
success_criteria: ["Object delivered to user's location", "Memory updated with new object location"]
---
## Task Flow

1. Understand the user's intent (fetch_object vs check_presence)
2. Retrieve memory for target object location hints
3. Ground the target using reliable memory hits
4. Navigate to the target location
5. Observe and identify the target object
6. Pick up the object (manipulate)
7. Navigate to the delivery target
8. Deliver the object
9. Verify delivery
10. Update memory with new location
