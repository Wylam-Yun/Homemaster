"""Typed permission configuration without runtime execution dependencies."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator


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


__all__ = ["PathRuleConfig", "PermissionMode", "PermissionSettingsConfig"]
