"""Skills — progressive-disclosure layer for task-specific skill metadata.

Contains SkillSpec, SkillLoader, SkillRegistry, and builtin SKILL.md packages
(fetch_object, check_object_state). Skills do not contain executors, return
ToolResults, or mutate AgentState — they provide context to Mimo via get_skill.
"""
