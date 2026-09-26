"""Small, ordinary-name tool contract used by every HomeMaster runtime."""

from __future__ import annotations

import inspect
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, model_validator

from homemaster.permissions.resources import PhysicalDeviceAdapter
from homemaster.tools.contracts import ToolExecutionContext, ToolExecutionResult

_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_STABLE_ID_RE = re.compile(r"^homemaster\.[a-z][a-z0-9_]*\.v[1-9][0-9]*$")


class BaseTool(ABC):
    """One model-selectable HomeMaster tool."""

    name: str
    stable_id: str
    description: str
    input_model: type[BaseModel]
    verification_required: bool = False
    requires_model_observation: bool = False
    external_terminal_owner: bool = False
    required_capabilities: tuple[str, ...] = ()
    concurrency_policy: Literal["parallel", "serialized", "resource_key"] = "parallel"
    resource_key: str | None = None
    resource_key_resolver: Callable[[Mapping[str, Any], ToolExecutionContext], str] | None = None
    physical: bool = False
    physical_adapter: PhysicalDeviceAdapter | None = None

    @abstractmethod
    async def execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        """Execute a validated invocation."""

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return False

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }

    def validate_identity(self) -> None:
        if _TOOL_NAME_RE.fullmatch(self.name) is None:
            raise ValueError(f"invalid ordinary tool name: {self.name!r}")
        expected = f"homemaster.{self.name}.v1"
        if _STABLE_ID_RE.fullmatch(self.stable_id) is None or self.stable_id != expected:
            raise ValueError(
                f"stable_id for {self.name!r} must be hidden HomeMaster metadata {expected!r}"
            )
        if not isinstance(self.input_model, type) or not issubclass(self.input_model, BaseModel):
            raise TypeError("input_model must be a Pydantic BaseModel type")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("tool description must be non-empty")
        if len(self.required_capabilities) != len(set(self.required_capabilities)):
            raise ValueError("required_capabilities must be unique")
        if any(not value or not isinstance(value, str) for value in self.required_capabilities):
            raise ValueError("required_capabilities must contain non-empty strings")
        if self.physical and self.physical_adapter is None:
            raise ValueError(
                f"physical tool {self.name!r} declares no physical_adapter; "
                "refusing to register it as an ordinary tool"
            )
        if self.physical_adapter is not None and not self.physical:
            raise ValueError(
                f"tool {self.name!r} carries a physical_adapter without declaring physical=True"
            )


ToolFunction = Callable[
    [Mapping[str, Any], ToolExecutionContext],
    ToolExecutionResult | Awaitable[ToolExecutionResult],
]


