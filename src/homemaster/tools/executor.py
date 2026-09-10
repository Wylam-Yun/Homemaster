"""Single ordinary-name execution path for the universal ToolRegistry."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from homemaster.agent.messages import ToolCall
from homemaster.permissions.models import (
    ApprovalCancelled,
    ApprovalConflict,
    ApprovalExpired,
    ApprovalResolution,
    ApprovalSubmission,
    ExecutionObservation,
    ItemDecision,
    PermissionStorageUnavailable,
    TargetChanged,
    TargetUnresolved,
)
from homemaster.permissions.store import MAX_ATTEMPTS_DEFAULT, PermissionStore
from homemaster.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult

log = logging.getLogger(__name__)


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

    def evaluate_physical(
        self,
        *,
        request: Any,
        context: ToolExecutionContext,
    ) -> Any:
        """Judge one prepared physical call.

        The concrete verdict type is
        ``homemaster.permissions.policy.PhysicalDecision``; ``Any`` here
        only avoids a runtime import cycle between the tool and permission
        layers.
        """
        ...


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

    def evaluate_physical(
        self,
        *,
        request: Any,
        context: ToolExecutionContext,
    ) -> Any:
        del request, context
        raise RuntimeError(
            "physical calls need a real household PermissionChecker with a"
            " PermissionStore; refusing to allow"
        )


class ResourceManager(Protocol):
    def acquire(self, resource_key: str, context: ToolExecutionContext) -> Any: ...


class NoopResourceManager:
    @asynccontextmanager
    async def acquire(self, resource_key: str, context: ToolExecutionContext):
        del resource_key, context
        yield


class _GrantRevoked(Exception):
    """A covering grant disappeared after approval but before step start."""


class PhysicalGateOwner:
    """Application-owned serialization for physical step starts.

    The lock is held only across the synchronous grant-recheck-and-claim
    window; it is never held while a device moves. The SQLite claim and
    revoke transactions remain the linearization point.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._in_flight: set[tuple[str, str]] = set()

    @property
    def in_flight(self) -> frozenset[tuple[str, str]]:
        return frozenset(self._in_flight)

    @asynccontextmanager
    async def linearize(self, request_id: str, step_id: str):
        await self._lock.acquire()
        self._in_flight.add((request_id, step_id))
        try:
            yield
        except BaseException:
            self._in_flight.discard((request_id, step_id))
            raise
        finally:
            self._lock.release()

    def complete(self, request_id: str, step_id: str) -> None:
        self._in_flight.discard((request_id, step_id))


def _physical_result(
    message: str, status: str, *, backend_attempted: bool = False
) -> ToolResult:
    return ToolResult(
        message,
        True,
        {
            "status": status,
            "error_code": status,
            "backend_attempted": backend_attempted,
        },
    )


