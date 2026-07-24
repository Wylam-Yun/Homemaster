from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from homemaster.agent.messages import ToolCall
from homemaster.tools.catalog import ToolCatalog
from homemaster.tools.contracts import (
    PermissionSubject,
    RegisteredTool,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
)
from homemaster.tools.pipeline import ToolExecutionPipeline


class Executor:
    def __init__(self, result: ToolExecutionResult | None = None) -> None:
        self.calls = 0
        self.result = result or ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            data={"answer": "ok"},
            backend_attempted=True,
        )

    async def execute(self, arguments, context):
        del arguments, context
        self.calls += 1
        return self.result


@dataclass
class Context:
    catalog: ToolCatalog
    internal_id: str

    def build(self) -> ToolExecutionContext:
        view = self.catalog.freeze([self.internal_id])
        return ToolExecutionContext(
            session_id="session",
            run_id="run",
            turn_index=0,
            tool_call_id="call-1",
            internal_tool_id=self.internal_id,
            tool_view=view,
            permission_subject=PermissionSubject(subject_id="user", channel="test"),
            backend=None,
            deadline=None,
            cancellation=None,
            domain_observer=None,
            working_directory=Path.cwd(),
        )


def definition(*, input_schema=None, output_schema=None) -> ToolDefinition:
    return ToolDefinition(
        internal_id="test.validate.v1",
        model_alias="validate",
        description="Validate input.",
        input_schema=input_schema or {"type": "object"},
        output_schema=output_schema or {},
        verification_policy=VerificationPolicy(),
        provenance=ToolProvenance(source="test", reference="validation"),
        version="1.0.0",
        state_effects=("none",),
    )


def pipeline_for(tool_definition: ToolDefinition, executor: Executor, **kwargs):
    catalog = ToolCatalog()
    catalog.register(RegisteredTool(definition=tool_definition, executor=executor))
    return ToolExecutionPipeline(catalog, **kwargs), Context(
        catalog, tool_definition.internal_id
    ).build()


def test_definition_rejects_structurally_invalid_draft_2020_12_schema() -> None:
    with pytest.raises(ValueError, match="invalid JSON Schema"):
        definition(input_schema={"type": "not-a-json-schema-type"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"mode": "fast", "count": "2", "nested": {"enabled": True}},
        {"mode": "unsafe", "count": 2, "nested": {"enabled": True}},
        {"mode": "fast", "count": 2, "nested": {"enabled": "yes"}},
        {"mode": "fast", "count": 2, "nested": {"enabled": True}, "extra": 1},
    ],
)
async def test_input_validation_rejects_before_executor(arguments) -> None:
    executor = Executor()
    tool_definition = definition(
        input_schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["fast", "safe"]},
                "count": {"type": "integer"},
                "nested": {
                    "type": "object",
                    "properties": {"enabled": {"type": "boolean"}},
                    "required": ["enabled"],
                    "additionalProperties": False,
                },
            },
            "required": ["mode", "count", "nested"],
            "additionalProperties": False,
        }
    )
    pipeline, context = pipeline_for(tool_definition, executor)

    result = await pipeline.execute(
        ToolCall(id="call-1", name="validate", arguments=arguments),
        context,
    )

    assert result.status is ToolExecutionStatus.INVALID
    assert result.error is not None
    assert result.error.code == "invalid_tool_arguments"
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_valid_nested_input_reaches_executor() -> None:
    executor = Executor()
    tool_definition = definition(
        input_schema={
            "type": "object",
            "properties": {"nested": {"type": "object", "required": ["value"]}},
            "required": ["nested"],
        }
    )
    pipeline, context = pipeline_for(tool_definition, executor)

    result = await pipeline.execute(
        ToolCall(id="call-1", name="validate", arguments={"nested": {"value": 1}}),
        context,
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert executor.calls == 1


@pytest.mark.asyncio
async def test_nonempty_output_schema_rejects_invalid_executor_result() -> None:
    executor = Executor(
        ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            data={"answer": 7},
            backend_attempted=True,
        )
    )
    tool_definition = definition(
        output_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        }
    )
    pipeline, context = pipeline_for(tool_definition, executor)

    result = await pipeline.execute(
        ToolCall(id="call-1", name="validate", arguments={}),
        context,
    )

    assert result.status is ToolExecutionStatus.FAILURE
    assert result.error is not None
    assert result.error.code == "invalid_tool_result"
    assert result.backend_attempted is True


def test_empty_output_schema_is_not_replaced_with_a_fabricated_schema() -> None:
    executor = Executor()
    pipeline, _context = pipeline_for(definition(output_schema={}), executor)
    pipeline.validate_catalog()


def test_custom_format_requires_explicit_checker() -> None:
    executor = Executor()
    tool_definition = definition(
        input_schema={
            "type": "object",
            "properties": {"device": {"type": "string", "format": "robot-device-id"}},
        }
    )
    catalog = ToolCatalog()
    catalog.register(RegisteredTool(definition=tool_definition, executor=executor))

    with pytest.raises(ValueError, match="register an explicit checker"):
        ToolExecutionPipeline(catalog)

    ToolExecutionPipeline(
        catalog,
        custom_formats={"robot-device-id": lambda value: str(value).startswith("robot-")},
    )
