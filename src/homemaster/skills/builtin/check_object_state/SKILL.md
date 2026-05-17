---
name: check_object_state
description: Navigate to a location, observe a target object, and report its state to the user.
allowed_tools: ["understand_task", "retrieve_memory", "ground_target", "get_skill", "navigate", "observe", "verify", "update_memory", "update_user_profile"]
constraints: ["Must ground target before navigating", "Must verify observation before reporting", "Update user profile with findings"]
success_criteria: ["Object state reported to user", "Memory and profile updated"]
---
## Task Flow

1. Understand the user's intent (check_presence, check_state)
2. Retrieve memory for target object location hints
3. Ground the target using reliable memory hits
4. Navigate to the target location
5. Observe the target object
6. Verify observation (is the object present? in what state?)
7. Report findings to user
8. Update memory with observation
9. Update user profile if relevant
