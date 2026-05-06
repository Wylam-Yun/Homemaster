"""Pipeline skeleton for HomeMaster Stage 02-06 execution.

Provides PipelineContext (immutable state snapshot), Stage Protocol,
and StageRegistry (ordered stage collection).  No run_pipeline() —
stage loop lives in task_runner.py so the except block retains access
to the latest partial context.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, Sequence


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
    live_models: bool
    mock_skills: bool
    config_path: Path
    provider_name: str
    embedding_provider_name: str

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

    def has(self, name: str) -> bool:
        return name in self._names

    def __len__(self) -> int:
        return len(self._stages)


# ---------------------------------------------------------------------------
# Default registry factory
# ---------------------------------------------------------------------------


def build_default_registry() -> StageRegistry:
    """Build the standard Stage02-06 registry.

    Uses lazy imports to avoid circular dependency with pipeline_stages.
    """
    from homemaster.pipeline_stages import (
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
