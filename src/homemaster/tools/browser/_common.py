"""Shared execution and schema helpers for V3.1 browser tools."""

from __future__ import annotations

import base64
import hashlib
import uuid
from collections.abc import Mapping
from typing import Any

from homemaster.browser.contracts import BrowserSessionError, BrowserSnapshot
from homemaster.tools.contracts import (
    ConcurrencyPolicy,
    ExecutionProof,
    OutcomeCertainty,
    RegisteredTool,
    ResultImage,
    ToolDefinition,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
    VerificationRecord,
    VerificationStatus,
)

_OUTPUT_SCHEMA = {"type": "object"}

TARGET_SCHEMA: dict[str, object] = {
    "type": "object",
    "description": "Unique semantic target or retained target_ref. Do not guess CSS, XPath, or coordinates.",
    "properties": {
        "role": {
            "type": "string",
            "description": "ARIA or native role, such as button, textbox, option, or dialog.",
        },
        "name": {
            "type": "string",
            "description": "Accessible name. Exact matching is the default for actions.",
        },
        "label": {"type": "string", "description": "Associated label text for a form control."},
        "text": {
            "type": "string",
            "description": "Visible text used for discovery or exact action targeting.",
        },
        "testid": {
            "type": "string",
            "description": "Allowlisted data-testid value; not an arbitrary CSS selector.",
        },
        "match": {
            "type": "string",
            "enum": ["exact", "contains", "regex"],
            "description": "Exact for writes; contains for discovery; regex is read-only.",
        },
        "nth": {
            "type": "integer",
            "minimum": 0,
            "description": "Explicit candidate index when a read or discovery result has multiple matches.",
        },
        "frame_ref": {
            "type": "string",
            "description": "Frame identity returned by inspect; do not mix frames.",
        },
        "tab_ref": {
            "type": "string",
            "description": "Run-owned tab identity returned by browser_tabs.",
        },
        "target_ref": {
            "type": "string",
            "description": "Stable reference returned by inspect/find/screenshot annotation.",
        },
    },
    "additionalProperties": False,
}

CONDITION_SCHEMA: dict[str, object] = {
    "type": "object",
    "description": "One bounded observable browser condition used by wait or action verification.",
    "properties": {
        "kind": {
            "type": "string",
            "enum": [
                "text_present",
                "text_absent",
                "selector_present",
                "selector_absent",
                "time",
                "xhr",
                "response",
                "dom_stable",
                "url",
                "element_state",
                "popup",
                "dialog",
                "download",
            ],
            "description": "Exact condition family; event conditions observe this run-owned session only.",
        },
        "value": {
            "type": ["string", "integer"],
            "description": "Expected text, read-only CSS selector, URL/pattern, or host wait duration in milliseconds as required by kind.",
        },
        "target": TARGET_SCHEMA,
        "match": {
            "type": "string",
            "enum": ["exact", "contains"],
            "description": "URL comparison mode; defaults to exact and is not used by other kinds.",
        },
        "state": {
            "type": "string",
            "enum": [
                "visible",
                "enabled",
                "disabled",
                "editable",
                "checked",
                "unchecked",
                "expanded",
                "collapsed",
            ],
            "description": "Requested final state for kind=element_state; defaults to visible.",
        },
        "timeout_ms": {
            "type": "integer",
            "minimum": 1,
            "description": "Wall-clock bound in milliseconds, capped by the run browser policy.",
        },
        "request_ref": {
            "type": "string",
            "description": "Stable request ref returned by browser_network when waiting on one authorized response.",
        },
    },
    "required": ["kind"],
    "allOf": [
        {
            "if": {
                "properties": {
                    "kind": {
                        "enum": [
                            "text_present",
                            "text_absent",
                            "time",
                            "xhr",
                            "response",
                            "url",
                        ]
                    }
                }
            },
            "then": {"required": ["value"]},
        },
        {
            "if": {"properties": {"kind": {"const": "element_state"}}},
            "then": {"required": ["target"]},
        },
        {
            "if": {
                "properties": {
                    "kind": {"enum": ["selector_present", "selector_absent"]}
                }
            },
            "then": {"anyOf": [{"required": ["value"]}, {"required": ["target"]}]},
        },
    ],
    "additionalProperties": False,
}


