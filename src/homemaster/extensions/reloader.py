"""Atomic hooks-only extension generation reload."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from homemaster.extensions.contracts import ExtensionApproval, ExtensionGeneration
from homemaster.extensions.hook_runner import HookRunner
from homemaster.extensions.loader import ExtensionLoadError, load_extension_generation_async


class ReloadStatus(StrEnum):
    RELOADED = "reloaded"
    BUSY = "busy"
    RESTART_REQUIRED = "restart_required"
    FAILED = "failed"


@dataclass(frozen=True)
class ReloadResult:
    status: ReloadStatus
    generation: int
    diagnostics: tuple[str, ...] = ()


class ExtensionReloader:
    def __init__(self, runner: HookRunner) -> None:
        self._runner = runner
        self._lock = asyncio.Lock()

    async def reload(self, approvals: Sequence[ExtensionApproval]) -> ReloadResult:
        async with self._lock:
            current = self._runner.generation
            if not self._runner.accepts_reload:
                return ReloadResult(
                    ReloadStatus.FAILED,
                    current.generation,
                    ("extension runner is closing or closed",),
                )
            if self._runner.active_callbacks:
                return ReloadResult(ReloadStatus.BUSY, current.generation)
            try:
                candidate = await load_extension_generation_async(
                    approvals,
                    generation=current.generation + 1,
                )
            except ExtensionLoadError as exc:
                return ReloadResult(
                    ReloadStatus.FAILED,
                    current.generation,
                    exc.diagnostics,
                )
            if _reload_boundary(candidate) != _reload_boundary(current):
                cleanup = await self._runner.cleanup_generation(candidate)
                return ReloadResult(
                    ReloadStatus.RESTART_REQUIRED,
                    current.generation,
                    ("extension deployment boundary changed", *cleanup),
                )
            if not await self._runner.swap(candidate):
                cleanup = await self._runner.cleanup_generation(candidate)
                return ReloadResult(ReloadStatus.BUSY, current.generation, cleanup)
            cleanup = await self._runner.cleanup_generation(current)
            return ReloadResult(ReloadStatus.RELOADED, candidate.generation, cleanup)


def _reload_boundary(generation: ExtensionGeneration) -> tuple[object, ...]:
    extensions = tuple(
        (
            extension.manifest.extension_id,
            extension.manifest.version,
            extension.manifest.requested_capabilities,
            extension.granted_capabilities,
        )
        for extension in generation.extensions
    )
    return (extensions, generation.tool_plane_digest)


__all__ = ["ExtensionReloader", "ReloadResult", "ReloadStatus"]
