"""Provider-only projection for tool availability and execution fences."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from homemaster.agent.messages import ContentBlock, Message, ToolCall, ToolResultMessage


def project_model_tool_context(
    messages: Sequence[Message],
    *,
    tools: Sequence[Mapping[str, object]] | None,
) -> list[Message]:
    """Keep V3.1 semantic targets and stable refs in provider-visible history."""

    del tools
    return list(messages)


def project_model_tool_schemas(
    tools: Sequence[Mapping[str, object]] | None,
    *,
    messages: Sequence[Message],
) -> list[dict[str, object]] | None:
    """Keep the frozen Registry visible; V3.1 actions do not require inspect leases."""

    del messages
    if tools is None:
        return None
    return [dict(schema) for schema in tools]


def unavailable_tool_protocol_results(
    tool_calls: Sequence[ToolCall],
    *,
    tools: Sequence[Mapping[str, object]] | None,
) -> list[ToolResultMessage] | None:
    """Block calls that were not offered in the current provider request.

    Provider tool schemas are advisory rather than an execution boundary: a
    model may still emit a remembered or invented tool name. Reject the whole
    batch before dispatch so no valid companion call can create a partial side
    effect.
    """

    if tools is None:
        return None
    available = {name for schema in tools if (name := _schema_name(schema)) is not None}
    unavailable = sorted({call.name for call in tool_calls if call.name not in available})
    if not unavailable:
        return None

    unavailable_text = ", ".join(unavailable)
    results: list[ToolResultMessage] = []
    for call in tool_calls:
        if call.name in unavailable:
            code = "tool_not_available"
            message = (
                f"Tool {call.name!r} was not executed because it was not offered in "
                "this model request. Use only a currently available tool. For browser "
                "interaction, inspect the semantic target first and then use an offered "
                "browser tool."
            )
        else:
            code = "tool_batch_contains_unavailable_call"
            message = (
                "This tool was not executed because its batch also contained unavailable "
                f"tool(s): {unavailable_text}. Retry using only currently available tools."
            )
        payload = {
            "status": "protocol_blocked",
            "backend_attempted": False,
            "error_code": code,
            "rejected_tool": call.name,
            "unavailable_tools": unavailable,
            "message": message,
        }
        results.append(
            ToolResultMessage(
                tool_call_id=call.id,
                name=call.name,
                content=[
                    ContentBlock(
                        text=json.dumps(
                            payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                    )
                ],
                is_error=False,
                data=payload,
            )
        )
    return results


def terminal_command_protocol_results(
    tool_calls: Sequence[ToolCall],
    *,
    allowed_commands: Sequence[str],
) -> list[ToolResultMessage] | None:
    """Block non-allowlisted terminal commands before executor dispatch.

    An empty allowlist preserves the ordinary unrestricted terminal behavior.
    When configured, comparison is exact and the allowlist itself is never
    returned to the provider.
    """

    if not allowed_commands:
        return None
    allowed = frozenset(allowed_commands)
    rejected_commands = [
        call
        for call in tool_calls
        if call.name == "terminal"
        and (
            not isinstance(call.arguments.get("command"), str)
            or call.arguments["command"] not in allowed
        )
    ]
    if not rejected_commands:
        return None

    results: list[ToolResultMessage] = []
    for call in tool_calls:
        if call in rejected_commands:
            code = "terminal_command_not_allowed"
            message = (
                "This terminal call was not executed because its command did not exactly "
                "match the configured allowlist. Re-read the authoritative source and use "
                "only an explicitly permitted command verbatim."
            )
        else:
            code = "tool_batch_contains_disallowed_terminal_command"
            message = (
                "This tool was not executed because its batch also contained a terminal "
                "command that failed the exact allowlist. Retry the calls separately."
            )
        payload = {
            "status": "protocol_blocked",
            "backend_attempted": False,
            "error_code": code,
            "rejected_tool": call.name,
            "message": message,
        }
        results.append(
            ToolResultMessage(
                tool_call_id=call.id,
                name=call.name,
                content=[
                    ContentBlock(
                        text=json.dumps(
                            payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                    )
                ],
                is_error=False,
                data=payload,
            )
        )
    return results


def _schema_name(schema: Mapping[str, object]) -> str | None:
    name = schema.get("name")
    if isinstance(name, str):
        return name
    function = schema.get("function")
    if isinstance(function, Mapping) and isinstance(function.get("name"), str):
        return str(function["name"])
    return None


__all__ = [
    "project_model_tool_context",
    "project_model_tool_schemas",
    "terminal_command_protocol_results",
    "unavailable_tool_protocol_results",
]
