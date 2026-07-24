"""Trusted Feishu group operations and model-callable typed tools."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from homemaster.channels.contracts import ChannelIdentity
from homemaster.channels.impl.feishu import FeishuApiService
from homemaster.tools.contracts import (
    ConcurrencyPolicy,
    ExecutionProof,
    OutcomeCertainty,
    RegisteredTool,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
    VerificationRecord,
    VerificationStatus,
)


class GroupOutcomeCertainty(StrEnum):
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FeishuGroupApiResult:
    api_success: bool
    api_code: int | str | None
    api_message: str
    chat_id: str | None = None


@dataclass(frozen=True)
class FeishuChatState:
    chat_id: str
    name: str
    member_open_ids: tuple[str, ...]


@dataclass(frozen=True)
class FeishuGroupReceipt:
    operation_id: str
    action: str
    chat_id: str | None
    requested_name: str
    api_success: bool
    api_code: int | str | None
    api_message: str
    outcome_certainty: GroupOutcomeCertainty
    verified_state: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "action": self.action,
            "chat_id": self.chat_id,
            "requested_name": self.requested_name,
            "api_success": self.api_success,
            "api_code": self.api_code,
            "api_message": self.api_message,
            "outcome_certainty": self.outcome_certainty.value,
            "verified_state": self.verified_state,
        }


@dataclass(frozen=True)
class _RouteBinding:
    identity: ChannelIdentity
    generation: int


class FeishuGroupOperations:
    def __init__(self, api_service: FeishuApiService) -> None:
        self.api_service = api_service
        self._bindings: dict[str, _RouteBinding] = {}
        self._ledger: dict[str, FeishuGroupReceipt] = {}
        self._targets: dict[str, tuple[str, str, str]] = {}
        self._lock = asyncio.Lock()

    def bind(self, session_id: str, identity: ChannelIdentity, *, generation: int) -> None:
        if identity.channel != "feishu":
            raise ValueError("Feishu group binding requires a Feishu identity")
        current = self._bindings.get(session_id)
        if current is not None and generation < current.generation:
            raise ValueError("stale Feishu group binding generation")
        self._bindings[session_id] = _RouteBinding(identity, generation)

    def clear(self, session_id: str, *, generation: int) -> None:
        current = self._bindings.get(session_id)
        if current is not None and current.generation <= generation:
            self._bindings.pop(session_id, None)

    async def create(self, *, session_id: str, operation_id: str, name: str) -> FeishuGroupReceipt:
        binding = self._binding(session_id)
        requested_name = _normalize_group_name(name)
        target = ("create", binding.identity.sender_id, requested_name)
        async with self._lock:
            cached = self._cached(operation_id, target)
            if cached is not None:
                return cached
            self._targets[operation_id] = target
            try:
                api = await self.api_service.create_group(
                    member_open_id=binding.identity.sender_id,
                    name=requested_name,
                    operation_id=operation_id,
                )
            except TimeoutError:
                return self._remember(
                    FeishuGroupReceipt(
                        operation_id,
                        "create",
                        None,
                        requested_name,
                        False,
                        None,
                        "TimeoutError",
                        GroupOutcomeCertainty.UNKNOWN,
                        False,
                    )
                )
            verified = False
            if api.api_success and api.chat_id:
                state = await self.api_service.get_chat(api.chat_id)
                verified = (
                    state.name == requested_name
                    and binding.identity.sender_id in state.member_open_ids
                )
            return self._remember(
                FeishuGroupReceipt(
                    operation_id,
                    "create",
                    api.chat_id,
                    requested_name,
                    api.api_success,
                    api.api_code,
                    api.api_message,
                    GroupOutcomeCertainty.CONFIRMED,
                    verified,
                )
            )

    async def rename(self, *, session_id: str, operation_id: str, name: str) -> FeishuGroupReceipt:
        binding = self._binding(session_id)
        if binding.identity.chat_type != "group":
            raise ValueError("Feishu rename requires the current authenticated group route")
        requested_name = _normalize_group_name(name)
        chat_id = binding.identity.chat_id
        target = ("rename", chat_id, requested_name)
        async with self._lock:
            cached = self._cached(operation_id, target)
            if cached is not None:
                return cached
            self._targets[operation_id] = target
            try:
                api = await self.api_service.rename_group(chat_id=chat_id, name=requested_name)
            except TimeoutError:
                return self._remember(
                    FeishuGroupReceipt(
                        operation_id,
                        "rename",
                        chat_id,
                        requested_name,
                        False,
                        None,
                        "TimeoutError",
                        GroupOutcomeCertainty.UNKNOWN,
                        False,
                    )
                )
            verified = False
            if api.api_success:
                state = await self.api_service.get_chat(chat_id)
                verified = state.name == requested_name
            return self._remember(
                FeishuGroupReceipt(
                    operation_id,
                    "rename",
                    chat_id,
                    requested_name,
                    api.api_success,
                    api.api_code,
                    api.api_message,
                    GroupOutcomeCertainty.CONFIRMED,
                    verified,
                )
            )

    def _binding(self, session_id: str) -> _RouteBinding:
        try:
            return self._bindings[session_id]
        except KeyError as exc:
            raise ValueError("Feishu session has no authenticated route binding") from exc

    def _cached(self, operation_id: str, target: tuple[str, str, str]) -> FeishuGroupReceipt | None:
        existing_target = self._targets.get(operation_id)
        if existing_target is not None and existing_target != target:
            raise ValueError("Feishu operation id is already locked to another target")
        return self._ledger.get(operation_id)

    def _remember(self, receipt: FeishuGroupReceipt) -> FeishuGroupReceipt:
        self._ledger[receipt.operation_id] = receipt
        return receipt


class _GroupExecutor:
    def __init__(self, operations: FeishuGroupOperations, action: str) -> None:
        self.operations = operations
        self.action = action

    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        try:
            method = self.operations.create if self.action == "create" else self.operations.rename
            receipt = await method(
                session_id=context.session_id,
                operation_id=context.tool_call_id,
                name=str(arguments.get("name") or ""),
            )
        except ValueError as exc:
            return ToolExecutionResult(
                status=ToolExecutionStatus.INVALID,
                error=ToolExecutionError("invalid_group_operation", str(exc)),
            )
        if receipt.outcome_certainty is GroupOutcomeCertainty.UNKNOWN:
            return ToolExecutionResult(
                status=ToolExecutionStatus.OUTCOME_UNKNOWN,
                data=receipt.to_dict(),
                error=ToolExecutionError("group_outcome_unknown", receipt.api_message),
                outcome_certainty=OutcomeCertainty.UNKNOWN,
                backend_attempted=True,
            )
        if not receipt.api_success or not receipt.verified_state:
            return ToolExecutionResult(
                status=ToolExecutionStatus.FAILURE,
                data=receipt.to_dict(),
                error=ToolExecutionError("group_operation_failed", receipt.api_message),
                backend_attempted=True,
            )
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text=f"Feishu group {self.action} confirmed",
            data=receipt.to_dict(),
            backend_attempted=True,
        )


class _GroupVerifier:
    async def verify(
        self, result: ToolExecutionResult, context: ToolExecutionContext
    ) -> VerificationRecord:
        del context
        verified = result.data.get("verified_state") is True
        chat_id = str(result.data.get("chat_id") or "unknown")
        action = str(result.data.get("action") or "group")
        return VerificationRecord(
            status=VerificationStatus.PASSED if verified else VerificationStatus.FAILED,
            detail="independent Feishu chat read matched" if verified else "chat state mismatch",
            evidence_refs=(f"feishu/chat/{chat_id}/{action}",),
        )


def build_feishu_group_tools(
    operations: FeishuGroupOperations,
) -> tuple[RegisteredTool, RegisteredTool]:
    return tuple(_group_tool(operations, action) for action in ("create", "rename"))  # type: ignore[return-value]


def _group_tool(operations: FeishuGroupOperations, action: str) -> RegisteredTool:
    capability = f"channel.feishu.group.{action}"
    definition = ToolDefinition(
        internal_id=f"homemaster.feishu_group_{action}.v1",
        model_alias=f"feishu_group_{action}",
        description=f"{action.title()} the authenticated Feishu group route.",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 100}},
            "required": ["name"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        verification_policy=VerificationPolicy(execution_proof=ExecutionProof.EXTERNAL_STATE),
        provenance=ToolProvenance("homemaster", "feishu-single-channel-migration"),
        version="1.0.0",
        concurrency_policy=ConcurrencyPolicy.SERIALIZED,
        state_effects=(f"channel.group.{action}",),
        required_capabilities=(capability,),
    )
    return RegisteredTool(
        definition=definition,
        executor=_GroupExecutor(operations, action),
        verifier=_GroupVerifier(),
    )


def _normalize_group_name(value: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value)).strip()
    if not normalized or len(normalized) > 100:
        raise ValueError("Feishu group name must contain 1-100 normalized characters")
    return normalized


__all__ = [
    "FeishuChatState",
    "FeishuGroupApiResult",
    "FeishuGroupOperations",
    "FeishuGroupReceipt",
    "GroupOutcomeCertainty",
    "build_feishu_group_tools",
]
