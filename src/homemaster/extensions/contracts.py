"""Typed contracts for trusted, deployment-approved extensions."""

from __future__ import annotations

import inspect
import math
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from homemaster.tools.contracts import RegisteredTool

_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*$")
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_.:-]*$")


class HookEvent(StrEnum):
    APPLICATION_START = "application_start"
    RUN_START = "run_start"
    RUN_END = "run_end"
    APPLICATION_STOP = "application_stop"


@dataclass(frozen=True)
class ExtensionManifest:
    schema_version: int
    extension_id: str
    version: str
    requested_capabilities: tuple[str, ...]
    entrypoint: str
    dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("extension manifest schema_version must be 1")
        if _ID_RE.fullmatch(self.extension_id) is None:
            raise ValueError("extension_id must be a stable lowercase id")
        if _SEMVER_RE.fullmatch(self.version) is None:
            raise ValueError("extension version must be semantic")
        capabilities = _capabilities(self.requested_capabilities, "requested capabilities")
        object.__setattr__(self, "requested_capabilities", capabilities)
        path = Path(self.entrypoint)
        if (
            not self.entrypoint.strip()
            or path.is_absolute()
            or ".." in path.parts
            or path.suffix != ".py"
        ):
            raise ValueError("extension entrypoint must be a contained Python file")
        if isinstance(self.dependencies, str) or not isinstance(self.dependencies, (list, tuple)):
            raise TypeError("extension dependencies must be a sequence")
        dependencies = tuple(self.dependencies)
        if len(dependencies) != len(set(dependencies)):
            raise ValueError("extension dependencies must be unique")
        for dependency in dependencies:
            dependency_path = Path(dependency)
            if (
                not isinstance(dependency, str)
                or not dependency.strip()
                or dependency_path.is_absolute()
                or len(dependency_path.parts) != 1
                or dependency_path.suffix != ".py"
                or not dependency_path.stem.isidentifier()
                or dependency_path.name == "__init__.py"
                or dependency == self.entrypoint
            ):
                raise ValueError(
                    "extension dependencies must be unique flat Python files "
                    "distinct from entrypoint"
                )
        object.__setattr__(self, "dependencies", dependencies)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ExtensionManifest:
        required = {
            "schema_version",
            "extension_id",
            "version",
            "requested_capabilities",
            "entrypoint",
        }
        if frozenset(value) not in {
            frozenset(required),
            frozenset((*required, "dependencies")),
        }:
            raise ValueError("extension manifest fields do not match schema version 1")
        requested = value["requested_capabilities"]
        if isinstance(requested, str) or not isinstance(requested, (list, tuple)):
            raise TypeError("requested_capabilities must be a sequence")
        dependencies = value.get("dependencies", ())
        if isinstance(dependencies, str) or not isinstance(dependencies, (list, tuple)):
            raise TypeError("dependencies must be a sequence")
        return cls(
            schema_version=value["schema_version"],  # type: ignore[arg-type]
            extension_id=value["extension_id"],  # type: ignore[arg-type]
            version=value["version"],  # type: ignore[arg-type]
            requested_capabilities=tuple(requested),  # type: ignore[arg-type]
            entrypoint=value["entrypoint"],  # type: ignore[arg-type]
            dependencies=tuple(dependencies),  # type: ignore[arg-type]
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "extension_id": self.extension_id,
            "version": self.version,
            "requested_capabilities": list(self.requested_capabilities),
            "entrypoint": self.entrypoint,
        }
        if self.dependencies:
            payload["dependencies"] = list(self.dependencies)
        return payload


@dataclass(frozen=True)
class ExtensionApproval:
    manifest_path: Path
    extension_id: str
    version: str
    expected_sha256: str
    granted_capabilities: tuple[str, ...] = ()
    enabled_tool_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        path = Path(self.manifest_path).expanduser()
        if path.is_symlink():
            raise ValueError("approved extension manifest cannot be a symlink")
        object.__setattr__(self, "manifest_path", path)
        if _ID_RE.fullmatch(self.extension_id) is None:
            raise ValueError("approved extension_id must be stable")
        if _SEMVER_RE.fullmatch(self.version) is None:
            raise ValueError("approved extension version must be semantic")
        if _SHA256_RE.fullmatch(self.expected_sha256) is None:
            raise ValueError("approved extension SHA-256 is invalid")
        object.__setattr__(
            self,
            "granted_capabilities",
            _capabilities(self.granted_capabilities, "granted capabilities"),
        )
        tool_ids = tuple(self.enabled_tool_ids)
        if len(tool_ids) != len(set(tool_ids)) or any(
            not isinstance(value, str) or not value for value in tool_ids
        ):
            raise ValueError("enabled extension tool ids must be unique non-empty strings")
        object.__setattr__(self, "enabled_tool_ids", tool_ids)


@dataclass(frozen=True)
class ExtensionBuildContext:
    extension_id: str
    version: str
    content_sha256: str
    provenance_reference: str
    granted_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class HookContext:
    event: HookEvent
    extension_id: str
    generation: int
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.event, HookEvent):
            raise TypeError("hook event must be HookEvent")
        if self.generation < 0:
            raise ValueError("hook generation must be non-negative")
        object.__setattr__(self, "payload", _freeze_mapping(self.payload))


