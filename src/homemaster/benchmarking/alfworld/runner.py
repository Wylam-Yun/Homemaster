"""Runner for HomeMaster ALFWorld benchmark episodes."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
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
)
from homemaster.benchmarking.alfworld.prompt import build_episode_prompt, extract_task_text
from homemaster.benchmarking.alfworld.registry import build_alfworld_tool_registry
from homemaster.benchmarking.alfworld.tracing import (
    AlfworldTraceWriter,
    split_trace_bucket,
    write_readable_trajectories,
)
from homemaster.benchmarking.alfworld.translator import create_translator
from homemaster.benchmarking.alfworld.types import (
    AlfworldBenchmarkConfig,
    AlfworldEpisodeResult,
    AlfworldEnvState,
    AlfworldSummary,
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
        trace.write_model_event({
            "env_type": self.config.env_type,
            "event": "episode_started",
            "run_id": episode_run_id,
            "split": self.config.split,
            "state": _initial_model_trace_state(state, self.config.observation_mode),
        })
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
            _tool_results: list[ToolResultMessage],
        ) -> RuntimeStopDecision | None:
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
