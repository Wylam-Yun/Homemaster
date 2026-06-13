"""Generic tool primitives used by domain registries and the agent runtime.

Responsibility boundary:
  ToolSpec:      declares tool metadata + executor; generates model manifest
  ToolRegistry:  stores ToolSpec by name; returns selectable manifests
  ToolResult:    typed execution outcome; no state_patch
  Dispatcher:    validates + invokes executor and returns ToolResultMessage
"""
