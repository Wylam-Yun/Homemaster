"""Capability-aware HomeMaster permission policy.

Mode and deterministic allow/deny control flow are adapted from OpenHarness
9b2efd7 ``src/openharness/permissions/{modes,checker}.py``. HomeMaster adds
typed tenant principals and device/MCP capabilities.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from homemaster.permissions.config import PermissionMode, PermissionSettingsConfig
from homemaster.tools.base import ToolExecutionContext as UniversalToolExecutionContext
from homemaster.tools.contracts import ExecutionBackend, ToolDefinition
from homemaster.tools.executor import PermissionDecision as UniversalPermissionDecision

_SENSITIVE_PATH_PATTERNS = (
    "*/.ssh/*",
    "*/.aws/credentials",
    "*/.aws/config",
    "*/.config/gcloud/*",
    "*/.azure/*",
    "*/.gnupg/*",
    "*/.docker/config.json",
    "*/.kube/config",
    "*/.homemaster/credentials*",
    "*/config/homemaster.yaml",
)
_PATH_ARGUMENTS = (
    "path",
    "file_path",
    "root",
    "attachment_path",
    "cwd",
    "image_path",
    "image_paths",
    "mask_path",
    "output_path",
    "output_dir",
)


class PermissionChecker:
    """OpenHarness-style permission checks keyed by ordinary tool name."""

    def __init__(self, settings: PermissionSettingsConfig) -> None:
        if not isinstance(settings, PermissionSettingsConfig):
            raise TypeError("settings must be PermissionSettingsConfig")
        self._settings = settings

    def evaluate_tool(
        self,
        *,
        tool_name: str,
        is_read_only: bool,
        required_capabilities: tuple[str, ...],
        arguments: dict[str, Any],
        context: UniversalToolExecutionContext,
    ) -> UniversalPermissionDecision:
        path_denial = self._path_denial(arguments, context)
        if path_denial:
            return UniversalPermissionDecision(False, reason=path_denial)
        command = arguments.get("command")
        if isinstance(command, str):
            for pattern in self._settings.denied_commands:
                if fnmatch.fnmatch(command, pattern):
                    return UniversalPermissionDecision(
                        False,
                        reason=f"access denied: command matches deny rule {pattern}",
                    )
            if (
                tool_name == "terminal"
                and self._settings.allowed_terminal_commands
                and command not in self._settings.allowed_terminal_commands
            ):
                return UniversalPermissionDecision(
                    False,
                    reason="access denied: terminal command is not in the exact allowlist",
                )
        if tool_name in self._settings.denied_tools:
            return UniversalPermissionDecision(
                False,
                reason=f"{tool_name} is explicitly denied",
            )
        subject = context.metadata.get("permission_subject")
        capabilities = tuple(getattr(subject, "capabilities", ()))
        missing = tuple(
            capability
            for capability in required_capabilities
            if capability not in capabilities
        )
        if missing:
            return UniversalPermissionDecision(
                False,
                reason=f"principal lacks required capability: {', '.join(missing)}",
            )
        plan_mode = context.services.get("plan_mode")
        if (
            plan_mode is not None
            and callable(getattr(plan_mode, "enabled", None))
            and plan_mode.enabled(str(context.metadata.get("session_id", "")))
            and not is_read_only
            and tool_name != "exit_plan_mode"
        ):
            return UniversalPermissionDecision(
                False,
                reason="plan mode blocks mutating tools",
            )
        if tool_name in self._settings.allowed_tools:
            return UniversalPermissionDecision(True, reason=f"{tool_name} is explicitly allowed")
        if self._settings.mode is PermissionMode.FULL_AUTO or is_read_only:
            return UniversalPermissionDecision(True, reason="permission policy allowed")
        if self._settings.mode is PermissionMode.PLAN:
            return UniversalPermissionDecision(False, reason="plan mode blocks mutating tools")
        if "tool.auto" in capabilities:
            return UniversalPermissionDecision(True, reason="principal may auto-run tools")
        return UniversalPermissionDecision(
            False,
            requires_confirmation=True,
            reason="mutating tools require explicit confirmation in default mode",
        )

    def _path_denial(
        self,
        arguments: dict[str, Any],
        context: UniversalToolExecutionContext,
    ) -> str:
        for key in _PATH_ARGUMENTS:
            value = arguments.get(key)
            values = value if isinstance(value, list) else [value]
            for item in values:
                if not isinstance(item, str) or not item.strip():
                    continue
                candidate_path = Path(item).expanduser()
                if not candidate_path.is_absolute():
                    candidate_path = context.cwd / candidate_path
                path = str(candidate_path.resolve(strict=False))
                candidates = (path.rstrip("/"), path.rstrip("/") + "/")
                for candidate in candidates:
                    for pattern in _SENSITIVE_PATH_PATTERNS:
                        if fnmatch.fnmatch(candidate, pattern):
                            return f"access denied: path matches protected pattern {pattern}"
                    for rule in self._settings.path_rules:
                        if fnmatch.fnmatch(candidate, rule.pattern) and not rule.allow:
                            return f"access denied: path matches deny rule {rule.pattern}"
        return ""


def required_capability(definition: ToolDefinition) -> str:
    if definition.execution_backend is ExecutionBackend.MCP:
        return "mcp.call"
    device_tool = (
        definition.resource_key is not None and definition.resource_key.endswith(":backend")
    ) or (".robot_" in definition.internal_id or definition.internal_id.endswith(".observe.v1"))
    if device_tool:
        return "device.control" if _is_mutating(definition) else "device.read"
    return "tool.mutate" if _is_mutating(definition) else "tool.read"


def _is_mutating(definition: ToolDefinition) -> bool:
    return any(effect not in {"none", "read", "read_only"} for effect in definition.state_effects)


__all__ = [
    "PermissionChecker",
    "PermissionMode",
    "PermissionSettingsConfig",
    "required_capability",
]
