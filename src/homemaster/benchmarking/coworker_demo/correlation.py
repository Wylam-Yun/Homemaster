"""Stable correlation between model tool calls and external actions."""

from __future__ import annotations

import json
import uuid

from homemaster.agent.normalized import RunContext


def correlated_action_id(run_context: RunContext) -> str:
    tool_call_id = run_context.deps.get("current_tool_call_id")
    if not isinstance(tool_call_id, str) or not tool_call_id:
        raise RuntimeError("current model tool_call_id is unavailable")
    seed = json.dumps(
        [run_context.run_id, tool_call_id], ensure_ascii=False, separators=(",", ":")
    )
    return f"action-{uuid.uuid5(uuid.NAMESPACE_URL, seed).hex}"
