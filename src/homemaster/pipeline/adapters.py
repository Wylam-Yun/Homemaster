"""Thin stage adapters wrapping existing Stage02-06 functions.

Each adapter imports from stage_runtime (not task_runner) to avoid
reverse dependencies.  Adapters only extract inputs from PipelineContext,
call existing functions, and write outputs back via copy-on-write helpers.
"""

from __future__ import annotations

import json

from homemaster.pipeline.core import PipelineContext

# ---------------------------------------------------------------------------
# Stage 02 — Task Understanding
# ---------------------------------------------------------------------------


class Stage02Adapter:
    """Adapter: utterance → TaskCard."""

    @property
    def name(self) -> str:
        return "stage02"

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        from homemaster.pipeline.stage_runtime import run_stage02

        task_card = run_stage02(
            utterance=ctx.utterance,
            run_id=ctx.run_id,
            config_path=str(ctx.config_path),
            provider_name=ctx.provider_name,
        )
        rm = ctx.runtime_mode
        return ctx.with_updates(task_card=task_card).with_stage_status(
            "stage02",
            {
                "status": "PASS",
                "mode": ctx.model_boundary.get("stage02", "unknown"),
                "component_modes": {
                    "task_understanding": rm.task_understanding if rm else "unknown",
                },
            },
        )


# ---------------------------------------------------------------------------
# Stage 03 — Memory RAG
# ---------------------------------------------------------------------------


class Stage03Adapter:
    """Adapter: TaskCard → MemoryRagResult."""

    @property
    def name(self) -> str:
        return "stage03"

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        from homemaster.pipeline.stage_runtime import run_stage03

        runtime_memory_dir = ctx.runtime_memory_dir
        object_memory = runtime_memory_dir / "object_memory.json"
        memory_path = object_memory if object_memory.exists() else ctx.resolved_memory_path

        # P9: convert list-of-dicts negative_evidence to dict format for run_memory_rag
        neg_evidence_dict = None
        if ctx.negative_evidence:
            excluded_ids = [
                e["memory_id"] for e in ctx.negative_evidence if "memory_id" in e
            ]
            excluded_locs = [
                e["location_key"] for e in ctx.negative_evidence if "location_key" in e
            ]
            neg_evidence_dict = {}
            if excluded_ids:
                neg_evidence_dict["excluded_memory_ids"] = excluded_ids
            if excluded_locs:
                neg_evidence_dict["excluded_location_keys"] = excluded_locs

        memory_result = run_stage03(
            task_card=ctx.task_card,
            memory_path=memory_path,
            scenario=ctx.scenario,
            run_id=ctx.run_id,
            config_path=str(ctx.config_path),
            provider_name=ctx.provider_name,
            embedding_provider_name=ctx.embedding_provider_name,
            case_root=ctx.case_dir / "stage_03_cases",
            results_dir=ctx.results_dir,
            negative_evidence=neg_evidence_dict,
        )
        rm = ctx.runtime_mode
        return ctx.with_updates(memory_result=memory_result).with_stage_status(
            "stage03",
            {
                "status": "PASS",
                "mode": ctx.model_boundary.get("stage03_query", "unknown"),
                "embedding": ctx.model_boundary.get("stage03_embedding", "unknown"),
                "component_modes": {
                    "memory_query": rm.memory_query if rm else "unknown",
                    "embedding": rm.embedding if rm else "unknown",
                },
            },
        )


# ---------------------------------------------------------------------------
# Stage 04 — Planning Context (Grounding)
# ---------------------------------------------------------------------------


class Stage04Adapter:
    """Adapter: TaskCard + MemoryRagResult → PlanningContext."""

    @property
    def name(self) -> str:
        return "stage04"

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        from homemaster.planning_context import build_planning_context

        world = json.loads(ctx.resolved_world_path.read_text(encoding="utf-8"))
        planning_build = build_planning_context(
            ctx.task_card, ctx.memory_result.memory_result, world
        )
        planning_context = planning_build.context
        rm = ctx.runtime_mode
        return ctx.with_updates(planning_context=planning_context).with_stage_status(
            "stage04",
            {
                "status": "PASS",
                "grounding_status": planning_context.runtime_state_summary.get("grounding_status"),
                "selected_target": (
                    planning_context.selected_target.memory_id
                    if planning_context.selected_target
                    else None
                ),
                "component_modes": {
                    "grounding": "programmatic",
                },
            },
        )


# ---------------------------------------------------------------------------
# Stage 05 — Orchestration + Execution (merged per P1C)
# ---------------------------------------------------------------------------


