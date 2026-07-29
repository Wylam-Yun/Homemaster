"""Migration adapters that make existing HomeMaster tool bodies BaseTools."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from homemaster.tools.base import FunctionTool, ToolExecutionContext, normalize_tool_result
from homemaster.tools.contracts import (
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    VerificationRecord,
    VerificationStatus,
)


def from_registered_tool(registered: Any) -> FunctionTool:
    """Adapt one current canonical registration without exposing its namespaced ID."""

    definition = registered.definition

    async def execute(arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        executor = registered.executor
        legacy = getattr(executor, "_executor", None)
        run_context = context.metadata.get("run_context")
        if callable(legacy) and run_context is not None:
            thread_adapter = run_context.deps.get("sync_backend_adapter")
            if thread_adapter is None:
                value = await asyncio.to_thread(
                    legacy,
                    arguments=dict(arguments),
                    run_context=run_context,
                )
            else:
                value = await thread_adapter.run(
                    legacy,
                    arguments=dict(arguments),
                    run_context=run_context,
                )
            value = await value if inspect.isawaitable(value) else value
        else:
            value = await executor.execute(arguments, context)
        if registered.verifier is not None and _verifier_applies(registered, value):
            value = await _verify_registered_tool(registered, value, context)
        return normalize_tool_result(value)

    effects = tuple(getattr(definition, "state_effects", ()))
    verification_policy = definition.verification_policy
    from homemaster.permissions.policy import required_capability

    required_capabilities = tuple(
        dict.fromkeys((required_capability(definition), *definition.required_capabilities))
    )
    dynamic_read_only = getattr(registered.executor, "is_read_only", None)
    if callable(dynamic_read_only):

        def read_only(arguments: Mapping[str, Any]) -> bool:
            return bool(dynamic_read_only(arguments))

    else:
        read_only = not any(effect not in {"none", "read", "read_only"} for effect in effects)
    return FunctionTool(
        name=definition.model_alias,
        description=definition.description,
        input_schema=definition.input_schema,
        execute=execute,
        read_only=read_only,
        verification_required=verification_policy.execution_proof.value != "none",
        requires_model_observation=definition.requires_model_observation,
        external_terminal_owner=(
            verification_policy.terminal_rule.value == "external_terminal_owner"
        ),
        required_capabilities=required_capabilities,
        concurrency_policy=definition.concurrency_policy.value,
        resource_key=definition.resource_key,
        resource_key_resolver=registered.resource_key_resolver,
    )


def _verifier_applies(registered: Any, value: Any) -> bool:
    if not isinstance(value, ToolExecutionResult):
        return False
    proof = registered.definition.verification_policy.execution_proof
    return proof.value != "none" and value.status in {
        ToolExecutionStatus.SUCCESS,
        ToolExecutionStatus.FAILURE,
    }


async def _verify_registered_tool(
    registered: Any,
    result: ToolExecutionResult,
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    try:
        awaitable = registered.verifier.verify(result, context)
        deadline = context.metadata.get("deadline")
        remaining = deadline.remaining_s() if deadline is not None else None
        if remaining is None:
            verification = await awaitable
        elif remaining <= 0:
            raise TimeoutError
        else:
            verification = await asyncio.wait_for(awaitable, timeout=remaining)
    except TimeoutError:
        verification = VerificationRecord(
            status=VerificationStatus.PENDING,
            detail="verification deadline expired",
        )
        return replace(
            result,
            status=ToolExecutionStatus.VERIFICATION_PENDING,
            error=ToolExecutionError(
                "deadline_exceeded",
                "verification did not finish before the deadline",
            ),
            verification=verification,
            retryable=False,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        verification = VerificationRecord(
            status=VerificationStatus.PENDING,
            detail=f"{type(exc).__name__}: {exc}",
        )
        return replace(
            result,
            status=ToolExecutionStatus.VERIFICATION_PENDING,
            error=ToolExecutionError("verification_exception", verification.detail),
            verification=verification,
            retryable=False,
        )
    if not isinstance(verification, VerificationRecord):
        raise TypeError("verifier must return VerificationRecord")
    if verification.status is VerificationStatus.FAILED:
        return replace(
            result,
            status=ToolExecutionStatus.FAILURE,
            error=ToolExecutionError(
                "verification_failed",
                verification.detail or "verification failed",
            ),
            verification=verification,
        )
    if verification.status is VerificationStatus.PENDING:
        return replace(
            result,
            status=ToolExecutionStatus.VERIFICATION_PENDING,
            error=ToolExecutionError(
                "verification_pending",
                verification.detail or "verification pending",
            ),
            verification=verification,
            retryable=False,
        )
    return replace(result, verification=verification)


def from_tool_spec(spec: Any) -> FunctionTool:
    """Adapt a ToolSpec directly to the universal Registry."""

    if not callable(spec.executor):
        raise ValueError(f"tool {spec.name!r} has no executor")

    async def execute(arguments: dict[str, Any], context: ToolExecutionContext) -> Any:
        run_context = context.metadata.get("run_context")
        if run_context is None:
            return {
                "error": "unsupported_capability",
                "detail": "tool invocation has no connected runtime backend",
            }
        thread_adapter = run_context.deps.get("sync_backend_adapter")
        if thread_adapter is None:
            value = await asyncio.to_thread(
                spec.executor,
                arguments=dict(arguments),
                run_context=run_context,
            )
        else:
            value = await thread_adapter.run(
                spec.executor,
                arguments=dict(arguments),
                run_context=run_context,
            )
        return await value if inspect.isawaitable(value) else value

    effects = tuple(getattr(spec, "state_effects", ()))
    return FunctionTool(
        name=spec.name,
        description=spec.description,
        input_schema=spec.input_schema,
        execute=execute,
        read_only=not any(effect not in {"none", "read", "read_only"} for effect in effects),
    )


__all__ = ["from_registered_tool", "from_tool_spec"]
