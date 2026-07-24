"""Capability-aware HomeMaster permission policy.

Mode and deterministic allow/deny control flow are adapted from OpenHarness
9b2efd7 ``src/openharness/permissions/{modes,checker}.py``. HomeMaster adds
typed tenant principals and device/MCP capabilities.
"""

from __future__ import annotations

import fnmatch
from typing import Any

from homemaster.permissions.config import PermissionMode, PermissionSettingsConfig
from homemaster.tools.contracts import ExecutionBackend, ToolDefinition, ToolExecutionContext
from homemaster.tools.paths import ToolPathError, resolve_context_tool_path
from homemaster.tools.pipeline import PermissionDecision

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


class HomePermissionPolicy:
    """Evaluate every tool call against immutable subject capabilities and rules."""

    def __init__(self, settings: PermissionSettingsConfig) -> None:
        if not isinstance(settings, PermissionSettingsConfig):
            raise TypeError("settings must be PermissionSettingsConfig")
        self._settings = settings

    def evaluate(
        self,
        definition: ToolDefinition,
        arguments: Any,
        context: ToolExecutionContext,
    ) -> PermissionDecision:
        subject = context.permission_subject
        evidence_ref = f"permission/{context.run_id}/{context.tool_call_id}"
        path_denial = self._path_denial(arguments, context)
        if path_denial:
            return PermissionDecision(False, reason=path_denial, evidence_ref=evidence_ref)
        command_denial = self._command_denial(arguments)
        if command_denial:
            return PermissionDecision(False, reason=command_denial, evidence_ref=evidence_ref)
        if definition.internal_id in self._settings.denied_tools:
            return PermissionDecision(
                False,
                reason=f"{definition.internal_id} is explicitly denied",
                evidence_ref=evidence_ref,
            )

        exact = f"tool:{definition.internal_id}"
        base_capability = required_capability(definition)
        required = tuple(dict.fromkeys(definition.required_capabilities))
        missing_base = (
            (base_capability,)
            if base_capability not in subject.capabilities and exact not in subject.capabilities
            else ()
        )
        missing = missing_base + tuple(
            capability
            for capability in required
            if capability != base_capability and capability not in subject.capabilities
        )
        if missing:
            return PermissionDecision(
                False,
                reason=f"principal lacks required capability: {', '.join(missing)}",
                evidence_ref=evidence_ref,
            )

        plan_mode = getattr(context, "services", {}).get("plan_mode")
        if (
            plan_mode is not None
            and callable(getattr(plan_mode, "enabled", None))
            and plan_mode.enabled(getattr(context, "session_id", ""))
            and _is_mutating(definition)
            and definition.internal_id != "openharness.exit_plan_mode.v1"
        ):
            return PermissionDecision(
                False,
                reason="plan mode blocks mutating tools",
                evidence_ref=evidence_ref,
            )

        if definition.internal_id in self._settings.allowed_tools:
            return PermissionDecision(
                True,
                reason=f"{definition.internal_id} is explicitly allowed",
                evidence_ref=evidence_ref,
            )
        mutating = _is_mutating(definition)
        if self._settings.mode is PermissionMode.PLAN and mutating:
            return PermissionDecision(
                False,
                reason="plan mode blocks mutating tools",
                evidence_ref=evidence_ref,
            )
        if (
            self._settings.mode is PermissionMode.DEFAULT
            and mutating
            and "tool.auto" not in subject.capabilities
        ):
            return PermissionDecision(
                False,
                requires_confirmation=True,
                reason="mutating tools require explicit confirmation in default mode",
                evidence_ref=evidence_ref,
            )
        return PermissionDecision(
            True,
            reason="permission policy allowed",
            evidence_ref=evidence_ref,
        )

    def _path_denial(self, arguments: Any, context: ToolExecutionContext) -> str:
        if not isinstance(arguments, dict):
            return ""
        for key in _PATH_ARGUMENTS:
            value = arguments.get(key)
            values = value if isinstance(value, list) else [value]
            for item in values:
                if not isinstance(item, str) or not item.strip():
                    continue
                try:
                    path = str(resolve_context_tool_path(context, item))
                except ToolPathError as exc:
                    return f"access denied: invalid path: {exc}"
                candidates = (path.rstrip("/"), path.rstrip("/") + "/")
                for candidate in candidates:
                    for pattern in _SENSITIVE_PATH_PATTERNS:
                        if fnmatch.fnmatch(candidate, pattern):
                            return f"access denied: path matches protected pattern {pattern}"
                    for rule in self._settings.path_rules:
                        if fnmatch.fnmatch(candidate, rule.pattern) and not rule.allow:
                            return f"access denied: path matches deny rule {rule.pattern}"
        return ""

    def _command_denial(self, arguments: Any) -> str:
        if not isinstance(arguments, dict):
            return ""
        command = arguments.get("command")
        if not isinstance(command, str):
            return ""
        for pattern in self._settings.denied_commands:
            if fnmatch.fnmatch(command, pattern):
                return f"access denied: command matches deny rule {pattern}"
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
    "HomePermissionPolicy",
    "PermissionMode",
    "PermissionSettingsConfig",
    "required_capability",
]
