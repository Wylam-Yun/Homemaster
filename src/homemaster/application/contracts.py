"""Typed contracts shared by application entry points.

The contracts in this module intentionally contain references to run-scoped
dependencies, but never own those dependencies.  In particular, an
``EnvironmentBackend`` supplied to a request is borrowed from the benchmark
or deployment controller and must not be closed by the application runtime.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, TypeVar, runtime_checkable

from homemaster.agent.messages import ToolCall
from homemaster.tools.contracts import ToolExecutionResult


class RunStatus(StrEnum):
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    REPLIED = "replied"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResourceOwnership(StrEnum):
    OWNED = "owned"
    BORROWED = "borrowed"


class ResourceLifetime(StrEnum):
    APPLICATION = "application"
    TENANT = "tenant"
    SESSION = "session"
    RUN = "run"


@runtime_checkable
class EnvironmentBackend(Protocol):
    """Minimal marker protocol for a backend borrowed for one run.

    Concrete environments may expose additional operations.  The application
    layer deliberately does not require ``close`` or ``reset`` here.
    """

    @property
    def backend_id(self) -> str: ...


@runtime_checkable
class TerminalPolicy(Protocol):
    """Terminal gate consulted before a tool backend is invoked."""

    def before_execute(
        self,
        tool_call: ToolCall,
        context: Any,
    ) -> ToolExecutionResult | None | Awaitable[ToolExecutionResult | None]: ...


StopCondition = Callable[[Any], bool | Awaitable[bool]]
T = TypeVar("T")


@dataclass(frozen=True)
class RunPolicy:
    """Termination and budget rules for one run.

    The callable is intentionally opaque to the application contract.  Domain
    scorers and benchmark completion rules stay outside generic runtime code.
    """

    max_turns: int = 12
    max_tool_iterations: int | None = 12
    deadline_s: float | None = None
    stop_condition: StopCondition | None = None

    def __post_init__(self) -> None:
        if isinstance(self.max_turns, bool) or self.max_turns < 1:
            raise ValueError("max_turns must be a positive integer")
        if self.max_tool_iterations is not None and (
            isinstance(self.max_tool_iterations, bool) or self.max_tool_iterations < 1
        ):
            raise ValueError("max_tool_iterations must be positive or None")
        if self.deadline_s is not None and (
            isinstance(self.deadline_s, bool)
            or not math.isfinite(self.deadline_s)
            or self.deadline_s <= 0
        ):
            raise ValueError("deadline_s must be a finite positive number or None")
        if self.stop_condition is not None and not callable(self.stop_condition):
            raise TypeError("stop_condition must be callable or None")


@dataclass(frozen=True)
class ResourceBinding:
    """A resource reference plus the owner allowed to release it."""

    name: str
    resource: Any
    ownership: ResourceOwnership
    lifetime: ResourceLifetime = ResourceLifetime.RUN
    release: Callable[[Any], Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("resource binding name must be non-empty")
        if not isinstance(self.ownership, ResourceOwnership):
            raise TypeError("resource binding ownership must be ResourceOwnership")
        if not isinstance(self.lifetime, ResourceLifetime):
            raise TypeError("resource binding lifetime must be ResourceLifetime")
        if self.release is not None and not callable(self.release):
            raise TypeError("resource binding release must be callable or None")

    @classmethod
    def owned(
        cls,
        name: str,
        resource: Any,
        *,
        lifetime: ResourceLifetime = ResourceLifetime.RUN,
        release: Callable[[Any], Any] | None = None,
    ) -> ResourceBinding:
        return cls(name, resource, ResourceOwnership.OWNED, lifetime, release)

    @classmethod
    def borrowed(
        cls,
        name: str,
        resource: Any,
        *,
        lifetime: ResourceLifetime = ResourceLifetime.RUN,
    ) -> ResourceBinding:
        return cls(name, resource, ResourceOwnership.BORROWED, lifetime)


@dataclass(frozen=True)
class RunRequest:
    """Immutable input submitted by CLI, gateway, or benchmark callers."""

    text: str
    session_id: str | None = None
    environment: EnvironmentBackend | ResourceBinding | None = None
    enabled_tool_ids: tuple[str, ...] = ()
    run_policy: RunPolicy = field(default_factory=RunPolicy)
    terminal_policy: TerminalPolicy | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("run text must be non-empty")
        if self.session_id is not None and (
            not isinstance(self.session_id, str) or not self.session_id.strip()
        ):
            raise ValueError("session_id must be a non-empty string or None")
        if not isinstance(self.run_policy, RunPolicy):
            raise TypeError("run_policy must be RunPolicy")
        if self.terminal_policy is not None and not (
            callable(getattr(self.terminal_policy, "before_execute", None))
            or callable(getattr(self.terminal_policy, "check", None))
        ):
            raise TypeError("terminal_policy must provide before_execute() or check()")
        ids = _freeze_tool_ids(self.enabled_tool_ids)
        object.__setattr__(self, "enabled_tool_ids", ids)
        metadata = _freeze_json_mapping(self.metadata)
        object.__setattr__(self, "metadata", metadata)
        if isinstance(self.environment, ResourceBinding):
            if self.environment.ownership is not ResourceOwnership.BORROWED:
                raise ValueError("run environment must be borrowed")
        elif self.environment is not None and callable(getattr(self.environment, "close", None)):
            # A plain backend is still accepted for compatibility, but it is
            # always treated as borrowed.  The application never discovers or
            # invokes this close method.
            pass

    @property
    def borrowed_environment(self) -> Any | None:
        if isinstance(self.environment, ResourceBinding):
            return self.environment.resource
        return self.environment

    @property
    def environment_binding(self) -> ResourceBinding | None:
        if self.environment is None:
            return None
        if isinstance(self.environment, ResourceBinding):
            return self.environment
        backend_id = getattr(self.environment, "backend_id", type(self.environment).__name__)
        return ResourceBinding.borrowed(str(backend_id), self.environment)

    def metadata_dict(self) -> dict[str, object]:
        return _thaw_json(self.metadata)


@dataclass(frozen=True)
class RunResult:
    """Immutable result envelope shared by every application entry point."""

    run_id: str
    session_id: str
    status: RunStatus | str
    final_reply: str = ""
    error_code: str | None = None
    events: tuple[Any, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (("run_id", self.run_id), ("session_id", self.session_id)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.status, (RunStatus, str)) or not str(self.status).strip():
            raise ValueError("status must be a non-empty string")
        if not isinstance(self.final_reply, str):
            raise TypeError("final_reply must be a string")
        if self.error_code is not None and (
            not isinstance(self.error_code, str) or not self.error_code.strip()
        ):
            raise ValueError("error_code must be a non-empty string or None")
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "metadata", _freeze_json_mapping(self.metadata))

    def metadata_dict(self) -> dict[str, object]:
        return _thaw_json(self.metadata)


def _freeze_tool_ids(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError("enabled_tool_ids must be a sequence of strings")
    ids = tuple(value)
    if any(not isinstance(item, str) or not item.strip() for item in ids):
        raise ValueError("enabled_tool_ids must contain non-empty strings")
    if len(ids) != len(set(ids)):
        raise ValueError("enabled_tool_ids must be unique")
    return ids


def _freeze_json_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("metadata must be a mapping")
    if any(not isinstance(key, str) or not key for key in value):
        raise ValueError("metadata keys must be non-empty strings")
    return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})


def _freeze_json(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("metadata numbers must be finite")
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError(f"metadata contains unsupported value: {type(value).__name__}")


def _thaw_json(value: object) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


__all__ = [
    "EnvironmentBackend",
    "ResourceBinding",
    "ResourceLifetime",
    "ResourceOwnership",
    "RunPolicy",
    "RunRequest",
    "RunResult",
    "RunStatus",
    "TerminalPolicy",
]
