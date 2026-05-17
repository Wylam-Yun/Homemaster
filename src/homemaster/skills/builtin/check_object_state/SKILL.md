---
name: check_object_state
description: Navigate to a location, observe a target object, and report its state to the user.
allowed_tools: ["understand_task", "retrieve_memory", "ground_target", "get_skill", "navigate", "observe", "verify", "update_memory", "update_user_profile"]
constraints: ["Must ground target before navigating", "Must verify observation before reporting", "Update user profile with findings"]
success_criteria: ["Object state reported to user", "Memory and profile updated"]
---

## Strategy
1. Understand the user's intent and extract target object
2. Retrieve memory for object location candidates
3. Ground the target by selecting the best memory anchor
4. Load this skill's full context via get_skill if needed
5. Navigate to the candidate location
6. Observe to determine current object state
7. Verify observation matches expectations
8. Report findings to user
9. Update user profile with observations

## Constraints
- Observe and verify are primary actions; do not default to manipulate
- User preference updates only via update_user_profile proposal
- Failed observations are evidence for Mimo's next decision

## Success Criteria
- Object state reported to user
- Memory and user profile updated with findings
