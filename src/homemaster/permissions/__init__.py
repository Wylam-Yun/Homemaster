"""Typed permission configuration and policy for HomeMaster runs."""

from homemaster.permissions.config import (
    PathRuleConfig,
    PermissionMode,
    PermissionSettingsConfig,
)


def __getattr__(name: str):
    if name == "PermissionChecker":
        from homemaster.permissions.policy import PermissionChecker

        return PermissionChecker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "PermissionChecker",
    "PathRuleConfig",
    "PermissionMode",
    "PermissionSettingsConfig",
]