class Stage05Adapter:
    """Adapter: PlanningContext → OrchestrationPlan + Stage05ExecutionResult.

    Merges plan generation and execution per P1C requirement — no sub-stage split.
    """

    @property
    def name(self) -> str:
        return "stage05"

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        from homemaster.pipeline.stage_runtime import (
            LiveStepDecisionProvider,
            run_stage05_plan,
        )
        from homemaster.runtime import load_provider_config
        from homemaster.stages.recovery_loop import run_stage05_with_recovery

        plan = run_stage05_plan(
            context=ctx.planning_context,
            config_path=str(ctx.config_path),
            provider_name=ctx.provider_name,
        )
        provider = load_provider_config(
            str(ctx.config_path), provider_name=ctx.provider_name,
        )
        decision_provider = LiveStepDecisionProvider(
            provider,
            scenario=ctx.scenario,
            failure_provider=ctx.failure_provider,
        )
        execution_result, recovery_attempts = run_stage05_with_recovery(
            ctx=ctx,
            plan=plan,
            decision_provider=decision_provider,
            config_path=str(ctx.config_path),
            provider_name=ctx.provider_name,
        )
        rm = ctx.runtime_mode
        return (
            ctx.with_updates(
                orchestration_plan=plan,
                execution_result=execution_result,
                recovery_attempts=recovery_attempts or None,
            )
            .with_final_status(execution_result.final_state.task_status)
            .with_stage_status(
                "stage05",
                {
                    "status": "PASS",
                    "mode": ctx.model_boundary.get("stage05_plan", "unknown"),
                    "final_task_status": execution_result.final_state.task_status,
                    "recovery_attempts_count": len(recovery_attempts),
                    "component_modes": {
                        "planning": rm.planning if rm else "unknown",
                        "step_decision": rm.step_decision if rm else "unknown",
                        "skills": rm.skills if rm else "unknown",
                        "verification": rm.verification if rm else "unknown",
                    },
                },
            )
        )


# ---------------------------------------------------------------------------
# Stage 06 — Summary + Memory Commit
# ---------------------------------------------------------------------------


class Stage06Adapter:
    """Adapter: execution results → evidence + summary + memory commit."""

    @property
    def name(self) -> str:
        return "stage06"

    def execute(self, ctx: PipelineContext) -> PipelineContext:
        from homemaster.memory_commit import (
            build_evidence_bundle,
            build_memory_commit_plan,
            utc_now_iso,
        )
        from homemaster.pipeline.stage_runtime import run_stage06_summary
        from homemaster.stages.summary_runner import persist_stage_06_commit

        evidence_bundle = build_evidence_bundle(
            task_id=ctx.run_id,
            verification_results=ctx.execution_result.verification_results,
            skill_results=ctx.execution_result.skill_results,
            failure_records=ctx.execution_result.failure_records,
            trace_events=[{"event_id": f"stage07:{ctx.run_id}", "summary": "stage07 task run"}],
            created_at=utc_now_iso(),
        )
        task_summary = run_stage06_summary(
            task_card=ctx.task_card,
            execution_state=ctx.execution_result.final_state,
            evidence_bundle=evidence_bundle,
            config_path=str(ctx.config_path),
            provider_name=ctx.provider_name,
            recovery_attempts=ctx.recovery_attempts,
        )
        commit_plan = build_memory_commit_plan(
            task_id=ctx.run_id,
            task_card=ctx.task_card,
            planning_context=ctx.planning_context,
            orchestration_plan=ctx.orchestration_plan,
            execution_state=ctx.execution_result.final_state,
            evidence_bundle=evidence_bundle,
            task_summary=task_summary,
            started_at=utc_now_iso(),
            completed_at=utc_now_iso(),
        )
        memory_commit = persist_stage_06_commit(
            memory_root=ctx.runtime_memory_dir,
            base_memory_path=ctx.resolved_memory_path,
            plan=commit_plan,
            task_id=ctx.run_id,
        )
        rm = ctx.runtime_mode
        return ctx.with_updates(
            evidence_bundle=evidence_bundle,
            task_summary=task_summary,
            memory_commit=memory_commit,
        ).with_stage_status(
            "stage06",
            {
                "status": "PASS",
                "mode": ctx.model_boundary.get("stage06_summary", "unknown"),
                "task_summary_result": task_summary.result,
                "object_memory_update_count": len(commit_plan.object_memory_updates),
                "fact_memory_write_count": len(commit_plan.fact_memory_writes),
                "component_modes": {
                    "summary": rm.summary if rm else "unknown",
                    "memory_commit": rm.memory_commit if rm else "programmatic",
                },
            },
        )
