"""Tool adapter: HomeMaster BaseTool/ToolExecutor -> AgentScope ToolBase.

Per plan/V3.7/decision-physical-chain.md the entire HM physical permission
chain stays inside the tool body; AS sees an ordinary local tool. Per-call
``tool_call_id`` reaches the body through a contextvar set by
``RunScopeMiddleware.on_acting`` (AS does not pass the ToolCallBlock into
``ToolBase.call``).
"""

from __future__ import annotations

import contextvars
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from agentscope.message import (
    Base64Source,
    DataBlock,
    TextBlock,
    ToolResultState,
)
from agentscope.middleware import MiddlewareBase
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import ToolBase, ToolChunk
from homemaster.agent.messages import ToolCall
from homemaster.tools.base import BaseTool

if TYPE_CHECKING:
    from homemaster.agent.messages import ToolResultMessage
from homemaster.tools.contracts import (
    CancellationHandle,
    DeadlineHandle,
    PermissionSubject,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolExecutionStatus,
)

_current_tool_call_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "hm_substrate_tool_call_id", default=""
)


@dataclass
class RunScope:
    """Per-run bridge carrying the fields ``ToolExecutionContext`` needs that
    AgentScope's ``AgentState`` does not hold (deadline, cancellation, subject,
    run identity). One instance per session-run; ``turn_index`` is updated by
    the orchestrator between turns."""

    session_id: str
    run_id: str
    permission_subject: PermissionSubject
    working_directory: Path
    deadline: DeadlineHandle | None = None
    cancellation: CancellationHandle | None = None
    backend: object | None = None
    domain_observer: object | None = None
    services: Mapping[str, object] = field(default_factory=dict)
    turn_index: int = 0


class RunScopeMiddleware(MiddlewareBase):
    """Binds the current ``ToolCallBlock.id`` into a contextvar so the tool
    body can build a correlated ``ToolExecutionContext``. ``on_acting`` wraps
    ``toolkit.call_tool`` and receives ``tool_call`` in ``input_kwargs`` —
    verified against ``agent/_agent.py::_acting``."""

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        tool_call = input_kwargs.get("tool_call")
        call_id = getattr(tool_call, "id", "") if tool_call is not None else ""
        token = _current_tool_call_id.set(call_id)
        try:
            async for event in next_handler():
                yield event
        finally:
            _current_tool_call_id.reset(token)


def current_tool_call_id() -> str:
    return _current_tool_call_id.get()


_STATUS_TO_AS: dict[ToolExecutionStatus, ToolResultState] = {
    ToolExecutionStatus.SUCCESS: ToolResultState.SUCCESS,
    ToolExecutionStatus.FAILURE: ToolResultState.ERROR,
    ToolExecutionStatus.INVALID: ToolResultState.ERROR,
    ToolExecutionStatus.DENIED: ToolResultState.DENIED,
    ToolExecutionStatus.CANCELLED: ToolResultState.INTERRUPTED,
    # outcome_unknown MUST NOT map to INTERRUPTED (implies "did not happen").
    ToolExecutionStatus.OUTCOME_UNKNOWN: ToolResultState.ERROR,
    # The tool ran; verification is outstanding. Text explains it.
    ToolExecutionStatus.VERIFICATION_PENDING: ToolResultState.SUCCESS,
}


def result_to_chunk(result: ToolExecutionResult) -> ToolChunk:
    """Project a canonical ``ToolExecutionResult`` into one terminal chunk.

    Model-visible content = text + images only; every machine field rides in
    ``metadata["hm"]`` so ``backend_attempted``/``outcome_unknown`` survive on
    the serialized ``ToolResultBlock`` (CLAUDE.md machine-field discipline).
    """
    content: list[TextBlock | DataBlock] = []
    if result.text:
        content.append(TextBlock(text=result.text))
    for image in result.images:
        content.append(
            DataBlock(
                source=Base64Source(
                    data=image.data_base64,
                    media_type=image.media_type,
                ),
            )
        )
    if not content:
        content.append(TextBlock(text=""))
    data = dict(result.data)
    existing_status = data.get("status")
    if existing_status is not None and existing_status != result.status.value:
        data.setdefault("domain_status", existing_status)
    data["status"] = result.status.value
    data["backend_attempted"] = result.backend_attempted
    if result.error is not None:
        data.setdefault("error_code", result.error.code)
    return ToolChunk(
        content=content,
        state=_STATUS_TO_AS[result.status],
        is_last=True,
        metadata={
            "hm": {
                "status": result.status.value,
                "data": data,
                "backend_attempted": result.backend_attempted,
                "outcome_certainty": result.outcome_certainty.value,
                "retryable": result.retryable,
                "error": result.error.to_dict() if result.error else None,
                "verification": result.verification.to_dict(),
                "terminal": result.terminal.to_dict() if result.terminal else None,
                "external_return_code": result.external_return_code,
                "evidence_refs": list(result.evidence_refs),
                "attachments": [a.to_dict() for a in result.attachments],
            }
        },
    )


