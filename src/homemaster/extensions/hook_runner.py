"""Deterministic cooperative execution for trusted async extension hooks."""

from __future__ import annotations

import asyncio
import fnmatch
from collections.abc import Mapping, Sequence

from homemaster.events.public_projection import PublicEventProjection
from homemaster.extensions.contracts import (
    AggregatedHookResult,
    ExtensionGeneration,
    HookContext,
    HookEvent,
    HookResult,
    HookSpec,
)


class HookRunner:
    _CLOSE_CANCEL_GRACE_S = 0.1

    def __init__(self, generation: ExtensionGeneration) -> None:
        self._generation = generation
        self._active_tasks: set[asyncio.Task[object]] = set()
        self._swap_lock = asyncio.Lock()
        self._closing = False
        self._closed = False
        self._cleaned_generation_ids: set[int] = set()
        self._sanitizer = PublicEventProjection()

    @property
    def generation(self) -> ExtensionGeneration:
        return self._generation

    @property
    def active_callbacks(self) -> int:
        for task in tuple(self._active_tasks):
            if task.done():
                self._task_completed(task)
        return len(self._active_tasks)

    @property
    def accepts_reload(self) -> bool:
        return not self._closing and not self._closed

    @property
    def closed(self) -> bool:
        return self._closed

    async def execute(
        self,
        event: HookEvent,
        payload: Mapping[str, object],
        *,
        principal_capabilities: Sequence[str] = (),
        best_effort: bool = False,
    ) -> AggregatedHookResult:
        if self._closed or (self._closing and event is not HookEvent.APPLICATION_STOP):
            return AggregatedHookResult()
        captured = self._generation
        capabilities = frozenset(principal_capabilities)
        hooks = tuple(
            hook for hook in captured.hooks if hook.event is event and _matches(hook, payload)
        )
        ordered = tuple(sorted(enumerate(hooks), key=lambda item: (-item[1].priority, item[0])))
        results: list[HookResult] = []
        for _, hook in ordered:
            if self._closing and event is not HookEvent.APPLICATION_STOP:
                break
            authorized = (
                event in {HookEvent.APPLICATION_START, HookEvent.APPLICATION_STOP}
                or hook.required_capability in capabilities
            )
            if authorized:
                result = await self._run_one(
                    hook,
                    captured,
                    event,
                    payload,
                    best_effort=best_effort,
                )
            else:
                result = HookResult(
                    extension_id=hook.extension_id,
                    hook_id=hook.hook_id,
                    success=False,
                    blocked=hook.block_on_failure,
                    reason=(
                        f"principal lacks required hook capability: {hook.required_capability}"
                    ),
                )
            results.append(result)
        return AggregatedHookResult(tuple(results))

    async def swap(self, generation: ExtensionGeneration) -> bool:
        async with self._swap_lock:
            if (
                self._closing
                or self._closed
                or self._active_tasks
                or generation.generation <= self._generation.generation
            ):
                return False
            self._generation = generation
            return True

    async def begin_close(self) -> None:
        async with self._swap_lock:
            self._closing = True

    async def aclose(self) -> tuple[str, ...]:
        if self._closed:
            return ()
        quiesce_diagnostics = await self.quiesce()
        if quiesce_diagnostics:
            return quiesce_diagnostics
        diagnostics = await self.cleanup_generation(self._generation)
        if self.active_callbacks:
            return (
                *diagnostics,
                "extension cleanup callback is still active after cancellation",
            )
        async with self._swap_lock:
            self._closed = True
        return diagnostics

    async def quiesce(self) -> tuple[str, ...]:
        """Seal the runner and cooperatively join callbacks before resource cleanup."""

        await self.begin_close()
        active = tuple(self._active_tasks)
        for task in active:
            task.cancel()
        if active:
            await asyncio.wait(active, timeout=self._CLOSE_CANCEL_GRACE_S)
        if self.active_callbacks:
            return (
                "extension cleanup deferred because "
                f"{self.active_callbacks} callback(s) resisted cancellation",
            )
        return ()

    async def cleanup_generation(
        self,
        generation: ExtensionGeneration,
    ) -> tuple[str, ...]:
        identity = id(generation)
        if identity in self._cleaned_generation_ids:
            return ()
        if generation is self._generation and self._active_tasks:
            return ("current extension generation still has active callbacks",)
        self._cleaned_generation_ids.add(identity)
        diagnostics: list[str] = []
        for extension in reversed(generation.extensions):
            cleanup = extension.contributions.cleanup
            if cleanup is not None:
                task = self._track_task(asyncio.create_task(cleanup()))
                try:
                    done, _ = await asyncio.wait((task,), timeout=30)
                    if not done:
                        task.cancel()
                        diagnostics.append(f"{extension.manifest.extension_id}: cleanup timed out")
                        continue
                    task.result()
                except asyncio.CancelledError:
                    task.cancel()
                    diagnostics.append(f"{extension.manifest.extension_id}: cleanup cancelled")
                except Exception as exc:
                    diagnostics.append(
                        self._safe_text(
                            f"{extension.manifest.extension_id}: {type(exc).__name__}: {exc}"
                        )
                    )
        return tuple(diagnostics)

    async def _run_one(
        self,
        hook: HookSpec,
        captured: ExtensionGeneration,
        event: HookEvent,
        payload: Mapping[str, object],
        *,
        best_effort: bool,
    ) -> HookResult:
        context = HookContext(
            event=event,
            extension_id=hook.extension_id,
            generation=captured.generation,
            payload=payload,
        )
        task = self._track_task(asyncio.create_task(hook.callback(context)))
        try:
            try:
                done, _ = await asyncio.wait((task,), timeout=hook.timeout_s)
                if not done:
                    task.cancel()
                    result = HookResult(
                        extension_id=hook.extension_id,
                        hook_id=hook.hook_id,
                        success=False,
                        blocked=hook.block_on_failure,
                        reason=f"hook timed out after {hook.timeout_s}s",
                        timed_out=True,
                    )
                    return self._fence_result(hook, captured, result)
                value = task.result()
                self._active_tasks.discard(task)
                success, output, reason = _normalize_result(value)
                result = HookResult(
                    extension_id=hook.extension_id,
                    hook_id=hook.hook_id,
                    success=success,
                    blocked=hook.block_on_failure and not success,
                    output=self._safe_text(output),
                    reason=self._safe_text(reason),
                )
            except asyncio.CancelledError:
                task.cancel()
                await asyncio.sleep(0)
                if task.done():
                    self._task_completed(task)
                if not best_effort:
                    raise
                result = HookResult(
                    extension_id=hook.extension_id,
                    hook_id=hook.hook_id,
                    success=False,
                    blocked=hook.block_on_failure,
                    reason="hook callback cancelled during best-effort lifecycle cleanup",
                )
            except Exception as exc:
                result = HookResult(
                    extension_id=hook.extension_id,
                    hook_id=hook.hook_id,
                    success=False,
                    blocked=hook.block_on_failure,
                    reason=self._safe_text(f"{type(exc).__name__}: {exc}"),
                )
            return self._fence_result(hook, captured, result)
        except BaseException:
            task.cancel()
            raise

    def _track_task(self, task: asyncio.Task[object]) -> asyncio.Task[object]:
        self._active_tasks.add(task)
        task.add_done_callback(self._task_completed)
        return task

    def _task_completed(self, task: asyncio.Task[object]) -> None:
        self._active_tasks.discard(task)
        try:
            task.exception()
        except asyncio.CancelledError:
            pass

    def _fence_result(
        self,
        hook: HookSpec,
        captured: ExtensionGeneration,
        result: HookResult,
    ) -> HookResult:
        if self._generation is not captured:
            return HookResult(
                extension_id=hook.extension_id,
                hook_id=hook.hook_id,
                success=False,
                reason="hook result belongs to a stale extension generation",
                stale_generation=True,
            )
        return result

    def _safe_text(self, value: str) -> str:
        return self._sanitizer.sanitize_content(value)[:4000]


def _matches(hook: HookSpec, payload: Mapping[str, object]) -> bool:
    if hook.matcher is None:
        return True
    subject = str(payload.get("tool_name") or payload.get("prompt") or payload.get("event") or "")
    return fnmatch.fnmatch(subject, hook.matcher)


def _normalize_result(value: object) -> tuple[bool, str, str]:
    if value is None:
        return True, "", ""
    if isinstance(value, bool):
        return value, "", "" if value else "hook rejected the event"
    if isinstance(value, str):
        return True, value, ""
    if isinstance(value, Mapping):
        ok = value.get("ok")
        if not isinstance(ok, bool):
            return False, "", "hook mapping result requires boolean ok"
        output = value.get("output", "")
        reason = value.get("reason", "")
        if not isinstance(output, str) or not isinstance(reason, str):
            return False, "", "hook output and reason must be strings"
        return ok, output, reason or ("" if ok else "hook rejected the event")
    return False, "", f"unsupported hook result type: {type(value).__name__}"


__all__ = ["HookRunner"]
