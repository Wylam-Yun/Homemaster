"""Runner for HomeMaster ALFWorld benchmark episodes."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homemaster.agent.context import ContextAssembler
from homemaster.agent.generic_runtime import (
    GenericAgentRuntime,
    RuntimeStopDecision,
)
from homemaster.agent.generic_runtime import (
    ToolSpec as RuntimeToolSpec,
)
from homemaster.agent.messages import ContentBlock, ToolResultMessage
from homemaster.agent.normalized import RunContext
from homemaster.agent.session import AgentSession
from homemaster.benchmarking.alfworld.env_adapter import (
    AlfworldEnvAdapter,
    build_alfworld_batch_env,
    build_alfworld_batch_env_with_first_trial,
)
from homemaster.benchmarking.alfworld.prompt import build_episode_prompt, extract_task_text
from homemaster.benchmarking.alfworld.registry import build_alfworld_tool_registry
from homemaster.benchmarking.alfworld.tracing import (
    AlfworldTraceWriter,
    split_trace_bucket,
    write_readable_trajectories,
)
from homemaster.benchmarking.alfworld.traj_index import load_traj_data
from homemaster.benchmarking.alfworld.translator import create_translator
from homemaster.benchmarking.alfworld.types import (
    AGENT_SCORE_CLASSIFICATIONS,
    NOT_RUN_DUE_TO_INFRASTRUCTURE_FAILURE,
    AlfworldBenchmarkConfig,
    AlfworldEnvState,
    AlfworldEpisodeResult,
    AlfworldSummary,
    EpisodeOutcome,
    Subtask,
    SubtaskResult,
    Taskset,
    TasksetResult,
    TasksetRunConfig,
    TasksetRunSummary,
)
from homemaster.config import load_config
from homemaster.events.sinks import JsonlEventSink
from homemaster.prompts.loader import load_prompt
from homemaster.providers.llm_client import LLMClient
from homemaster.task_state.store import TaskStateStore
from homemaster.tools.dispatcher import ToolDispatcher

TransportFactory = Callable[[], Any]
AdapterFactory = Callable[[AlfworldBenchmarkConfig], AlfworldEnvAdapter]


class AlfworldBenchmarkRunner:
    """Run ALFWorld episodes through GenericAgentRuntime and ALFWorld tools."""

    def __init__(
        self,
        *,
        config: AlfworldBenchmarkConfig,
        transport_factory: TransportFactory | None = None,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        self.config = config
        self._transport_factory = transport_factory or self._build_transport
        self._adapter_factory = adapter_factory or self._build_adapter
        self.run_id = config.run_id or uuid.uuid4().hex[:12]
        self.trace_bucket = split_trace_bucket(config.split)
        self.run_dir = config.trace_root / self.trace_bucket / self.run_id

    def run(self) -> AlfworldSummary:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        adapter = self._adapter_factory(self.config)
        episodes = [
            self._run_episode(adapter=adapter, episode_index=index)
            for index in range(self.config.episodes)
        ]
        summary = AlfworldSummary(
            run_id=self.run_id,
            episodes=episodes,
            config=self._summary_config(),
        )
        summary_path = self.run_dir / "summary.json"
        summary_path.write_text(
            json.dumps(summary.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        write_readable_trajectories(self.run_dir)
        return summary

    def _run_episode(
        self,
        *,
        adapter: AlfworldEnvAdapter,
        episode_index: int,
    ) -> AlfworldEpisodeResult:
        episode_run_id = f"{self.run_id}-{episode_index + 1:04d}"
        episode_dir = self.run_dir / f"episode-{episode_index + 1:04d}"
        trace = AlfworldTraceWriter(episode_dir)
        adapter.set_frame_dir(episode_dir / "frames")
        state = adapter.reset()
        trace.write_model_event(
            {
                "env_type": self.config.env_type,
                "event": "episode_started",
                "run_id": episode_run_id,
                "split": self.config.split,
                "state": _initial_model_trace_state(state, self.config.observation_mode),
            }
        )
        runtime_sink = JsonlEventSink(episode_dir / "runtime")
        translator = create_translator(self.config.env_type)
        dispatcher = ToolDispatcher()
        tool_specs = self._register_tools(dispatcher)
        provider_profile = self._resolve_provider_profile()
        config = load_config(self.config.provider_config)
        settings = SimpleNamespace(
            run_id=episode_run_id,
            max_turns=12,
            runtime_root=self.run_dir / "runtime",
            debug_root=self.run_dir / "debug",
            results_root=self.run_dir / "results",
            provider_name=provider_profile.name,
            embedding_provider_name=config.runtime_defaults.default_embedding_provider_name,
            config_path=config.config_path,
            memory_path=None,
            context=config.context,
            runtime_guards=config.runtime,
            prompts=config.prompts,
            observability=config.observability,
        )
        task_state_store = TaskStateStore(run_id=episode_run_id)
        outcome = EpisodeOutcome()
        run_context = RunContext(
            session_id=episode_run_id,
            run_id=episode_run_id,
            turn_index=0,
            settings=settings,
            event_sink=runtime_sink,
            deps={
                "alfworld_env": adapter,
                "alfworld_translator": translator,
                "alfworld_trace": trace,
                "alfworld_config": self.config,
                "alfworld_episode_outcome": outcome,
                "alfworld_semantic_judge_config": (
                    self.config.alfworld_root / "configs" / "semantic_judge_agnes.yaml"
                ),
                "task_state_store": task_state_store,
            },
        )
        dispatcher.set_run_context(run_context)
        system_prompt = load_prompt(settings.prompts.agent_system_prompt)
        context_assembler = ContextAssembler(
            provider=provider_profile,
            policy=settings.context,
            system_prompt=system_prompt,
        )

        runtime = GenericAgentRuntime(
            transport=self._transport_factory(),
            tool_executor=dispatcher,
            max_tool_iterations=self.config.max_tool_iterations,
            stop_condition=self._stop_condition(adapter),
            context_assembler=context_assembler,
            system_prompt=system_prompt,
        )
        prompt = build_episode_prompt(
            state=state,
            translator=translator,
            memory_mode=self.config.memory_mode,
            max_invalid_actions=self.config.max_invalid_actions,
            max_env_steps=self.config.max_env_steps,
            observation_mode=self.config.observation_mode,
        )
        result = runtime.run(
            AgentSession(session_id=episode_run_id),
            prompt,
            tools=tool_specs,
            user_content=_initial_user_content(prompt, state.frame_path),
            event_sink=runtime_sink,
            run_id=episode_run_id,
            settings=settings,
        )
        trace.write_session_messages(result.session)

        final_state = adapter.current_state
        success = final_state.won
        failure_reason = None
        if not success:
            failure_reason = _episode_failure_reason(result.error_code, final_state.done)
        classification = _episode_classification(
            success=success,
            failure_reason=failure_reason,
            outcome=outcome,
        )
        score_eligible = classification in {"agent_success", "agent_model_failure"}
        episode_result = AlfworldEpisodeResult(
            episode_id=final_state.episode_id,
            success=success,
            failure_reason=failure_reason,
            steps=final_state.step_index,
            invalid_actions=final_state.invalid_action_count,
            goal_condition_success_rate=final_state.goal_condition_success_rate,
            runtime_status=result.status,
            run_id=episode_run_id,
            trace_path=trace.trace_path,
            classification=classification,
            score_eligible=score_eligible,
            agent_tool_call_count=outcome.agent_tool_call_count,
            backend_action_count=outcome.backend_action_count,
        )
        episode_summary = {
            "episode_id": episode_result.episode_id,
            "failure_reason": episode_result.failure_reason,
            "goal_condition_success_rate": episode_result.goal_condition_success_rate,
            "invalid_actions": episode_result.invalid_actions,
            "run_id": episode_result.run_id,
            "runtime_status": episode_result.runtime_status,
            "steps": episode_result.steps,
            "success": episode_result.success,
            "classification": episode_result.classification,
            "score_eligible": episode_result.score_eligible,
        }
        trace.write_summary(episode_summary)
        trace.write_trajectory(episode_summary)
        return episode_result

    def _register_tools(self, dispatcher: ToolDispatcher) -> list[RuntimeToolSpec]:
        registry = build_alfworld_tool_registry(memory_mode=self.config.memory_mode)
        tool_specs: list[RuntimeToolSpec] = []
        for name in registry.all_names():
            spec = registry.get(name)
            if spec is None:
                continue
            dispatcher.register(spec)
            if spec.selectable_by_model:
                tool_specs.append(
                    RuntimeToolSpec(
                        name=spec.name,
                        description=spec.description,
                        input_schema=spec.input_schema,
                    )
                )
        return tool_specs

    def _summary_config(self) -> dict[str, object]:
        return {
            "env_type": self.config.env_type,
            "split": self.config.split,
            "trace_bucket": self.trace_bucket,
            "trace_dir": str(self.run_dir),
            "episodes": self.config.episodes,
            "memory_mode": self.config.memory_mode,
            "max_invalid_actions": self.config.max_invalid_actions,
            "max_env_steps": self.config.max_env_steps,
            "max_tool_iterations": self.config.max_tool_iterations,
            "observation_mode": self.config.observation_mode,
            "provider_name": self._resolve_provider_profile().name,
            "seed": self.config.seed,
        }

    def _stop_condition(
        self,
        adapter: AlfworldEnvAdapter,
    ) -> Callable[[AgentSession, list[ToolResultMessage]], RuntimeStopDecision | None]:
        def decide(
            _session: AgentSession,
            tool_results: list[ToolResultMessage],
        ) -> RuntimeStopDecision | None:
            terminal = _terminal_tool_payload(tool_results)
            if terminal is not None:
                classification = str(
                    terminal.get("classification") or "unclassified_execution_failure"
                )
                return RuntimeStopDecision(
                    status="failed",
                    error_code=classification,
                    payload={
                        "terminal": True,
                        "classification": classification,
                        "score_eligible": bool(terminal.get("score_eligible", False)),
                    },
                )
            state = adapter.current_state
            if state.won:
                return RuntimeStopDecision(
                    status="replied",
                    final_reply="Environment reports won=true.",
                    payload={"reason": "alfworld_won"},
                )
            if state.invalid_action_count >= self.config.max_invalid_actions:
                return RuntimeStopDecision(
                    status="failed",
                    error_code="benchmark_invalid_action_limit",
                    payload={"reason": "invalid action limit reached"},
                )
            if state.step_index >= self.config.max_env_steps:
                return RuntimeStopDecision(
                    status="failed",
                    error_code="benchmark_env_step_limit",
                    payload={
                        "max_env_steps": self.config.max_env_steps,
                        "reason": "environment action step limit reached",
                        "steps": state.step_index,
                    },
                )
            if state.done and not state.won:
                return RuntimeStopDecision(
                    status="failed",
                    error_code="benchmark_done_without_won",
                    payload={"reason": "environment ended without won=true"},
                )
            return None

        return decide

    def _build_transport(self) -> LLMClient:
        provider = self._resolve_provider_profile()
        return LLMClient(provider, timeout_s=300.0)

    def _resolve_provider_profile(self):
        return load_config(self.config.provider_config).get_provider(
            self.config.provider_name,
            kind="chat",
        )

    @staticmethod
    def _build_adapter(config: AlfworldBenchmarkConfig) -> AlfworldEnvAdapter:
        return AlfworldEnvAdapter(
            env=build_alfworld_batch_env(config),
            episode_prefix=config.split,
            seed=config.seed,
        )


def _episode_failure_reason(error_code: str | None, done: bool) -> str:
    if error_code is not None:
        return error_code
    if done:
        return "done_without_won"
    return "not_won"


def _terminal_tool_payload(
    tool_results: list[ToolResultMessage],
) -> dict[str, Any] | None:
    for result in tool_results:
        data = getattr(result, "data", None)
        if isinstance(data, dict) and data.get("terminal") is True:
            return data
    return None


def _episode_classification(
    *,
    success: bool,
    failure_reason: str | None,
    outcome: EpisodeOutcome,
) -> str:
    if outcome.terminal and outcome.classification:
        return outcome.classification
    if success:
        return "agent_success"
    infrastructure = {
        "artifact_failure",
        "cancelled",
        "execution_state_uncertain",
        "harness_grounding_failure",
        "harness_navigation_failure",
        "harness_operation_failure",
        "provider_failure",
        "runtime_failure",
        "unclassified_execution_failure",
    }
    if failure_reason in infrastructure:
        return str(failure_reason)
    return "agent_model_failure"


def _initial_model_trace_state(
    state: AlfworldEnvState,
    observation_mode: str,
) -> dict[str, object]:
    if observation_mode == "textual_debug":
        return state.to_model_visible_dict()
    return {
        "episode_id": state.episode_id,
        "frame_path": state.frame_path,
        "task": extract_task_text(state.task),
    }


def _initial_user_content(prompt: str, frame_path: str | None) -> list[ContentBlock]:
    content = [ContentBlock(text=prompt)]
    if frame_path:
        try:
            content.append(ContentBlock.from_image_path(frame_path))
        except OSError:
            pass
    return content


# ----------------------------------------------------------------------
# Long-horizon taskset runner: one persistent agent session per taskset,
# swapping goals in the same loaded scene via adapter.advance_goal.
# ----------------------------------------------------------------------


class AlfworldTasksetRunner(AlfworldBenchmarkRunner):
    """Run long-horizon tasksets (a chain of subtasks in one persistent scene).

    Reuses AlfworldBenchmarkRunner helpers (transport, adapter, stop_condition,
    tool registry). Overrides run() to iterate tasksets instead of episodes,
    and shares one AgentSession + runtime across subtasks within a taskset.
    """

    def __init__(
        self,
        *,
        taskset_config: TasksetRunConfig,
        transport_factory: TransportFactory | None = None,
    ) -> None:
        # Build an AlfworldBenchmarkConfig shim so the inherited helpers
        # (_resolve_provider_profile, _stop_condition, _register_tools, ...) work
        # without modification.
        shim = AlfworldBenchmarkConfig(
            alfworld_root=taskset_config.alfworld_root,
            alfworld_config=taskset_config.alfworld_config,
            trace_root=taskset_config.trace_root,
            env_type=taskset_config.env_type,
            split=taskset_config.split,
            episodes=1,
            memory_mode=taskset_config.memory_mode,
            max_invalid_actions=taskset_config.max_invalid_actions,
            max_env_steps=taskset_config.max_env_steps,
            max_tool_iterations=taskset_config.max_tool_iterations,
            provider_config=taskset_config.provider_config,
            provider_name=taskset_config.provider_name,
            run_id=taskset_config.run_id,
            observation_mode=taskset_config.observation_mode,
            seed=taskset_config.seed,
        )
        super().__init__(
            config=shim,
            transport_factory=transport_factory,
            adapter_factory=self._build_taskset_adapter,
        )
        self.taskset_config = taskset_config
        self._first_trial_by_taskset: dict[str, Path] = {}

    def _build_taskset_adapter(self, config: AlfworldBenchmarkConfig) -> AlfworldEnvAdapter:
        # adapter_factory is called once per run(); the taskset runner builds a
        # fresh adapter per taskset in _run_taskset, so this should not be hit.
        raise RuntimeError(
            "AlfworldTasksetRunner builds adapters per taskset; this adapter_factory path is unused"
        )

    def run(self) -> TasksetRunSummary:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        results: list[TasksetResult] = []
        for taskset in self.taskset_config.tasksets:
            results.append(self._run_taskset(taskset))
        summary = TasksetRunSummary(
            run_id=self.run_id,
            taskset_results=results,
            config=self._taskset_summary_config(),
        )
        summary_path = self.run_dir / "summary.json"
        summary_path.write_text(
            json.dumps(summary.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        write_readable_trajectories(self.run_dir)
        return summary

    def _run_taskset(self, taskset: Taskset) -> TasksetResult:
        taskset_dir = self.run_dir / f"taskset-{taskset.id}"
        taskset_dir.mkdir(parents=True, exist_ok=True)
        first_trial = taskset.subtasks[0].traj_path
        assert first_trial is not None  # TasksetRunConfig.__post_init__ guarantees this

        adapter = AlfworldEnvAdapter(
            env=build_alfworld_batch_env_with_first_trial(
                self.config, first_trial_path=first_trial
            ),
            episode_prefix=f"{self.config.split}/{taskset.id}",
            seed=self.config.seed,
        )

        subtask_results: list[SubtaskResult] = []
        session = AgentSession(session_id=f"{self.run_id}-{taskset.id}")
        task_state_store = TaskStateStore(run_id=f"{self.run_id}-{taskset.id}")
        runtime_sink = JsonlEventSink(taskset_dir / "runtime")
        translator = create_translator(self.config.env_type)
        dispatcher = ToolDispatcher()
        tool_specs = self._register_tools(dispatcher)
        provider_profile = self._resolve_provider_profile()
        config = load_config(self.config.provider_config)
        settings = SimpleNamespace(
            run_id=f"{self.run_id}-{taskset.id}",
            max_turns=12,
            runtime_root=taskset_dir / "runtime",
            debug_root=taskset_dir / "debug",
            results_root=taskset_dir / "results",
            provider_name=provider_profile.name,
            embedding_provider_name=config.runtime_defaults.default_embedding_provider_name,
            config_path=config.config_path,
            memory_path=None,
            context=config.context,
            runtime_guards=config.runtime,
            prompts=config.prompts,
            observability=config.observability,
        )
        run_context = RunContext(
            session_id=f"{self.run_id}-{taskset.id}",
            run_id=f"{self.run_id}-{taskset.id}",
            turn_index=0,
            settings=settings,
            event_sink=runtime_sink,
            deps={
                "alfworld_env": adapter,
                "alfworld_translator": translator,
                "alfworld_config": self.config,
                "alfworld_semantic_judge_config": (
                    self.config.alfworld_root / "configs" / "semantic_judge_agnes.yaml"
                ),
                "task_state_store": task_state_store,
            },
        )
        dispatcher.set_run_context(run_context)
        system_prompt = load_prompt(settings.prompts.agent_system_prompt)
        context_assembler = ContextAssembler(
            provider=provider_profile,
            policy=settings.context,
            system_prompt=system_prompt,
        )

        for idx, subtask in enumerate(taskset.subtasks):
            outcome = EpisodeOutcome()
            subtask_dir = taskset_dir / f"subtask-{idx + 1:02d}"
            subtask_dir.mkdir(parents=True, exist_ok=True)
            trace = AlfworldTraceWriter(subtask_dir)
            adapter.set_frame_dir(subtask_dir / "frames")
            run_context.deps["alfworld_trace"] = trace
            run_context.deps["alfworld_current_subtask"] = subtask
            run_context.deps["alfworld_episode_outcome"] = outcome

            if idx == 0:
                state = adapter.reset()
                traj_data = load_traj_data(subtask.traj_path)
            else:
                traj_data = load_traj_data(subtask.traj_path)
                state = adapter.advance_goal(
                    traj_data,
                    subtask_label=f"{taskset.id}-subtask-{idx + 1:02d}",
                )
            run_context.deps["alfworld_current_traj_data"] = traj_data

            trace.write_model_event(
                {
                    "env_type": self.config.env_type,
                    "event": "subtask_started",
                    "run_id": f"{self.run_id}-{taskset.id}-subtask-{idx + 1:02d}",
                    "taskset_id": taskset.id,
                    "subtask_index": idx,
                    "state": _initial_model_trace_state(state, self.config.observation_mode),
                }
            )

            runtime = GenericAgentRuntime(
                transport=self._transport_factory(),
                tool_executor=dispatcher,
                max_tool_iterations=self.config.max_tool_iterations,
                stop_condition=self._stop_condition(adapter),
                context_assembler=context_assembler,
                system_prompt=system_prompt,
            )
            prompt = build_episode_prompt(
                state=state,
                translator=translator,
                memory_mode=self.config.memory_mode,
                max_invalid_actions=self.config.max_invalid_actions,
                max_env_steps=self.config.max_env_steps,
                observation_mode=self.config.observation_mode,
                subtask_instruction=subtask.instruction,
            )
            result = runtime.run(
                session,
                prompt,
                tools=tool_specs,
                user_content=_initial_user_content(prompt, state.frame_path),
                event_sink=runtime_sink,
                run_id=f"{self.run_id}-{taskset.id}-subtask-{idx + 1:02d}",
                settings=settings,
            )
            trace.write_session_messages(result.session)

            final_state = adapter.current_state
            success = adapter.is_current_goal_satisfied()
            runtime_failure_reason = (
                None
                if success
                else _subtask_failure_reason(
                    result.error_code,
                    final_state,
                )
            )
            classification = _episode_classification(
                success=success,
                failure_reason=runtime_failure_reason,
                outcome=outcome,
            )
            score_eligible = classification in AGENT_SCORE_CLASSIFICATIONS
            failure_reason = runtime_failure_reason if score_eligible else classification
            subtask_results.append(
                SubtaskResult(
                    index=idx,
                    goal_type=subtask.goal_type,
                    object=subtask.object,
                    target=subtask.toggle or subtask.parent or "",
                    instruction=subtask.instruction,
                    success=success,
                    failure_reason=failure_reason,
                    steps=final_state.step_index,
                    invalid_actions=final_state.invalid_action_count,
                    goal_condition_success_rate=adapter.current_goal_condition_success_rate(),
                    runtime_status=result.status,
                    trace_path=trace.trace_path,
                    classification=classification,
                    score_eligible=score_eligible,
                    agent_tool_call_count=outcome.agent_tool_call_count,
                    backend_action_count=outcome.backend_action_count,
                    terminal_tool_call_id=outcome.terminal_tool_call_id,
                    terminal_evidence_ref=outcome.terminal_evidence_ref,
                )
            )
            trace.write_summary(subtask_results[-1].to_dict())

            if not score_eligible:
                for pending_idx, pending in enumerate(
                    taskset.subtasks[idx + 1 :],
                    start=idx + 1,
                ):
                    subtask_results.append(
                        _not_run_subtask_result(
                            subtask=pending,
                            index=pending_idx,
                            taskset_dir=taskset_dir,
                        )
                    )
                break

            # Agent failure breaks the chain without converting unattempted
            # work into an infrastructure failure.
            if not success:
                break

        chain_success = all(r.success for r in subtask_results) and (
            len(subtask_results) == len(taskset.subtasks)
        )
        return TasksetResult(
            taskset_id=taskset.id,
            floorplan=taskset.floorplan,
            difficulty=taskset.difficulty,
            description=taskset.description,
            subtasks=subtask_results,
            chain_success=chain_success,
            trace_dir=taskset_dir,
        )

    def _taskset_summary_config(self) -> dict[str, object]:
        return {
            "env_type": self.config.env_type,
            "split": self.config.split,
            "trace_dir": str(self.run_dir),
            "taskset_count": len(self.taskset_config.tasksets),
            "memory_mode": self.config.memory_mode,
            "max_invalid_actions": self.config.max_invalid_actions,
            "max_env_steps": self.config.max_env_steps,
            "max_tool_iterations": self.config.max_tool_iterations,
            "observation_mode": self.config.observation_mode,
            "provider_name": self._resolve_provider_profile().name,
            "seed": self.config.seed,
            "failure_simulation": {
                "enabled": self.taskset_config.failure_simulation.enabled,
                "grasp_failure_rate": self.taskset_config.failure_simulation.grasp_failure_rate,
                "put_failure_rate": self.taskset_config.failure_simulation.put_failure_rate,
                "navigate_failure_rate": (
                    self.taskset_config.failure_simulation.navigate_failure_rate
                ),
            },
            "long_horizon": {
                "keep_scene_across_subtasks": (
                    self.taskset_config.long_horizon.keep_scene_across_subtasks
                ),
            },
            "tasksets": [
                {
                    "id": t.id,
                    "floorplan": t.floorplan,
                    "difficulty": t.difficulty,
                    "description": t.description,
                    "subtask_count": len(t.subtasks),
                }
                for t in self.taskset_config.tasksets
            ],
        }


def _subtask_failure_reason(error_code: str | None, state: AlfworldEnvState) -> str:
    if error_code is not None:
        return error_code
    if state.done and not state.won:
        return "done_without_won"
    if state.step_index >= state.invalid_action_count and state.step_index > 0:
        # Heuristic: ran out of steps without satisfying the goal.
        return "goal_not_satisfied"
    return "goal_not_satisfied"


def _not_run_subtask_result(
    *,
    subtask: Subtask,
    index: int,
    taskset_dir: Path,
) -> SubtaskResult:
    trace = AlfworldTraceWriter(taskset_dir / f"subtask-{index + 1:02d}")
    result = SubtaskResult(
        index=index,
        goal_type=subtask.goal_type,
        object=subtask.object,
        target=subtask.toggle or subtask.parent or "",
        instruction=subtask.instruction,
        success=False,
        failure_reason=NOT_RUN_DUE_TO_INFRASTRUCTURE_FAILURE,
        steps=0,
        invalid_actions=0,
        goal_condition_success_rate=0.0,
        runtime_status="not_run",
        trace_path=trace.trace_path,
        classification=NOT_RUN_DUE_TO_INFRASTRUCTURE_FAILURE,
        score_eligible=False,
    )
    trace.write_summary(result.to_dict())
    return result
