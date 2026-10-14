"""RoboCasa benchmark runner: cell loop over the isolated worker."""

from __future__ import annotations

import dataclasses
import json
import logging
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from homemaster.agent.messages import ToolResultMessage
from homemaster.alfworld.benchmark.episode import BenchmarkApplicationLifecycle
from homemaster.application import RunPolicy, RunRequest, RuntimeStopDecision
from homemaster.config import ConfigError, load_config
from homemaster.events.sinks import JsonlEventSink
from homemaster.providers.attempts import JsonlProviderAttemptSink
from homemaster.robocasa.adapter import RoboCasaEnvAdapter
from homemaster.robocasa.benchmark.cells import (
    load_cells,
    manifest_planner_reference,
    manifest_runtime_protocol,
)
from homemaster.robocasa.prompt import build_episode_prompt
from homemaster.robocasa.tracing import (
    RoboCasaToolDispatchObserver,
    RoboCasaTraceWriter,
)
from homemaster.robocasa.trajectory_memory import (
    RoboCasaFinalEnvironmentState,
    RoboCasaTrajectoryRecord,
    RoboCasaTrajectoryStep,
    determine_outcome,
    extract_failure_guidance,
)
from homemaster.robocasa.types import (
    EpisodeOutcome,
    RoboCasaBenchmarkConfig,
    RoboCasaCell,
    RoboCasaEpisodeResult,
    RoboCasaSummary,
)
from homemaster.robocasa.worker_client import RoboCasaWorkerClient, RoboCasaWorkerError

logger = logging.getLogger(__name__)

TransportFactory = Callable[[], Any]
AdapterFactory = Callable[[RoboCasaBenchmarkConfig], RoboCasaEnvAdapter]
WorkerFactory = Callable[[Path, Path], RoboCasaWorkerClient]


def _require_full_memory_tier(config: RoboCasaBenchmarkConfig) -> None:
    """Fail fast when ``memory_mode=full`` runs on the files tier."""

    if config.memory_mode != "full":
        return
    home_config = load_config(config.provider_config)
    memory = home_config.memory
    if memory.mode == "full" and memory.enabled:
        return
    raise ConfigError(
        "benchmark --memory-mode full requires the full memory tier "
        f"(resolved config has memory.mode={memory.mode!r}); set "
        "memory.mode: full in the HomeMaster config (and install the 'memory' "
        "extra), or rerun with --memory-mode readonly/disabled"
    )


class _RoboCasaTerminalOwner:
    def __init__(self, adapter: RoboCasaEnvAdapter) -> None:
        self._adapter = adapter

    @property
    def succeeded(self) -> bool:
        return self._adapter.current_state.success


