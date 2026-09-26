from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homemaster.adapters import build_tool_registry
from homemaster.agent.messages import ToolCall
from homemaster.permissions import PermissionChecker, PermissionMode, PermissionSettingsConfig
from homemaster.tools import ToolRegistry
from homemaster.tools.contracts import (
    PermissionSubject,
    ToolExecutionResult,
    ToolExecutionStatus,
)
from homemaster.tools.executor import ToolExecutor
from tests.homemaster.tools.test_support import ToolExecutionContext


def registry() -> ToolRegistry:
    return build_tool_registry(environment="local_robot")


async def execute(
    tool_registry: ToolRegistry,
    root: Path,
    name: str,
    arguments: dict[str, object],
    *,
    capabilities: tuple[str, ...],
    path_rules: tuple[dict[str, object], ...] = (),
    backend: object | None = None,
    services: dict[str, object] | None = None,
    run_context: object | None = None,
    call_id: str | None = None,
) -> ResultView:
    resolved_call_id = call_id or f"call-{name}"
    metadata: dict[str, Any] = {
        "tool_registry": tool_registry,
        "permission_subject": PermissionSubject(
            subject_id="operator",
            channel="test",
            capabilities=capabilities,
        ),
        "backend": backend,
        "services": dict(services or {}),
        "session_id": "test-session",
        "run_id": "test-run",
        "turn_index": 0,
        "tool_call_id": resolved_call_id,
        "internal_tool_id": f"homemaster.{name}.v1",
    }
    metadata.update(services or {})
    if run_context is not None:
        metadata["run_context"] = run_context
    executor = ToolExecutor(
        tool_registry,
        permission_checker=PermissionChecker(
            PermissionSettingsConfig(
                mode=PermissionMode.FULL_AUTO,
                path_rules=path_rules,
            )
        ),
    )
    result = await executor.execute(
        ToolCall(id=resolved_call_id, name=name, arguments=arguments),
        ToolExecutionContext(root, metadata=metadata),
    )
    return ResultView(result)


class ResultView:
    """Readable assertions over the public small ToolResult contract."""

    def __init__(self, result: ToolExecutionResult) -> None:
        self.raw = result
        self.text = result.text
        # Keep the fixture view compatible with the pre-canonical assertions:
        # ``data`` is the tool payload, while the canonical envelope remains
        # available through ``raw`` and ``to_public_dict``.
        public = result.to_public_dict()
        payload = public.get("data")
        self.data = dict(payload) if isinstance(payload, dict) else {}
        status = result.status.value
        legacy_status = {
            "permission_denied": "denied",
            "invalid_tool_arguments": "invalid",
            "unknown_tool": "not_found",
            "deadline_exceeded": "failure",
        }.get(status, status)
        self.status = ToolExecutionStatus(legacy_status)
        error_code = result.error.code if result.error is not None else None
        self.error = (
            SimpleNamespace(code=error_code, message=result.failure_reason) if error_code else None
        )
        verification_status = result.verification.status.value
        self.verification = SimpleNamespace(
            status=SimpleNamespace(value=verification_status),
            detail=result.verification.detail or "",
        )
        self.backend_attempted = result.backend_attempted
        self.is_error = result.is_error
        self.images = list(result.images)

    def to_dict(self) -> dict[str, object]:
        return {
            "output": self.text,
            "is_error": self.is_error,
            "metadata": dict(self.data),
        }
