"""Single ordinary-name execution path for the universal ToolRegistry."""

from __future__ import annotations

import asyncio
import inspect
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from homemaster.agent.messages import ToolCall
from homemaster.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    requires_confirmation: bool = False
    reason: str = ""


class PermissionChecker(Protocol):
    def evaluate_tool(
        self,
        *,
        tool_name: str,
        is_read_only: bool,
        required_capabilities: tuple[str, ...],
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> PermissionDecision: ...


class AllowAllPermissionChecker:
    def evaluate_tool(
        self,
        *,
        tool_name: str,
        is_read_only: bool,
        required_capabilities: tuple[str, ...],
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> PermissionDecision:
        del tool_name, is_read_only, required_capabilities, arguments, context
        return PermissionDecision(True, reason="allowed")


class ResourceManager(Protocol):
    def acquire(self, resource_key: str, context: ToolExecutionContext) -> Any: ...


class NoopResourceManager:
    @asynccontextmanager
    async def acquire(self, resource_key: str, context: ToolExecutionContext):
        del resource_key, context
        yield


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        permission_checker: PermissionChecker | None = None,
        confirmation_handler: Any | None = None,
        resource_manager: ResourceManager | None = None,
    ) -> None:
        self.registry = registry
        self.permission_checker = permission_checker or AllowAllPermissionChecker()
        self.confirmation_handler = confirmation_handler
        self.resource_manager = resource_manager or NoopResourceManager()

    async def execute(self, call: ToolCall, context: ToolExecutionContext) -> ToolResult:
        tool = self.registry.get(call.name)
        if tool is None:
            return ToolResult(
                f"unknown tool: {call.name}",
                True,
                {"status": "unknown_tool"},
            )
        try:
            arguments = tool.input_model.model_validate(call.arguments)
        except ValidationError as exc:
            return _invalid_arguments_result(tool, call, exc)
        is_read_only = tool.is_read_only(arguments)
        decision = self.permission_checker.evaluate_tool(
            tool_name=tool.name,
            is_read_only=is_read_only,
            required_capabilities=tool.required_capabilities,
            arguments=call.arguments,
            context=context,
        )
        if decision.requires_confirmation:
            approved = False
            confirm = getattr(self.confirmation_handler, "confirm", None)
            if callable(confirm):
                value = confirm(tool, call.arguments, context, decision)
                approved = bool(await value if inspect.isawaitable(value) else value)
            if not approved:
                return ToolResult(
                    decision.reason or "tool confirmation was not granted",
                    True,
                    {"status": "permission_denied", "error_code": "permission_denied"},
                )
        elif not decision.allowed:
            return ToolResult(
                decision.reason or "tool execution was denied",
                True,
                {"status": "permission_denied", "error_code": "permission_denied"},
            )
        resource_key, resource_error = self._resource_key(tool, call.arguments, context)
        if resource_error is not None:
            return resource_error
        backend_started = False

        async def invoke() -> ToolResult:
            nonlocal backend_started
            async with _lease(self.resource_manager, resource_key, context):
                backend_started = True
                return await tool.execute(arguments, context)

        remaining = _remaining_s(context)
        if remaining is not None and remaining <= 0:
            return _deadline_result(
                is_read_only=is_read_only,
                backend_attempted=False,
            )
        try:
            if remaining is None:
                return await invoke()
            return await asyncio.wait_for(invoke(), timeout=remaining)
        except TimeoutError:
            return _deadline_result(
                is_read_only=is_read_only,
                backend_attempted=backend_started,
            )
        except asyncio.CancelledError:
            if backend_started and not is_read_only:
                return ToolResult(
                    "tool cancellation occurred after a mutation may have started",
                    True,
                    {
                        "status": "outcome_unknown",
                        "error_code": "execution_cancelled",
                        "backend_attempted": True,
                    },
                )
            if backend_started:
                return _cancelled_result(backend_attempted=True)
            raise
        except Exception as exc:
            manager_error = _resource_manager_error(exc)
            if manager_error is not None:
                return manager_error
            if backend_started and not is_read_only:
                return ToolResult(
                    f"backend outcome is unknown after {type(exc).__name__}: {exc}",
                    True,
                    {
                        "status": "outcome_unknown",
                        "error_code": "execution_exception",
                        "exception_type": type(exc).__name__,
                        "backend_attempted": True,
                    },
                )
            return ToolResult(
                f"{type(exc).__name__}: {exc}",
                True,
                {
                    "status": "tool_error",
                    "exception_type": type(exc).__name__,
                    "backend_attempted": backend_started,
                },
            )

    async def execute_many(
        self,
        calls: list[tuple[ToolCall, ToolExecutionContext]],
    ) -> list[ToolResult]:
        grouped: dict[tuple[str, str | int], list[tuple[int, ToolCall, ToolExecutionContext]]] = {}
        for index, (call, context) in enumerate(calls):
            grouped.setdefault(self._execution_conflict_key(call, context, index), []).append(
                (index, call, context)
            )

        async def run_group(
            items: list[tuple[int, ToolCall, ToolExecutionContext]],
        ) -> list[tuple[int, ToolResult | BaseException]]:
            values: list[tuple[int, ToolResult | BaseException]] = []
            for position, (index, call, context) in enumerate(items):
                if _cancellation_requested(context):
                    values.extend(
                        (remaining_index, _cancelled_result(backend_attempted=False))
                        for remaining_index, _call, _context in items[position:]
                    )
                    break
                try:
                    value: ToolResult | BaseException = await self.execute(call, context)
                except asyncio.CancelledError:
                    values.extend(
                        (remaining_index, _cancelled_result(backend_attempted=False))
                        for remaining_index, _call, _context in items[position:]
                    )
                    break
                except Exception as exc:
                    value = exc
                values.append((index, value))
            return values

        group_tasks = [asyncio.create_task(run_group(items)) for items in grouped.values()]

        async def collect_groups() -> list[list[tuple[int, ToolResult | BaseException]]]:
            return await asyncio.gather(*group_tasks)

        collector = asyncio.create_task(collect_groups())
        try:
            batches = await asyncio.shield(collector)
        except asyncio.CancelledError:
            for task in group_tasks:
                if not task.done():
                    task.cancel()
            batches = await collector
        ordered: list[ToolResult | None] = [None] * len(calls)
        for batch in batches:
            for index, value in batch:
                if isinstance(value, Exception):
                    value = ToolResult(
                        f"Tool {calls[index][0].name} failed: {type(value).__name__}: {value}",
                        True,
                        {"status": "tool_error", "exception_type": type(value).__name__},
                    )
                ordered[index] = value
        assert all(result is not None for result in ordered)
        return [result for result in ordered if result is not None]

    def _execution_conflict_key(
        self,
        call: ToolCall,
        context: ToolExecutionContext,
        index: int,
    ) -> tuple[str, str | int]:
        tool = self.registry.get(call.name)
        if tool is None:
            return ("parallel", index)
        policy = getattr(tool, "concurrency_policy", "parallel")
        if policy == "serialized":
            return ("serialized", tool.name)
        if policy != "resource_key":
            return ("parallel", index)
        resource_key, error = self._resource_key(tool, call.arguments, context)
        if error is not None or resource_key is None:
            return ("parallel", index)
        return ("resource", resource_key)

    def _resource_key(
        self,
        tool: Any,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> tuple[str | None, ToolResult | None]:
        policy = getattr(tool, "concurrency_policy", "parallel")
        if policy == "serialized":
            return f"tool:{tool.name}", None
        if policy != "resource_key":
            return None, None
        try:
            resolver = getattr(tool, "resource_key_resolver", None)
            value = resolver(arguments, context) if callable(resolver) else tool.resource_key
        except Exception as exc:
            return None, ToolResult(
                f"resource key resolution failed: {type(exc).__name__}: {exc}",
                True,
                {
                    "status": "resource_key_resolution_failed",
                    "exception_type": type(exc).__name__,
                },
            )
        if not isinstance(value, str) or not value or "\x00" in value:
            return None, ToolResult(
                "resource key resolver must return a non-empty string",
                True,
                {"status": "resource_key_resolution_failed"},
            )
        return value, None


def _invalid_arguments_result(
    tool: BaseTool,
    call: ToolCall,
    error: ValidationError,
) -> ToolResult:
    schema = tool.input_model.model_json_schema()
    required = schema.get("required", [])
    missing_required = sorted(
        name for name in required if isinstance(name, str) and name not in call.arguments
    )
    issues = []
    for item in error.errors(include_url=False, include_context=False, include_input=False):
        message = str(item.get("msg", "invalid value"))
        for prefix in ("Value error, invalid tool arguments: ", "invalid tool arguments: "):
            if message.startswith(prefix):
                message = message.removeprefix(prefix)
                break
        issues.append(
            {
                "location": [
                    part if isinstance(part, str | int) else str(part)
                    for part in item.get("loc", ())
                ],
                "message": message,
                "type": str(item.get("type", "value_error")),
            }
        )
    payload = {
        "status": "invalid_tool_arguments",
        "error_code": "invalid_tool_arguments",
        "tool": tool.name,
        "backend_attempted": False,
        "received_argument_keys": sorted(str(name) for name in call.arguments),
        "missing_required_arguments": missing_required,
        "issues": issues,
    }
    return ToolResult(json.dumps(payload, ensure_ascii=False, sort_keys=True), True, payload)


@asynccontextmanager
async def _lease(
    manager: ResourceManager,
    key: str | None,
    context: ToolExecutionContext,
):
    if key is None:
        yield
        return
    value = manager.acquire(key, context)
    if hasattr(value, "__aenter__"):
        async with value:
            yield
    else:
        with value:
            yield


def _remaining_s(context: ToolExecutionContext) -> float | None:
    deadline = context.metadata.get("deadline")
    if deadline is None or not callable(getattr(deadline, "remaining_s", None)):
        return None
    return deadline.remaining_s()


def _cancellation_requested(context: ToolExecutionContext) -> bool:
    cancellation = context.metadata.get("cancellation")
    return bool(getattr(cancellation, "cancelled", False))


def _cancelled_result(*, backend_attempted: bool) -> ToolResult:
    return ToolResult(
        "tool execution was cancelled",
        True,
        {
            "status": "execution_cancelled",
            "error_code": "execution_cancelled",
            "backend_attempted": backend_attempted,
        },
    )


def _deadline_result(*, is_read_only: bool, backend_attempted: bool) -> ToolResult:
    status = "outcome_unknown" if backend_attempted and not is_read_only else "deadline_exceeded"
    message = (
        "backend outcome is unknown after timeout"
        if status == "outcome_unknown"
        else "tool deadline expired before a mutating backend started"
        if not is_read_only
        else "tool execution timed out"
    )
    return ToolResult(
        message,
        True,
        {"status": status, "backend_attempted": backend_attempted},
    )


def _resource_manager_error(exc: BaseException) -> ToolResult | None:
    code = getattr(exc, "error_code", None)
    status = getattr(exc, "execution_status", None)
    if not isinstance(code, str) or not isinstance(status, str):
        return None
    return ToolResult(
        str(exc),
        True,
        {
            "status": status,
            "error_code": code,
            "backend_attempted": bool(getattr(exc, "backend_attempted", False)),
        },
    )


__all__ = [
    "AllowAllPermissionChecker",
    "NoopResourceManager",
    "PermissionChecker",
    "PermissionDecision",
    "ResourceManager",
    "ToolExecutor",
]
