"""Provider-only projection for short-lived tool references."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy

from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    Message,
    ToolCall,
    ToolResultMessage,
)

_BROWSER_INSPECT = "browser_inspect"
_BROWSER_MUTATIONS = frozenset(
    {
        "browser_fill",
        "browser_select",
        "browser_check",
        "browser_uncheck",
        "browser_click",
        "browser_backfill",
    }
)
_EXPIRED_REFERENCE_MODE = "expired_review_only"


def project_model_tool_context(
    messages: Sequence[Message],
    *,
    tools: Sequence[Mapping[str, object]] | None,
) -> list[Message]:
    """Hide superseded browser references without changing canonical history.

    A browser mutation may use only the single inspect result that immediately
    precedes the provider request. Older inspect results remain auditable in the
    session/trace but lose executable IDs in the provider projection. Full
    review-only snapshots are similarly retained only while they are the latest
    tool result.
    """

    if not _has_browser_protocol(tools):
        return list(messages)

    current_inspect_index = _current_inspect_result_index(messages)
    latest_result_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if isinstance(messages[index], ToolResultMessage)
        ),
        None,
    )
    projected: list[Message] = []
    for index, message in enumerate(messages):
        if not isinstance(message, ToolResultMessage):
            projected.append(message)
            continue
        if message.name == _BROWSER_INSPECT and index != current_inspect_index:
            projected.append(_project_result_data(message, _expire_inspect_data))
            continue
        if message.name in _BROWSER_MUTATIONS and index != latest_result_index:
            projected.append(_project_result_data(message, _expire_review_snapshot))
            continue
        projected.append(message)
    return projected


def project_model_tool_schemas(
    tools: Sequence[Mapping[str, object]] | None,
    *,
    messages: Sequence[Message],
) -> list[dict[str, object]] | None:
    """Expose browser mutations only while a current inspect lease exists."""

    if tools is None:
        return None
    projected = [dict(schema) for schema in tools]
    if not _has_browser_protocol(projected) or _has_current_browser_reference(messages):
        return projected
    return [
        schema for schema in projected if _schema_name(schema) not in _BROWSER_MUTATIONS
    ]


def browser_reference_protocol_results(
    tool_calls: Sequence[ToolCall],
    *,
    messages: Sequence[Message],
) -> list[ToolResultMessage] | None:
    """Block a browser mutation that lacks the exact current inspect lease."""

    mutations = [call for call in tool_calls if call.name in _BROWSER_MUTATIONS]
    if not mutations:
        return None
    if len(tool_calls) != 1 or len(mutations) != 1:
        return None
    call = mutations[0]
    elements = _current_browser_elements(messages)
    references = set(elements)
    received = (
        str(call.arguments.get("snapshot_id") or ""),
        str(call.arguments.get("element_id") or ""),
    )
    if not references:
        code = "browser_inspect_required"
        message = (
            "This browser mutation was not executed. Call browser_inspect in a separate "
            "model response, then use its exact snapshot_id and element_id immediately."
        )
    elif received not in references:
        code = "browser_inspect_reference_mismatch"
        message = (
            "This browser mutation was not executed because its snapshot_id/element_id "
            "does not match the immediately preceding browser_inspect result. Inspect the "
            "exact target again before mutating."
        )
    elif reasons := _non_actionable_reasons(elements[received]):
        code = "browser_target_not_actionable"
        message = (
            "This browser mutation was not executed because the inspected target is "
            f"not actionable ({', '.join(reasons)}). Wait for an actionable state or "
            "inspect a different enabled, visible, unobscured semantic target."
        )
    else:
        return None
    payload = {
        "status": "protocol_blocked",
        "backend_attempted": False,
        "error_code": code,
        "required_tool": _BROWSER_INSPECT,
        "rejected_tool": call.name,
        "message": message,
    }
    return [
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
    ]


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
    available = {
        name for schema in tools if (name := _schema_name(schema)) is not None
    }
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


def _has_browser_protocol(tools: Sequence[Mapping[str, object]] | None) -> bool:
    if not tools:
        return False
    return any(_schema_name(schema) == _BROWSER_INSPECT for schema in tools)


def _schema_name(schema: Mapping[str, object]) -> str | None:
    name = schema.get("name")
    if isinstance(name, str):
        return name
    function = schema.get("function")
    if isinstance(function, Mapping) and isinstance(function.get("name"), str):
        return str(function["name"])
    return None


def _current_inspect_result_index(messages: Sequence[Message]) -> int | None:
    if len(messages) < 2:
        return None
    assistant = messages[-2]
    result = messages[-1]
    if not isinstance(assistant, AssistantMessage) or not isinstance(
        result, ToolResultMessage
    ):
        return None
    if result.name != _BROWSER_INSPECT or len(assistant.tool_calls) != 1:
        return None
    call = assistant.tool_calls[0]
    if call.id != result.tool_call_id or call.name != _BROWSER_INSPECT:
        return None
    return len(messages) - 1


def _has_current_browser_reference(messages: Sequence[Message]) -> bool:
    return bool(_current_browser_references(messages))


def _current_browser_references(messages: Sequence[Message]) -> set[tuple[str, str]]:
    return set(_current_browser_elements(messages))


def _current_browser_elements(
    messages: Sequence[Message],
) -> dict[tuple[str, str], dict[str, object]]:
    index = _current_inspect_result_index(messages)
    if index is None:
        return {}
    result = messages[index]
    assert isinstance(result, ToolResultMessage)
    if result.is_error:
        return {}
    payload = _tool_result_payload(result)
    if payload is None:
        return {}
    data = payload.get("data")
    if not isinstance(data, dict):
        return {}
    snapshot_id = data.get("snapshot_id")
    elements = data.get("elements")
    if not isinstance(snapshot_id, str) or not snapshot_id or not isinstance(elements, list):
        return {}
    return {
        (snapshot_id, str(element["element_id"])): element
        for element in elements
        if isinstance(element, dict)
        and isinstance(element.get("element_id"), str)
        and bool(element["element_id"])
    }


def _non_actionable_reasons(element: Mapping[str, object]) -> tuple[str, ...]:
    reasons = []
    if element.get("enabled") is False:
        reasons.append("enabled=false")
    if element.get("visible") is False:
        reasons.append("visible=false")
    if element.get("obscured") is True:
        reasons.append("obscured=true")
    return tuple(reasons)


def _tool_result_payload(message: ToolResultMessage) -> dict[str, object] | None:
    for block in message.content:
        if block.type != "text" or not block.text:
            continue
        try:
            payload = json.loads(block.text)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            return payload
    if isinstance(message.data, dict):
        return message.data
    return None


def _project_result_data(
    message: ToolResultMessage,
    transform: Callable[[dict[str, object]], dict[str, object]],
) -> ToolResultMessage:
    content = list(message.content)
    for index, block in enumerate(content):
        if block.type != "text" or not block.text:
            continue
        try:
            payload = json.loads(block.text)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            continue
        projected_data = transform(deepcopy(payload["data"]))
        payload["data"] = projected_data
        content[index] = block.model_copy(
            update={
                "text": json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            }
        )
        return message.model_copy(update={"content": content, "data": payload})
    return message


def _expire_inspect_data(data: dict[str, object]) -> dict[str, object]:
    elements = data.get("elements")
    summary = {
        key: deepcopy(data[key])
        for key in (
            "url",
            "title",
            "total_matches",
            "truncated",
            "frames",
            "evidence_ref",
        )
        if key in data
    }
    summary.update(
        {
            "elements": [],
            "expired_element_count": len(elements) if isinstance(elements, list) else 0,
            "reference_mode": _EXPIRED_REFERENCE_MODE,
        }
    )
    return summary


def _expire_review_snapshot(data: dict[str, object]) -> dict[str, object]:
    snapshot = data.get("next_snapshot")
    if not isinstance(snapshot, dict):
        return data
    elements = snapshot.get("elements")
    summary = {
        key: deepcopy(snapshot[key])
        for key in (
            "url",
            "title",
            "total_matches",
            "truncated",
            "frames",
            "status",
            "error_code",
            "message",
        )
        if key in snapshot
    }
    summary.update(
        {
            "expired_element_count": len(elements) if isinstance(elements, list) else 0,
            "reference_mode": _EXPIRED_REFERENCE_MODE,
        }
    )
    data["next_snapshot"] = summary
    return data


__all__ = [
    "browser_reference_protocol_results",
    "project_model_tool_context",
    "project_model_tool_schemas",
    "terminal_command_protocol_results",
    "unavailable_tool_protocol_results",
]
