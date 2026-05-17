"""Stage 07 single-task runner that wires Stage 02 through Stage 06.

After P1 refactoring, run_homemaster_task() delegates stage execution to
PipelineContext + StageRegistry adapters.  Stage helpers live in
stage_runtime.py.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homemaster.config.runtime_paths import validate_run_id
from homemaster.contracts import (
    EvidenceBundle,
    PlanningContext,
    TaskCard,
)
from homemaster.failure_rule_provider import FailureRuleProvider
from homemaster.logger import get_logger
from homemaster.pipeline.core import PipelineContext, build_default_registry
from homemaster.pipeline.stage_runtime import (
    RuntimeMode,
    validate_runtime_services,
)
from homemaster.runtime import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_EMBEDDING_PROVIDER_NAME,
    DEFAULT_PROVIDER_NAME,
    DEFAULT_STAGE_07_DEBUG_ROOT,
    DEFAULT_STAGE_07_RESULTS_ROOT,
    DEFAULT_STAGE_07_RUNTIME_ROOT,
    LLM_CASE_ROOT,
    REPO_ROOT,
    TEST_RESULTS_ROOT,
)
from homemaster.trace import append_jsonl_event, sanitize_for_log, write_json

STAGE_07_CASE_ROOT = LLM_CASE_ROOT / "stage_07"
STAGE_07_RESULTS_DIR = TEST_RESULTS_ROOT / "stage_07"


class HomeMasterRunError(RuntimeError):
    """Raised when Stage 07 cannot construct or run a task safely."""


@dataclass(frozen=True)
class HomeMasterRunResult:
    run_id: str
    scenario: str
    utterance: str
    final_status: str
    stage_statuses: dict[str, dict[str, Any]]
    model_boundary: dict[str, str]
    paths: dict[str, str]
    task_card: TaskCard | None
    planning_context: PlanningContext | None
    orchestration_plan: Any | None
    execution_result: Any | None
    evidence_bundle: EvidenceBundle | None
    memory_commit: dict[str, Any] | None
    case_dir: Path
    results_dir: Path
    runtime_memory_root: Path

    def to_dict(self) -> dict[str, Any]:
        def _ser(v: object) -> object:
            if hasattr(v, "model_dump"):
                return v.model_dump(mode="json")  # type: ignore[union-attr]
            if hasattr(v, "as_debug_payload"):
                return v.as_debug_payload()  # type: ignore[union-attr]
            if isinstance(v, Path):
                return str(v)
            return v

        return {
            "run_id": self.run_id,
            "scenario": self.scenario,
            "utterance": self.utterance,
            "final_status": self.final_status,
            "stage_statuses": self.stage_statuses,
            "model_boundary": self.model_boundary,
            "paths": self.paths,
            "task_card": _ser(self.task_card) if self.task_card else None,
            "planning_context": _ser(self.planning_context) if self.planning_context else None,
            "orchestration_plan": (
                _ser(self.orchestration_plan) if self.orchestration_plan else None
            ),
            "execution_result": _ser(self.execution_result) if self.execution_result else None,
            "evidence_bundle": _ser(self.evidence_bundle) if self.evidence_bundle else None,
            "memory_commit": self.memory_commit,
            "case_dir": str(self.case_dir),
            "results_dir": str(self.results_dir),
            "runtime_memory_root": str(self.runtime_memory_root),
        }


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def _compact_modes(modes: dict[str, str] | None) -> str:
    """Format component_modes as compact 'k=v k=v' string for logging."""
    if not modes:
        return ""
    return "  modes=" + " ".join(f"{k}={v}" for k, v in modes.items())


_STAGE_MODE_KEYS: dict[str, list[str]] = {
    "stage02": ["task_understanding"],
    "stage03": ["memory_query", "embedding"],
    "stage04": [],  # always programmatic, hardcoded below
    "stage05": ["planning", "step_decision", "skills", "verification"],
    "stage06": ["summary", "memory_commit"],
}


def _stage_modes(ctx: PipelineContext, stage_name: str) -> dict[str, str]:
    """Extract component_modes for a stage from ctx.runtime_mode (pre-execution)."""
    rm = ctx.runtime_mode
    if stage_name == "stage04":
        return {"grounding": "programmatic"}
    if not rm:
        return {}
    return {k: getattr(rm, k, "unknown") for k in _STAGE_MODE_KEYS.get(stage_name, [])}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_homemaster_task(
    *,
    utterance: str,
    scenario: str,
    world_path: str | Path | None = None,
    memory_path: str | Path | None = None,
    runtime_memory_root: str | Path = DEFAULT_STAGE_07_RUNTIME_ROOT,
    debug_root: str | Path = DEFAULT_STAGE_07_DEBUG_ROOT,
    results_root: str | Path = DEFAULT_STAGE_07_RESULTS_ROOT,
    run_id: str | None = None,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    provider_name: str = DEFAULT_PROVIDER_NAME,
    embedding_provider_name: str = DEFAULT_EMBEDDING_PROVIDER_NAME,
) -> HomeMasterRunResult:
    # -- Phase 0: validation & data-source resolution --
    if not scenario:
        raise HomeMasterRunError("scenario is required for Stage07 run")
    run_id = run_id or _slug_run_id(scenario)
    validate_run_id(run_id)
    scenario_root = REPO_ROOT / "data" / "scenarios" / scenario
    if not scenario_root.is_dir():
        raise HomeMasterRunError(f"unknown scenario: {scenario}")

    failure_provider = FailureRuleProvider.from_scenario(scenario, scenario_root)
    runtime_memory_dir = Path(runtime_memory_root) / run_id / "memory"
    case_dir = Path(debug_root) / "stage_07" / run_id

    # -- P2: runtime contract guards (before any file materialization) --
    rm = RuntimeMode.live()
    checks = validate_runtime_services(
        rm,
        config_path=str(config_path),
        provider_name=provider_name,
        embedding_provider_name=embedding_provider_name,
    )
    unavailable = [c for c in checks if not c.available]
    if unavailable:
        names = ", ".join(f"{c.component}: {c.error}" for c in unavailable)
        raise HomeMasterRunError(f"required services unavailable: {names}")

    # -- Data-source resolution (may materialize files) --
    resolved_world, resolved_memory = _resolve_data_source(
        scenario, scenario_root, case_dir, runtime_memory_dir,
        world_path, memory_path,
    )

    results_dir = Path(results_root)
    mb = rm.to_boundary_dict()
    paths = {
        "world_path": str(resolved_world),
        "base_memory_path": str(resolved_memory),
        "runtime_memory_root": str(runtime_memory_dir),
        "case_dir": str(case_dir),
        "results_dir": str(results_dir),
    }

    # -- Build PipelineContext --
    ctx = PipelineContext(
        run_id=run_id,
        scenario=scenario,
        utterance=utterance,
        resolved_world_path=resolved_world,
        resolved_memory_path=resolved_memory,
        runtime_memory_dir=runtime_memory_dir,
        case_dir=case_dir,
        results_dir=results_dir,
        config_path=Path(config_path),
        provider_name=provider_name,
        embedding_provider_name=embedding_provider_name,
        model_boundary=mb,
        paths=paths,
        failure_provider=failure_provider,
        runtime_mode=rm,
    )

    # -- P3: run header --
    logger = get_logger()
    logger.info("[%s] run started  scenario=%s  runtime_mode=live(simulated)",
                run_id, scenario)

    # -- Stage loop (no run_pipeline wrapper; ctx accessible in except) --
    try:
        registry = build_default_registry()
        ctx = ctx.with_updates(registry=registry)
        for stage in registry.stages():
            modes = _stage_modes(ctx, stage.name)
            logger.info("[%s] stage %s started  scenario=%s%s",
                        run_id, stage.name, scenario, _compact_modes(modes))
            t0 = time.monotonic()
            try:
                ctx = stage.execute(ctx)
            except Exception as stage_exc:
                elapsed = time.monotonic() - t0
                logger.error(
                    "[%s] ERROR in stage %s: %s: %s  elapsed=%.2fs%s",
                    run_id, stage.name, type(stage_exc).__name__, stage_exc, elapsed,
                    _compact_modes(modes),
                )
                raise
            elapsed = time.monotonic() - t0
            stage_status = ctx.stage_statuses.get(stage.name, {})
            status = stage_status.get("status", "unknown")
            modes = stage_status.get("component_modes") or modes
            logger.info("[%s] stage %s completed in %.2fs  status=%s%s",
                        run_id, stage.name, elapsed, status, _compact_modes(modes))
    except Exception as exc:
        logger.error("[%s] run failed  error_type=%s  message=%s",
                     run_id, type(exc).__name__, exc)
        if not ctx.stage_statuses:
            ctx = ctx.with_stage_status("stage07", {"status": "FAIL", "error": str(exc)})
        else:
            ctx = ctx.with_stage_status(
                "stage07_error",
                {
                    "status": "FAIL",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            )
        _write_stage_07_assets(
            case_dir=case_dir,
            results_dir=results_dir,
            expected={"scenario": scenario, "utterance": utterance},
            actual={
                "run_id": run_id,
                "scenario": scenario,
                "utterance": utterance,
                "final_status": "failed",
                "stage_statuses": ctx.stage_statuses,
                "model_boundary": mb,
                "paths": paths,
                "error_type": type(exc).__name__,
                "message": str(exc),
            },
            status="FAIL",
        )
        raise

    logger.info("[%s] run finished  final_status=%s", run_id, ctx.final_status)

    # -- Convert to HomeMasterRunResult --
    result = HomeMasterRunResult(
        run_id=ctx.run_id,
        scenario=ctx.scenario,
        utterance=ctx.utterance,
        final_status=ctx.final_status,
        stage_statuses=ctx.stage_statuses,
        model_boundary=ctx.model_boundary,
        paths=ctx.paths,
        task_card=ctx.task_card,
        planning_context=ctx.planning_context,
        orchestration_plan=ctx.orchestration_plan,
        execution_result=ctx.execution_result,
        evidence_bundle=ctx.evidence_bundle,
        memory_commit=ctx.memory_commit,
        case_dir=case_dir,
        results_dir=results_dir,
        runtime_memory_root=runtime_memory_dir,
    )
    _write_stage_07_assets(
        case_dir=case_dir,
        results_dir=results_dir,
        expected={"scenario": scenario, "utterance": utterance},
        actual=result.to_dict(),
        status="PASS",
    )
    return result


# ---------------------------------------------------------------------------
# Data-source resolution (extracted from old run_homemaster_task Phase 0)
# ---------------------------------------------------------------------------


def _resolve_data_source(
    scenario: str,
    scenario_root: Path,
    case_dir: Path,
    runtime_memory_dir: Path,
    world_path: str | Path | None,
    memory_path: str | Path | None,
) -> tuple[Path, Path]:
    """Resolve world and memory paths based on data_source type.

    Returns (resolved_world_path, resolved_memory_path).
    """
    from homemaster.scenario_catalog import load_catalog, load_memory_profile

    catalog_entries = load_catalog()
    catalog_entry = next((e for e in catalog_entries if e.name == scenario), None)

    if catalog_entry and catalog_entry.data_source == "homeworld_profile":
        from homemaster.world_overlay import apply_world_overlay

        global_world_path = REPO_ROOT / "data" / "homes" / "elder_home_v1" / "world.json"
        overlay_path = scenario_root / "world_overlay.json"
        if not overlay_path.is_file():
            raise HomeMasterRunError(
                f"homeworld_profile scenario {scenario!r} missing world_overlay.json"
            )
        world_dict = json.loads(global_world_path.read_text(encoding="utf-8"))
        world_dict = apply_world_overlay(
            world_dict, json.loads(overlay_path.read_text(encoding="utf-8"))
        )
        case_dir.mkdir(parents=True, exist_ok=True)
        resolved_world = case_dir / "resolved_world.json"
        resolved_world.write_text(
            json.dumps(world_dict, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        corpus_path = REPO_ROOT / "data" / "memory" / "elder_home_v1" / "object_memory_corpus.json"
        profile = load_memory_profile(scenario)
        if not profile:
            raise HomeMasterRunError(
                f"homeworld_profile scenario {scenario!r} missing memory_profile.json"
            )
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
        from homemaster.memory_profile import materialize_memory

        materialized = materialize_memory(corpus, profile)
        runtime_memory_dir.mkdir(parents=True, exist_ok=True)
        resolved_memory = runtime_memory_dir / "base_object_memory.json"
        resolved_memory.write_text(
            json.dumps(materialized, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        resolved_world = _resolve_path(world_path, scenario_root / "world.json")
        resolved_memory = _resolve_path(memory_path, scenario_root / "memory.json")
        if not resolved_world.is_file():
            raise HomeMasterRunError(f"missing world file: {resolved_world}")
        if not resolved_memory.is_file():
            raise HomeMasterRunError(f"missing memory file: {resolved_memory}")

    return resolved_world, resolved_memory


# ---------------------------------------------------------------------------
# Debug asset helpers (post-pipeline I/O)
# ---------------------------------------------------------------------------


def _write_stage_07_assets(
    *,
    case_dir: Path,
    results_dir: Path,
    expected: dict[str, Any],
    actual: dict[str, Any],
    status: str,
) -> None:
    safe_expected = sanitize_for_log(expected)
    safe_actual = sanitize_for_log(actual)
    write_json(case_dir / "input.json", safe_expected)
    write_json(case_dir / "expected.json", safe_expected)
    write_json(case_dir / "actual.json", safe_actual)
    _write_stage_07_markdown(case_dir / "result.md", status=status, actual=safe_actual)
    append_jsonl_event(results_dir / "llm_samples.jsonl", event="stage_07", payload=safe_actual)
    append_jsonl_event(
        results_dir / "trace" / f"{actual.get('run_id', case_dir.name)}.jsonl",
        event="stage_07",
        payload=safe_actual,
    )


def _write_stage_07_markdown(path: Path, *, status: str, actual: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"""# Stage 07 Run - {actual.get("run_id", path.parent.name)}

Status: {status}

## Summary

- Scenario: {actual.get("scenario")}
- Utterance: {actual.get("utterance")}
- Final status: {actual.get("final_status")}

## Stage Statuses

```json
{json.dumps(actual.get("stage_statuses", {}), ensure_ascii=False, indent=2)}
```

## Model And Skill Boundary

```json
{json.dumps(actual.get("model_boundary", {}), ensure_ascii=False, indent=2)}
```

## Paths

```json
{json.dumps(actual.get("paths", {}), ensure_ascii=False, indent=2)}
```

## Full Actual

```json
{json.dumps(actual, ensure_ascii=False, indent=2)}
```
"""
    path.write_text(text, encoding="utf-8")


def _resolve_path(value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else REPO_ROOT / path


def _slug_run_id(scenario: str) -> str:
    return f"{scenario}-{int(time.time())}"