class FunctionTool(BaseTool):
    """A BaseTool backed by an ordinary async or sync Python callable."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_schema: Mapping[str, Any],
        execute: ToolFunction,
        read_only: bool | Callable[[Mapping[str, Any]], bool] = False,
        verification_required: bool = False,
        requires_model_observation: bool = False,
        external_terminal_owner: bool = False,
        required_capabilities: tuple[str, ...] = (),
        concurrency_policy: Literal["parallel", "serialized", "resource_key"] = "parallel",
        resource_key: str | None = None,
        resource_key_resolver: Callable[[Mapping[str, Any], ToolExecutionContext], str]
        | None = None,
        physical: bool = False,
        physical_adapter: PhysicalDeviceAdapter | None = None,
    ) -> None:
        if physical and physical_adapter is None:
            raise ValueError(
                f"physical tool {name!r} declares no physical_adapter; "
                "refusing to construct it as an ordinary tool"
            )
        if physical_adapter is not None and not physical:
            raise ValueError(
                f"tool {name!r} carries a physical_adapter without declaring physical=True"
            )
        self.name = name
        self.stable_id = f"homemaster.{name}.v1"
        self.description = description
        self.input_model = _schema_model(name, input_schema)
        self._execute = execute
        self._read_only = read_only
        self.verification_required = verification_required
        self.requires_model_observation = requires_model_observation
        self.external_terminal_owner = external_terminal_owner
        self.required_capabilities = tuple(required_capabilities)
        self.concurrency_policy = concurrency_policy
        self.resource_key = resource_key
        self.resource_key_resolver = resource_key_resolver
        self.physical = physical
        self.physical_adapter = physical_adapter

    async def execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        if self.physical and self.physical_adapter is None:
            raise RuntimeError(
                f"physical tool {self.name!r} has no physical_adapter; refusing execution"
            )
        raw = arguments.model_dump(mode="python")
        value = self._execute(raw, context)
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, ToolExecutionResult):
            raise TypeError(
                f"tool {self.name!r} executor must return ToolExecutionResult, "
                f"got {type(value).__name__}"
            )
        return value

    def is_read_only(self, arguments: BaseModel) -> bool:
        raw = arguments.model_dump(mode="python")
        if callable(self._read_only):
            return bool(self._read_only(raw))
        return self._read_only


def _schema_model(tool_name: str, schema: Mapping[str, Any]) -> type[BaseModel]:
    frozen_schema = _plain_json(schema)
    Draft202012Validator.check_schema(frozen_schema)
    validator = Draft202012Validator(frozen_schema)

    class ToolInput(BaseModel):
        model_config = ConfigDict(extra="allow")

        @model_validator(mode="before")
        @classmethod
        def validate_json_schema(cls, value: Any) -> Any:
            error = next(iter(validator.iter_errors(value)), None)
            if error is not None:
                location = ".".join(str(part) for part in error.absolute_path)
                suffix = f" at {location}" if location else ""
                raise ValueError(f"invalid tool arguments{suffix}: {error.message}")
            return value

        @classmethod
        def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
            del args, kwargs
            return dict(frozen_schema)

    ToolInput.__name__ = f"{''.join(part.title() for part in tool_name.split('_'))}Input"
    return ToolInput


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain_json(item) for item in value]
    return value


class ToolRegistryError(ValueError):
    """Raised when universal Registry composition is ambiguous."""


class ToolRegistry:
    """Ordered universal Registry keyed only by ordinary model-facing names."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._frozen = False

    def replace(self, tool: BaseTool) -> None:
        """Replace one already-composed tool before the registry is frozen."""
        if self._frozen:
            raise ToolRegistryError("tool registry is frozen")
        if not isinstance(tool, BaseTool):
            raise TypeError("registry entries must be BaseTool instances")
        tool.validate_identity()
        if tool.name not in self._tools:
            raise ToolRegistryError(f"cannot replace unknown tool name {tool.name!r}")
        self._tools[tool.name] = tool

    def register(self, tool: BaseTool) -> None:
        if self._frozen:
            raise ToolRegistryError("tool registry is frozen")
        if not isinstance(tool, BaseTool):
            raise TypeError("registry entries must be BaseTool instances")
        tool.validate_identity()
        existing = self._tools.get(tool.name)
        if existing is not None:
            raise ToolRegistryError(
                f"duplicate tool name {tool.name!r}: "
                f"{existing.stable_id!r} conflicts with {tool.stable_id!r}"
            )
        self._tools[tool.name] = tool

    def register_many(self, tools: list[BaseTool] | tuple[BaseTool, ...]) -> None:
        if self._frozen:
            raise ToolRegistryError("tool registry is frozen")
        staged: dict[str, BaseTool] = {}
        for tool in tools:
            if not isinstance(tool, BaseTool):
                raise TypeError("registry entries must be BaseTool instances")
            tool.validate_identity()
            existing = self._tools.get(tool.name) or staged.get(tool.name)
            if existing is not None:
                raise ToolRegistryError(
                    f"duplicate tool name {tool.name!r}: "
                    f"{existing.stable_id!r} conflicts with {tool.stable_id!r}"
                )
            staged[tool.name] = tool
        self._tools.update(staged)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def all_names(self) -> list[str]:
        return list(self._tools)

    def to_api_schema(self) -> list[dict[str, Any]]:
        return [tool.to_api_schema() for tool in self._tools.values()]

    def manifests(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.to_api_schema())

    @property
    def frozen(self) -> bool:
        return self._frozen

    def freeze(self) -> ToolRegistry:
        self._frozen = True
        return self


__all__ = [
    "BaseTool",
    "FunctionTool",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolRegistryError",
]
