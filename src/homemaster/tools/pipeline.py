"""Stateless canonical tool execution pipeline.

The async call/exception-isolation shape is adapted from the locked
OpenHarness ``engine/query.py`` ``_execute_tool_call`` and parallel gather
path.  HomeMaster owns the surrounding policy stages because terminal,
observation, evidence, and domain state are not OpenHarness concerns.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from jsonschema import Draft202012Validator, FormatChecker

from homemaster.agent.messages import ToolCall
from homemaster.tools.catalog import ToolCatalog, ToolLookupStatus
from homemaster.tools.contracts import (
    OutcomeCertainty,
    RegisteredTool,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    VerificationRecord,
    VerificationStatus,
    _thaw_json,
)


class PermissionPolicy(Protocol):
    def evaluate(
        self,
        definition: ToolDefinition,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> PermissionDecision | Awaitable[PermissionDecision]: ...


class ResourceManager(Protocol):
    def acquire(
        self,
        resource_key: str,
        context: ToolExecutionContext,
    ) -> Any: ...


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str = ""
    requires_confirmation: bool = False
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise TypeError("permission allowed must be a boolean")
        if not isinstance(self.requires_confirmation, bool):
            raise TypeError("requires_confirmation must be a boolean")
        if self.allowed and self.requires_confirmation:
            raise ValueError("an allowed decision cannot require confirmation")
        if self.evidence_ref is not None and not self.evidence_ref.strip():
            raise ValueError("permission evidence_ref must be non-empty")


class AllowAllPermissionPolicy:
    """Default policy seam; calls are still recorded by the pipeline."""

    def evaluate(
        self,
        definition: ToolDefinition,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> PermissionDecision:
        del definition, arguments, context
        return PermissionDecision(allowed=True, reason="default allow")


class NoopResourceManager:
    @asynccontextmanager
    async def acquire(self, resource_key: str, context: ToolExecutionContext):
        del resource_key, context
        yield


class NoopObservationService:
    async def before_action(
        self,
        definition: ToolDefinition,
        context: ToolExecutionContext,
    ) -> bool | ToolExecutionResult | None:
        del definition, context
        return True

    async def after_action(
        self,
        definition: ToolDefinition,
        result: ToolExecutionResult,
        context: ToolExecutionContext,
    ) -> None:
        del definition, result, context


class NoopAuthoritativeLedger:
    async def record_permission(
        self,
        tool_call: ToolCall,
        decision: PermissionDecision,
        context: ToolExecutionContext,
        attempt_index: int,
    ) -> None:
        del tool_call, decision, context, attempt_index

    async def record_execution(
        self,
        tool_call: ToolCall,
        result: ToolExecutionResult,
        context: ToolExecutionContext,
        attempt_index: int,
    ) -> None:
        del tool_call, result, context, attempt_index


class NoopPublicEventSink:
    async def publish(
        self,
        tool_call: ToolCall,
        result: ToolExecutionResult,
        context: ToolExecutionContext,
        attempt_index: int,
    ) -> None:
        del tool_call, result, context, attempt_index


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded tool retry policy; provider retries are outside this class."""

    max_attempts: int = 1
    retryable_internal_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        object.__setattr__(self, "retryable_internal_ids", frozenset(self.retryable_internal_ids))

    def should_retry(
        self,
        definition: ToolDefinition,
        result: ToolExecutionResult,
        attempt_index: int,
        context: ToolExecutionContext,
    ) -> bool:
        if attempt_index >= self.max_attempts:
            return False
        if definition.internal_id not in self.retryable_internal_ids:
            return False
        if result.status is ToolExecutionStatus.OUTCOME_UNKNOWN:
            return False
        if result.status is not ToolExecutionStatus.FAILURE or not result.retryable:
            return False
        if _is_mutating(definition):
            return False
        if context.deadline is not None:
            remaining = context.deadline.remaining_s()
            if remaining is not None and remaining <= 0:
                return False
        return True


