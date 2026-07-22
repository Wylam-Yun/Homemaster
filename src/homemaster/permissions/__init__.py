"""Typed permission configuration and policy for HomeMaster runs."""

from homemaster.permissions.config import (
    PathRuleConfig,
    PermissionMode,
    PermissionSettingsConfig,
)


def __getattr__(name: str):
    if name == "HomePermissionPolicy":
        from homemaster.permissions.policy import HomePermissionPolicy

        return HomePermissionPolicy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "HomePermissionPolicy",
    "PathRuleConfig",
    "PermissionMode",
    "PermissionSettingsConfig",
]
