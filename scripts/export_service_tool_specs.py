"""Regenerate HomeMaster service tool metadata from the locked upstream source."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic_core import PydanticUndefined

from openharness.tools import create_default_tool_registry

NAMES = (
    "ask_user_question",
    "lsp",
    "mcp_auth",
    "image_to_text",
    "image_generation",
    "config",
    "enter_plan_mode",
    "exit_plan_mode",
    "cron_create",
    "cron_list",
    "cron_delete",
    "cron_toggle",
    "remote_trigger",
    "task_create",
    "task_get",
    "task_list",
    "task_stop",
    "task_output",
    "task_update",
    "agent",
    "send_message",
    "team_create",
    "team_delete",
)


def main() -> None:
    upstream = {tool.name: tool for tool in create_default_tool_registry().list_tools()}
    payload = [
        {
            "name": name,
            "description": upstream[name].description,
            "input_schema": upstream[name].input_model.model_json_schema(),
            "defaults": _model_defaults(upstream[name].input_model),
        }
        for name in NAMES
    ]
    target = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "homemaster"
        / "tools"
        / "service_tool_specs.json"
    )
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _model_defaults(model: type) -> dict[str, object]:
    defaults: dict[str, object] = {}
    for name, field in model.model_fields.items():
        if field.default is not PydanticUndefined:
            defaults[name] = field.default
        elif field.default_factory is not None:
            defaults[name] = field.default_factory()
    return defaults


if __name__ == "__main__":
    main()
