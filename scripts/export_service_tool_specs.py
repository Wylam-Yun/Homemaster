"""Regenerate service tool metadata from HomeMaster's owned default registry."""

from __future__ import annotations

import json
from pathlib import Path

from homemaster.adapters import build_universal_tool_registry

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
    registry = build_universal_tool_registry()
    tools = {name: registry.get(name) for name in NAMES}
    missing = [name for name, tool in tools.items() if tool is None]
    if missing:
        raise RuntimeError(f"HomeMaster registry is missing service tools: {missing}")
    payload = [
        {
            "name": name,
            "description": tools[name].description,
            "input_schema": tools[name].input_model.model_json_schema(),
            "defaults": _schema_defaults(tools[name].input_model.model_json_schema()),
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


def _schema_defaults(schema: dict[str, object]) -> dict[str, object]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    return {
        str(name): definition["default"]
        for name, definition in properties.items()
        if isinstance(definition, dict) and "default" in definition
    }


if __name__ == "__main__":
    main()