class RoboCasaBenchmarkRunner:
    """Run RoboCasa cells through the unified application runtime."""

    def __init__(
        self,
        *,
        config: RoboCasaBenchmarkConfig,
        transport_factory: TransportFactory | None = None,
        adapter_factory: AdapterFactory | None = None,
        worker_factory: WorkerFactory | None = None,
    ) -> None:
        self.config = config
        self._transport_factory = transport_factory
        self._adapter_factory = adapter_factory
        self._worker_factory = worker_factory or self._build_worker
        self._custom_adapter_factory = adapter_factory is not None
        self.run_id = config.run_id or uuid.uuid4().hex[:12]
        self.run_dir = config.trace_root / "robocasa" / self.run_id
        self._worker: RoboCasaWorkerClient | None = None

    # -- orchestration ---------------------------------------------------

    def run(self) -> RoboCasaSummary:
        _require_full_memory_tier(self.config)
        # Fail closed on a reused run_id: a second run must not merge into an
        # existing trace root and corrupt the first run's evidence.
        self.run_dir.mkdir(parents=True)
        cells = load_cells(
            self.config.task_manifest,
            tasks=self.config.tasks,
            seeds=self.config.seeds,
            splits=self.config.task_splits or None,
            limit=self.config.episodes,
        )
        results: list[RoboCasaEpisodeResult] = []
        try:
            for index, cell in enumerate(cells):
                results.append(self._run_cell(cell, index))
        finally:
            self._shutdown_worker()
        summary = RoboCasaSummary(
            run_id=self.run_id, cells=results, config=self._summary_config()
        )
        (self.run_dir / "summary.json").write_text(
            json.dumps(summary.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return summary

    def _run_cell(self, cell: RoboCasaCell, index: int) -> RoboCasaEpisodeResult:
        cell_run_id = f"{self.run_id}-{index + 1:04d}"
        episode_dir = self.run_dir / f"cell-{index + 1:04d}-{cell.task_name}-s{cell.seed}"
        episode_dir.mkdir(parents=True, exist_ok=True)
        trace = RoboCasaTraceWriter(episode_dir)
        try:
            worker = self._ensure_worker(episode_dir)
            adapter = (
                self._adapter_factory(self.config)
                if self._custom_adapter_factory
                else RoboCasaEnvAdapter(worker=worker)
            )
            reconfigured = adapter.ensure_cell(
                rpent_root=self.config.rpent_root,
                task_name=cell.task_name,
                split=cell.split,
                seed=cell.seed,
                cuda_device=self.config.cuda_device,
                hi_res=self.config.hi_res,
                workdir=episode_dir / "worker",
            )
        except Exception as exc:
            logger.warning("cell %s setup failed: %s", cell_run_id, exc)
            self._kill_worker()
            return self._setup_failure_result(
                cell=cell,
                cell_run_id=cell_run_id,
                trace=trace,
                reason="worker_setup_failed",
                detail=str(exc),
            )
        task_language = str(reconfigured.get("task_language") or "")
        outcome = EpisodeOutcome()
        runtime_sink = JsonlEventSink(episode_dir / "runtime")
        try:
            provider_profile = self._resolve_provider_profile()
            config = load_config(self.config.provider_config)
            prompt = build_episode_prompt(
                task_language=task_language,
                memory_mode=self.config.memory_mode,
                max_tool_iterations=self.config.max_tool_iterations,
                max_invalid_actions=self.config.max_invalid_actions,
            )
            entry = BenchmarkApplicationLifecycle(
                config=config,
                memory_mode=self.config.memory_mode,
                profile="robocasa",
                runtime_root=episode_dir / "application",
                session_root=episode_dir / "sessions",
                transport_factory=self._transport_factory,
                event_sink=runtime_sink,
                disable_memory_for_worker=True,
            )
            entry.begin_session(cell_run_id, exit_reason="robocasa_episode_end")
        except Exception as exc:
            logger.exception("cell %s application setup failed", cell_run_id)
            runtime_sink.close()
            return self._setup_failure_result(
                cell=cell,
                cell_run_id=cell_run_id,
                trace=trace,
                reason="application_setup_failed",
                detail=str(exc),
                outcome=outcome,
            )
        episode_result: RoboCasaEpisodeResult | None = None
        try:
            result = entry.run(
                RunRequest(
                    text=prompt,
                    session_id=cell_run_id,
                    profile="robocasa",
                    provider_name=provider_profile.name,
                    environment=adapter,
                    run_policy=RunPolicy(
                        # Keep the turn budget aligned with the tool budget;
                        # the RunPolicy default (12) would cut skill-heavy
                        # episodes short.
                        max_turns=self.config.max_tool_iterations,
                        max_tool_iterations=self.config.max_tool_iterations,
                        deadline_s=cell.timeout_seconds,
                        stop_condition=self._application_stop_condition(adapter),
                    ),
                    dependencies={
                        "robocasa_trace": trace,
                        "robocasa_episode_outcome": outcome,
                        "tool_dispatch_observer": RoboCasaToolDispatchObserver(outcome),
                        "external_terminal_owner": _RoboCasaTerminalOwner(adapter),
                        "provider_attempt_sink_factory": lambda: JsonlProviderAttemptSink(
                            episode_dir / "provider_attempts.jsonl"
                        ),
                    },
                )
            )
            session = entry.application.session_manager.get(cell_run_id).session
            trace.write_session_messages(session)
            final_state = adapter.current_state
            success = final_state.success
            failure_reason = None if success else _failure_reason(result.error_code)
            classification = _classification(
                success=success,
                outcome=outcome,
                error_code=result.error_code,
            )
            score_eligible = classification in {"agent_success", "agent_model_failure"}
            episode_result = RoboCasaEpisodeResult(
                cell=cell,
                episode_id=cell_run_id,
                success=success,
                failure_reason=failure_reason,
                steps=final_state.step_index,
                invalid_actions=final_state.invalid_action_count,
                runtime_status=result.status,
                run_id=cell_run_id,
                trace_path=trace.trace_path,
                classification=classification,
                score_eligible=score_eligible,
                agent_tool_call_count=outcome.agent_tool_call_count,
                backend_action_count=outcome.backend_action_count,
                terminal_tool_call_id=outcome.terminal_tool_call_id,
                terminal_evidence_ref=outcome.terminal_evidence_ref,
                task_language=task_language,
            )
            outcome_value, outcome_reason = determine_outcome(
                {"success": final_state.success, "failure_reason": failure_reason},
                infrastructure_failure=classification
                in {"infrastructure_error", "runtime_failure", "provider_failure", "cancelled"},
            )
            episode_summary = {
                "episode_id": episode_result.episode_id,
                "task_name": cell.task_name,
                "seed": cell.seed,
                "success": episode_result.success,
                "failure_reason": episode_result.failure_reason,
                "steps": episode_result.steps,
                "invalid_actions": episode_result.invalid_actions,
                "run_id": episode_result.run_id,
                "runtime_status": episode_result.runtime_status,
                "outcome": outcome_value,
                "classification": episode_result.classification,
                "score_eligible": episode_result.score_eligible,
            }
            trace.write_episode_finished(episode_summary)
            source_trace_sha256 = trace.source_trace_sha256()
            trace_steps = [
                RoboCasaTrajectoryStep(index=step_index, payload=payload)
                for step_index, payload in enumerate(_read_jsonl(trace.trace_path))
            ]
            failure_summary, failure_advice = extract_failure_guidance(tuple(trace_steps))
            trajectory_record = RoboCasaTrajectoryRecord(
                trajectory_id=str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"robocasa:{cell_run_id}")
                ),
                source_session_id=cell_run_id,
                run_id=cell_run_id,
                episode_id=cell_run_id,
                task_name=cell.task_name,
                split=cell.split,
                seed=cell.seed,
                task=task_language or cell.task_name,
                outcome=outcome_value,
                classification=classification,
                failure_reason=outcome_reason,
                source_trace_path=str(trace.trace_path),
                source_trace_sha256=source_trace_sha256,
                steps=tuple(trace_steps),
                failure_summary=failure_summary,
                failure_advice=failure_advice,
                final_environment_state=RoboCasaFinalEnvironmentState(
                    success=final_state.success,
                    step_index=final_state.step_index,
                    invalid_action_count=final_state.invalid_action_count,
                    task_progress=final_state.task_progress,
                ),
            )
            episode_summary["source_trace_sha256"] = source_trace_sha256
            episode_summary["trajectory_id"] = trajectory_record.trajectory_id
            trace.write_summary(episode_summary)
            trace.write_trajectory_memory(trajectory_record.model_dump(mode="json"))
            if entry.bundle.robocasa_trajectory_writer is not None:
                entry.bundle.robocasa_trajectory_writer.enqueue(trajectory_record)
            result_path = self._write_cell_result(
                cell=cell,
                episode_dir=episode_dir,
                episode=episode_result,
                provider_model=str(provider_profile.model),
            )
            return dataclasses.replace(episode_result, result_path=result_path)
        except Exception as exc:
            logger.exception("cell %s run failed", cell_run_id)
            if isinstance(exc, RoboCasaWorkerError):
                # A worker-level failure (timeout, death) leaves the runtime
                # state uncertain; force a fresh worker for the next cell.
                self._kill_worker()
            return self._setup_failure_result(
                cell=cell,
                cell_run_id=cell_run_id,
                trace=trace,
                reason="cell_run_failed",
                detail=str(exc),
                outcome=outcome,
            )
        finally:
            entry.end_session(cell_run_id)
            entry.close()
            runtime_sink.close()

    # -- stop / classification -------------------------------------------

    def _application_stop_condition(
        self, adapter: RoboCasaEnvAdapter
    ) -> Callable[[Mapping[str, object]], RuntimeStopDecision | None]:
        def application_decide(context: Mapping[str, object]) -> RuntimeStopDecision | None:
            session = context.get("session")
            tool_results = context.get("tool_results")
            if session is None:
                raise TypeError("RoboCasa stop context requires a session")
            if not isinstance(tool_results, list) or not all(
                isinstance(result, ToolResultMessage) for result in tool_results
            ):
                raise TypeError("RoboCasa stop context requires ToolResultMessage results")
            state = adapter.current_state
            if state.success:
                return RuntimeStopDecision(
                    status="replied",
                    final_reply="Environment reports success=true.",
                    payload={"reason": "robocasa_success"},
                )
            if state.invalid_action_count >= self.config.max_invalid_actions:
                return RuntimeStopDecision(
                    status="failed",
                    error_code="benchmark_invalid_action_limit",
                    payload={"reason": "invalid action limit reached"},
                )
            return None

        return application_decide

    # -- worker lifecycle ---------------------------------------------------

    def _ensure_worker(self, episode_dir: Path) -> RoboCasaWorkerClient:
        if self._worker is not None and self._worker.worker_exit_code is None:
            return self._worker
        self._shutdown_worker()
        self._worker = self._worker_factory(
            episode_dir / "worker", episode_dir / "worker.stderr.log"
        )
        return self._worker

    def _shutdown_worker(self) -> None:
        if self._worker is None:
            return
        try:
            self._worker.close()
        except Exception:
            self._worker.terminate()
        finally:
            self._worker = None

    def _kill_worker(self) -> None:
        """Drop the worker after a failed op; servers die via --parent-watch."""
        if self._worker is None:
            return
        try:
            self._worker.terminate()
        finally:
            self._worker = None

    def _build_worker(self, workdir: Path, log_path: Path) -> RoboCasaWorkerClient:
        worker_script = self.config.worker_script
        if worker_script is None:
            worker_script = (
                Path(__file__).resolve().parents[4]
                / "workers"
                / "robocasa_worker"
                / "main.py"
            )
        python_executable = self.config.rpent_python
        if python_executable is None:
            python_executable = (
                self.config.rpent_root / ".venv-robocasa" / "bin" / "python"
            )
        vla_model = (
            self.config.vla_model_path
            or self.config.rpent_root / "checkpoints" / "rldx-1-ft-rc365"
        )
        # NOTE: ProcessDaemon prepends <rpent_root>/../rlinf to the spawned
        # servers' PYTHONPATH — the hkust4 layout (RPent and rlinf checkouts
        # as siblings) satisfies this.  If the checkouts are not siblings,
        # export RPENT_RLINF_ROOT/RLINF_REPO_PATH in the ambient environment.
        # MUJOCO_GL=egl requires GPU EGL for env_server's offscreen rendering.
        env_overrides = {
            "HF_HOME": str(self.config.rpent_root / ".cache" / "huggingface"),
            "RLDX_MODEL_PATH": str(vla_model),
            "RLDX_MAX_CHUNKS": "40",
            "RLDX_SETTLE_PATIENCE": "999",
            "RLDX_ACTION_STEPS_PER_CHUNK": "8",
            "ROBOCASA_ASSETS_PATH": str(Path.home() / ".robocasa" / "assets"),
        }
        return RoboCasaWorkerClient.start(
            python_executable=python_executable,
            worker_source=worker_script,
            artifact_root=workdir,
            log_path=log_path,
            env_overrides=env_overrides,
            startup_timeout_s=self.config.worker_startup_timeout_s,
            request_timeout_s=self.config.request_timeout_s,
        )

    # -- results -------------------------------------------------------------

    def _setup_failure_result(
        self,
        *,
        cell: RoboCasaCell,
        cell_run_id: str,
        trace: RoboCasaTraceWriter,
        reason: str,
        detail: str,
        outcome: EpisodeOutcome | None = None,
    ) -> RoboCasaEpisodeResult:
        payload = {
            "episode_id": cell_run_id,
            "task_name": cell.task_name,
            "seed": cell.seed,
            "success": False,
            "failure_reason": reason,
            "detail": detail,
            "classification": "infrastructure_error",
            "score_eligible": False,
        }
        trace.write_episode_finished(payload)
        trace.write_summary(payload)
        # Per-cell result.json even for infra failures: Target50 consumers
        # expect a file per attempted cell and read valid=false from it.
        result_path = trace.episode_dir / "result.json"
        result_path.write_text(
            json.dumps(
                {
                    "task_name": cell.task_name,
                    "seed": cell.seed,
                    "split": cell.split,
                    "environment_split": cell.split,
                    "evaluation_split": cell.evaluation_split,
                    "success": False,
                    "valid": False,
                    "success_source": "state.success",
                    "failure_reason": reason,
                    "detail": detail,
                    "steps": 0,
                    "planner": {"backend": "homemaster", "model": "unavailable"},
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return RoboCasaEpisodeResult(
            cell=cell,
            episode_id=cell_run_id,
            success=False,
            failure_reason=reason,
            steps=0,
            invalid_actions=0,
            runtime_status="failed",
            run_id=cell_run_id,
            trace_path=trace.trace_path,
            result_path=result_path,
            classification="infrastructure_error",
            score_eligible=False,
            agent_tool_call_count=outcome.agent_tool_call_count if outcome else 0,
            backend_action_count=outcome.backend_action_count if outcome else 0,
        )

    def _write_cell_result(
        self,
        *,
        cell: RoboCasaCell,
        episode_dir: Path,
        episode: RoboCasaEpisodeResult,
        provider_model: str,
    ) -> Path:
        result = {
            "schema_version": 1,
            "task_name": cell.task_name,
            "environment_split": cell.split,
            "evaluation_split": cell.evaluation_split,
            "seed": cell.seed,
            "success": episode.success,
            "valid": episode.score_eligible,
            "success_source": "state.success",
            "termination_reason": episode.runtime_status,
            "failure_reason": episode.failure_reason,
            "classification": episode.classification,
            "steps": episode.steps,
            "invalid_actions": episode.invalid_actions,
            "agent_tool_call_count": episode.agent_tool_call_count,
            "runtime": {"cell_timeout_seconds": cell.timeout_seconds},
            "planner": {
                "backend": "homemaster",
                "model": provider_model,
                "reasoning_effort": None,
                "max_turns": self.config.max_tool_iterations,
            },
        }
        path = episode_dir / "result.json"
        path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path

    def _summary_config(self) -> dict[str, Any]:
        manifest = self.config.task_manifest
        return {
            "split": self.config.split,
            "run_dir": str(self.run_dir),
            "memory_mode": self.config.memory_mode,
            "max_tool_iterations": self.config.max_tool_iterations,
            "max_invalid_actions": self.config.max_invalid_actions,
            "provider_name": self._resolve_provider_profile().name,
            "rpent_root": str(self.config.rpent_root),
            "task_manifest": str(manifest),
            "manifest_planner_reference": manifest_planner_reference(manifest),
            "manifest_runtime_protocol": manifest_runtime_protocol(manifest),
            "cuda_device": self.config.cuda_device,
            "tasks": list(self.config.tasks),
            "seeds": list(self.config.seeds),
        }

    def _resolve_provider_profile(self) -> Any:
        return load_config(self.config.provider_config).get_provider(
            self.config.provider_name,
            kind="chat",
        )


def _failure_reason(error_code: str | None) -> str:
    return str(error_code or "not_success")


def _classification(
    *, success: bool, outcome: EpisodeOutcome, error_code: str | None
) -> str:
    if success:
        return "agent_success"
    if outcome.terminal and outcome.classification:
        return outcome.classification
    if error_code in {"transport_error", "provider_protocol_error", "reply_error"}:
        return "provider_failure"
    if error_code == "user_interrupted":
        return "cancelled"
    # deadline_exceeded / max_tool_iterations_exceeded /
    # benchmark_invalid_action_limit / model_output_truncated all mean the
    # environment ran its course without success — a score-eligible failure,
    # matching Target50 semantics where a timed-out cell counts as failed.
    return "agent_model_failure"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


__all__ = ["RoboCasaBenchmarkRunner"]