def object_schema(
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


def target_object(
    extra: Mapping[str, object] | None = None, *, required: tuple[str, ...] = ("target",)
) -> dict[str, object]:
    properties = {"target": TARGET_SCHEMA}
    properties.update(extra or {})
    return object_schema(properties, required)


class BrowserToolExecutor:
    def __init__(self, session: object, operation: str) -> None:
        self._session = session
        self._operation = operation

    async def execute(self, arguments: Mapping[str, object], context: Any) -> ToolExecutionResult:
        del context
        try:
            data = await self._dispatch(arguments)
        except BrowserSessionError as exc:
            return ToolExecutionResult(
                status=ToolExecutionStatus.OUTCOME_UNKNOWN
                if exc.outcome_unknown
                else ToolExecutionStatus.FAILURE,
                error=ToolExecutionError(exc.code, str(exc), exc.details),
                backend_attempted=exc.backend_attempted,
                outcome_certainty=OutcomeCertainty.UNKNOWN
                if exc.outcome_unknown
                else OutcomeCertainty.CONFIRMED,
            )
        if isinstance(data, bytes):
            data = {"png": base64.b64encode(data).decode("ascii"), "byte_count": len(data)}
        result = dict(data)
        evidence_ref = str(result.get("evidence_ref") or f"browser:{uuid.uuid4().hex}")
        result["evidence_ref"] = evidence_ref
        images: tuple[ResultImage, ...] = ()
        if self._operation == "screenshot":
            encoded = result.pop("png", None)
            if not isinstance(encoded, str) or not encoded:
                raise BrowserSessionError(
                    "invalid_screenshot",
                    "browser screenshot did not return PNG content",
                    backend_attempted=True,
                )
            content = base64.b64decode(encoded, validate=True)
            images = (
                ResultImage(
                    media_type="image/png",
                    data_base64=encoded,
                    content_sha256=hashlib.sha256(content).hexdigest(),
                ),
            )
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            data=result,
            images=images,
            evidence_refs=(evidence_ref,),
            backend_attempted=self._operation
            not in {"inspect", "find", "read", "extract", "screenshot", "console", "analyze"},
        )

    async def _dispatch(self, arguments: Mapping[str, object]) -> Mapping[str, object] | bytes:
        op = self._operation
        if op == "navigate":
            return dict(
                await self._session.navigate(
                    str(arguments["url"]),
                    wait_until=arguments.get("wait_until", "domcontentloaded"),
                    timeout_ms=arguments.get("timeout_ms"),
                )
            )
        if op == "history":
            return dict(
                await self._session.history(
                    str(arguments["action"]),
                    wait_until=arguments.get("wait_until", "domcontentloaded"),
                    timeout_ms=arguments.get("timeout_ms"),
                )
            )
        if op == "inspect":
            value = await self._session.inspect(arguments)
            return value.to_public_dict() if isinstance(value, BrowserSnapshot) else dict(value)
        if op in {
            "find",
            "read",
            "extract",
            "network",
            "analyze",
            "console",
            "tabs",
            "dialog",
            "download",
            "wait",
            "scroll",
            "screenshot",
            "eval",
        }:
            value = (
                await getattr(self._session, op)(dict(arguments))
                if op not in {"screenshot"}
                else await self._session.screenshot(**dict(arguments))
            )
            return value.to_public_dict() if isinstance(value, BrowserSnapshot) else value
        if op == "fill":
            return dict(await self._session.fill(arguments["target"], str(arguments["value"])))
        if op == "type":
            return dict(
                await self._session.type(
                    arguments["target"],
                    str(arguments["text"]),
                    mode=arguments.get("mode", "replace"),
                    delay_ms=arguments.get("delay_ms", 0),
                )
            )
        if op == "select":
            return dict(
                await self._session.select(
                    arguments["target"],
                    arguments.get("option"),
                    options=arguments.get("options"),
                    match=arguments.get("match", "label"),
                )
            )
        if op in {"check", "uncheck", "focus", "hover"}:
            return dict(
                await getattr(self._session, op)(
                    arguments["target"],
                    **{key: value for key, value in arguments.items() if key not in {"target"}},
                )
            )
        if op == "press":
            return dict(
                await self._session.press(
                    str(arguments["key"]),
                    arguments.get("target"),
                    modifiers=arguments.get("modifiers"),
                    expect=arguments.get("expect"),
                )
            )
        if op == "click":
            return dict(
                await self._session.click(
                    arguments["target"],
                    click_count=int(arguments.get("click_count", 1)),
                    button=arguments.get("button", "left"),
                    modifiers=arguments.get("modifiers", []),
                )
            )
        if op == "upload":
            return dict(
                await self._session.upload(
                    arguments["target"], [str(item) for item in arguments.get("artifact_refs", [])]
                )
            )
        if op == "drag":
            return dict(
                await self._session.drag(
                    arguments["source"], arguments["destination"], expect=arguments.get("expect")
                )
            )
        if op == "backfill":
            return dict(
                await self._session.backfill(
                    arguments["target"],
                    full_page=arguments.get("full_page", False),
                    width=arguments.get("width"),
                    height=arguments.get("height"),
                )
            )
        raise RuntimeError(f"unsupported browser operation: {op}")


class ReceiptVerifier:
    async def verify(self, result: ToolExecutionResult, context: Any) -> VerificationRecord:
        del context
        if not result.success:
            return VerificationRecord()
        return VerificationRecord(
            status=VerificationStatus.PASSED,
            detail="browser receipt recorded",
            evidence_refs=result.evidence_refs,
        )


def registered(
    *,
    session: object,
    name: str,
    description: str,
    schema: Mapping[str, object],
    operation: str,
    effects: tuple[str, ...],
    capabilities: tuple[str, ...],
    proof: ExecutionProof,
    observe: bool = False,
) -> RegisteredTool:
    definition = ToolDefinition(
        internal_id=f"homemaster.{name}.v1",
        model_alias=name,
        description=description,
        input_schema=schema,
        output_schema=_OUTPUT_SCHEMA,
        verification_policy=VerificationPolicy(execution_proof=proof),
        provenance=ToolProvenance(source="homemaster", reference="homemaster.browser.v3.1"),
        version="3.1.0",
        concurrency_policy=ConcurrencyPolicy.RESOURCE_KEY,
        resource_key="browser:backend",
        state_effects=effects,
        required_capabilities=capabilities,
        requires_model_observation=observe,
    )
    return RegisteredTool(
        definition=definition,
        executor=BrowserToolExecutor(session, operation),
        verifier=ReceiptVerifier() if proof is not ExecutionProof.NONE else None,
    )


__all__ = [
    "CONDITION_SCHEMA",
    "ExecutionProof",
    "TARGET_SCHEMA",
    "object_schema",
    "registered",
]
