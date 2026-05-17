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
from homemaster.pipeline.core import PipelineContext, PipelineRunner, build_default_registry
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
    use_agent_runtime: bool = True,
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

    # -- Phase 4: AgentRuntime opt-in path --
    if use_agent_runtime:
        return _run_agent_runtime(
            utterance=utterance,
            scenario=scenario,
            run_id=run_id,
            config_path=config_path,
            provider_name=provider_name,
            embedding_provider_name=embedding_provider_name,
            resolved_world=resolved_world,
            resolved_memory=resolved_memory,
            runtime_memory_dir=runtime_memory_dir,
            case_dir=case_dir,
            results_dir=results_dir,
            scenario_root=scenario_root,
            mb=mb,
            paths=paths,
        )

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

    # -- Stage loop via PipelineRunner (compat layer) --
    try:
        registry = build_default_registry()
        ctx = ctx.with_updates(registry=registry)
        runner = PipelineRunner(
            registry,
            stage_modes_fn=_stage_modes,
            logger=logger,
        )
        ctx = runner.run(ctx)
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

    # -- Compat annotations: mark pipeline path boundary --
    from homemaster.pipeline.core import (
        DEFAULT_ENTRYPOINT,
        MIGRATION_REQUIRED,
        RUNTIME_ENTRYPOINT,
    )
    ctx = ctx.with_stage_status("_pipeline_compat_meta", {
        "runtime_entrypoint": RUNTIME_ENTRYPOINT,
        "migration_required": MIGRATION_REQUIRED,
        "default_entrypoint": DEFAULT_ENTRYPOINT,
    })

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
# Phase 4: AgentRuntime opt-in path
# ---------------------------------------------------------------------------


def _run_agent_runtime(
    *,
    utterance: str,
    scenario: str,
    run_id: str,
    config_path: str | Path,
    provider_name: str,
    embedding_provider_name: str,
    resolved_world: Path,
    resolved_memory: Path,
    runtime_memory_dir: Path,
    case_dir: Path,
    results_dir: Path,
    scenario_root: Path,
    mb: dict[str, str],
    paths: dict[str, str],
) -> HomeMasterRunResult:
    """Run a task using AgentRuntime instead of the legacy stage pipeline."""
    from homemaster.agent.context_builder import ContextBuilder
    from homemaster.agent.runtime import AgentRuntime
    from homemaster.config.runtime_settings import RuntimeSettings
    from homemaster.events.sinks import JsonlEventSink
    from homemaster.memory.context_snapshot import ContextSnapshot
    from homemaster.providers.mimo_decision_client import LiveMimoDecisionClient
    from homemaster.runtime import load_provider_config
    from homemaster.tools.builtin import build_skill_registry, build_tool_registry
    from homemaster.tools.dispatcher import ToolDispatcher
    from homemaster.tools.state_updater import StateUpdater

    settings = RuntimeSettings(
        run_id=run_id,
        runtime_root=runtime_memory_dir.parent,
        debug_root=case_dir.parent,
        results_root=results_dir,
        provider_name=provider_name,
        embedding_provider_name=embedding_provider_name,
        config_path=Path(config_path),
        scenario=scenario,
        scenario_root=scenario_root,
        memory_path=resolved_memory,
        world_path=resolved_world,
        case_dir=case_dir,
    )

    tool_registry = build_tool_registry()
    skill_registry = build_skill_registry()
    provider_config = load_provider_config(str(config_path), provider_name=provider_name)

    runtime = AgentRuntime(
        settings=settings,
        decision_client=LiveMimoDecisionClient(provider_config),
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        event_sink=JsonlEventSink(output_dir=results_dir),
        context_builder=ContextBuilder(),
        dispatcher=ToolDispatcher(),
        state_updater=StateUpdater(),
        context_snapshot=ContextSnapshot(output_dir=results_dir),
    )

    agent_result = runtime.run(utterance)

    return _agent_result_to_home_master_result(
        agent_result,
        scenario=scenario,
        utterance=utterance,
        paths=paths,
        model_boundary=mb,
        case_dir=case_dir,
        results_dir=results_dir,
        runtime_memory_root=runtime_memory_dir,
    )


