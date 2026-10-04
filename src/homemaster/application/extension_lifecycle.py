"""Extension hook lifecycle for the application runtime.

Owns the HookRunner lifecycle end to end: APPLICATION_START/STOP hooks,
per-run RUN_START/RUN_END turn hooks, and the quiesce→stop→close shutdown
sequence including the ``extension.cleanup_completed`` event. The event bus
is shared (owned by ApplicationRuntime), not owned by this object — hook
completion events must remain observable through application shutdown, so
``close`` must run before the bus is closed.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any

from homemaster.events.bus import EventBus
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.extensions.contracts import AggregatedHookResult, HookEvent
from homemaster.extensions.hook_runner import HookRunner


class ExtensionLifecycle:
    """Drive extension hooks and own the runner's shutdown contract."""

    def __init__(self, extension_runner: HookRunner | None, event_bus: EventBus) -> None:
        self._runner = extension_runner
        self._event_bus = event_bus
        self._stop_lock = asyncio.Lock()
        self._stop_started = False
        self._closed = False

    @property
    def runner(self) -> HookRunner | None:
        return self._runner

    @property
    def released(self) -> bool:
        """True when no runner is installed or the runner has fully closed."""
        return self._runner is None or self._runner.closed

    async def execute(
        self,
        event: HookEvent,
        payload: Mapping[str, object],
        *,
        session_id: str,
        run_id: str,
        principal_capabilities: tuple[str, ...] = (),
        best_effort: bool = False,
    ) -> AggregatedHookResult:
        runner = self._runner
        if runner is None:
            return AggregatedHookResult()
        started = time.monotonic()
        result = await runner.execute(
            event,
            payload,
            principal_capabilities=principal_capabilities,
            best_effort=best_effort,
        )
        await self._event_bus.aemit(
            RuntimeEvent(
                type="extension.hook_completed",
                session_id=session_id,
                run_id=run_id,
                turn_index=None,
                payload={
                    "event": event.value,
                    "generation": runner.generation.generation,
                    "blocked": result.blocked,
                    "results": [
                        {
                            "extension_id": item.extension_id,
                            "hook_id": item.hook_id,
                            "success": item.success,
                            "blocked": item.blocked,
                            "timed_out": item.timed_out,
                            "stale_generation": item.stale_generation,
                            "reason": item.reason,
                            "output": item.output,
                        }
                        for item in result.results
                    ],
                },
                duration_ms=(time.monotonic() - started) * 1000,
            )
        )
        return result

    @asynccontextmanager
    async def turn(
        self,
        turn_context: Any,
        *,
        request: Any,
        run_id: str,
    ):
        async with turn_context as (runtime, generation, resumed):
            blocked_reason = ""
            try:
                if self._runner is not None:
                    result = await self.execute(
                        HookEvent.RUN_START,
                        {
                            "event": HookEvent.RUN_START.value,
                            "run_id": run_id,
                            "session_id": runtime.session.session_id,
                            "generation": generation,
                            "profile": request.profile,
                            "prompt": request.text,
                        },
                        session_id=runtime.session.session_id,
                        run_id=run_id,
                        principal_capabilities=request.permission_subject.capabilities,
                    )
                    blocked_reason = result.reason if result.blocked else ""
                yield runtime, generation, resumed, blocked_reason
            finally:
                if self._runner is not None:
                    await self.execute(
                        HookEvent.RUN_END,
                        {
                            "event": HookEvent.RUN_END.value,
                            "run_id": run_id,
                            "session_id": runtime.session.session_id,
                            "generation": generation,
                            "profile": request.profile,
                        },
                        session_id=runtime.session.session_id,
                        run_id=run_id,
                        principal_capabilities=request.permission_subject.capabilities,
                        best_effort=True,
                    )

    async def close(self) -> None:
        if self._runner is None or self._closed:
            return
        async with self._stop_lock:
            if self._closed:
                return
            if not self._stop_started:
                quiesce_diagnostics = await self._runner.quiesce()
                if quiesce_diagnostics:
                    raise RuntimeError("; ".join(quiesce_diagnostics))
                self._stop_started = True
                await self.execute(
                    HookEvent.APPLICATION_STOP,
                    {"event": HookEvent.APPLICATION_STOP.value},
                    session_id="application",
                    run_id="application",
                    best_effort=True,
                )
            cleanup_diagnostics = await self._runner.aclose()
            await self._event_bus.aemit(
                RuntimeEvent(
                    type="extension.cleanup_completed",
                    session_id="application",
                    run_id="application",
                    turn_index=None,
                    payload={
                        "generation": self._runner.generation.generation,
                        "success": self._runner.closed and not cleanup_diagnostics,
                        "diagnostics": list(cleanup_diagnostics),
                    },
                )
            )
            self._closed = self._runner.closed
            if not self._closed:
                raise RuntimeError(
                    "extension callbacks remain active; application resources were not closed"
                )