HookCallback = Callable[[HookContext], Awaitable[object]]
ExtensionCleanup = Callable[[], Awaitable[object]]


@dataclass(frozen=True)
class HookSpec:
    extension_id: str
    hook_id: str
    event: HookEvent
    callback: HookCallback
    required_capability: str
    priority: int = 0
    timeout_s: float = 30.0
    matcher: str | None = None
    block_on_failure: bool = False

    def __post_init__(self) -> None:
        if _ID_RE.fullmatch(self.extension_id) is None:
            raise ValueError("hook extension_id must be stable")
        if _ID_RE.fullmatch(self.hook_id) is None:
            raise ValueError("hook_id must be stable")
        if not isinstance(self.event, HookEvent):
            raise TypeError("hook event must be HookEvent")
        if not inspect.iscoroutinefunction(self.callback):
            raise TypeError("extension hook callbacks must be async functions")
        _require_capability(self.required_capability, "hook required capability")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TypeError("hook priority must be an integer")
        if (
            isinstance(self.timeout_s, bool)
            or not isinstance(self.timeout_s, (int, float))
            or not math.isfinite(self.timeout_s)
            or self.timeout_s <= 0
        ):
            raise ValueError("hook timeout must be a finite positive number")
        if self.matcher is not None and (
            not isinstance(self.matcher, str) or not self.matcher.strip()
        ):
            raise ValueError("hook matcher must be non-empty")


@dataclass(frozen=True)
class HookResult:
    extension_id: str
    hook_id: str
    success: bool
    blocked: bool = False
    output: str = ""
    reason: str = ""
    timed_out: bool = False
    stale_generation: bool = False


@dataclass(frozen=True)
class AggregatedHookResult:
    results: tuple[HookResult, ...] = ()

    @property
    def blocked(self) -> bool:
        return any(result.blocked for result in self.results)

    @property
    def reason(self) -> str:
        return next(
            (result.reason or result.output for result in self.results if result.blocked),
            "",
        )


@dataclass(frozen=True)
class ExtensionContributions:
    hooks: tuple[HookSpec, ...] = ()
    tools: tuple[RegisteredTool, ...] = ()
    cleanup: ExtensionCleanup | None = None

    def __post_init__(self) -> None:
        hooks = tuple(self.hooks)
        tools = tuple(self.tools)
        if any(not isinstance(hook, HookSpec) for hook in hooks):
            raise TypeError("extension hooks must be HookSpec values")
        if any(not isinstance(tool, RegisteredTool) for tool in tools):
            raise TypeError("extension tools must be RegisteredTool values")
        if self.cleanup is not None and not inspect.iscoroutinefunction(self.cleanup):
            raise TypeError("extension cleanup must be an async function")
        object.__setattr__(self, "hooks", hooks)
        object.__setattr__(self, "tools", tools)


@dataclass(frozen=True)
class LoadedExtension:
    manifest: ExtensionManifest
    root: Path
    content_sha256: str
    granted_capabilities: tuple[str, ...]
    enabled_tool_ids: tuple[str, ...]
    contributions: ExtensionContributions

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, ExtensionManifest):
            raise TypeError("loaded extension manifest must be ExtensionManifest")
        if _SHA256_RE.fullmatch(self.content_sha256) is None:
            raise ValueError("loaded extension content SHA-256 is invalid")
        object.__setattr__(self, "granted_capabilities", tuple(self.granted_capabilities))
        object.__setattr__(self, "enabled_tool_ids", tuple(self.enabled_tool_ids))


@dataclass(frozen=True)
class ExtensionGeneration:
    generation: int
    extensions: tuple[LoadedExtension, ...]
    hooks: tuple[HookSpec, ...]
    tools: tuple[RegisteredTool, ...]
    enabled_tool_ids: tuple[str, ...]
    tool_plane_digest: str
    diagnostics: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.generation < 0:
            raise ValueError("extension generation must be non-negative")
        if _SHA256_RE.fullmatch(self.tool_plane_digest) is None:
            raise ValueError("extension tool-plane digest is invalid")
        object.__setattr__(self, "extensions", tuple(self.extensions))
        object.__setattr__(self, "hooks", tuple(self.hooks))
        object.__setattr__(self, "tools", tuple(self.tools))
        object.__setattr__(self, "enabled_tool_ids", tuple(self.enabled_tool_ids))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))


def _capabilities(values: object, label: str) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, (list, tuple)):
        raise TypeError(f"{label} must be a sequence")
    result = tuple(values)
    if len(result) != len(set(result)):
        raise ValueError(f"{label} must be unique")
    for value in result:
        _require_capability(value, label)
    return result


def _require_capability(value: object, label: str) -> None:
    if not isinstance(value, str) or _CAPABILITY_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must contain stable capability tokens")


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("hook payload must be a mapping")
    return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})


def _freeze_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return repr(value)


__all__ = [
    "AggregatedHookResult",
    "ExtensionApproval",
    "ExtensionBuildContext",
    "ExtensionCleanup",
    "ExtensionContributions",
    "ExtensionGeneration",
    "ExtensionManifest",
    "HookCallback",
    "HookContext",
    "HookEvent",
    "HookResult",
    "HookSpec",
    "LoadedExtension",
]
