"""Pipeline skeleton for HomeMaster Stage 02-06 execution.

Provides PipelineContext (immutable state snapshot), Stage Protocol,
StageRegistry (ordered stage collection), and PipelineRunner (compat
layer wrapping the stage loop previously inline in task_runner.py).

PipelineRunner is the legacy-compatible entrypoint.  AgentRuntime is
the forward-path entrypoint.  Both produce HomeMasterRunResult.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# Stage Protocol
# ---------------------------------------------------------------------------


class Stage(Protocol):
    """Minimal stage contract: named, takes context, returns updated context."""

    @property
    def name(self) -> str: ...

    def execute(self, ctx: PipelineContext) -> PipelineContext: ...


# ---------------------------------------------------------------------------
# PipelineContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PipelineContext:
    """Immutable snapshot of all pipeline state at a given point.

    Dict fields (stage_statuses, model_boundary, paths) are mutated via
    copy-on-write helpers so that each snapshot is truly immutable.
    """

    # Run identity
    run_id: str
    scenario: str
    utterance: str

    # Paths (all resolved before pipeline starts)
    resolved_world_path: Path
    resolved_memory_path: Path
    runtime_memory_dir: Path
    case_dir: Path
    results_dir: Path

    # Runtime contract flags
    config_path: Path
    provider_name: str
    embedding_provider_name: str
    skill_mode: str = "simulated"

    # Stage outputs (None until produced)
    task_card: Any = None
    memory_result: Any = None
    planning_context: Any = None
    orchestration_plan: Any = None
    execution_result: Any = None
    evidence_bundle: Any = None
    task_summary: Any = None
    memory_commit: Any = None

    # Accumulated status (immutable snapshots via copy-on-write methods)
    stage_statuses: dict[str, dict[str, Any]] = field(default_factory=dict)
    model_boundary: dict[str, str] = field(default_factory=dict)
    paths: dict[str, str] = field(default_factory=dict)
    final_status: str = "failed"

    # Failure tracking
    failure_provider: Any = None

    # P2: structured runtime mode
    runtime_mode: Any = None  # RuntimeMode | None; Any avoids circular import

    # P9: recovery support
    registry: Any = None  # StageRegistry | None; set by task_runner, used by recovery loop
    recovery_attempts: list[dict[str, Any]] | None = None  # set by recovery loop
    negative_evidence: list[dict[str, Any]] | None = None  # injected for retrieve_again

    # -- Copy-on-write helpers ------------------------------------------------

    def with_updates(self, **kwargs: Any) -> PipelineContext:
        """Generic copy-on-write for scalar/object fields.

        For dict fields prefer the specific helpers below to avoid
        accidental in-place mutation of shared dicts.
        """
        return replace(self, **kwargs)

    def with_stage_status(self, name: str, status: dict[str, Any]) -> PipelineContext:
        """Copy-on-write: add/overwrite one stage status entry."""
        merged = {**self.stage_statuses, name: status}
        return replace(self, stage_statuses=merged)

    def with_final_status(self, status: str) -> PipelineContext:
        """Copy-on-write: set final_status."""
        return replace(self, final_status=status)


# ---------------------------------------------------------------------------
# StageRegistry
# ---------------------------------------------------------------------------


class StageRegistry:
    """Ordered registry of pipeline stages."""

    def __init__(self) -> None:
        self._stages: list[Stage] = []
        self._names: set[str] = set()

    def register(self, stage: Stage) -> None:
        if stage.name in self._names:
            raise ValueError(f"stage {stage.name!r} already registered")
        self._stages.append(stage)
        self._names.add(stage.name)

    def stages(self) -> Sequence[Stage]:
        return tuple(self._stages)

    def get_stage(self, name: str) -> Stage:
        """Return a stage by name. Raises KeyError if not found."""
        for stage in self._stages:
            if stage.name == name:
                return stage
        raise KeyError(f"stage {name!r} not registered")

    def has(self, name: str) -> bool:
        return name in self._names

    def __len__(self) -> int:
        return len(self._stages)


# ---------------------------------------------------------------------------
# PipelineRunner — compat layer for legacy stage loop
# ---------------------------------------------------------------------------

# Compat annotations: this runner wraps the legacy Stage 02-06 loop.
# AgentRuntime is the forward-path entrypoint.
RUNTIME_ENTRYPOINT = "pipeline_compat"
MIGRATION_REQUIRED = True
DEFAULT_ENTRYPOINT = False


class PipelineRunner:
    """Runs the Stage 02-06 pipeline loop with error handling and status tracking.

    This is a compatibility wrapper around the stage loop that was previously
    inline in task_runner.py.  It preserves the same logging, timing, and
    error-propagation semantics while making the loop reusable and testable.
    """

    def __init__(
        self,
        registry: StageRegistry,
        *,
        stage_modes_fn: Callable[[PipelineContext, str], dict[str, str]] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._registry = registry
        self._stage_modes_fn = stage_modes_fn
        self._logger = logger

    def run(self, ctx: PipelineContext) -> PipelineContext:
        """Execute all registered stages in order.

        Returns the final PipelineContext.  Raises on stage failure
        (the caller is responsible for writing error assets).
        """
        run_id = ctx.run_id
        logger = self._logger

        for stage in self._registry.stages():
            modes = self._stage_modes_fn(ctx, stage.name) if self._stage_modes_fn else {}
            if logger:
                logger.info(
                    "[%s] stage %s started  scenario=%s%s",
                    run_id, stage.name, ctx.scenario,
                    _compact_modes(modes),
                )
            t0 = time.monotonic()
            try:
                ctx = stage.execute(ctx)
            except Exception as stage_exc:
                elapsed = time.monotonic() - t0
                if logger:
                    logger.error(
                        "[%s] ERROR in stage %s: %s: %s  elapsed=%.2fs%s",
                        run_id, stage.name, type(stage_exc).__name__,
                        stage_exc, elapsed, _compact_modes(modes),
                    )
                raise
            elapsed = time.monotonic() - t0
            stage_status = ctx.stage_statuses.get(stage.name, {})
            status = stage_status.get("status", "unknown")
            modes = stage_status.get("component_modes") or modes
            if logger:
                logger.info(
                    "[%s] stage %s completed in %.2fs  status=%s%s",
                    run_id, stage.name, elapsed, status,
                    _compact_modes(modes),
                )

        return ctx


def _compact_modes(modes: dict[str, str] | None) -> str:
    """Format component_modes as compact 'k=v k=v' string for logging."""
    if not modes:
        return ""
    return "  modes=" + " ".join(f"{k}={v}" for k, v in modes.items())


# ---------------------------------------------------------------------------
# Default registry factory
# ---------------------------------------------------------------------------


def build_default_registry() -> StageRegistry:
    """Build the standard Stage02-06 registry.

    Uses lazy imports to avoid circular dependency with pipeline_stages.
    """
    from homemaster.pipeline.adapters import (
        Stage02Adapter,
        Stage03Adapter,
        Stage04Adapter,
        Stage05Adapter,
        Stage06Adapter,
    )

    registry = StageRegistry()
    registry.register(Stage02Adapter())
    registry.register(Stage03Adapter())
    registry.register(Stage04Adapter())
    registry.register(Stage05Adapter())
    registry.register(Stage06Adapter())
    return registry
