"""Verified same-bytes loading for explicitly approved local extensions."""

from __future__ import annotations

import asyncio
import builtins
import hashlib
import inspect
import json
import os
import stat
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol

from homemaster.events.public_projection import PublicEventProjection
from homemaster.extensions.contracts import (
    ExtensionApproval,
    ExtensionBuildContext,
    ExtensionContributions,
    ExtensionGeneration,
    ExtensionManifest,
    HookSpec,
    LoadedExtension,
)
from homemaster.tools.catalog import ToolCatalog, ToolCatalogError
from homemaster.tools.contracts import ExecutionBackend, RegisteredTool

_FACTORY_NAME = "build_extension"
_DIAGNOSTIC_SANITIZER = PublicEventProjection()


class _Digest(Protocol):
    def update(self, value: bytes) -> None: ...


class ExtensionLoadError(RuntimeError):
    """An approved extension failed closed before application mutation."""

    def __init__(self, message: str, *, diagnostics: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.diagnostics = tuple(diagnostics) or (message,)


class _UnownedContributionsError(BaseException):
    def __init__(
        self,
        extension_id: str,
        contributions: ExtensionContributions,
        cause: BaseException,
    ) -> None:
        self.extension_id = extension_id
        self.contributions = contributions
        self.cause = cause
        super().__init__(str(cause))


def extension_content_sha256(manifest_path: Path) -> str:
    """Return the canonical host digest for one manifest and all declared sources."""

    (
        manifest,
        canonical_manifest,
        _entrypoint_path,
        entrypoint_bytes,
        dependency_bytes,
        _local_modules,
    ) = _read_source(Path(manifest_path))
    return _content_digest(
        canonical_manifest,
        ((manifest.entrypoint, entrypoint_bytes), *dependency_bytes),
    )


def load_extension_generation(
    approvals: Sequence[ExtensionApproval],
    *,
    generation: int = 1,
) -> ExtensionGeneration:
    """Build a complete immutable candidate without mutating a ToolCatalog."""

    if generation < 0:
        raise ValueError("extension generation must be non-negative")
    loaded: list[LoadedExtension] = []
    seen_ids: set[str] = set()
    diagnostics: list[str] = []
    try:
        for approval in approvals:
            if not isinstance(approval, ExtensionApproval):
                raise TypeError("extension approvals must be ExtensionApproval values")
            if approval.extension_id in seen_ids:
                diagnostics.append(f"duplicate approved extension id: {approval.extension_id}")
                continue
            seen_ids.add(approval.extension_id)
            try:
                loaded.append(_load_one(approval))
            except _UnownedContributionsError as exc:
                diagnostics.extend(
                    _dispose_contributions_sync(((exc.extension_id, exc.contributions),))
                )
                if not isinstance(exc.cause, Exception):
                    raise exc.cause from None
                diagnostics.append(_load_diagnostic(approval.extension_id, exc.cause))
            except Exception as exc:
                diagnostics.append(_load_diagnostic(approval.extension_id, exc))
    except BaseException:
        _dispose_loaded_sync(loaded)
        raise
    if diagnostics:
        _dispose_loaded_sync(loaded)
        raise ExtensionLoadError(
            "one or more approved extensions failed validation",
            diagnostics=diagnostics,
        )

    hooks = tuple(hook for item in loaded for hook in item.contributions.hooks)
    tools = tuple(tool for item in loaded for tool in item.contributions.tools)
    enabled_tool_ids = tuple(tool_id for item in loaded for tool_id in item.enabled_tool_ids)
    try:
        _validate_cross_extension_uniqueness(tools, hooks, enabled_tool_ids)
    except BaseException:
        _dispose_loaded_sync(loaded)
        raise
    return ExtensionGeneration(
        generation=generation,
        extensions=tuple(loaded),
        hooks=hooks,
        tools=tools,
        enabled_tool_ids=enabled_tool_ids,
        tool_plane_digest=_tool_plane_digest(tools, enabled_tool_ids),
    )


async def load_extension_generation_async(
    approvals: Sequence[ExtensionApproval],
    *,
    generation: int = 1,
) -> ExtensionGeneration:
    """Build a candidate and await every rollback before reporting failure."""

    if generation < 0:
        raise ValueError("extension generation must be non-negative")
    loaded: list[LoadedExtension] = []
    seen_ids: set[str] = set()
    diagnostics: list[str] = []
    try:
        for approval in approvals:
            if not isinstance(approval, ExtensionApproval):
                raise TypeError("extension approvals must be ExtensionApproval values")
            if approval.extension_id in seen_ids:
                diagnostics.append(f"duplicate approved extension id: {approval.extension_id}")
                continue
            seen_ids.add(approval.extension_id)
            try:
                loaded.append(_load_one(approval))
            except _UnownedContributionsError as exc:
                diagnostics.extend(
                    await _dispose_contributions_async(((exc.extension_id, exc.contributions),))
                )
                if not isinstance(exc.cause, Exception):
                    raise exc.cause from None
                diagnostics.append(_load_diagnostic(approval.extension_id, exc.cause))
            except Exception as exc:
                diagnostics.append(_load_diagnostic(approval.extension_id, exc))
    except BaseException:
        await _dispose_loaded_async(loaded)
        raise
    if diagnostics:
        diagnostics.extend(await _dispose_loaded_async(loaded))
        raise ExtensionLoadError(
            "one or more approved extensions failed validation",
            diagnostics=diagnostics,
        )

    hooks = tuple(hook for item in loaded for hook in item.contributions.hooks)
    tools = tuple(tool for item in loaded for tool in item.contributions.tools)
    enabled_tool_ids = tuple(tool_id for item in loaded for tool_id in item.enabled_tool_ids)
    try:
        _validate_cross_extension_uniqueness(tools, hooks, enabled_tool_ids)
    except BaseException:
        await _dispose_loaded_async(loaded)
        raise
    return ExtensionGeneration(
        generation=generation,
        extensions=tuple(loaded),
        hooks=hooks,
        tools=tools,
        enabled_tool_ids=enabled_tool_ids,
        tool_plane_digest=_tool_plane_digest(tools, enabled_tool_ids),
    )


def register_extension_tools_atomically(
    catalog: ToolCatalog,
    generation: ExtensionGeneration,
) -> tuple[str, ...]:
    """Register a fully validated generation after checking every collision."""

    existing = catalog.list_tools()
    existing_ids = {tool.definition.internal_id for tool in existing}
    existing_aliases = {tool.definition.model_alias for tool in existing}
    staged_ids: set[str] = set()
    staged_aliases: set[str] = set()
    for tool in generation.tools:
        definition = tool.definition
        if definition.internal_id in existing_ids or definition.internal_id in staged_ids:
            raise ToolCatalogError(f"extension internal id conflict: {definition.internal_id}")
        if definition.model_alias in existing_aliases or definition.model_alias in staged_aliases:
            raise ToolCatalogError(f"extension model alias conflict: {definition.model_alias}")
        staged_ids.add(definition.internal_id)
        staged_aliases.add(definition.model_alias)
    for tool in generation.tools:
        catalog.register(tool)
    return generation.enabled_tool_ids


def _load_one(approval: ExtensionApproval) -> LoadedExtension:
    (
        manifest,
        canonical_manifest,
        entrypoint_path,
        entrypoint_bytes,
        dependency_bytes,
        local_modules,
    ) = _read_source(approval.manifest_path)
    if manifest.extension_id != approval.extension_id:
        raise ValueError("manifest extension_id does not match approval")
    if manifest.version != approval.version:
        raise ValueError("manifest version does not match approval")
    digest = _content_digest(
        canonical_manifest,
        ((manifest.entrypoint, entrypoint_bytes), *dependency_bytes),
    )
    if digest != approval.expected_sha256:
        raise ValueError("extension content SHA-256 does not match approval")
    requested = set(manifest.requested_capabilities)
    granted = set(approval.granted_capabilities)
    undeclared_grants = sorted(granted - requested)
    if undeclared_grants:
        raise ValueError(f"approval grants undeclared capabilities: {undeclared_grants}")
    provenance_reference = f"extension:{manifest.extension_id}@{manifest.version}#sha256:{digest}"
    context = ExtensionBuildContext(
        extension_id=manifest.extension_id,
        version=manifest.version,
        content_sha256=digest,
        provenance_reference=provenance_reference,
        granted_capabilities=approval.granted_capabilities,
    )
    contributions = _execute_verified_bytes(
        entrypoint_path,
        entrypoint_bytes,
        context,
        dependencies=dependency_bytes,
        local_modules=local_modules,
    )
    try:
        _validate_contributions(
            manifest,
            approval,
            provenance_reference,
            contributions,
        )
    except BaseException as exc:
        raise _UnownedContributionsError(manifest.extension_id, contributions, exc) from exc
    return LoadedExtension(
        manifest=manifest,
        root=approval.manifest_path.resolve(strict=True).parent,
        content_sha256=digest,
        granted_capabilities=approval.granted_capabilities,
        enabled_tool_ids=approval.enabled_tool_ids,
        contributions=contributions,
    )


def _read_source(
    path: Path,
) -> tuple[
    ExtensionManifest,
    bytes,
    Path,
    bytes,
    tuple[tuple[str, bytes], ...],
    frozenset[str],
]:
    manifest_path = path.expanduser()
    root_path = manifest_path.parent
    directory_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    try:
        root_descriptor = os.open(root_path, directory_flags)
    except OSError as exc:
        raise ValueError(f"cannot securely open approved directory: {root_path}") from exc
    try:
        manifest_bytes = _read_regular_file_nofollow(
            Path(manifest_path.name),
            root_descriptor=root_descriptor,
        )
        try:
            raw_manifest = json.loads(manifest_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("extension manifest must be valid UTF-8 JSON") from exc
        if not isinstance(raw_manifest, dict):
            raise TypeError("extension manifest must be a JSON object")
        manifest = ExtensionManifest.from_mapping(raw_manifest)
        canonical_manifest = json.dumps(
            manifest.to_dict(),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        entrypoint_path = root_path / manifest.entrypoint
        entrypoint_bytes = _read_regular_file_nofollow(
            Path(manifest.entrypoint),
            root_descriptor=root_descriptor,
        )
        dependencies = tuple(
            (
                dependency,
                _read_regular_file_nofollow(
                    Path(dependency),
                    root_descriptor=root_descriptor,
                ),
            )
            for dependency in manifest.dependencies
        )
        local_modules = _local_python_modules(root_descriptor)
        return (
            manifest,
            canonical_manifest,
            entrypoint_path,
            entrypoint_bytes,
            dependencies,
            local_modules,
        )
    finally:
        os.close(root_descriptor)


def _local_python_modules(root_descriptor: int) -> frozenset[str]:
    modules: set[str] = set()
    for name in os.listdir(root_descriptor):
        path = Path(name)
        if path.suffix == ".py" and path.stem.isidentifier():
            modules.add(path.stem)
        elif name.isidentifier():
            metadata = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                modules.add(name)
    return frozenset(modules)


def _read_regular_file_nofollow(
    path: Path,
    *,
    root_descriptor: int | None = None,
) -> bytes:
    parts = Path(path).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"cannot securely open approved file: {path}")
    owned_root = root_descriptor is None
    if owned_root:
        directory_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
        root_descriptor = os.open(Path(path).parent, directory_flags)
        parts = (Path(path).name,)
    assert root_descriptor is not None
    directory_descriptor = os.dup(root_descriptor)
    try:
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        for component in parts[:-1]:
            try:
                next_descriptor = os.open(
                    component,
                    directory_flags,
                    dir_fd=directory_descriptor,
                )
            except OSError as exc:
                raise ValueError(f"cannot securely open approved file: {path}") from exc
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(parts[-1], flags, dir_fd=directory_descriptor)
        except OSError as exc:
            raise ValueError(f"cannot securely open approved file: {path}") from exc
    finally:
        os.close(directory_descriptor)
        if owned_root:
            os.close(root_descriptor)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"approved path is not a regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def dispose_extension_generation(generation: ExtensionGeneration) -> tuple[str, ...]:
    """Release a candidate that failed before ownership reached HookRunner."""

    return _dispose_loaded_sync(list(generation.extensions))


def _dispose_loaded_sync(loaded: list[LoadedExtension]) -> tuple[str, ...]:
    contributions = tuple(
        (extension.manifest.extension_id, extension.contributions) for extension in reversed(loaded)
    )
    return _dispose_contributions_sync(contributions)


async def _dispose_loaded_async(loaded: list[LoadedExtension]) -> tuple[str, ...]:
    contributions = tuple(
        (extension.manifest.extension_id, extension.contributions) for extension in reversed(loaded)
    )
    return await _dispose_contributions_async(contributions)


def _dispose_contributions_sync(
    contributions: Sequence[tuple[str, ExtensionContributions]],
) -> tuple[str, ...]:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_dispose_contributions_async(contributions))
    task = loop.create_task(_dispose_contributions_async(contributions))
    task.add_done_callback(_consume_disposal_result)
    return ()


async def _dispose_contributions_async(
    contributions: Sequence[tuple[str, ExtensionContributions]],
) -> tuple[str, ...]:
    diagnostics: list[str] = []
    for extension_id, contribution in contributions:
        cleanup = contribution.cleanup
        if cleanup is None:
            continue
        try:
            await cleanup()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            diagnostics.append(
                _DIAGNOSTIC_SANITIZER.sanitize_content(
                    f"{extension_id}: cleanup {type(exc).__name__}: {exc}"
                )[:4000]
            )
    return tuple(diagnostics)


def _load_diagnostic(extension_id: str, exc: BaseException) -> str:
    return _DIAGNOSTIC_SANITIZER.sanitize_content(f"{extension_id}: {type(exc).__name__}: {exc}")[
        :4000
    ]


def _consume_disposal_result(task: asyncio.Task[tuple[str, ...]]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        pass


def _content_digest(manifest_bytes: bytes, entries: Sequence[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    _digest_record(digest, b"manifest", manifest_bytes)
    for relative_path, content in sorted(entries):
        _digest_record(digest, relative_path.encode("utf-8"), content)
    return digest.hexdigest()


def _digest_record(digest: _Digest, label: bytes, content: bytes) -> None:
    digest.update(len(label).to_bytes(8, "big"))
    digest.update(label)
    digest.update(len(content).to_bytes(8, "big"))
    digest.update(content)


def _execute_verified_bytes(
    path: Path,
    source: bytes,
    context: ExtensionBuildContext,
    *,
    dependencies: Sequence[tuple[str, bytes]] = (),
    local_modules: frozenset[str] = frozenset(),
) -> ExtensionContributions:
    module_prefix = (
        f"_homemaster_extension_{context.extension_id.replace('.', '_')}_{context.content_sha256}"
    )
    dependency_sources = {Path(name).stem: (name, content) for name, content in dependencies}
    module_cache: dict[str, ModuleType] = {}
    default_import = builtins.__import__

    def verified_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        del globals, locals
        if level:
            raise ImportError("extension dependency imports must be absolute")
        if name in dependency_sources:
            return load_dependency(name)
        root_name = name.partition(".")[0]
        if root_name in local_modules:
            raise ImportError(f"undeclared local extension dependency: {root_name}.py")
        return default_import(name, globals=None, locals=None, fromlist=fromlist, level=level)

    extension_builtins = dict(vars(builtins))
    extension_builtins["__import__"] = verified_import

    def load_dependency(name: str) -> ModuleType:
        cached = module_cache.get(name)
        if cached is not None:
            return cached
        relative_path, dependency_source = dependency_sources[name]
        module = ModuleType(f"{module_prefix}.{name}")
        module.__file__ = f"<homemaster-extension:{context.extension_id}/{relative_path}>"
        module.__package__ = ""
        module.__dict__["__builtins__"] = extension_builtins
        module_cache[name] = module
        try:
            code = compile(
                dependency_source,
                module.__file__,
                "exec",
                dont_inherit=True,
            )
            exec(code, module.__dict__)
        except BaseException:
            module_cache.pop(name, None)
            raise
        return module

    module = ModuleType(module_prefix)
    module.__file__ = f"<homemaster-extension:{context.extension_id}/{path.name}>"
    module.__package__ = ""
    module.__dict__["__builtins__"] = extension_builtins
    code = compile(source, module.__file__, "exec", dont_inherit=True)
    exec(code, module.__dict__)
    factory = module.__dict__.get(_FACTORY_NAME)
    if not callable(factory):
        raise TypeError(f"extension entrypoint must define {_FACTORY_NAME}(context)")
    value = factory(context)
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        raise TypeError("extension factory must be synchronous")
    if not isinstance(value, ExtensionContributions):
        raise TypeError("extension factory must return ExtensionContributions")
    return value


def _validate_contributions(
    manifest: ExtensionManifest,
    approval: ExtensionApproval,
    provenance_reference: str,
    contributions: ExtensionContributions,
) -> None:
    requested = set(manifest.requested_capabilities)
    granted = set(approval.granted_capabilities)
    tool_ids = {tool.definition.internal_id for tool in contributions.tools}
    unknown_enabled = sorted(set(approval.enabled_tool_ids) - tool_ids)
    if unknown_enabled:
        raise ValueError(f"approval enables unknown extension tools: {unknown_enabled}")
    if contributions.tools and not {"tool.register"}.issubset(requested & granted):
        raise ValueError("extension tools require requested and granted tool.register")
    if contributions.hooks and not {"hook.lifecycle"}.issubset(requested & granted):
        raise ValueError("extension hooks require requested and granted hook.lifecycle")
    for tool in contributions.tools:
        definition = tool.definition
        if definition.execution_backend is not ExecutionBackend.PLUGIN:
            raise ValueError("extension tools must use execution_backend=plugin")
        if definition.provenance.source != "plugin":
            raise ValueError("extension tool provenance source must be plugin")
        if definition.provenance.reference != provenance_reference:
            raise ValueError("extension tool provenance reference is not content-bound")
        required = set(definition.required_capabilities)
        if not required:
            raise ValueError(f"tool {definition.internal_id} must declare required_capabilities")
        if not required.issubset(requested & granted):
            raise ValueError(
                f"tool {definition.internal_id} requires undeclared or ungranted capabilities"
            )
    hook_ids: set[str] = set()
    for hook in contributions.hooks:
        if hook.extension_id != manifest.extension_id:
            raise ValueError("hook extension_id does not match manifest")
        if hook.hook_id in hook_ids:
            raise ValueError(f"duplicate hook id: {hook.hook_id}")
        hook_ids.add(hook.hook_id)
        if hook.required_capability not in requested or hook.required_capability not in granted:
            raise ValueError(f"hook {hook.hook_id} requires an undeclared or ungranted capability")


def _validate_cross_extension_uniqueness(
    tools: tuple[RegisteredTool, ...],
    hooks: tuple[HookSpec, ...],
    enabled_tool_ids: tuple[str, ...],
) -> None:
    tool_ids = [tool.definition.internal_id for tool in tools]
    aliases = [tool.definition.model_alias for tool in tools]
    hook_keys = [(hook.extension_id, hook.hook_id) for hook in hooks]
    for label, values in (
        ("tool id", tool_ids),
        ("tool alias", aliases),
        ("hook identity", hook_keys),
        ("enabled tool id", list(enabled_tool_ids)),
    ):
        if len(values) != len(set(values)):
            raise ExtensionLoadError(f"duplicate extension {label}")


def _tool_plane_digest(
    tools: tuple[RegisteredTool, ...],
    enabled_tool_ids: tuple[str, ...],
) -> str:
    payload = {
        "enabled_tool_ids": list(enabled_tool_ids),
        "tools": [tool.to_definition_snapshot() for tool in tools],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "ExtensionLoadError",
    "dispose_extension_generation",
    "extension_content_sha256",
    "load_extension_generation",
    "load_extension_generation_async",
    "register_extension_tools_atomically",
]