class SchemaValidator:
    """Draft 2020-12 validator with explicit custom-format registration."""

    def __init__(
        self,
        custom_formats: Mapping[str, Callable[[object], bool]] | None = None,
    ) -> None:
        self._format_checker = FormatChecker()
        self._custom_formats = dict(custom_formats or {})
        for name, checker in self._custom_formats.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("custom format names must be non-empty")
            self._format_checker.checks(name)(checker)

    def check_definition(self, definition: ToolDefinition) -> None:
        for schema_name, schema in (
            ("input_schema", definition.input_schema),
            ("output_schema", definition.output_schema),
        ):
            if not schema:
                continue
            self._reject_unenabled_formats(schema, schema_name)
            Draft202012Validator.check_schema(_thaw_json(schema))

    def validate_input(
        self,
        definition: ToolDefinition,
        arguments: Mapping[str, object],
    ) -> str | None:
        if not isinstance(arguments, Mapping):
            return "tool arguments must be an object"
        if not definition.input_schema:
            return None
        return self._validate(definition.input_schema, arguments, "input")

    def validate_output(
        self,
        definition: ToolDefinition,
        result: ToolExecutionResult,
    ) -> str | None:
        if not definition.output_schema or result.status is not ToolExecutionStatus.SUCCESS:
            return None
        return self._validate(definition.output_schema, dict(result.data), "output")

    def _validate(
        self,
        schema: Mapping[str, object],
        value: object,
        label: str,
    ) -> str | None:
        validator = Draft202012Validator(
            _thaw_json(schema),
            format_checker=self._format_checker,
        )
        error = next(iter(validator.iter_errors(value)), None)
        if error is None:
            return None
        location = ".".join(str(part) for part in error.absolute_path)
        suffix = f" at {location}" if location else ""
        return f"{label} schema validation failed{suffix}: {error.message}"

    def _reject_unenabled_formats(self, schema: Mapping[str, object], label: str) -> None:
        for format_name in _schema_formats(schema):
            if format_name not in self._format_checker.checkers:
                raise ValueError(
                    f"{label} uses custom format {format_name!r}; register an explicit checker"
                )


