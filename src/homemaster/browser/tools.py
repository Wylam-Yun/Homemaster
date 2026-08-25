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
    "snapshot_id": {
        "type": "string",
        "minLength": 1,
        "description": (
            "Inspection-batch identifier returned by browser_inspect. Copy it exactly "
            "with element_id from the same result; it is local to that inspection and "
            "can become stale after any later page change or inspection."
        ),
    },
    "element_id": {
        "type": "string",
        "minLength": 1,
        "description": (
            "Element identifier local to snapshot_id. It is not globally stable: copy "
            "the snapshot_id/element_id pair from one browser_inspect result and never "
            "mix, guess, or reuse identifiers from another inspection or next_snapshot."
        ),
    },
}
_SNAPSHOT_CONSUMING_OPERATIONS = {
    "fill",
    "select",
    "check",
    "uncheck",
    "click",
    "backfill",
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
            elif self._operation in {"check", "uncheck", "click", "backfill"}:
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
        if self._operation in _SNAPSHOT_CONSUMING_OPERATIONS:
            data["snapshot_consumed"] = True
            data["next_action_requires_new_inspect"] = True
            try:
                next_value = await self._session.inspect(
                    {"interactive_only": True, "actionable_only": True, "limit": 200}
                )
            except BrowserSessionError as exc:
                data["next_snapshot"] = {
                    "status": "unavailable",
                    "error_code": exc.code,
                    "message": str(exc),
                }
            else:
                next_snapshot = (
                    next_value.to_public_dict()
                    if isinstance(next_value, BrowserSnapshot)
                    else dict(next_value)
                )
                next_snapshot.pop("text", None)
                next_snapshot = _review_only_snapshot(next_snapshot)
                data["next_snapshot"] = next_snapshot
                data["next_snapshot_ready"] = True
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
        if not result.success:
            return VerificationRecord()
        return VerificationRecord(
            status=VerificationStatus.PASSED,
            detail="browser receipt recorded",
            evidence_refs=result.evidence_refs,
        )


def build_browser_registered_tools(session: object) -> tuple[RegisteredTool, ...]:
    specs = (
        (
            "browser_navigate",
            "Open one allowed absolute HTTP(S) URL in the current browser session. Use this "
            "only when the current page is not already the required page; otherwise inspect "
            "the current page directly. Navigation does not grant an action reference, so call "
            "browser_inspect before any later browser write or interaction.",
            _object_schema(
                {
                    "url": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "Complete absolute http:// or https:// URL allowed by the configured "
                            "browser policy. Relative paths such as /ops/change are invalid."
                        ),
                    }
                },
                ("url",),
            ),
            "navigate",
            ("browser.navigate",),
            ("device.control", "network.http"),
            ExecutionProof.STRUCTURED_RECEIPT,
        ),
        (
            "browser_inspect",
            "Read the current page and find the exact intended target using visible semantic "
            "filters. This tool never changes the page and accepts no snapshot or element "
            "reference. Before a browser write or interaction, call it alone, narrow multiple "
            "matches, and confirm the target is visible, enabled, and unobscured. Each inspect "
            "creates a new snapshot_id and invalidates every earlier snapshot reference.",
            _object_schema(
                {
                    "role": {
                        "type": "string",
                        "description": (
                            "Optional semantic role used to narrow results, such as button, "
                            "link, textbox, combobox, checkbox, or option."
                        ),
                    },
                    "name": {
                        "type": "string",
                        "description": (
                            "Optional accessible-name filter for the visible target. Prefer a "
                            "specific user-visible name that distinguishes one control."
                        ),
                    },
                    "label": {
                        "type": "string",
                        "description": (
                            "Optional associated-label filter, useful for form controls such as "
                            "Version, Region, or Service."
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": (
                            "Optional visible-text filter. Use it to narrow results, then verify "
                            "the returned role, name, and state before acting."
                        ),
                    },
                    "interactive_only": {
                        "type": "boolean",
                        "description": (
                            "When true, return only interactive controls rather than all matching "
                            "page content."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "description": (
                            "Maximum number of matches to return. Use a small limit with specific "
                            "filters; multiple matches require another, narrower inspection."
                        ),
                    },
                }
            ),
            "inspect",
            ("read",),
            ("device.read",),
            ExecutionProof.NONE,
        ),
        (
            "browser_fill",
            "Fill one editable text, date, or time input and verify its DOM readback. Call "
            "browser_inspect alone immediately before this tool, then copy its matching "
            "snapshot_id/element_id pair. Run this as the only tool call in the response. "
            "Prefer it over click-driven typing or date/time pickers. Success consumes the "
            "snapshot; next_snapshot is review-only, and the next write requires a new inspect.",
            _object_schema(
                {
                    **_REF_PROPERTIES,
                    "value": {
                        "type": "string",
                        "description": (
                            "Complete value to place in the target input and verify by exact "
                            "readback; use an empty string only when the field must be cleared."
                        ),
                    },
                },
                ("snapshot_id", "element_id", "value"),
            ),
            "fill",
            ("browser.dom_write",),
            ("device.control",),
            ExecutionProof.STRUCTURED_RECEIPT,
        ),
        (
            "browser_select",
            "Select one exact visible option in a select or combobox and verify the selected "
            "state. Call browser_inspect alone immediately before this tool, copy the matching "
            "snapshot_id/element_id pair, and run this as the only tool call in the response. "
            "Prefer it over separately clicking a combobox and an option. Success consumes the "
            "snapshot; next_snapshot is review-only, and the next write requires a new inspect.",
            _object_schema(
                {
                    **_REF_PROPERTIES,
                    "option": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "Exact visible option name to select, matched case-insensitively. "
                            "This names the option, not the combobox itself."
                        ),
                    },
                },
                ("snapshot_id", "element_id", "option"),
            ),
            "select",
            ("browser.dom_write",),
            ("device.control",),
            ExecutionProof.STRUCTURED_RECEIPT,
        ),
        (
            "browser_check",
            "Ensure one checkbox, switch, or radio ends selected; if already selected, leave it "
            "unchanged. Call browser_inspect alone immediately before this tool, copy the matching "
            "snapshot_id/element_id pair, and run it as the only tool call in the response. "
            "Success consumes the snapshot; next_snapshot is review-only, and the next write "
            "requires a new inspect.",
            _object_schema(_REF_PROPERTIES, ("snapshot_id", "element_id")),
            "check",
            ("browser.dom_write",),
            ("device.control",),
            ExecutionProof.STRUCTURED_RECEIPT,
        ),
        (
            "browser_uncheck",
            "Ensure one checkbox or switch ends unselected; if already unselected, leave it "
            "unchanged. This does not apply to radio controls or ordinary select options. Call "
            "browser_inspect alone immediately before this tool, copy the matching snapshot_id/"
            "element_id pair, and run it as the only tool call in the response. Success consumes "
            "the snapshot; next_snapshot is review-only, and the next write requires a new "
            "inspect.",
            _object_schema(_REF_PROPERTIES, ("snapshot_id", "element_id")),
            "uncheck",
            ("browser.dom_write",),
            ("device.control",),
            ExecutionProof.STRUCTURED_RECEIPT,
        ),
        (
            "browser_click",
            "Click one exact semantic button, link, tab, date cell, or other target that has no "
            "more specific browser tool. Call browser_inspect alone immediately before this "
            "tool and act only when the matching target reports visible=true, enabled=true, and "
            "obscured=false. Run it as the only tool call in the response. Do not use click to "
            "fill an input or replace browser_select. Success consumes that snapshot and returns "
            "next_snapshot; next_snapshot is review-only, and the next write requires a new "
            "inspect.",
            _object_schema(_REF_PROPERTIES, ("snapshot_id", "element_id")),
            "click",
            ("browser.interact",),
            ("device.control",),
            ExecutionProof.STRUCTURED_RECEIPT,
        ),
        (
            "browser_backfill",
            "Capture the current page as PNG and paste it into one editable image-backfill "
            "control that explicitly accepts clipboard images. Use this only when the page "
            "requires image backfill; structured evidence without such a control does not. Call "
            "browser_inspect alone immediately before this tool, copy the matching snapshot_id/"
            "element_id pair, and run it as the only tool call in the response. Success requires "
            "an exact rendered preview and consumes the snapshot; next_snapshot is review-only.",
            _object_schema(_REF_PROPERTIES, ("snapshot_id", "element_id")),
            "backfill",
            ("browser.dom_write",),
            ("device.control",),
            ExecutionProof.STRUCTURED_RECEIPT,
        ),
        (
            "browser_wait",
            "Read-only wait for one bounded page condition and report the last observed state. "
            "Use value for text and URL conditions; use a snapshot_id/element_id pair from one "
            "inspection for element conditions. A timeout means the condition was not reached, "
            "not that the preceding action succeeded. Waiting grants no new action reference, "
            "so call browser_inspect before any later browser write or interaction.",
            _object_schema(
                {
                    "condition": {
                        "type": "object",
                        "description": (
                            "The single condition to wait for. Put kind, its required target or "
                            "value, and optional timeout_ms inside this object."
                        ),
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
                                "description": (
                                    "Condition type. text_present, text_absent, url_contains, and "
                                    "element_text use value; element_* kinds use the reference "
                                    "pair; dom_stable needs neither."
                                ),
                            },
                            "value": {
                                "type": "string",
                                "description": (
                                    "Expected text, URL fragment, or element text for the selected "
                                    "condition kind."
                                ),
                            },
                            **_REF_PROPERTIES,
                            "timeout_ms": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 30000,
                                "description": (
                                    "Maximum wait in milliseconds, from 1 through 30000. Keep it "
                                    "inside condition, not beside condition."
                                ),
                            },
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
            description=(
                "Capture the current browser page as one PNG image without changing the page. "
                "Call this when semantic text and controls are insufficient to understand layout, "
                "images, charts, canvas content, or visual obstruction. It returns no actionable "
                "element reference: after reviewing the image, call browser_inspect before any "
                "interaction. Browser writes already receive an automatic post-action image, so "
                "do not immediately duplicate that capture unless more visual evidence is needed."
            ),
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
        "browser_backfill",
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


def _review_only_snapshot(snapshot: Mapping[str, object]) -> dict[str, object]:
    review = dict(snapshot)
    review.pop("snapshot_id", None)
    elements = review.get("elements")
    if isinstance(elements, (list, tuple)):
        review["elements"] = [
            {key: value for key, value in dict(element).items() if key != "element_id"}
            for element in elements
            if isinstance(element, Mapping)
        ]
    review["reference_mode"] = "review_only"
    return review


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
        requires_model_observation=name
        in {
            "browser_fill",
            "browser_select",
            "browser_check",
            "browser_uncheck",
            "browser_click",
            "browser_backfill",
        },
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
