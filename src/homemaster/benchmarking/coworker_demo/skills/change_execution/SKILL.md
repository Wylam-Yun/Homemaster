---
name: change_execution
description: Execute an operations change through explicit gates and a verified terminal outcome.
tool_names: ["task_planner", "task_progress_check", "browser_navigate", "browser_observe", "browser_click", "browser_fill", "browser_select", "browser_wait", "terminal_execute", "sop_decide"]
constraints: ["Do not submit a change before every required precheck passes.", "Use only values visible in the current ticket and page."]
success_criteria: ["The implementation and independent verification both succeed.", "A confirmed post-change anomaly leads to verified rollback."]
version: v1
---

Create a concise plan from the visible ticket. Complete all required prechecks before recording a proceed decision. Submit the locked change, wait for its exact job, and verify the external file through the permitted terminal command. Perform the required post-change checks. Complete only when every gate is supported by current-run evidence; otherwise block, escalate, or roll back according to the visible procedure.