def message_to_chunk(result: ToolResultMessage) -> ToolChunk:
    """Project a canonical ``ToolResultMessage`` (e.g. an observer-synthesized
    terminal message) into one terminal chunk — mirrors ``result_to_chunk``
    for the already-projected message shape."""
    content: list[TextBlock | DataBlock] = []
    for block in result.content:
        if block.type == "text":
            content.append(TextBlock(text=block.text or ""))
        elif block.type == "image" and isinstance(block.source, dict):
            src = block.source
            content.append(
                DataBlock(
                    source=Base64Source(
                        data=src.get("data", ""),
                        media_type=src.get("media_type", "image/png"),
                    ),
                )
            )
    if not content:
        content.append(TextBlock(text=""))
    return ToolChunk(
        content=content,
        state=(
            ToolResultState.ERROR
            if result.is_error
            else ToolResultState.SUCCESS
        ),
        is_last=True,
        metadata={
            "hm": {
                "status": "error" if result.is_error else "success",
                "data": dict(result.data or {}),
                "backend_attempted": (result.data or {}).get(
                    "backend_attempted"
                ),
                "error_code": (result.data or {}).get("error_code"),
            }
        },
    )


class HomeToolAdapter(ToolBase):
    """Wrap one ``BaseTool`` (+ the canonical ``ToolExecutor`` funnel) as an
    AgentScope ``ToolBase``. AS-level permission is delegated back to the HM
    ``PermissionChecker``; the physical chain runs inside ``call`` exactly as
    it does inside ``ToolExecutor.execute`` today."""

    def __init__(
        self,
        tool: BaseTool,
        executor: Any,
        scope: RunScope,
        middlewares: list | None = None,
    ) -> None:
        super().__init__(middlewares=middlewares)
        self._tool = tool
        self._executor = executor
        self._scope = scope
        self.name = tool.name
        self.description = tool.description
        schema = dict(tool.to_api_schema()["input_schema"])
        # AS RegisteredTool requires an explicit ``properties`` mapping;
        # an empty-properties object schema is legal JSON Schema (and what
        # pydantic emits for field-less models) but fails AS validation.
        if schema.get("type") == "object":
            schema.setdefault("properties", {})
        self.input_schema = schema
        # HM serializes anything that is not declared parallel; resource_key
        # degrades to sequential (documented loss in decision ③ §2).
        self.is_concurrency_safe = tool.concurrency_policy == "parallel"
        self.is_read_only = False  # static floor; per-input via check_read_only
        self.is_external_tool = False
        self.metadata_schema = None
        self.is_mcp = False
        self.mcp_name = None

    @property
    def hm_tool(self) -> BaseTool:
        return self._tool

    async def check_read_only(self, tool_input: dict[str, Any]) -> bool:
        arguments = self._validate(tool_input)
        return self._tool.is_read_only(arguments)

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        del context
        arguments = self._validate(tool_input)
        decision = self._executor.permission_checker.evaluate_tool(
            tool_name=self._tool.name,
            is_read_only=self._tool.is_read_only(arguments),
            required_capabilities=self._tool.required_capabilities,
            arguments=arguments.model_dump(mode="json"),
            context=self._build_context(),
        )
        if not decision.allowed:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=decision.reason or "denied by HomeMaster permission",
            )
        # HM's own confirmation channel handles requires_confirmation inside
        # the tool body; AS-level ASK is intentionally unused (decision ③ §2).
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=decision.reason or "allowed",
        )

    async def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        del tool_input
        return rule_content is None

    async def call(self, **kwargs: Any) -> AsyncGenerator[ToolChunk, None]:
        from homemaster.agent.messages import ToolResultMessage

        arguments = self._validate(kwargs)
        call = ToolCall(
            id=current_tool_call_id() or f"{self.name}-call",
            name=self._tool.name,
            arguments=arguments.model_dump(mode="json"),
        )
        # Prefer the application-owned funnel when the executor exposes it —
        # it carries completion guards, evidence registration, and artifact
        # publication that ``ToolExecutor.execute`` alone does not.
        for_substrate = getattr(self._executor, "execute_for_substrate", None)
        if callable(for_substrate):
            outcome = await for_substrate(
                call, run_context=self._scope.services.get("run_context")
            )
            if isinstance(outcome, ToolResultMessage):
                yield message_to_chunk(outcome)
            else:
                yield result_to_chunk(outcome)
            return
        result = await self._executor.execute(call, self._build_context())
        yield result_to_chunk(result)

    def _validate(self, tool_input: dict[str, Any]) -> BaseModel:
        return self._tool.input_model.model_validate(tool_input)

    def _build_context(self) -> ToolExecutionContext:
        scope = self._scope
        return ToolExecutionContext(
            session_id=scope.session_id,
            run_id=scope.run_id,
            turn_index=scope.turn_index,
            tool_call_id=current_tool_call_id() or f"{self.name}-call",
            internal_tool_id=self._tool.stable_id,
            permission_subject=scope.permission_subject,
            backend=scope.backend,
            deadline=scope.deadline,
            cancellation=scope.cancellation,
            domain_observer=scope.domain_observer,
            working_directory=scope.working_directory,
            services=scope.services,
        )


__all__ = [
    "HomeToolAdapter",
    "RunScope",
    "RunScopeMiddleware",
    "current_tool_call_id",
    "message_to_chunk",
    "result_to_chunk",
]
