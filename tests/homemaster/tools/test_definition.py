from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest
from pydantic import BaseModel

from homemaster.tools.contracts import (
    BaseTool,
    ConcurrencyPolicy,
    ExecutionProof,
    RegisteredTool,
    ToolDefinition,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
)


class EchoInput(BaseModel):
    text: str


class EchoTool(BaseTool):
    name = "echo"
    description = "Echo one string."
    input_model = EchoInput

    async def execute(self, arguments, context):
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text=arguments.text,
        )


class Executor:
    async def execute(self, arguments, context):
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            data={"arguments": dict(arguments)},
        )


class Verifier:
    async def verify(self, result, context):
        return result.verification


class SyncExecutor:
    def execute(self, arguments, context):
        return ToolExecutionResult(status=ToolExecutionStatus.SUCCESS)


def _definition(**overrides) -> ToolDefinition:
    values = {
        "internal_id": "home.echo.v1",
        "model_alias": "echo",
        "description": "Echo one string.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "enum": ["a", "b"]}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "output_schema": {"type": "object", "properties": {"echo": {"type": "string"}}},
        "verification_policy": VerificationPolicy(),
        "provenance": ToolProvenance(source="builtin", reference="homemaster.tools.echo"),
        "version": "1.0.0",
        "state_effects": ("none",),
    }
    values.update(overrides)
    return ToolDefinition(**values)


def test_openharness_base_tool_api_schema_characterization() -> None:
    schema = EchoTool().to_api_schema()
    assert schema == {
        "name": "echo",
        "description": "Echo one string.",
        "input_schema": EchoInput.model_json_schema(),
    }


def test_definition_is_deeply_immutable_serializable_and_manifest_is_derived() -> None:
    raw_input = {
        "type": "object",
        "properties": {"text": {"type": "string", "enum": ["a", "b"]}},
        "required": ["text"],
    }
    definition = _definition(input_schema=raw_input)
    raw_input["properties"]["text"]["enum"].append("mutated")

    assert definition.input_schema["properties"]["text"]["enum"] == ("a", "b")
    with pytest.raises(TypeError):
        definition.input_schema["type"] = "array"
    with pytest.raises(TypeError):
        definition.input_schema["properties"]["text"]["type"] = "integer"
    with pytest.raises(FrozenInstanceError):
        definition.model_alias = "changed"

    snapshot = definition.to_dict()
    assert json.loads(json.dumps(snapshot)) == snapshot
    assert definition.to_model_manifest() == {
        "name": "echo",
        "description": "Echo one string.",
        "input_schema": snapshot["input_schema"],
    }
    assert len(definition.snapshot_sha256) == 64
    assert (
        definition.snapshot_sha256
        == _definition(
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string", "enum": ["a", "b"]}},
                "required": ["text"],
            }
        ).snapshot_sha256
    )


def test_model_observation_flag_is_canonical_but_not_provider_visible() -> None:
    definition = _definition(requires_model_observation=True)

    assert definition.to_dict()["requires_model_observation"] is True
    assert "requires_model_observation" not in definition.to_model_manifest()


def test_registered_tool_keeps_capabilities_out_of_definition_snapshot() -> None:
    executor = Executor()
    verifier = Verifier()
    registered = RegisteredTool(
        definition=_definition(
            verification_policy=VerificationPolicy(execution_proof=ExecutionProof.EXTERNAL_STATE)
        ),
        executor=executor,
        verifier=verifier,
    )
    snapshot = registered.to_definition_snapshot()
    encoded = json.dumps(snapshot)
    assert "executor" not in snapshot
    assert "verifier" not in snapshot
    assert "Executor" not in encoded
    assert snapshot == registered.definition.to_dict()

    with pytest.raises(ValueError, match="requires a verifier"):
        RegisteredTool(
            definition=_definition(
                verification_policy=VerificationPolicy(
                    execution_proof=ExecutionProof.EXTERNAL_STATE
                )
            ),
            executor=executor,
        )


@pytest.mark.parametrize(
    ("override", "error"),
    [
        ({"internal_id": "echo"}, "namespaced"),
        ({"internal_id": "Home.echo.v1"}, "namespaced"),
        ({"model_alias": "Echo"}, "provider-safe"),
        ({"version": "v1"}, "semantic version"),
        ({"input_schema": {"minimum": float("nan")}}, "finite JSON"),
        ({"timeout_s": 0}, "finite positive"),
        ({"state_effects": ("robot.move", "robot.move")}, "unique"),
        ({"state_effects": "robot.move"}, "sequence of tokens"),
        ({"resource_key": "robot:one"}, "requires concurrency_policy"),
        ({"requires_model_observation": "yes"}, "must be a boolean"),
        (
            {"concurrency_policy": ConcurrencyPolicy.RESOURCE_KEY},
            "resource_key must be",
        ),
    ],
)
def test_definition_rejects_unstable_or_incoherent_fields(override, error: str) -> None:
    with pytest.raises((TypeError, ValueError), match=error):
        _definition(**override)


def test_definition_resource_policy_accepts_a_normalized_static_key() -> None:
    definition = _definition(
        concurrency_policy=ConcurrencyPolicy.RESOURCE_KEY,
        resource_key="robot:arm-1",
    )
    assert definition.to_dict()["resource_key"] == "robot:arm-1"


def test_registered_tool_rejects_a_sync_executor_before_canonical_adaptation() -> None:
    with pytest.raises(TypeError, match="async execute"):
        RegisteredTool(definition=_definition(), executor=SyncExecutor())


def test_contract_module_has_no_benchmark_dependency() -> None:
    import homemaster.tools.contracts as contracts

    source_names = set(contracts.__dict__)
    assert not any(
        name.startswith("Alfworld") or name.startswith("Coworker") for name in source_names
    )