@dataclass
class ToolExecutionPipeline:
    """One stateless async execution path for canonical registered tools."""

    catalog: ToolCatalog
    permission_policy: PermissionPolicy = field(default_factory=AllowAllPermissionPolicy)
    resource_manager: ResourceManager = field(default_factory=NoopResourceManager)
    observation_service: Any = field(default_factory=NoopObservationService)
    authoritative_ledger: Any = field(default_factory=NoopAuthoritativeLedger)
    public_event_sink: Any = field(default_factory=NoopPublicEventSink)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    custom_formats: Mapping[str, Callable[[object], bool]] | None = None
    terminal_policy: Any | None = None
    confirmation_handler: Any | None = None

    def __post_init__(self) -> None:
        self._validator = SchemaValidator(self.custom_formats)
        for registered in self.catalog.list_tools():
            self._validator.check_definition(registered.definition)

    def validate_catalog(self) -> None:
        for registered in self.catalog.list_tools():
            self._validator.check_definition(registered.definition)

    async def execute(
        self,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        registered, result = self._resolve(tool_call, context)
        if result is not None:
            return result
        assert registered is not None
        definition = registered.definition

        attempt = 1
        while True:
            terminal = await self._terminal_result(tool_call, context)
            if terminal is not None:
                if attempt > 1:
                    await self._record_and_publish(tool_call, terminal, context, attempt)
                return terminal

            validation_error = self._validator.validate_input(
                definition,
                tool_call.arguments,
            )
            if validation_error is not None:
                invalid = _result(
                    ToolExecutionStatus.INVALID,
                    "invalid_tool_arguments",
                    validation_error,
                )
                if attempt > 1:
                    await self._record_and_publish(tool_call, invalid, context, attempt)
                return invalid

            try:
                decision = await _maybe_await(
                    self.permission_policy.evaluate(
                        definition,
                        tool_call.arguments,
                        context,
                    )
                )
            except Exception as exc:
                denied = _result(
                    ToolExecutionStatus.DENIED,
                    "permission_policy_error",
                    f"{type(exc).__name__}: {exc}",
                )
                await self._record_and_publish(tool_call, denied, context, attempt)
                return denied
            if not isinstance(decision, PermissionDecision):
                raise TypeError("permission policy must return PermissionDecision")
            if decision.requires_confirmation:
                approved = False
                if self.confirmation_handler is not None:
                    approved = bool(
                        await _call(
                            self.confirmation_handler,
                            "confirm",
                            definition,
                            tool_call.arguments,
                            context,
                            decision,
                            default=False,
                        )
                    )
                decision = replace(
                    decision,
                    allowed=approved,
                    requires_confirmation=False,
                )
            if not decision.allowed:
                denied = _result(
                    ToolExecutionStatus.DENIED,
                    "permission_denied",
                    decision.reason or "tool execution was denied",
                )
                await self._record_and_publish(
                    tool_call,
                    denied,
                    context,
                    attempt,
                    decision,
                )
                return denied

            early = self._check_cancel_deadline(context)
            if early is not None:
                await self._record_and_publish(
                    tool_call,
                    early,
                    context,
                    attempt,
                    decision,
                )
                return early

            gate = await _call(
                self.observation_service,
                "before_action",
                definition,
                context,
                default=True,
            )
            blocked = _observation_block(gate)
            if blocked is not None:
                await self._record_and_publish(
                    tool_call,
                    blocked,
                    context,
                    attempt,
                    decision,
                )
                return blocked

            result = await self._execute_once(registered, tool_call, context)
            output_error = self._validator.validate_output(definition, result)
            if output_error is not None:
                result = _result(
                    ToolExecutionStatus.FAILURE,
                    "invalid_tool_result",
                    output_error,
                    backend_attempted=result.backend_attempted,
                )
            if registered.verifier is not None and _verifier_applies(definition, result):
                result = await self._verify(registered, result, context)
            await _call(
                self.observation_service,
                "after_action",
                definition,
                result,
                context,
            )
            await self._record_and_publish(tool_call, result, context, attempt, decision)
            if not self.retry_policy.should_retry(definition, result, attempt, context):
                return result
            attempt += 1

    async def execute_many(
        self,
        calls: Sequence[tuple[ToolCall, ToolExecutionContext]],
    ) -> list[ToolExecutionResult]:
        """Execute non-conflicting siblings while isolating each exception.

        This preserves the locked OpenHarness ``gather(return_exceptions=True)``
        pairing rule while serializing calls with the same typed conflict key.
        """

        grouped: dict[tuple[str, str | int], list[tuple[int, ToolCall, ToolExecutionContext]]] = {}
        for index, (tool_call, context) in enumerate(calls):
            key = self._execution_conflict_key(context, index)
            grouped.setdefault(key, []).append((index, tool_call, context))

        async def run_group(
            items: list[tuple[int, ToolCall, ToolExecutionContext]],
        ) -> list[tuple[int, ToolExecutionResult | BaseException]]:
            values: list[tuple[int, ToolExecutionResult | BaseException]] = []
            for index, tool_call, context in items:
                try:
                    value: ToolExecutionResult | BaseException = await self.execute(
                        tool_call,
                        context,
                    )
                except BaseException as exc:  # preserve sibling result pairing
                    value = exc
                values.append((index, value))
            return values

        batches = await asyncio.gather(
            *(run_group(items) for items in grouped.values()),
            return_exceptions=False,
        )
        ordered: list[ToolExecutionResult | None] = [None] * len(calls)
        for batch in batches:
            for index, value in batch:
                tool_call = calls[index][0]
                if isinstance(value, BaseException):
                    value = _result(
                        ToolExecutionStatus.FAILURE,
                        "executor_exception",
                        f"Tool {tool_call.name} failed: {type(value).__name__}: {value}",
                        backend_attempted=True,
                    )
                ordered[index] = value
        assert all(result is not None for result in ordered)
        return [result for result in ordered if result is not None]

    def _resolve(
        self,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> tuple[RegisteredTool | None, ToolExecutionResult | None]:
        registered: RegisteredTool | None = None
        lookup = getattr(context.tool_view, "lookup", None)
        if callable(lookup):
            lookup_result = lookup(context.internal_tool_id)
            if getattr(lookup_result, "status", None) is ToolLookupStatus.UNKNOWN_TOOL:
                return None, _result(ToolExecutionStatus.INVALID, "unknown_tool", "tool is unknown")
            if getattr(lookup_result, "status", None) is ToolLookupStatus.TOOL_DISABLED:
                return None, _result(
                    ToolExecutionStatus.DENIED,
                    "tool_disabled",
                    "tool is disabled",
                )
            if getattr(lookup_result, "status", None) is ToolLookupStatus.ENABLED:
                registered = getattr(lookup_result, "tool", None)
        elif not context.tool_view.is_enabled(context.internal_tool_id):
            return None, _result(ToolExecutionStatus.DENIED, "tool_disabled", "tool is disabled")
        else:
            registered = self.catalog.get(context.internal_tool_id)
        if registered is None:
            return None, _result(ToolExecutionStatus.INVALID, "unknown_tool", "tool is unknown")
        if tool_call.name not in {
            registered.definition.model_alias,
            registered.definition.internal_id,
        }:
            return None, _result(
                ToolExecutionStatus.INVALID,
                "tool_name_mismatch",
                "tool name does not match view",
            )
        return registered, None

    def _execution_conflict_key(
        self,
        context: ToolExecutionContext,
        index: int,
    ) -> tuple[str, str | int]:
        registered, result = self._resolve(
            ToolCall(
                id=context.tool_call_id,
                name=context.internal_tool_id,
                arguments={},
            ),
            context,
        )
        if result is not None or registered is None:
            return ("parallel", index)
        definition = registered.definition
        if definition.concurrency_policy.value == "resource_key":
            return ("resource", definition.resource_key or definition.internal_id)
        if definition.concurrency_policy.value == "serialized":
            return ("serialized", definition.internal_id)
        return ("parallel", index)

    async def _terminal_result(
        self,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult | None:
        policy = self.terminal_policy
        if policy is None:
            return None
        method = (
            "before_execute"
            if callable(getattr(policy, "before_execute", None))
            else "check"
        )
        value = await _call(policy, method, tool_call, context, default=None)
        if value is None:
            return None
        if not isinstance(value, ToolExecutionResult):
            raise TypeError("terminal policy must return ToolExecutionResult or None")
        return value

    def _check_cancel_deadline(self, context: ToolExecutionContext) -> ToolExecutionResult | None:
        if context.cancellation is not None and context.cancellation.cancelled:
            return _result(
                ToolExecutionStatus.CANCELLED,
                "cancelled",
                "tool execution was cancelled",
            )
        if context.deadline is not None:
            remaining = context.deadline.remaining_s()
            if remaining is not None and remaining <= 0:
                return _result(
                    ToolExecutionStatus.CANCELLED,
                    "deadline_exceeded",
                    "tool execution deadline expired",
                )
        return None

    async def _execute_once(
        self,
        registered: RegisteredTool,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        definition = registered.definition
        resource_key = _resource_key(definition)
        acquired = False

        async def execute_with_lease() -> ToolExecutionResult:
            nonlocal acquired
            async with _lease(self.resource_manager, resource_key, context):
                acquired = True
                return await self._invoke_executor(registered, tool_call, context)

        try:
            remaining = _remaining_s(context)
            if remaining is None:
                return await execute_with_lease()
            if remaining <= 0:
                return _result(
                    ToolExecutionStatus.CANCELLED,
                    "deadline_exceeded",
                    "tool execution deadline expired before resource acquisition",
                )
            return await asyncio.wait_for(execute_with_lease(), timeout=remaining)
        except TimeoutError:
            if acquired:
                return _timeout_result(definition)
            return _result(
                ToolExecutionStatus.CANCELLED,
                "deadline_exceeded",
                "tool execution deadline expired while waiting for a resource",
            )
        except asyncio.CancelledError:
            if acquired:
                return _cancelled_after_attempt_result(definition)
            raise
        except Exception as exc:
            return _result(
                ToolExecutionStatus.FAILURE,
                "resource_acquire_failed",
                f"{type(exc).__name__}: {exc}",
            )

    async def _invoke_executor(
        self,
        registered: RegisteredTool,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        definition = registered.definition
        try:
            awaitable = registered.executor.execute(tool_call.arguments, context)
            if context.deadline is not None:
                remaining = context.deadline.remaining_s()
                if remaining is not None:
                    value = await asyncio.wait_for(awaitable, timeout=max(0.0, remaining))
                else:
                    value = await awaitable
            else:
                value = await awaitable
            if not isinstance(value, ToolExecutionResult):
                raise TypeError("canonical executor must return ToolExecutionResult")
            return value
        except TimeoutError:
            return _timeout_result(definition)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if _is_mutating(definition):
                return ToolExecutionResult(
                    status=ToolExecutionStatus.OUTCOME_UNKNOWN,
                    error=ToolExecutionError("executor_exception", f"{type(exc).__name__}: {exc}"),
                    outcome_certainty=OutcomeCertainty.UNKNOWN,
                    backend_attempted=True,
                )
            return _result(
                ToolExecutionStatus.FAILURE,
                "executor_exception",
                f"{type(exc).__name__}: {exc}",
                backend_attempted=True,
            )

    async def _verify(
        self,
        registered: RegisteredTool,
        result: ToolExecutionResult,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        assert registered.verifier is not None
        try:
            awaitable = registered.verifier.verify(result, context)
            remaining = _remaining_s(context)
            if remaining is None:
                verification = await awaitable
            elif remaining <= 0:
                raise TimeoutError
            else:
                verification = await asyncio.wait_for(awaitable, timeout=remaining)
        except TimeoutError:
            return replace(
                result,
                status=ToolExecutionStatus.VERIFICATION_PENDING,
                error=ToolExecutionError(
                    "deadline_exceeded",
                    "verification did not finish before the deadline",
                ),
                verification=VerificationRecord(
                    status=VerificationStatus.PENDING,
                    detail="verification deadline expired",
                ),
                retryable=False,
            )
        except asyncio.CancelledError:
            if context.cancellation is not None and context.cancellation.cancelled:
                return replace(
                    result,
                    status=ToolExecutionStatus.VERIFICATION_PENDING,
                    error=ToolExecutionError(
                        "cancelled",
                        "verification was cancelled before certainty was established",
                    ),
                    verification=VerificationRecord(
                        status=VerificationStatus.PENDING,
                        detail="verification cancelled",
                    ),
                    retryable=False,
                )
            raise
        except Exception as exc:
            return _result(
                ToolExecutionStatus.FAILURE,
                "verification_exception",
                f"{type(exc).__name__}: {exc}",
                backend_attempted=result.backend_attempted,
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

    async def _record_and_publish(
        self,
        tool_call: ToolCall,
        result: ToolExecutionResult,
        context: ToolExecutionContext,
        attempt_index: int,
        permission_decision: PermissionDecision | None = None,
    ) -> None:
        if permission_decision is not None:
            await _call(
                self.authoritative_ledger,
                "record_permission",
                tool_call,
                permission_decision,
                context,
                attempt_index,
            )
        await _call(
            self.authoritative_ledger,
            "record_execution",
            tool_call,
            result,
            context,
            attempt_index,
        )
        await _call(
            self.public_event_sink,
            "publish",
            tool_call,
            result,
            context,
            attempt_index,
        )


def _result(
    status: ToolExecutionStatus,
    code: str,
    message: str,
    *,
    backend_attempted: bool = False,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        status=status,
        error=ToolExecutionError(code, message),
        backend_attempted=backend_attempted,
    )


def _timeout_result(definition: ToolDefinition) -> ToolExecutionResult:
    if _is_mutating(definition):
        return ToolExecutionResult(
            status=ToolExecutionStatus.OUTCOME_UNKNOWN,
            error=ToolExecutionError(
                "deadline_exceeded",
                "backend outcome is unknown after timeout",
            ),
            outcome_certainty=OutcomeCertainty.UNKNOWN,
            backend_attempted=True,
        )
    return _result(
        ToolExecutionStatus.FAILURE,
        "deadline_exceeded",
        "tool execution timed out",
        backend_attempted=True,
    )


def _cancelled_after_attempt_result(definition: ToolDefinition) -> ToolExecutionResult:
    if _is_mutating(definition):
        return ToolExecutionResult(
            status=ToolExecutionStatus.OUTCOME_UNKNOWN,
            error=ToolExecutionError(
                "cancelled",
                "backend outcome is unknown after cancellation",
            ),
            outcome_certainty=OutcomeCertainty.UNKNOWN,
            backend_attempted=True,
        )
    return _result(
        ToolExecutionStatus.CANCELLED,
        "cancelled",
        "tool execution was cancelled",
        backend_attempted=True,
    )


def _is_mutating(definition: ToolDefinition) -> bool:
    return any(effect not in {"none", "read", "read_only"} for effect in definition.state_effects)


def _resource_key(definition: ToolDefinition) -> str | None:
    if definition.concurrency_policy.value == "resource_key":
        return definition.resource_key
    if definition.concurrency_policy.value == "serialized":
        return f"tool:{definition.internal_id}"
    return None


def _remaining_s(context: ToolExecutionContext) -> float | None:
    if context.deadline is None:
        return None
    return context.deadline.remaining_s()


def _verifier_applies(definition: ToolDefinition, result: ToolExecutionResult) -> bool:
    return result.status in {
        ToolExecutionStatus.SUCCESS,
        ToolExecutionStatus.FAILURE,
    } and definition.verification_policy.execution_proof.value != "none"


def _observation_block(value: object) -> ToolExecutionResult | None:
    if value is None or value is True:
        return None
    if isinstance(value, ToolExecutionResult):
        return value
    if value is False:
        return _result(
            ToolExecutionStatus.OBSERVATION_REQUIRED,
            "observation_required",
            "a fresh bound observation is required before this action",
        )
    raise TypeError("observation before_action must return bool, result, or None")


async def _maybe_await(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


async def _call(target: Any, method: str, *args: object, default: object = None) -> object:
    function = getattr(target, method, None)
    if function is None and callable(target):
        function = target
    if function is None:
        return default
    return await _maybe_await(function(*args))


@asynccontextmanager
async def _lease(manager: ResourceManager, key: str | None, context: ToolExecutionContext):
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


def _schema_formats(schema: Mapping[str, object]) -> set[str]:
    found: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            format_name = value.get("format")
            if isinstance(format_name, str):
                found.add(format_name)
            for item in value.values():
                visit(item)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                visit(item)

    visit(schema)
    return found


__all__ = [
    "AllowAllPermissionPolicy",
    "NoopAuthoritativeLedger",
    "NoopObservationService",
    "NoopPublicEventSink",
    "NoopResourceManager",
    "PermissionDecision",
    "PermissionPolicy",
    "RetryPolicy",
    "SchemaValidator",
    "ToolExecutionPipeline",
]
