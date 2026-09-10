"""Capability-aware HomeMaster permission policy.

Mode and deterministic allow/deny control flow are adapted from OpenHarness
9b2efd7 ``src/openharness/permissions/{modes,checker}.py``. HomeMaster adds
typed tenant principals and device/MCP capabilities.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from homemaster.permissions.config import PermissionMode, PermissionSettingsConfig
from homemaster.permissions.models import PreparedPhysicalRequest
from homemaster.permissions.store import PermissionStore
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
    """OpenHarness-style ordinary tool checks plus exact household evaluation."""

    def __init__(
        self,
        settings: PermissionSettingsConfig,
        *,
        store: PermissionStore | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(settings, PermissionSettingsConfig):
            raise TypeError("settings must be PermissionSettingsConfig")
        self._settings = settings
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._denied_run_id: str | None = None
        self._denied_scopes: set[tuple[str, frozenset]] = set()

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
        return UniversalPermissionDecision(
            True,
            reason="ordinary tools need no interaction approval;"
            " household resources gate separately",
        )

    def evaluate_physical(
        self,
        *,
        request: PreparedPhysicalRequest,
        context: UniversalToolExecutionContext,
    ) -> PhysicalDecision:
        """Judge one prepared call against exact grants and current credentials.

        Ordinary tool modes, capabilities and allowlists never satisfy this:
        only an exact grant or a live once-credential for the same request
        allows an item.
        """
        if self._store is None:
            raise RuntimeError(
                "physical evaluation needs a PermissionStore; refusing to allow"
            )
        scope = frozenset(item.key for item in request.requirements)
        run_id = str(context.metadata.get("run_id", ""))
        session_id = str(context.metadata.get("session_id", ""))
        if run_id != self._denied_run_id:
            self._denied_run_id = run_id
            self._denied_scopes.clear()
        if (session_id, scope) in self._denied_scopes:
            return PhysicalDecision(
                False,
                (),
                "the same scope was already rejected in this run; not asking again",
            )
        try:
            stored = self._store.get_request(request.approval_id)
        except KeyError:
            return PhysicalDecision(False, (), "unknown approval; re-prepare the call")
        if stored.request_id != request.request_id or stored.revision != request.revision:
            return PhysicalDecision(False, (), "request changed after prepare; re-prepare")
        if stored.status not in ("prepared", "awaiting_approval", "ready", "running"):
            return PhysicalDecision(False, (), f"request is {stored.status}")
        try:
            expired = self._clock() > _parse_deadline(stored.deadline_at)
        except ValueError:
            return PhysicalDecision(False, (), "request deadline is unreadable")
        if expired:
            return PhysicalDecision(False, (), "request passed its deadline")
        once_live = stored.status in ("ready", "running")
        decisions = {item.item_id: item.decision for item in stored.items}
        grants = self._store.matching_grants([item.key for item in request.requirements])
        missing = tuple(
            item.item_id
            for item in request.requirements
            if not (
                (once_live and decisions.get(item.item_id) in ("allow_once", "allow_always"))
                or item.key in grants
            )
        )
        if missing:
            return PhysicalDecision(False, missing, "missing household permission")
        return PhysicalDecision(True, (), "household permission holds")

    def note_rejected(
        self,
        *,
        request: PreparedPhysicalRequest,
        context: UniversalToolExecutionContext,
    ) -> None:
        """Suppress re-asking the same rejected scope within the same run.

        The key comes from runtime-trusted run identity plus the exact
        scope, never from model-supplied intent ids. A new user input
        starts a new run and clears the suppression; nothing is stored
        permanently.
        """
        run_id = str(context.metadata.get("run_id", ""))
        session_id = str(context.metadata.get("session_id", ""))
        if run_id != self._denied_run_id:
            self._denied_run_id = run_id
            self._denied_scopes.clear()
        scope = frozenset(item.key for item in request.requirements)
        self._denied_scopes.add((session_id, scope))

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


def _parse_deadline(value: str) -> datetime:
    text = value.strip()
    candidate = f"{text[:-1]}+00:00" if text.endswith(("Z", "z")) else text
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None:
        raise ValueError("deadline must carry a timezone")
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class PhysicalDecision:
    """One exact-resource verdict: allow, or deny with the missing item ids."""

    allowed: bool
    missing_item_ids: tuple[str, ...] = ()
    reason: str = ""


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
    "PhysicalDecision",
    "required_capability",
]
