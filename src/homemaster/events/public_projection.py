"""Strict public event boundary for remote Gateway consumers."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homemaster.events.runtime_events import RuntimeEvent

if TYPE_CHECKING:
    from homemaster.events.bus import EventBus

_ALLOWED_TYPES = frozenset(
    {
        "assistant.reply",
        "context.compaction",
        "runtime.budget_exhausted",
        "runtime.cancelled",
        "runtime.turn_completed",
        "runtime.turn_failed",
        "tool.call_completed",
        "tool.call_failed",
        "tool.call_started",
        "transport.request_failed",
        "usage.update",
    }
)
_SAFE_METADATA_KEYS = frozenset(
    {
        "attempt",
        "checkpoint",
        "error_code",
        "finish_reason",
        "is_error",
        "phase",
        "status",
    }
)
_ARTIFACT_HANDLE_RE = re.compile(r"^hm-artifact:[A-Za-z0-9_-]{32,128}$")
_ARTIFACT_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class PublicGatewayEvent:
    event_type: str
    session_id: str
    run_id: str
    turn_index: int | None
    correlation_id: str
    content: str
    metadata: Mapping[str, object]
    artifacts: tuple[Mapping[str, str], ...] = ()
    gateway_generation: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "event_type": self.event_type,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "turn_index": self.turn_index,
            "correlation_id": self.correlation_id,
            "content": self.content,
            "metadata": _thaw(self.metadata),
            "artifacts": [_thaw(item) for item in self.artifacts],
            "gateway_generation": self.gateway_generation,
        }


class PublicEventProjection:
    """Project allowlisted event fields without rewriting their values."""

    def project(self, event: RuntimeEvent) -> PublicGatewayEvent | None:
        if event.type not in _ALLOWED_TYPES:
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        artifacts = self._artifact_refs(payload) if event.type == "tool.call_completed" else ()
        content = self._content(event.type, payload, tool_name=event.name)
        if content is None and not artifacts:
            return None
        metadata = {
            key: _copy_value(value) for key, value in payload.items() if key in _SAFE_METADATA_KEYS
        }
        if event.name and event.type.startswith("tool.call_"):
            metadata["tool_name"] = _copy_value(event.name)
        data = payload.get("data")
        if (
            event.name == "observe"
            and isinstance(data, Mapping)
            and isinstance(data.get("observation_of_tool_call_id"), str)
        ):
            metadata["observation_of_tool_call_id"] = str(data["observation_of_tool_call_id"])
        return PublicGatewayEvent(
            event_type=event.type,
            session_id=event.session_id,
            run_id=event.run_id,
            turn_index=event.turn_index,
            correlation_id=event.tool_call_id or event.event_id,
            content=self.project_content(content or ""),
            metadata=metadata,
            artifacts=artifacts,
            gateway_generation=event.gateway_generation,
        )

    @staticmethod
    def _artifact_refs(payload: Mapping[str, object]) -> tuple[Mapping[str, str], ...]:
        data = payload.get("data")
        if not isinstance(data, Mapping):
            return ()
        raw_refs = data.get("artifacts")
        if not isinstance(raw_refs, (list, tuple)):
            return ()
        valid: list[Mapping[str, str]] = []
        required = {
            "artifact_handle",
            "run_id",
            "filename",
            "media_type",
            "content_sha256",
        }
        for raw in raw_refs:
            if not isinstance(raw, Mapping) or set(raw) != required:
                continue
            ref = {key: str(raw[key]) for key in required}
            if (
                _ARTIFACT_HANDLE_RE.fullmatch(ref["artifact_handle"]) is None
                or _ARTIFACT_TOKEN_RE.fullmatch(ref["run_id"]) is None
                or not ref["filename"]
                or "/" in ref["filename"]
                or "\\" in ref["filename"]
                or not ref["media_type"].strip()
                or _SHA256_RE.fullmatch(ref["content_sha256"]) is None
            ):
                continue
            valid.append(ref)
        return tuple(valid)

    def project_content(self, content: object) -> str:
        """Compatibility name for exact public text projection."""

        return str(content)

    def copy_value(self, value: object) -> object:
        """Compatibility name for an exact recursive value copy."""

        return _copy_value(value)

    def _content(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        tool_name: str | None,
    ) -> str | None:
        if event_type == "assistant.reply":
            reply = str(payload.get("reply") or "")
            return reply or None
        if event_type == "runtime.turn_completed":
            return str(payload.get("final_reply") or "")
        if event_type in {
            "runtime.turn_failed",
            "runtime.budget_exhausted",
            "runtime.cancelled",
            "transport.request_failed",
        }:
            return str(payload.get("error_code") or event_type)
        if event_type == "tool.call_completed":
            return _tool_progress(tool_name, payload)
        if event_type == "tool.call_failed":
            return _tool_failure(tool_name, payload)
        return None


def _tool_progress(tool_name: str | None, payload: Mapping[str, object]) -> str | None:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return None
    if tool_name == "task_planner":
        subtasks = data.get("subtasks")
        if not isinstance(subtasks, (list, tuple)) or not subtasks:
            return None
        lines = ["执行计划："]
        for index, raw in enumerate(subtasks, start=1):
            if not isinstance(raw, Mapping):
                continue
            description = str(raw.get("description") or raw.get("id") or "").strip()
            if description:
                lines.append(f"{index}. {description}")
        return "\n".join(lines) if len(lines) > 1 else None
    if tool_name == "robot_go_to":
        target = str(data.get("target") or "").strip()
        if data.get("success") is True and target:
            return f"已导航到 {target}。下一张图片是该动作后的环境观察。"
        return f"未能导航到 {target or '目标'}。"
    if tool_name == "robot_manipulate":
        action = str(data.get("action") or "").strip()
        obj = str(data.get("object") or "").strip()
        target = str(data.get("target") or "").strip()
        if data.get("success") is not True:
            return f"操作未完成：{_action_phrase(action, obj, target)}。"
        return f"已{_action_phrase(action, obj, target)}。下一张图片是该动作后的环境观察。"
    if tool_name == "robot_verify":
        if data.get("success") is True:
            return "环境验证：任务已完成。"
        return "环境验证：任务尚未完成。"
    return None


def _tool_failure(tool_name: str | None, payload: Mapping[str, object]) -> str | None:
    if tool_name not in {"robot_go_to", "robot_manipulate", "robot_verify", "task_planner"}:
        return None
    data = payload.get("data")
    if (
        isinstance(data, Mapping)
        and data.get("backend_attempted") is False
        and str(data.get("error_code") or "").startswith("model_observation_")
    ):
        return None
    error = str(payload.get("error_code") or payload.get("status") or "执行失败")
    return f"操作失败：{error}"


def _action_phrase(action: str, obj: str, target: str) -> str:
    if action == "take":
        return f"拿起 {obj or '物体'}"
    if action == "put":
        suffix = f"并放到 {target}" if target else ""
        return f"放下 {obj or '物体'}{suffix}"
    translations = {
        "open": "打开",
        "close": "关闭",
        "use": "使用",
        "slice": "切开",
        "heat": "加热",
        "cool": "冷却",
        "clean": "清洁",
    }
    verb = translations.get(action, action or "执行操作")
    suffix = f"（目标：{target}）" if target else ""
    return f"{verb} {obj or '物体'}{suffix}".strip()


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _copy_value(value: object) -> object:
    return _thaw(value)


async def public_gateway_stream(
    bus: EventBus,
    projection: PublicEventProjection,
) -> AsyncIterator[PublicGatewayEvent]:
    """Keep private RuntimeEvent values inside the events trust-boundary module."""

    async for private_event in bus.stream():
        public_event = projection.project(private_event)
        if public_event is not None:
            yield public_event


__all__ = [
    "PublicEventProjection",
    "PublicGatewayEvent",
    "public_gateway_stream",
]
