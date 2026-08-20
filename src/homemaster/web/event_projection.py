"""Project private Runtime events into browser-facing Web events."""

from __future__ import annotations

import re
from collections.abc import Mapping

from homemaster.events.runtime_events import RuntimeEvent
from homemaster.web.schemas import WebEvent

_ARTIFACT_HANDLE_RE = re.compile(r"^hm-artifact:[A-Za-z0-9_-]{32,128}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_USAGE_KEYS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }
)


class WebEventProjection:
    """Allowlist and translate Runtime event fields for the Web Console."""

    def __init__(self, *, include_thinking: bool = True) -> None:
        if not isinstance(include_thinking, bool):
            raise TypeError("include_thinking must be a boolean")
        self._include_thinking = include_thinking

    def project(self, event: RuntimeEvent, *, request_id: str) -> tuple[WebEvent, ...]:
        """Return zero or more Web events for one private Runtime event."""

        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "transport.delta":
            return self._project_deltas(event, request_id, payload)
        if event.type == "runtime.turn_started":
            return (self._web_event(event, request_id, "run.started", {}),)
        if event.type == "assistant.thinking":
            thinking = payload.get("thinking")
            if self._include_thinking and isinstance(thinking, str) and thinking:
                return (
                    self._web_event(
                        event,
                        request_id,
                        "thinking.snapshot",
                        {"text": thinking},
                    ),
                )
            return ()
        if event.type == "assistant.reply":
            reply = payload.get("reply")
            if isinstance(reply, str) and reply:
                return (
                    self._web_event(event, request_id, "answer.snapshot", {"text": reply}),
                )
            return ()
        if event.type == "runtime.turn_completed":
            return (self._web_event(event, request_id, "run.completed", {}),)
        if event.type in {"runtime.turn_failed", "runtime.budget_exhausted"}:
            code = str(payload.get("error_code") or "run_failed")
            message = str(payload.get("error") or code)
            return (
                self._web_event(
                    event,
                    request_id,
                    "run.failed",
                    {"code": code, "message": message, "retryable": False},
                ),
            )
        if event.type == "runtime.cancelled":
            return (self._web_event(event, request_id, "run.cancelled", {}),)
        if event.type == "tool.call_started":
            return self._project_tool_started(event, request_id, payload)
        if event.type in {"tool.call_completed", "tool.call_failed"}:
            return self._project_tool_terminal(event, request_id, payload)
        if event.type == "permission.confirmation_requested":
            return self._project_approval_requested(event, request_id, payload)
        if event.type == "permission.confirmation_completed":
            return self._project_approval_resolved(event, request_id, payload)
        if event.type == "usage.update":
            usage = {
                key: value
                for key, value in payload.items()
                if key in _USAGE_KEYS and isinstance(value, int) and not isinstance(value, bool)
            }
            return (self._web_event(event, request_id, "usage.updated", usage),)
        if event.type == "context.compaction":
            compacted = {
                key: value
                for key, value in payload.items()
                if key in {"trigger", "before_tokens", "after_tokens"}
                and isinstance(value, (str, int))
                and not isinstance(value, bool)
            }
            return (self._web_event(event, request_id, "context.compacted", compacted),)
        return ()

    def _project_deltas(
        self,
        event: RuntimeEvent,
        request_id: str,
        payload: dict[str, object],
    ) -> tuple[WebEvent, ...]:
        projected: list[WebEvent] = []
        reasoning = payload.get("reasoning_delta")
        if self._include_thinking and isinstance(reasoning, str) and reasoning:
            projected.append(
                self._web_event(event, request_id, "thinking.delta", {"text": reasoning})
            )
        text = payload.get("text_delta")
        if isinstance(text, str) and text:
            projected.append(
                self._web_event(event, request_id, "answer.delta", {"text": text})
            )
        return tuple(projected)

    def _project_tool_started(
        self,
        event: RuntimeEvent,
        request_id: str,
        payload: dict[str, object],
    ) -> tuple[WebEvent, ...]:
        if not event.tool_call_id or not event.name:
            return ()
        arguments = payload.get("arguments")
        return (
            self._web_event(
                event,
                request_id,
                "tool.started",
                {
                    "tool_call_id": event.tool_call_id,
                    "name": event.name,
                    "arguments": _copy_mapping(arguments),
                },
            ),
        )

    def _project_tool_terminal(
        self,
        event: RuntimeEvent,
        request_id: str,
        payload: dict[str, object],
    ) -> tuple[WebEvent, ...]:
        if not event.tool_call_id or not event.name:
            return ()
        failed = event.type == "tool.call_failed"
        return (
            self._web_event(
                event,
                request_id,
                "tool.failed" if failed else "tool.completed",
                {
                    "tool_call_id": event.tool_call_id,
                    "name": event.name,
                    "status": "failed" if failed else "completed",
                    "output": str(payload.get("result") or ""),
                    "artifacts": _artifact_refs(payload, run_id=event.run_id),
                },
            ),
        )

    def _project_approval_requested(
        self,
        event: RuntimeEvent,
        request_id: str,
        payload: dict[str, object],
    ) -> tuple[WebEvent, ...]:
        approval_id = payload.get("approval_id")
        if (
            not isinstance(approval_id, str)
            or not approval_id
            or not event.tool_call_id
            or not event.name
        ):
            return ()
        return (
            self._web_event(
                event,
                request_id,
                "approval.requested",
                {
                    "approval_id": approval_id,
                    "tool_call_id": event.tool_call_id,
                    "name": event.name,
                    "arguments": _copy_mapping(payload.get("arguments")),
                    "cwd": str(payload.get("cwd") or ""),
                    "reason": str(payload.get("reason") or ""),
                },
            ),
        )

    def _project_approval_resolved(
        self,
        event: RuntimeEvent,
        request_id: str,
        payload: dict[str, object],
    ) -> tuple[WebEvent, ...]:
        approval_id = payload.get("approval_id")
        approved = payload.get("approved")
        outcome = payload.get("outcome")
        if (
            not isinstance(approval_id, str)
            or not approval_id
            or not isinstance(approved, bool)
            or not isinstance(outcome, str)
            or not event.tool_call_id
            or not event.name
        ):
            return ()
        return (
            self._web_event(
                event,
                request_id,
                "approval.resolved",
                {
                    "approval_id": approval_id,
                    "tool_call_id": event.tool_call_id,
                    "name": event.name,
                    "approved": approved,
                    "outcome": outcome,
                },
            ),
        )

    @staticmethod
    def _web_event(
        event: RuntimeEvent,
        request_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> WebEvent:
        return WebEvent(
            type=event_type,
            session_id=event.session_id,
            run_id=event.run_id,
            request_id=request_id,
            payload=payload,
        )


def _copy_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): _copy_json(item) for key, item in value.items()}


def _copy_json(value: object) -> object:
    if isinstance(value, Mapping):
        return _copy_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_copy_json(item) for item in value]
    return value


def _artifact_refs(payload: Mapping[str, object], *, run_id: str) -> list[dict[str, str]]:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return []
    raw_refs = data.get("artifacts")
    if not isinstance(raw_refs, (list, tuple)):
        return []
    required = {
        "artifact_handle",
        "run_id",
        "filename",
        "media_type",
        "content_sha256",
    }
    valid: list[dict[str, str]] = []
    for raw in raw_refs:
        if not isinstance(raw, Mapping) or set(raw) != required:
            continue
        ref = {key: str(raw[key]) for key in required}
        if (
            _ARTIFACT_HANDLE_RE.fullmatch(ref["artifact_handle"]) is None
            or _RUN_ID_RE.fullmatch(ref["run_id"]) is None
            or ref["run_id"] != run_id
            or not ref["filename"]
            or "/" in ref["filename"]
            or "\\" in ref["filename"]
            or not ref["media_type"].strip()
            or _SHA256_RE.fullmatch(ref["content_sha256"]) is None
        ):
            continue
        valid.append(ref)
    return valid


__all__ = ["WebEventProjection"]
