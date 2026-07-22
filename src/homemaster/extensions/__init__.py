"""Explicit, deployment-approved HomeMaster extensions."""

from homemaster.extensions.contracts import (
    AggregatedHookResult,
    ExtensionApproval,
    ExtensionBuildContext,
    ExtensionContributions,
    ExtensionGeneration,
    ExtensionManifest,
    HookContext,
    HookEvent,
    HookResult,
    HookSpec,
    LoadedExtension,
)
from homemaster.extensions.hook_runner import HookRunner
from homemaster.extensions.loader import (
    ExtensionLoadError,
    dispose_extension_generation,
    extension_content_sha256,
    load_extension_generation,
    load_extension_generation_async,
    register_extension_tools_atomically,
)
from homemaster.extensions.reloader import ExtensionReloader, ReloadResult, ReloadStatus

__all__ = [
    "AggregatedHookResult",
    "ExtensionApproval",
    "ExtensionBuildContext",
    "ExtensionContributions",
    "ExtensionGeneration",
    "ExtensionLoadError",
    "ExtensionManifest",
    "ExtensionReloader",
    "HookContext",
    "HookEvent",
    "HookResult",
    "HookRunner",
    "HookSpec",
    "LoadedExtension",
    "ReloadResult",
    "ReloadStatus",
    "dispose_extension_generation",
    "extension_content_sha256",
    "load_extension_generation",
    "load_extension_generation_async",
    "register_extension_tools_atomically",
]
