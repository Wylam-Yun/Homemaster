"""Tools — ToolSpec, ToolRegistry, Dispatcher, and simulated executors.

Contains 11 tools: 7 programmatic (understand_task, retrieve_memory,
ground_target, get_skill, update_memory, update_user_profile, finish_task)
and 4 simulated (navigate, observe, manipulate, verify).

Responsibility boundary:
  ToolSpec:      declares tool metadata + executor; generates Mimo manifest
  ToolRegistry:  stores ToolSpec by name; returns selectable manifests
  ToolResult:    typed execution outcome; no state_patch
  Dispatcher:    validates + invokes executor; does not mutate AgentState
  StateUpdater:  sole component that transforms AgentState
"""
