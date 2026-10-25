"""Shared types for the RoboCasa benchmark integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

MemoryMode = Literal["disabled", "readonly", "full"]

RoboCasaEpisodeClassification = Literal[
    "agent_success",
    "agent_model_failure",
    "infrastructure_error",
    "provider_failure",
    "runtime_failure",
    "cancelled",
]

AGENT_SCORE_CLASSIFICATIONS = frozenset({"agent_success", "agent_model_failure"})


@dataclass(frozen=True)
class RoboCasaEnvState:
    """Environment-authoritative state snapshot surfaced by the worker.

    RoboCasa has no ``done``/``terminated`` dimension distinct from success:
    ``env.terminated`` is literally ``env.check_success()`` upstream and the
    env server runs with ``ignore_done=True``.  Terminal semantics therefore
    collapse to ``success``; the ``won`` alias exists for the shared
    ``_ReceiptVerifier``/terminal-owner contracts that read ``state.won``.
    """

    task_name: str
    split: str
    seed: int
    task_language: str
    success: bool
    task_progress: Any = None
    proprio: dict[str, Any] | None = None
    step_index: int = 0
    invalid_action_count: int = 0

    @property
    def won(self) -> bool:
        return self.success

    def to_model_visible_dict(self) -> dict[str, Any]:
        return {
            "task_name": self.task_name,
            "split": self.split,
            "seed": self.seed,
            "task_language": self.task_language,
            "success": self.success,
            "task_progress": self.task_progress,
            "proprio": self.proprio,
            "step_index": self.step_index,
            "invalid_action_count": self.invalid_action_count,
        }


@dataclass
class EpisodeOutcome:
    terminal: bool = False
    classification: str | None = None
    terminal_tool_call_id: str | None = None
    score_eligible: bool = True
    agent_tool_call_count: int = 0
    backend_action_count: int = 0
    terminal_evidence_ref: str | None = None

    def mark_terminal(
        self,
        *,
        classification: str,
        tool_call_id: str | None,
        evidence_ref: str | None = None,
    ) -> None:
        if self.terminal:
            return
        self.terminal = True
        self.classification = classification
        self.terminal_tool_call_id = tool_call_id
        self.score_eligible = classification in AGENT_SCORE_CLASSIFICATIONS
        self.terminal_evidence_ref = evidence_ref


@dataclass(frozen=True)
class RoboCasaCell:
    """One (task_name, split, seed) evaluation unit from the manifest."""

    task_name: str
    split: str
    seed: int
    timeout_seconds: float
    evaluation_split: str


@dataclass(frozen=True)
class RoboCasaEpisodeResult:
    cell: RoboCasaCell
    episode_id: str
    success: bool
    failure_reason: str | None
    steps: int
    invalid_actions: int
    runtime_status: str
    run_id: str
    trace_path: Path
    classification: str = "agent_model_failure"
    score_eligible: bool = True
    agent_tool_call_count: int = 0
    backend_action_count: int = 0
    terminal_tool_call_id: str | None = None
    terminal_evidence_ref: str | None = None
    task_language: str = ""
    trajectory_memory_error: str | None = None
    result_path: Path | None = None


@dataclass(frozen=True)
class RoboCasaSummary:
    run_id: str
    cells: list[RoboCasaEpisodeResult]
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        """Fraction of all attempted cells that succeeded — deliberately
        includes score-ineligible cells in the denominator, unlike
        ``task_weighted_success_rate`` which aggregates only scored cells
        per Target50 rules.  Compare like-with-like when reporting.
        """
        if not self.cells:
            return 0.0
        return sum(1 for cell in self.cells if cell.success) / len(self.cells)

    @property
    def scored_cells(self) -> list[RoboCasaEpisodeResult]:
        return [cell for cell in self.cells if cell.score_eligible]

    @property
    def task_weighted_success_rate(self) -> float:
        """Per-task mean of per-seed success (Target50 aggregation)."""

        by_task: dict[str, list[bool]] = {}
        for cell in self.scored_cells:
            by_task.setdefault(cell.cell.task_name, []).append(cell.success)
        if not by_task:
            return 0.0
        return sum(sum(map(int, flags)) / len(flags) for flags in by_task.values()) / len(by_task)

    def to_dict(self) -> dict[str, Any]:
        total = len(self.cells)
        scored = self.scored_cells
        classifications = [cell.classification for cell in self.cells]
        classification_count = {name: classifications.count(name) for name in set(classifications)}
        infrastructure_failures = classification_count.get("infrastructure_error", 0)
        provider_failures = classification_count.get("provider_failure", 0)
        runtime_failures = classification_count.get("runtime_failure", 0)
        cancelled = classification_count.get("cancelled", 0)
        evaluation_coverage = len(scored) / total if total else 0.0
        provider_availability = 1.0 - provider_failures / total if total else 0.0
        runtime_availability = (
            1.0 - (runtime_failures + infrastructure_failures) / total if total else 0.0
        )
        per_task: dict[str, list[bool]] = {}
        for cell in scored:
            per_task.setdefault(cell.cell.task_name, []).append(cell.success)
        return {
            "run_id": self.run_id,
            "config": self.config,
            "cell_count": total,
            "success_rate": self.success_rate,
            "scored_cells": len(scored),
            "scored_successes": sum(1 for cell in scored if cell.success),
            "task_weighted_success_rate": self.task_weighted_success_rate,
            "per_task_success_rate": {
                task: sum(map(int, flags)) / len(flags) for task, flags in sorted(per_task.items())
            },
            "infrastructure_errors": infrastructure_failures,
            "provider_failures": provider_failures,
            "runtime_failures": runtime_failures,
            "evaluation_valid_coverage": evaluation_coverage,
            "provider_availability": provider_availability,
            "runtime_availability": runtime_availability,
            "cancelled_cells": cancelled,
            "cells": [
                {
                    "task_name": cell.cell.task_name,
                    "split": cell.cell.split,
                    "seed": cell.cell.seed,
                    "success": cell.success,
                    "classification": cell.classification,
                    "score_eligible": cell.score_eligible,
                    "steps": cell.steps,
                    "invalid_actions": cell.invalid_actions,
                    "trajectory_memory_error": cell.trajectory_memory_error,
                    "run_id": cell.run_id,
                    "trace_path": str(cell.trace_path),
                    "result_path": (
                        str(cell.result_path) if cell.result_path is not None else None
                    ),
                }
                for cell in self.cells
            ],
        }


@dataclass(frozen=True)
class RoboCasaBenchmarkConfig:
    rpent_root: Path
    task_manifest: Path
    trace_root: Path
    split: str = "target"
    memory_mode: MemoryMode = "disabled"
    max_tool_iterations: int = 100
    max_invalid_actions: int = 100
    provider_config: Path | None = None
    provider_name: str | None = None
    run_id: str | None = None
    rpent_python: Path | None = None
    vla_model_path: Path | None = None
    cuda_device: str | None = None
    hi_res: bool = False
    tasks: tuple[str, ...] = ()
    seeds: tuple[int, ...] = ()
    task_splits: tuple[str, ...] = ()
    episodes: int | None = None
    worker_startup_timeout_s: float = 600.0
    request_timeout_s: float = 120.0
    skill_timeout_s: float = 900.0
    worker_script: Path | None = None

    def __post_init__(self) -> None:
        if self.memory_mode not in {"disabled", "readonly", "full"}:
            raise ValueError(f"unsupported memory_mode: {self.memory_mode}")
        if self.max_tool_iterations <= 0:
            raise ValueError("max_tool_iterations must be > 0")
        if self.max_invalid_actions <= 0:
            raise ValueError("max_invalid_actions must be > 0")
        if self.episodes is not None and self.episodes <= 0:
            raise ValueError("episodes must be > 0")


__all__ = [
    "AGENT_SCORE_CLASSIFICATIONS",
    "EpisodeOutcome",
    "MemoryMode",
    "RoboCasaBenchmarkConfig",
    "RoboCasaCell",
    "RoboCasaEnvState",
    "RoboCasaEpisodeClassification",
    "RoboCasaEpisodeResult",
    "RoboCasaSummary",
]