def _subject_actor(context: ToolExecutionContext) -> str:
    subject = context.metadata.get("permission_subject")
    return str(getattr(subject, "subject_id", "") or "")


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        permission_checker: PermissionChecker | None = None,
        confirmation_handler: Any | None = None,
        resource_manager: ResourceManager | None = None,
        permission_store: PermissionStore | None = None,
        physical_owner: PhysicalGateOwner | None = None,
    ) -> None:
        self.registry = registry
        self.permission_checker = permission_checker or AllowAllPermissionChecker()
        self.confirmation_handler = confirmation_handler
        self.resource_manager = resource_manager or NoopResourceManager()
        self.permission_store = permission_store
        self.physical_owner = physical_owner or PhysicalGateOwner()

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
        normalized_arguments = arguments.model_dump(mode="json")
        is_read_only = tool.is_read_only(arguments)
        decision = self.permission_checker.evaluate_tool(
            tool_name=tool.name,
            is_read_only=is_read_only,
            required_capabilities=tool.required_capabilities,
            arguments=normalized_arguments,
            context=context,
        )
        if decision.requires_confirmation and not _is_physical(tool):
            approved = False
            confirm = getattr(self.confirmation_handler, "confirm", None)
            if callable(confirm):
                value = confirm(tool, normalized_arguments, context, decision)
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
        if _is_physical(tool):
            return await self._execute_physical(tool, normalized_arguments, context)
        resource_key, resource_error = self._resource_key(tool, normalized_arguments, context)
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

    async def _execute_physical(
        self,
        tool: BaseTool,
        normalized_arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Run one prepared physical call through the permission gate.

        Single path: prepare once, persist, judge, confirm the missing
        items, revalidate the locked bindings read-only, then claim and
        run each inner step. The adapter is the only execution route;
        the tool body never runs for physical tools.
        """
        adapter = getattr(tool, "physical_adapter", None)
        store = self.permission_store
        if adapter is None or store is None:
            return _physical_result(
                "physical tool is not wired to a device adapter and store",
                "permission_configuration_error",
            )
        try:
            prepared = await adapter.prepare(normalized_arguments, context)
        except TargetUnresolved as exc:
            return _physical_result(
                str(exc) or "target could not be resolved", "target_unresolved"
            )
        except Exception as exc:
            return ToolResult(
                f"{type(exc).__name__}: {exc}",
                True,
                {
                    "status": "tool_error",
                    "exception_type": type(exc).__name__,
                    "backend_attempted": False,
                },
            )
        if not prepared.requirements:
            return await self._execute_direct(
                tool, adapter, prepared, normalized_arguments, context
            )
        try:
            store.create_request(prepared)
        except ApprovalConflict as exc:
            return _physical_result(f"approval conflict: {exc}", "approval_conflict")
        except PermissionStorageUnavailable as exc:
            return _physical_result(
                f"permission storage unavailable: {exc}",
                "permission_storage_unavailable",
            )
        try:
            verdict = self.permission_checker.evaluate_physical(
                request=prepared, context=context
            )
        except RuntimeError as exc:
            return _physical_result(str(exc), "permission_configuration_error")
        if verdict.allowed and not verdict.missing_item_ids:
            choices: dict[str, str] | ApprovalResolution = {
                item.item_id: "allow_once" for item in prepared.requirements
            }
        elif verdict.missing_item_ids:
            try:
                store.mark_awaiting_approval(prepared.request_id)
            except ApprovalConflict as exc:
                return _physical_result(
                    f"approval conflict: {exc}", "approval_conflict"
                )
            try:
                approved = await self._confirm_physical(
                    prepared, verdict.missing_item_ids, context
                )
            except ApprovalCancelled:
                return _physical_result(
                    "the approval was cancelled before a decision",
                    "permission_denied",
                )
            if approved is None:
                store.cancel(prepared.request_id, "no approval channel available")
                return _physical_result(
                    "no approval channel can present this request",
                    "approval_channel_unavailable",
                )
            if isinstance(approved, ApprovalResolution):
                choices = approved
            elif approved:
                choices = {
                    item.item_id: "allow_once" for item in prepared.requirements
                }
            else:
                choices = {
                    item.item_id: (
                        "reject"
                        if item.item_id in verdict.missing_item_ids
                        else "allow_once"
                    )
                    for item in prepared.requirements
                }
        else:
            return _physical_result(
                verdict.reason or "household approval was not granted",
                "permission_denied",
            )
        try:
            rechecked = await adapter.prepare(normalized_arguments, context)
        except TargetUnresolved as exc:
            store.cancel(prepared.request_id, "target changed during approval")
            await self._release_adapter(adapter, prepared.request_id)
            return _physical_result(
                str(exc) or "target changed during approval", "target_changed"
            )
        except Exception as exc:
            store.cancel(prepared.request_id, "target changed during approval")
            await self._release_adapter(adapter, prepared.request_id)
            return ToolResult(
                f"{type(exc).__name__}: {exc}",
                True,
                {
                    "status": "tool_error",
                    "exception_type": type(exc).__name__,
                    "backend_attempted": False,
                },
            )
        if _binding_signature(rechecked) != _binding_signature(prepared):
            store.cancel(prepared.request_id, "target changed during approval")
            await self._release_adapter(adapter, prepared.request_id)
            return _physical_result(
                "target changed during approval", "target_changed"
            )
        # The credential opens only after the bindings revalidate, against a
        # fresh grant snapshot, so a target move or a revocation during the
        # approval wait cannot slip into execution.
        covered = store.matching_grants([item.key for item in prepared.requirements])
        grant_covered = frozenset(
            item.item_id for item in prepared.requirements if item.key in covered
        )
        if isinstance(choices, ApprovalResolution):
            resolution = choices
        else:
            resolution = self._submit_for_execution(
                store, prepared, choices, context
            )
            if isinstance(resolution, ToolResult):
                return resolution
        if resolution.request_status == "blocked":
            note = getattr(self.permission_checker, "note_rejected", None)
            if callable(note):
                note(request=prepared, context=context)
            return _physical_result(
                "household approval was not granted; this call did not run",
                "permission_denied",
            )
        keys_by_item = {item.item_id: item.key for item in prepared.requirements}
        resource_key, resource_error = self._resource_key(
            tool, normalized_arguments, context
        )
        if resource_error is not None:
            store.cancel(prepared.request_id, "resource key failed")
            await self._release_adapter(adapter, prepared.request_id)
            return resource_error
        owner = self.physical_owner
        last_result: ToolResult | None = None
        try:
            for step in prepared.steps:
                needed = [
                    item_id
                    for item_id in step.required_item_ids
                    if item_id in grant_covered
                ]
                try:
                    last_result, proceed = await self._run_physical_step(
                        adapter,
                        store,
                        prepared,
                        step,
                        needed,
                        keys_by_item,
                        resource_key,
                        context,
                        owner,
                    )
                finally:
                    owner.complete(prepared.request_id, step.step_id)
                if not proceed:
                    assert last_result is not None
                    return last_result
        finally:
            await self._release_adapter(adapter, prepared.request_id)
        assert last_result is not None
        return last_result

    async def _execute_direct(
        self,
        tool: BaseTool,
        adapter: Any,
        prepared: Any,
        normalized_arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Run permission-free steps without touching the store."""
        resource_key, resource_error = self._resource_key(
            tool, normalized_arguments, context
        )
        if resource_error is not None:
            return resource_error
        last: ToolResult | None = None
        for step in prepared.steps:
            backend_started = False

            async def invoke(bound: str = step.binding_ref) -> ToolResult:
                nonlocal backend_started
                async with _lease(self.resource_manager, resource_key, context):
                    backend_started = True
                    return await adapter.execute(bound, context)

            remaining = _remaining_s(context)
            if remaining is not None and remaining <= 0:
                return _deadline_result(
                    is_read_only=False, backend_attempted=False
                )
            try:
                if remaining is None:
                    last = await invoke()
                else:
                    last = await asyncio.wait_for(invoke(), timeout=remaining)
            except TimeoutError:
                return _deadline_result(
                    is_read_only=False, backend_attempted=backend_started
                )
            except asyncio.CancelledError:
                if backend_started:
                    return ToolResult(
                        "tool cancellation occurred after a mutation may have started",
                        True,
                        {
                            "status": "outcome_unknown",
                            "error_code": "execution_cancelled",
                            "backend_attempted": True,
                        },
                    )
                raise
            except Exception as exc:
                manager_error = _resource_manager_error(exc)
                if manager_error is not None:
                    return manager_error
                if backend_started:
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
                        "backend_attempted": False,
                    },
                )
            if last.is_error:
                return last
        if last is None:
            return ToolResult(
                "no physical effects required",
                False,
                {"status": "no_effect", "backend_attempted": False},
            )
        return last

    async def _confirm_physical(
        self,
        prepared: Any,
        missing_item_ids: Any,
        context: ToolExecutionContext,
    ) -> bool | ApprovalResolution | None:
        """Ask the approval channel; None means no channel can present."""
        confirm = getattr(self.confirmation_handler, "confirm", None)
        if not callable(confirm):
            return None
        value = confirm(prepared, missing_item_ids, context)
        if inspect.isawaitable(value):
            value = await value
        if isinstance(value, ApprovalResolution):
            return value
        return bool(value)

    def _submit_for_execution(
        self,
        store: PermissionStore,
        prepared: Any,
        choices: dict[str, str],
        context: ToolExecutionContext,
    ) -> ApprovalResolution | ToolResult:
        try:
            decisions = tuple(
                ItemDecision(item_id=item_id, choice=choice)  # type: ignore[arg-type]
                for item_id, choice in choices.items()
            )
            submission = ApprovalSubmission(
                protocol_version=2,
                submission_id=f"sub-{uuid.uuid4().hex[:16]}",
                request_revision=prepared.revision,
                decisions=decisions,
            )
        except ValueError as exc:
            return ToolResult(
                f"invalid approval submission: {exc}",
                True,
                {
                    "status": "tool_error",
                    "exception_type": type(exc).__name__,
                    "backend_attempted": False,
                },
            )
        try:
            return store.submit(
                prepared.approval_id, submission, _subject_actor(context)
            )
        except ApprovalExpired as exc:
            return _physical_result(f"approval expired: {exc}", "approval_expired")
        except ApprovalConflict as exc:
            return _physical_result(
                f"approval conflict: {exc}", "approval_conflict"
            )
        except ValueError as exc:
            return ToolResult(
                f"invalid approval submission: {exc}",
                True,
                {
                    "status": "tool_error",
                    "exception_type": type(exc).__name__,
                    "backend_attempted": False,
                },
            )
        except PermissionStorageUnavailable as exc:
            return _physical_result(
                f"permission storage unavailable: {exc}",
                "permission_storage_unavailable",
            )

    async def _run_physical_step(
        self,
        adapter: Any,
        store: PermissionStore,
        prepared: Any,
        step: Any,
        needed: list[str],
        keys_by_item: dict[str, Any],
        resource_key: str | None,
        context: ToolExecutionContext,
        owner: PhysicalGateOwner,
    ) -> tuple[ToolResult, bool]:
        """Run one step across its attempts: claim, execute, observe, finish."""
        while True:
            try:
                async with owner.linearize(prepared.request_id, step.step_id):
                    if needed:
                        live = store.matching_grants(
                            [keys_by_item[item_id] for item_id in needed]
                        )
                        if any(
                            keys_by_item[item_id] not in live for item_id in needed
                        ):
                            raise _GrantRevoked()
                    binding = store.claim_step(
                        prepared.request_id,
                        step.step_id,
                        prepared.revision,
                        MAX_ATTEMPTS_DEFAULT,
                    )
            except _GrantRevoked:
                store.cancel(prepared.request_id, "grant revoked before step started")
                return _physical_result(
                    "a grant was revoked before the step started",
                    "permission_denied",
                ), False
            except ApprovalConflict as exc:
                return _physical_result(
                    f"approval conflict: {exc}", "approval_conflict"
                ), False
            backend_started = False

            async def invoke(bound: str = binding) -> ToolResult:
                nonlocal backend_started
                async with _lease(self.resource_manager, resource_key, context):
                    backend_started = True
                    return await adapter.execute(bound, context)

            remaining = _remaining_s(context)
            if remaining is not None and remaining <= 0:
                store.cancel(prepared.request_id, "deadline before step started")
                return _deadline_result(
                    is_read_only=False, backend_attempted=False
                ), False
            try:
                if remaining is None:
                    exec_result = await invoke()
                else:
                    exec_result = await asyncio.wait_for(invoke(), timeout=remaining)
            except TimeoutError:
                if not backend_started:
                    store.cancel(prepared.request_id, "deadline before backend started")
                    return _deadline_result(
                        is_read_only=False, backend_attempted=False
                    ), False
                observation = await self._observe_best_effort(adapter, binding, context)
                self._finish_best_effort(store, prepared, step, observation)
                if observation.outcome == "succeeded":
                    return ToolResult(
                        "step completed (verified after timeout)",
                        False,
                        {"status": "success", "backend_attempted": True},
                    ), False
                return ToolResult(
                    "backend outcome is unknown after timeout",
                    True,
                    {
                        "status": "outcome_unknown",
                        "error_code": "timeout",
                        "backend_attempted": True,
                    },
                ), False
            except asyncio.CancelledError:
                if backend_started:
                    self._finish_best_effort(
                        store,
                        prepared,
                        step,
                        ExecutionObservation(
                            outcome="unknown",
                            backend_code="execution_cancelled",
                            binding_ref=binding,
                            observed_resource_id=None,
                            current_area_id=None,
                            evidence_ref="unobserved",
                        ),
                    )
                    return ToolResult(
                        "tool cancellation occurred after a mutation may have started",
                        True,
                        {
                            "status": "outcome_unknown",
                            "error_code": "execution_cancelled",
                            "backend_attempted": True,
                        },
                    ), False
                raise
            except (TargetChanged, TargetUnresolved) as exc:
                store.cancel(prepared.request_id, "target changed during execution")
                status = (
                    "target_changed"
                    if isinstance(exc, TargetChanged)
                    else "target_unresolved"
                )
                return _physical_result(str(exc) or status, status), False
            except Exception as exc:
                manager_error = _resource_manager_error(exc)
                if manager_error is not None:
                    return manager_error, False
                if backend_started:
                    observation = await self._observe_best_effort(
                        adapter, binding, context
                    )
                    self._finish_best_effort(store, prepared, step, observation)
                    return ToolResult(
                        f"backend outcome is unknown after {type(exc).__name__}: {exc}",
                        True,
                        {
                            "status": "outcome_unknown",
                            "error_code": "execution_exception",
                            "exception_type": type(exc).__name__,
                            "backend_attempted": True,
                        },
                    ), False
                return ToolResult(
                    f"{type(exc).__name__}: {exc}",
                    True,
                    {
                        "status": "tool_error",
                        "exception_type": type(exc).__name__,
                        "backend_attempted": False,
                    },
                ), False
            observation = await self._observe_best_effort(adapter, binding, context)
            try:
                request_status = store.finish_step(
                    prepared.request_id,
                    step.step_id,
                    observation,
                    MAX_ATTEMPTS_DEFAULT,
                )
            except (ApprovalConflict, TargetChanged) as exc:
                return _physical_result(
                    f"approval conflict: {exc}", "approval_conflict"
                ), False
            if observation.outcome == "no_effect_failure" and request_status == "running":
                continue
            if observation.outcome == "unknown":
                return ToolResult(
                    "backend outcome is unknown",
                    True,
                    {
                        "status": "outcome_unknown",
                        "error_code": "execution_unknown",
                        "backend_attempted": True,
                    },
                ), False
            if request_status == "failed":
                return ToolResult(
                    "step exhausted its attempt budget",
                    True,
                    {
                        "status": "execution_failed",
                        "error_code": "attempt_budget_exhausted",
                        "backend_attempted": True,
                    },
                ), False
            return exec_result, True

    async def _observe_best_effort(
        self, adapter: Any, binding: str, context: ToolExecutionContext
    ) -> ExecutionObservation:
        try:
            return await adapter.observe(binding, context)
        except Exception as exc:
            return ExecutionObservation(
                outcome="unknown",
                backend_code=f"{type(exc).__name__}: {exc}",
                binding_ref=binding,
                observed_resource_id=None,
                current_area_id=None,
                evidence_ref="unobserved",
            )

    def _finish_best_effort(
        self,
        store: PermissionStore,
        prepared: Any,
        step: Any,
        observation: ExecutionObservation,
    ) -> str | None:
        try:
            return store.finish_step(
                prepared.request_id,
                step.step_id,
                observation,
                MAX_ATTEMPTS_DEFAULT,
            )
        except (ApprovalConflict, TargetChanged, PermissionStorageUnavailable) as exc:
            log.warning("permission finish_step failed: %s", exc)
            return None

    async def _release_adapter(self, adapter: Any, request_id: str) -> None:
        try:
            await adapter.release(request_id)
        except Exception as exc:
            log.warning("permission adapter release failed: %s", exc)

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


def _is_physical(tool: BaseTool) -> bool:
    return getattr(tool, "physical_adapter", None) is not None or bool(
        getattr(tool, "physical", False)
    )


def _binding_signature(prepared: Any) -> tuple[tuple, tuple[str, ...]]:
    keys = tuple(
        sorted(
            (
                item.key.environment_id,
                item.key.resource_kind,
                item.key.resource_id,
                item.key.action,
            )
            for item in prepared.requirements
        )
    )
    bindings = tuple(sorted(step.binding_ref for step in prepared.steps))
    return keys, bindings


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
    "PhysicalGateOwner",
    "ResourceManager",
    "ToolExecutor",
]
