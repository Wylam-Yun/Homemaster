"""ToolSpec / ToolResult / registry contracts.

Responsibility boundary:
  ToolSpec:     declares tool metadata + executor reference; generates compact Mimo manifest
  ToolRegistry: stores ToolSpec by name; returns selectable manifests
  ToolResult:   typed execution outcome; no state_patch
  Dispatcher:   validates + invokes executor; does not mutate AgentState
  StateUpdater:  sole component that transforms AgentState
  EventSink:    append-only redacted events
"""
