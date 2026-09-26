"""Small adapters used while migrating pre-V3.5 test fixtures.

These helpers intentionally live under tests.  The runtime only accepts the
canonical contracts from ``homemaster.tools.contracts``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from homemaster.tools.contracts import (
    OutcomeCertainty,
    PermissionSubject,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
)
from homemaster.tools.contracts import (
    ToolExecutionContext as CanonicalToolExecutionContext,
)


def ToolResult(
    output: str = "",
    is_error: bool = False,
    metadata: Mapping[str, Any] | None = None,
    canonical_result: ToolExecutionResult | None = None,
) -> ToolExecutionResult:
    """Build a canonical result for legacy fixture call sites."""

    if canonical_result is not None:
        return canonical_result
    data = dict(metadata or {})
    raw_status = str(data.get("status", "success" if not is_error else "failure"))
    if not is_error:
        status = ToolExecutionStatus.SUCCESS
        certainty = OutcomeCertainty.CONFIRMED
        error = None
        backend_attempted = bool(data.get("backend_attempted", False))
    elif raw_status == ToolExecutionStatus.OUTCOME_UNKNOWN.value:
        status = ToolExecutionStatus.OUTCOME_UNKNOWN
        certainty = OutcomeCertainty.UNKNOWN
        error = ToolExecutionError(
            code=str(data.get("error_code", raw_status)),
            message=output or raw_status,
            details=data,
        )
        backend_attempted = True
    else:
        status = {
            "permission_denied": ToolExecutionStatus.DENIED,
            "denied": ToolExecutionStatus.DENIED,
            "invalid_tool_arguments": ToolExecutionStatus.INVALID,
            "cancelled": ToolExecutionStatus.CANCELLED,
        }.get(raw_status, ToolExecutionStatus.FAILURE)
        certainty = OutcomeCertainty.CONFIRMED
        error = ToolExecutionError(
            code=str(data.get("error_code", raw_status)),
            message=output or raw_status,
            details=data,
        )
        backend_attempted = bool(data.get("backend_attempted", False))
    return ToolExecutionResult(
        status=status,
        text=output,
        data=data,
        error=error,
        outcome_certainty=certainty,
        backend_attempted=backend_attempted,
        external_return_code=data.get("external_return_code"),
    )


def ToolExecutionContext(
    working_directory: Path | None = None,
    metadata: Mapping[str, Any] | None = None,
    **overrides: Any,
) -> CanonicalToolExecutionContext:
    """Translate the pre-V3.5 short context fixture into the canonical one."""

    if working_directory is None:
        working_directory = overrides.pop("cwd", None)
    if working_directory is None:
        raise TypeError("working_directory is required")
    values = dict(metadata or {})
    values.update(overrides)
    subject = values.pop(
        "permission_subject",
        PermissionSubject(subject_id="test", channel="pytest"),
    )
    if not isinstance(subject, PermissionSubject):
        subject = PermissionSubject(
            subject_id=str(getattr(subject, "subject_id", "test")),
            channel=str(getattr(subject, "channel", "pytest")),
            tenant_id=getattr(subject, "tenant_id", None),
            capabilities=tuple(getattr(subject, "capabilities", ())),
        )
    tool_call_id = str(values.pop("tool_call_id", "test-call"))
    service_values = dict(values.pop("services", {}))
    service_values.update(values)
    return _canonical_context(
        session_id=str(values.pop("session_id", "test-session")),
        run_id=str(values.pop("run_id", "test-run")),
        turn_index=int(values.pop("turn_index", 0)),
        tool_call_id=tool_call_id,
        internal_tool_id=str(values.pop("internal_tool_id", "homemaster.test.v1")),
        permission_subject=subject,
        backend=values.pop("backend", None),
        deadline=values.pop("deadline", None),
        cancellation=values.pop("cancellation", None),
        domain_observer=values.pop("domain_observer", None),
        working_directory=working_directory,
        services=service_values,
    )


def _canonical_context(**values: Any) -> CanonicalToolExecutionContext:
    return CanonicalToolExecutionContext(**values)


__all__ = ["ToolExecutionContext", "ToolResult"]
