"""Translate HomeMaster benchmark tool arguments into ALFWorld commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class TranslatorValidationError(ValueError):
    """Raised when tool arguments cannot be translated into an ALFWorld command."""


def _required(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TranslatorValidationError(f"{field_name} is required")
    return value.strip()


@dataclass(frozen=True)
class AlfworldCommandTranslator:
    env_type: str
    put_template: str

    def public_action_schema(self) -> dict[str, Any]:
        return {
            "environment": self.env_type,
            "observe_modes": [
                {"mode": "look", "command_template": "look"},
                {"mode": "inventory", "command_template": "inventory"},
                {"mode": "examine", "command_template": "examine {target}"},
            ],
            "navigation": {
                "tool": "robot_navigate",
                "required": ["target_receptacle"],
                "command_template": "go to {target_receptacle}",
            },
            "manipulation_actions": [
                {
                    "action": "take",
                    "required": ["object", "source_receptacle"],
                    "command_template": "take {object} from {source_receptacle}",
                },
                {
                    "action": "put",
                    "required": ["object", "target_receptacle"],
                    "command_template": self.put_template,
                },
                {
                    "action": "open",
                    "required": ["target_receptacle"],
                    "command_template": "open {target_receptacle}",
                },
                {
                    "action": "close",
                    "required": ["target_receptacle"],
                    "command_template": "close {target_receptacle}",
                },
                {
                    "action": "use",
                    "required": ["object"],
                    "command_template": "use {object}",
                },
                {
                    "action": "heat",
                    "required": ["object", "tool_receptacle"],
                    "command_template": "heat {object} with {tool_receptacle}",
                },
                {
                    "action": "cool",
                    "required": ["object", "tool_receptacle"],
                    "command_template": "cool {object} with {tool_receptacle}",
                },
                {
                    "action": "clean",
                    "required": ["object", "tool_receptacle"],
                    "command_template": "clean {object} with {tool_receptacle}",
                },
                {
                    "action": "slice",
                    "required": ["object", "tool_receptacle"],
                    "command_template": "slice {object} with {tool_receptacle}",
                },
            ],
        }

    def observe(self, *, mode: str = "look", target: str | None = None) -> str:
        mode = mode.strip() if isinstance(mode, str) and mode.strip() else "look"
        if mode == "look":
            return "look"
        if mode == "inventory":
            return "inventory"
        if mode == "examine":
            return f"examine {_required(target, 'target')}"
        raise TranslatorValidationError(f"unsupported observe mode: {mode}")

    def navigate(self, *, target_receptacle: str) -> str:
        return f"go to {_required(target_receptacle, 'target_receptacle')}"

    def manipulate(self, *, action: str, **kwargs: object) -> str:
        action = _required(action, "action")
        if action == "take":
            obj = _required(kwargs.get("object"), "object")
            source = _required(kwargs.get("source_receptacle"), "source_receptacle")
            return f"take {obj} from {source}"
        if action == "put":
            obj = _required(kwargs.get("object"), "object")
            target = _required(kwargs.get("target_receptacle"), "target_receptacle")
            return self.put_template.format(object=obj, target_receptacle=target)
        if action in {"open", "close"}:
            target = _required(kwargs.get("target_receptacle"), "target_receptacle")
            return f"{action} {target}"
        if action == "use":
            return f"use {_required(kwargs.get('object'), 'object')}"
        if action in {"heat", "cool", "clean", "slice"}:
            obj = _required(kwargs.get("object"), "object")
            tool = _required(kwargs.get("tool_receptacle"), "tool_receptacle")
            return f"{action} {obj} with {tool}"
        raise TranslatorValidationError(f"unsupported manipulation action: {action}")


def create_translator(env_type: str) -> AlfworldCommandTranslator:
    if env_type == "AlfredTWEnv":
        return AlfworldCommandTranslator(
            env_type=env_type,
            put_template="move {object} to {target_receptacle}",
        )
    if env_type == "AlfredThorEnv":
        return AlfworldCommandTranslator(
            env_type=env_type,
            put_template="put {object} in/on {target_receptacle}",
        )
    raise TranslatorValidationError(f"unsupported env_type: {env_type}")