def _agent_result_to_home_master_result(
    agent_result: Any,
    *,
    scenario: str,
    utterance: str,
    paths: dict[str, str],
    model_boundary: dict[str, str],
    case_dir: Path,
    results_dir: Path,
    runtime_memory_root: Path,
) -> HomeMasterRunResult:
    """Convert AgentRunResult to HomeMasterRunResult by synthesizing from state + events."""
    from homemaster.contracts import (
        EvidenceBundle,
        EvidenceRef,
        GroundedMemoryTarget,
        MemoryRetrievalHit,
        MemoryRetrievalResult,
        PlanningContext,
        TaskCard,
    )
    from homemaster.memory_commit import utc_now_iso

    state = agent_result.state
    run_id = agent_result.run_id

    # 6a: task_card
    task_card = None
    if state.task_card:
        try:
            task_card = TaskCard.model_validate(state.task_card)
        except Exception:
            pass

    # 6b: stage_statuses from event log
    tool_to_stage = {
        "understand_task": "stage02",
        "retrieve_memory": "stage03",
        "ground_target": "stage04",
        "get_skill": "stage05",
        "navigate": "stage05",
        "observe": "stage05",
        "manipulate": "stage05",
        "verify": "stage05",
        "update_memory": "stage06",
        "update_user_profile": "stage06",
    }
    stage_statuses: dict[str, dict[str, Any]] = {}
    for event in agent_result.events:
        if event.event_type == "tool_call":
            tool_name = event.payload.get("tool", "")
            stage = tool_to_stage.get(tool_name, "agent_other")
            if stage not in stage_statuses:
                stage_statuses[stage] = {"status": "PASS", "tools": []}
            stage_statuses[stage]["tools"].append(tool_name)
        elif event.event_type == "tool_result":
            tool_name = event.payload.get("tool", "")
            stage = tool_to_stage.get(tool_name, "agent_other")
            if stage in stage_statuses and not event.payload.get("success", True):
                stage_statuses[stage]["status"] = "FAIL"
    stage_statuses["agent_runtime"] = {
        "status": "PASS" if agent_result.final_status == "completed" else "FAIL",
    }

    # 6c: planning_context
    planning_context = None
    if state.task_card and state.memory_hits:
        try:
            tc = TaskCard.model_validate(state.task_card)
            hits = [MemoryRetrievalHit.model_validate(h) for h in state.memory_hits]
            memory_result = MemoryRetrievalResult(hits=hits)
            selected = None
            rejected: list[MemoryRetrievalHit] = []
            if state.selected_target:
                selected = GroundedMemoryTarget(
                    memory_id=state.selected_target.get("memory_id", ""),
                    room_id=state.selected_target.get("room_id", ""),
                    anchor_id=state.selected_target.get("anchor_id", ""),
                    viewpoint_id=state.selected_target.get("viewpoint_id", ""),
                )
            selected_id = selected.memory_id if selected else None
            for c in state.target_candidates:
                if c.get("memory_id") != selected_id:
                    rejected.append(
                        MemoryRetrievalHit(
                            document_id=c.get("memory_id", ""),
                            memory_id=c.get("memory_id"),
                            object_category=c.get("object_category"),
                            room_id=c.get("room_id"),
                            anchor_id=c.get("anchor_id"),
                        )
                    )
            planning_context = PlanningContext(
                task_card=tc,
                memory_evidence=memory_result,
                selected_target=selected,
                rejected_hits=rejected,
            )
        except Exception:
            planning_context = None

    # 6d: evidence_bundle
    evidence_refs: list[EvidenceRef] = []
    verified_facts: list[str] = []
    failure_facts: list[str] = []
    now = utc_now_iso()
    for i, v in enumerate(state.verifications, 1):
        vr = v.get("result", {})
        verified = vr.get("verified", False)
        if verified:
            verified_facts.append(f"verified {vr.get('target_object', 'unknown')}")
        evidence_refs.append(EvidenceRef(
            evidence_id=f"verification:{run_id}:{i}",
            evidence_type="verification_result",
            source_id=f"verification-{i}",
            created_at=now,
            summary=f"verified={verified}",
        ))
    for i, obs in enumerate(state.observations, 1):
        obs_result = obs.get("result", {})
        evidence_refs.append(EvidenceRef(
            evidence_id=f"observation:{run_id}:{i}",
            evidence_type="observation",
            source_id=f"observation-{i}",
            created_at=now,
            summary=f"observed {obs_result.get('object', 'unknown')}",
        ))
    for i, f in enumerate(state.failures, 1):
        failure_facts.append(f.get("error", "unknown failure"))
        evidence_refs.append(EvidenceRef(
            evidence_id=f"failure:{run_id}:{i}",
            evidence_type="failure_record",
            source_id=f"failure-{i}",
            created_at=now,
            summary=f.get("error", "unknown"),
        ))
    evidence_bundle = EvidenceBundle(
        task_id=run_id,
        evidence_refs=evidence_refs,
        verified_facts=verified_facts,
        failure_facts=failure_facts,
    )

    # 6e: execution_result
    execution_result = {
        "final_status": agent_result.final_status,
        "turn_count": state.turn_index,
        "current_location": state.current_location,
        "holding_object": state.holding_object,
    }

    # 6f: memory_commit
    memory_commit = None
    update_actions = [a for a in state.actions if a.get("tool") == "update_memory"]
    if update_actions:
        memory_commit = {"committed": True, "actions": update_actions}

    return HomeMasterRunResult(
        run_id=run_id,
        scenario=scenario,
        utterance=utterance,
        final_status=agent_result.final_status,
        stage_statuses=stage_statuses,
        model_boundary=model_boundary,
        paths=paths,
        task_card=task_card,
        planning_context=planning_context,
        orchestration_plan=None,
        execution_result=execution_result,
        evidence_bundle=evidence_bundle,
        memory_commit=memory_commit,
        case_dir=case_dir,
        results_dir=results_dir,
        runtime_memory_root=runtime_memory_root,
    )


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
