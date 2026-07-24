"""Small, ordinary-name tool contract used by every HomeMaster runtime."""

from __future__ import annotations

import inspect
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, model_validator

if TYPE_CHECKING:
    from homemaster.extensions.hook_runner import HookRunner

_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_STABLE_ID_RE = re.compile(r"^homemaster\.[a-z][a-z0-9_]*\.v[1-9][0-9]*$")


@dataclass
class ToolExecutionContext:
    """Execution resources shared by all tools.

    Application-only state lives in metadata so the tool API remains independent
    from profiles, tenants, backends, and Gateway transports.
    """

    cwd: Path
    metadata: dict[str, Any] = field(default_factory=dict)
    hook_executor: HookRunner | None = None

    def __post_init__(self) -> None:
        resolved = self.cwd.expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError("tool cwd must be an existing directory")
        self.cwd = resolved
        self.metadata = dict(self.metadata)

    @property
    def working_directory(self) -> Path:
        """Compatibility spelling used by existing HomeMaster tool bodies."""

        return self.cwd

    @property
    def services(self) -> dict[str, Any]:
        value = self.metadata.get("services", self.metadata)
        return value if isinstance(value, dict) else self.metadata

    @property
    def backend(self) -> object | None:
        return self.metadata.get("backend")

    @property
    def tool_registry(self) -> ToolRegistry | None:
        value = self.metadata.get("tool_registry")
        return value if isinstance(value, ToolRegistry) else None

    def __getattr__(self, name: str) -> Any:
        try:
            return self.metadata[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


@dataclass(frozen=True)
class ToolResult:
    """The complete model-facing tool result plus small machine metadata."""

    output: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.output, str):
            raise TypeError("tool result output must be a string")
        if not isinstance(self.is_error, bool):
            raise TypeError("tool result is_error must be a boolean")
        object.__setattr__(self, "metadata", dict(self.metadata))


class BaseTool(ABC):
    """One model-selectable HomeMaster tool."""

    name: str
    stable_id: str
    description: str
    input_model: type[BaseModel]
    verification_required: bool = False
    external_terminal_owner: bool = False
    required_capabilities: tuple[str, ...] = ()
    concurrency_policy: Literal["parallel", "serialized", "resource_key"] = "parallel"
    resource_key: str | None = None
    resource_key_resolver: Callable[[Mapping[str, Any], ToolExecutionContext], str] | None = None

    @abstractmethod
    async def execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolResult:
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


ToolFunction = Callable[
    [Mapping[str, Any], ToolExecutionContext],
    ToolResult | Any | Awaitable[ToolResult | Any],
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
        external_terminal_owner: bool = False,
        required_capabilities: tuple[str, ...] = (),
        concurrency_policy: Literal["parallel", "serialized", "resource_key"] = "parallel",
        resource_key: str | None = None,
        resource_key_resolver: Callable[[Mapping[str, Any], ToolExecutionContext], str]
        | None = None,
    ) -> None:
        self.name = name
        self.stable_id = f"homemaster.{name}.v1"
        self.description = description
        self.input_model = _schema_model(name, input_schema)
        self._execute = execute
        self._read_only = read_only
        self.verification_required = verification_required
        self.external_terminal_owner = external_terminal_owner
        self.required_capabilities = tuple(required_capabilities)
        self.concurrency_policy = concurrency_policy
        self.resource_key = resource_key
        self.resource_key_resolver = resource_key_resolver

    async def execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolResult:
        raw = arguments.model_dump(mode="python")
        value = self._execute(raw, context)
        if inspect.isawaitable(value):
            value = await value
        return normalize_tool_result(value)

    def is_read_only(self, arguments: BaseModel) -> bool:
        raw = arguments.model_dump(mode="python")
        if callable(self._read_only):
            return bool(self._read_only(raw))
        return self._read_only


def normalize_tool_result(value: Any) -> ToolResult:
    """Collapse pre-migration HomeMaster result values into the small contract."""

    if isinstance(value, ToolResult):
        return value
    if isinstance(value, str):
        return ToolResult(value)
    if isinstance(value, Mapping):
        return ToolResult(json.dumps(dict(value), ensure_ascii=False, sort_keys=True))

    content = getattr(value, "content", None)
    if isinstance(content, list):
        output = "\n".join(
            str(getattr(block, "text", "")) for block in content if getattr(block, "text", "")
        )
        metadata = _plain_json(dict(getattr(value, "data", None) or {}))
        return ToolResult(output, bool(getattr(value, "is_error", False)), metadata)

    data = _plain_json(dict(getattr(value, "data", None) or {}))
    images = getattr(value, "images", ())
    attachments = getattr(value, "attachments", ())
    evidence_refs = getattr(value, "evidence_refs", ())
    verification = getattr(value, "verification", None)
    if images:
        data["images"] = [
            item.to_dict() if callable(getattr(item, "to_dict", None)) else dict(item)
            for item in images
        ]
    if attachments:
        data["attachments"] = [
            item.to_dict() if callable(getattr(item, "to_dict", None)) else dict(item)
            for item in attachments
        ]
    verification_refs = tuple(getattr(verification, "evidence_refs", ()))
    combined_refs = tuple(dict.fromkeys((*evidence_refs, *verification_refs)))
    if combined_refs:
        data["evidence_refs"] = list(combined_refs)
    verification_status = getattr(verification, "status", None)
    if verification_status is not None:
        status_value = str(getattr(verification_status, "value", verification_status))
        if status_value != "not_requested":
            data["verification_status"] = status_value
            detail = getattr(verification, "detail", None)
            if detail is not None:
                data["verification_detail"] = str(detail)
    text = getattr(value, "text", None)
    summary = getattr(value, "summary", None)
    error = getattr(value, "error", None)
    failure_reason = getattr(value, "failure_reason", None)
    success = getattr(value, "success", None)
    status = getattr(value, "status", None)
    is_error = bool(success is False)
    backend_attempted = getattr(value, "backend_attempted", None)
    if backend_attempted is not None:
        data.setdefault("backend_attempted", bool(backend_attempted))
    if status is not None:
        status_value = str(getattr(status, "value", status))
        existing_status = data.get("status")
        if existing_status is not None and existing_status != status_value:
            data.setdefault("domain_status", existing_status)
        data["status"] = status_value
        is_error = status_value not in {"success", "ok"}
    if error is not None:
        is_error = True
        data.setdefault("error_code", getattr(error, "code", "tool_error"))
        failure_reason = getattr(error, "message", None) or failure_reason
    content_text = data.get("content")
    output = str(
        text
        or summary
        or failure_reason
        or (content_text if isinstance(content_text, str) else "")
    )
    if not output and data:
        renderable = {
            key: item
            for key, item in data.items()
            if key not in {"images", "attachments", "status", "backend_attempted"}
        }
        if renderable:
            output = json.dumps(renderable, ensure_ascii=False, sort_keys=True)
    return ToolResult(output, is_error, data)


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

    def register(self, tool: BaseTool) -> None:
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


__all__ = [
    "BaseTool",
    "FunctionTool",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolRegistryError",
    "ToolResult",
    "normalize_tool_result",
]
