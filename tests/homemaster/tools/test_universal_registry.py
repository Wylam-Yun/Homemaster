from pathlib import Path

import pytest
from pydantic import BaseModel

from homemaster.tools import (
    BaseTool,
    ToolExecutionContext,
    ToolRegistry,
    ToolRegistryError,
    ToolResult,
)


class _Input(BaseModel):
    value: str


class _EchoTool(BaseTool):
    name = "echo"
    stable_id = "homemaster.echo.v1"
    description = "Echo one value."
    input_model = _Input

    async def execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolResult:
        assert context.cwd.is_absolute()
        return ToolResult(arguments.value)


def test_registry_uses_only_ordinary_name_and_hides_stable_id() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())

    assert registry.get("echo") is not None
    assert registry.get("homemaster.echo.v1") is None
    assert registry.to_api_schema() == [
        {
            "name": "echo",
            "description": "Echo one value.",
            "input_schema": _Input.model_json_schema(),
        }
    ]


def test_duplicate_ordinary_name_fails_instead_of_overwriting() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())

    with pytest.raises(ToolRegistryError, match="duplicate tool name 'echo'"):
        registry.register(_EchoTool())


def test_register_many_is_atomic_on_duplicate_name() -> None:
    class OtherTool(_EchoTool):
        name = "other"
        stable_id = "homemaster.other.v1"

    registry = ToolRegistry()
    registry.register(_EchoTool())

    with pytest.raises(ToolRegistryError, match="duplicate tool name 'echo'"):
        registry.register_many([OtherTool(), _EchoTool()])

    assert registry.all_names() == ["echo"]


def test_stable_id_must_be_homemaster_name_v1() -> None:
    class WrongIdentityTool(_EchoTool):
        stable_id = "openharness.echo.v1"

    with pytest.raises(ValueError, match="homemaster.echo.v1"):
        ToolRegistry().register(WrongIdentityTool())


@pytest.mark.asyncio
async def test_tool_result_is_small_and_context_is_runtime_agnostic(tmp_path: Path) -> None:
    result = await _EchoTool().execute(
        _Input(value="exact-token"),
        ToolExecutionContext(cwd=tmp_path, metadata={"backend": object()}),
    )

    assert result == ToolResult(output="exact-token")
    assert not hasattr(result, "verification")
    assert not hasattr(result, "outcome_certainty")


@pytest.mark.asyncio
async def test_function_tool_validates_declared_json_schema(tmp_path: Path) -> None:
    from homemaster.tools import FunctionTool

    tool = FunctionTool(
        name="strict",
        description="Validate input.",
        input_schema={
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
            "additionalProperties": False,
        },
        execute=lambda arguments, context: ToolResult(arguments["target"]),
    )
    with pytest.raises(ValueError, match="target.*required"):
        tool.input_model.model_validate({})

    parsed = tool.input_model.model_validate({"target": "kitchen"})
    result = await tool.execute(parsed, ToolExecutionContext(tmp_path))
    assert result.output == "kitchen"


def test_composed_registry_is_universal_and_uses_alfworld_robot_contract() -> None:
    from homemaster.adapters.profiles import build_universal_tool_registry

    registry = build_universal_tool_registry()
    go_to = registry.get("robot_go_to")

    assert go_to is not None
    assert go_to.stable_id == "homemaster.robot_go_to.v1"
    schema = go_to.to_api_schema()["input_schema"]
    assert schema["required"] == ["target"]
    assert set(schema["properties"]) == {"target"}
    assert registry.get("robot_navigate") is None
    assert len(registry.all_names()) == len(set(registry.all_names()))


def test_every_composed_tool_implements_the_complete_public_contract() -> None:
    from homemaster.adapters.profiles import build_universal_tool_registry

    registry = build_universal_tool_registry()

    assert registry.list_tools()
    for tool in registry.list_tools():
        assert isinstance(tool, BaseTool), tool.name
        assert tool.stable_id == f"homemaster.{tool.name}.v1"
        assert issubclass(tool.input_model, BaseModel), tool.name
        assert callable(tool.execute), tool.name
        assert callable(tool.is_read_only), tool.name
        assert callable(tool.to_api_schema), tool.name
        assert callable(tool.validate_identity), tool.name


def test_composed_registry_preserves_execution_safety_capabilities() -> None:
    from homemaster.adapters.profiles import build_universal_tool_registry

    registry = build_universal_tool_registry()

    assert set(registry.get("bash").required_capabilities) >= {
        "tool.mutate",
        "process.exec",
    }
    assert "device.control" in registry.get("robot_go_to").required_capabilities
    assert "device.read" in registry.get("observe").required_capabilities


def test_universal_builder_rejects_unapproved_cross_source_name_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from homemaster.adapters import profiles

    duplicate = profiles._home_tools(
        world_path=None,
        memory_path=None,
        runtime_memory_root=None,
    )[0]
    monkeypatch.setattr(profiles, "_coworker_tools", lambda: (duplicate,))

    with pytest.raises(ValueError, match="unapproved duplicate tool name 'bash'"):
        profiles.build_universal_tool_registry()


def test_application_composition_api_has_no_profile_catalog_or_request_filter_layer() -> None:
    import inspect

    from homemaster.application.contracts import RunRequest
    from homemaster.application.factory import create_application
    from homemaster.application.runtime import ApplicationRuntime

    factory_parameters = inspect.signature(create_application).parameters
    runtime_parameters = inspect.signature(ApplicationRuntime).parameters

    for legacy_name in ("profiles", "catalog", "pipeline"):
        assert legacy_name not in factory_parameters
        assert legacy_name not in runtime_parameters
    assert "enabled_tool_ids" not in RunRequest.__dataclass_fields__
