---
name: evidence_discipline
description: Keep business conclusions bound to current-run external receipts and readback.
tool_names: ["browser_observe", "browser_click", "browser_wait", "terminal_execute", "sop_decide", "task_progress_check"]
constraints: ["Never reuse evidence from another run or stage.", "A submitted action is not a completed external state change."]
success_criteria: ["Each decision cites returned evidence references.", "Process status and independent readback agree."]
version: v1
---

Treat an accepted request, a terminal job result, and independent state readback as different facts. Keep the exact job identifier returned by submission and wait on that row. For terminal checks, use the exit code and visible output together. Cite only evidence references returned during this run. When evidence is missing or contradictory, stop with an explicit non-success decision.
