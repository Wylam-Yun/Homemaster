"""Canonical model-facing tools bound to one BrowserSession."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from homemaster.browser.contracts import BrowserSessionError, BrowserSnapshot
from homemaster.tools.adapters import from_registered_tool
from homemaster.tools.base import ToolRegistry
from homemaster.tools.contracts import (
    ConcurrencyPolicy,
    ExecutionProof,
    OutcomeCertainty,
    RegisteredTool,
    ToolDefinition,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
    VerificationRecord,
    VerificationStatus,
)
from homemaster.tools.observe import ScreenshotTool

_OUTPUT_SCHEMA = {"type": "object"}
_REF_PROPERTIES = {
    "snapshot_id": {"type": "string", "minLength": 1},
    "element_id": {"type": "string", "minLength": 1},
}


class _BrowserToolExecutor:
    def __init__(self, session: object, operation: str) -> None:
        self._session = session
        self._operation = operation

    async def execute(self, arguments: Mapping[str, object], context: Any) -> ToolExecutionResult:
        del context
        try:
            if self._operation == "inspect":
                value = await self._session.inspect(arguments)
                data = value.to_public_dict() if isinstance(value, BrowserSnapshot) else dict(value)
            elif self._operation == "navigate":
                data = dict(await self._session.navigate(str(arguments["url"])))
            elif self._operation == "fill":
                data = dict(
                    await self._session.fill(
                        str(arguments["snapshot_id"]),
                        str(arguments["element_id"]),
                        str(arguments["value"]),
                    )
                )
            elif self._operation == "select":
                data = dict(
                    await self._session.select(
                        str(arguments["snapshot_id"]),
                        str(arguments["element_id"]),
                        str(arguments["option"]),
                    )
                )
            elif self._operation in {"check", "uncheck", "click"}:
                method = getattr(self._session, self._operation)
                data = dict(
                    await method(str(arguments["snapshot_id"]), str(arguments["element_id"]))
                )
            elif self._operation == "wait":
                data = dict(await self._session.wait(dict(arguments["condition"])))
            else:
                raise RuntimeError(f"unsupported browser operation: {self._operation}")
        except BrowserSessionError as exc:
            status = (
                ToolExecutionStatus.OUTCOME_UNKNOWN
                if exc.outcome_unknown
                else ToolExecutionStatus.FAILURE
            )
            return ToolExecutionResult(
                status=status,
                error=ToolExecutionError(exc.code, str(exc), exc.details),
                backend_attempted=exc.backend_attempted,
                outcome_certainty=(
                    OutcomeCertainty.UNKNOWN if exc.outcome_unknown else OutcomeCertainty.CONFIRMED
                ),
            )
        evidence_ref = str(data.get("evidence_ref") or f"browser:{uuid.uuid4().hex}")
        data["evidence_ref"] = evidence_ref
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            data=data,
            evidence_refs=(evidence_ref,),
            backend_attempted=self._operation != "inspect",
        )


class _ObserveExecutor:
    def __init__(self, session: object) -> None:
        self._session = session

    async def execute(self, arguments: Mapping[str, object], context: Any) -> ToolExecutionResult:
        class _Context:
            backend = self._session

        return await ScreenshotTool().execute(arguments, _Context())


class _ReceiptVerifier:
    async def verify(self, result: ToolExecutionResult, context: Any) -> VerificationRecord:
        del context
        return VerificationRecord(
            status=VerificationStatus.PASSED,
            detail="browser receipt recorded",
            evidence_refs=result.evidence_refs,
        )


def build_browser_registered_tools(session: object) -> tuple[RegisteredTool, ...]:
    specs = (
        (
            "browser_navigate",
            "Open an allowed web page.",
            _object_schema({"url": {"type": "string", "minLength": 1}}, ("url",)),
            "navigate",
            ("browser.navigate",),
            ("device.control", "network.http"),
            ExecutionProof.STRUCTURED_RECEIPT,
        ),
        (
            "browser_inspect",
            "Inspect visible page text and interactive elements.",
            _object_schema(
                {
                    "role": {"type": "string"},
                    "name": {"type": "string"},
                    "label": {"type": "string"},
                    "text": {"type": "string"},
                    "interactive_only": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                }
            ),
            "inspect",
            ("read",),
            ("device.read",),
            ExecutionProof.NONE,
        ),
        (
            "browser_fill",
            "Fill one input using a current inspected element reference.",
            _object_schema(
                {**_REF_PROPERTIES, "value": {"type": "string"}},
                ("snapshot_id", "element_id", "value"),
            ),
            "fill",
            ("browser.dom_write",),
            ("device.control",),
            ExecutionProof.STRUCTURED_RECEIPT,
        ),
        (
            "browser_select",
            "Select one option using a current inspected element reference.",
            _object_schema(
                {**_REF_PROPERTIES, "option": {"type": "string", "minLength": 1}},
                ("snapshot_id", "element_id", "option"),
            ),
            "select",
            ("browser.dom_write",),
            ("device.control",),
            ExecutionProof.STRUCTURED_RECEIPT,
        ),
        (
            "browser_check",
            "Ensure a checkbox, switch, or radio is selected.",
            _object_schema(_REF_PROPERTIES, ("snapshot_id", "element_id")),
            "check",
            ("browser.dom_write",),
            ("device.control",),
            ExecutionProof.STRUCTURED_RECEIPT,
        ),
        (
            "browser_uncheck",
            "Ensure a checkbox or switch is not selected.",
            _object_schema(_REF_PROPERTIES, ("snapshot_id", "element_id")),
            "uncheck",
            ("browser.dom_write",),
            ("device.control",),
            ExecutionProof.STRUCTURED_RECEIPT,
        ),
        (
            "browser_click",
            "Click one current inspected element.",
            _object_schema(_REF_PROPERTIES, ("snapshot_id", "element_id")),
            "click",
            ("browser.interact",),
            ("device.control",),
            ExecutionProof.STRUCTURED_RECEIPT,
        ),
        (
            "browser_wait",
            "Wait for a bounded page condition and report the last observed state.",
            _object_schema(
                {
                    "condition": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "text_present",
                                    "text_absent",
                                    "element_present",
                                    "element_absent",
                                    "element_enabled",
                                    "element_disabled",
                                    "element_text",
                                    "url_contains",
                                    "dom_stable",
                                ],
                            },
                            "value": {"type": "string"},
                            **_REF_PROPERTIES,
                            "timeout_ms": {"type": "integer", "minimum": 1, "maximum": 30000},
                        },
                        "required": ["kind"],
                        "additionalProperties": False,
                    }
                },
                ("condition",),
            ),
            "wait",
            ("read",),
            ("device.read",),
            ExecutionProof.STRUCTURED_RECEIPT,
        ),
    )
    tools = [
        _registered(
            name=name,
            description=description,
            schema=schema,
            executor=_BrowserToolExecutor(session, operation),
            effects=effects,
            capabilities=capabilities,
            proof=proof,
        )
        for name, description, schema, operation, effects, capabilities, proof in specs
    ]
    tools.append(
        _registered(
            name="observe",
            description="Capture the current browser page as one PNG image.",
            schema=_object_schema({}),
            executor=_ObserveExecutor(session),
            effects=("read",),
            capabilities=("device.read",),
            proof=ExecutionProof.NONE,
        )
    )
    return tuple(tools)


def build_browser_run_registry(base: ToolRegistry, session: object) -> ToolRegistry:
    """Derive one frozen run view without mutating the application Registry."""

    if not isinstance(base, ToolRegistry):
        raise TypeError("base must be a ToolRegistry")
    browser_names = {
        "browser_navigate",
        "browser_inspect",
        "browser_fill",
        "browser_select",
        "browser_check",
        "browser_uncheck",
        "browser_click",
        "browser_wait",
        "observe",
    }
    registry = ToolRegistry()
    registry.register_many(
        tuple(tool for tool in base.list_tools() if tool.name not in browser_names)
    )
    registry.register_many(
        tuple(from_registered_tool(tool) for tool in build_browser_registered_tools(session))
    )
    return registry.freeze()


def _registered(
    *,
    name: str,
    description: str,
    schema: Mapping[str, object],
    executor: Any,
    effects: tuple[str, ...],
    capabilities: tuple[str, ...],
    proof: ExecutionProof,
) -> RegisteredTool:
    definition = ToolDefinition(
        internal_id=f"homemaster.{name}.v1",
        model_alias=name,
        description=description,
        input_schema=schema,
        output_schema=_OUTPUT_SCHEMA,
        verification_policy=VerificationPolicy(execution_proof=proof),
        provenance=ToolProvenance(source="homemaster", reference="homemaster.browser"),
        version="2.1.0",
        concurrency_policy=ConcurrencyPolicy.RESOURCE_KEY,
        resource_key="browser:backend",
        state_effects=effects,
        required_capabilities=capabilities,
    )
    return RegisteredTool(
        definition=definition,
        executor=executor,
        verifier=_ReceiptVerifier() if proof is ExecutionProof.STRUCTURED_RECEIPT else None,
    )


def _object_schema(
    properties: Mapping[str, object], required: tuple[str, ...] = ()
) -> dict[str, object]:
    schema: dict[str, object] = {
        "type": "object",
        "properties": dict(properties),
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


__all__ = ["build_browser_registered_tools", "build_browser_run_registry"]
