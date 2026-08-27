"""Typed permission configuration without runtime execution dependencies."""

from __future__ import annotations

import warnings
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_LEGACY_TOOL_PREFIXES = frozenset(
    {"openharness", "home", "core", "alfworld", "legacy"}
)


class PermissionMode(StrEnum):
    DEFAULT = "default"
    PLAN = "plan"
    FULL_AUTO = "full_auto"


class PathRuleConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    pattern: str
    allow: bool = True

    @field_validator("pattern")
    @classmethod
    def _pattern_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("permission path pattern must not be blank")
        return value


class PermissionSettingsConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: PermissionMode = PermissionMode.FULL_AUTO
    allowed_tools: tuple[str, ...] = ()
    denied_tools: tuple[str, ...] = ()
    path_rules: tuple[PathRuleConfig, ...] = ()
    denied_commands: tuple[str, ...] = ()
    allowed_terminal_commands: tuple[str, ...] = ()

    @field_validator("allowed_terminal_commands")
    @classmethod
    def _terminal_commands_are_exact_and_nonblank(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        if any(not command or command != command.strip() for command in value):
            raise ValueError(
                "allowed terminal commands must be nonblank exact strings without "
                "leading or trailing whitespace"
            )
        if len(set(value)) != len(value):
            raise ValueError("allowed terminal commands must be unique")
        return value

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_tool_ids(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        for field in ("allowed_tools", "denied_tools"):
            raw = migrated.get(field, ())
            if isinstance(raw, str):
                raw = (raw,)
            converted = tuple(dict.fromkeys(_ordinary_tool_name(str(item)) for item in raw))
            migrated[field] = converted
        overlap = set(migrated.get("allowed_tools", ())) & set(
            migrated.get("denied_tools", ())
        )
        if overlap:
            raise ValueError(f"tools cannot be both allowed and denied: {sorted(overlap)}")
        return migrated


def _ordinary_tool_name(value: str) -> str:
    parts = value.split(".")
    if len(parts) == 3 and parts[0] in _LEGACY_TOOL_PREFIXES and parts[2] == "v1":
        ordinary = parts[1]
        warnings.warn(
            f"legacy tool id {value!r} migrated to ordinary name {ordinary!r}",
            FutureWarning,
            stacklevel=3,
        )
        return ordinary
    return value


__all__ = [
    "PathRuleConfig",
    "PermissionMode",
    "PermissionSettingsConfig",
]
