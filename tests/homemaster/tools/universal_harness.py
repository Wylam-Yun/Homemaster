from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homemaster.adapters import build_universal_tool_registry
from homemaster.agent.messages import ToolCall
from homemaster.permissions import PermissionChecker, PermissionMode, PermissionSettingsConfig
from homemaster.tools import ToolExecutionContext, ToolRegistry, ToolResult
from homemaster.tools.contracts import PermissionSubject, ToolExecutionStatus
from homemaster.tools.executor import ToolExecutor


def registry() -> ToolRegistry:
    return build_universal_tool_registry()


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

    def __init__(self, result: ToolResult) -> None:
        self.raw = result
        self.text = result.output
        self.data = result.metadata
        status = str(result.metadata.get("status", "failure" if result.is_error else "success"))
        legacy_status = {
            "permission_denied": "denied",
            "invalid_tool_arguments": "invalid",
            "unknown_tool": "not_found",
            "deadline_exceeded": "failure",
        }.get(status, status)
        self.status = ToolExecutionStatus(legacy_status)
        error_code = result.metadata.get("error_code")
        self.error = (
            SimpleNamespace(code=error_code, message=result.output) if error_code else None
        )
        verification_status = result.metadata.get("verification_status", "not_requested")
        self.verification = SimpleNamespace(
            status=SimpleNamespace(value=verification_status),
            detail=result.metadata.get("verification_detail", ""),
        )
        self.backend_attempted = bool(result.metadata.get("backend_attempted", False))
        self.is_error = result.is_error
        self.images = [SimpleNamespace(**item) for item in result.metadata.get("images", [])]

    def to_dict(self) -> dict[str, object]:
        return {
            "output": self.text,
            "is_error": self.is_error,
            "metadata": dict(self.data),
        }
