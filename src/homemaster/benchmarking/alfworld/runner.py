"""Runner for HomeMaster ALFWorld benchmark episodes."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable

from homemaster.agent.generic_runtime import (
    GenericAgentRuntime,
    RuntimeStopDecision,
)
from homemaster.agent.generic_runtime import (
    ToolSpec as RuntimeToolSpec,
)
from homemaster.agent.messages import ToolResultMessage
from homemaster.agent.normalized import RunContext
from homemaster.agent.session import AgentSession
from homemaster.benchmarking.alfworld.env_adapter import (
    AlfworldEnvAdapter,
    build_alfworld_batch_env,
)
from homemaster.benchmarking.alfworld.prompt import build_episode_prompt
from homemaster.benchmarking.alfworld.registry import build_alfworld_tool_registry
from homemaster.benchmarking.alfworld.tracing import AlfworldTraceWriter
from homemaster.benchmarking.alfworld.translator import create_translator
from homemaster.benchmarking.alfworld.types import (
    AlfworldBenchmarkConfig,
    AlfworldEpisodeResult,
    AlfworldSummary,
)
from homemaster.config.runtime_settings import RuntimeSettings
from homemaster.events.sinks import JsonlEventSink
from homemaster.providers.mimo_transport import MimoTransport
from homemaster.providers.transport import LLMTransport
from homemaster.runtime import DEFAULT_CONFIG_PATH, load_provider_config
from homemaster.tools.dispatcher import ToolDispatcher

TransportFactory = Callable[[], LLMTransport]
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

    def run(self) -> AlfworldSummary:
        self.config.trace_root.mkdir(parents=True, exist_ok=True)
        adapter = self._adapter_factory(self.config)
        episodes = [
            self._run_episode(adapter=adapter, episode_index=index)
            for index in range(self.config.episodes)
        ]
        summary = AlfworldSummary(run_id=self.run_id, episodes=episodes)
        summary_path = self.config.trace_root / self.run_id / "summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return summary

    def _run_episode(
        self,
        *,
        adapter: AlfworldEnvAdapter,
        episode_index: int,
    ) -> AlfworldEpisodeResult:
        state = adapter.reset()
        episode_run_id = f"{self.run_id}-{episode_index + 1:04d}"
        episode_dir = (
            self.config.trace_root / self.run_id / f"episode-{episode_index + 1:04d}"
        )
        trace = AlfworldTraceWriter(episode_dir)
        runtime_sink = JsonlEventSink(episode_dir / "runtime")
        translator = create_translator(self.config.env_type)
        dispatcher = ToolDispatcher()
        tool_specs = self._register_tools(dispatcher)
        settings = RuntimeSettings(
            run_id=episode_run_id,
            runtime_root=self.config.trace_root / self.run_id / "runtime",
            debug_root=self.config.trace_root / self.run_id / "debug",
            results_root=self.config.trace_root / self.run_id / "results",
            provider_name=self.config.provider_name,
            config_path=self.config.provider_config,
        )
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
            },
        )
        dispatcher.set_run_context(run_context)

        runtime = GenericAgentRuntime(
            transport=self._transport_factory(),
            tool_executor=dispatcher,
            max_tool_iterations=self.config.max_tool_iterations,
            stop_condition=self._stop_condition(adapter),
        )
        result = runtime.run(
            AgentSession(session_id=episode_run_id),
            build_episode_prompt(
                state=state,
                translator=translator,
                memory_mode=self.config.memory_mode,
                max_invalid_actions=self.config.max_invalid_actions,
                max_env_steps=self.config.max_env_steps,
            ),
            tools=tool_specs,
            event_sink=runtime_sink,
            run_id=episode_run_id,
            settings=settings,
        )

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
        trace.write_summary({
            "episode_id": episode_result.episode_id,
            "failure_reason": episode_result.failure_reason,
            "goal_condition_success_rate": episode_result.goal_condition_success_rate,
            "invalid_actions": episode_result.invalid_actions,
            "run_id": episode_result.run_id,
            "runtime_status": episode_result.runtime_status,
            "steps": episode_result.steps,
            "success": episode_result.success,
        })
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

    def _build_transport(self) -> LLMTransport:
        provider = load_provider_config(
            self.config.provider_config or DEFAULT_CONFIG_PATH,
            provider_name=self.config.provider_name,
        )
        return MimoTransport(
            base_url=provider.base_url,
            model=provider.model,
            api_key=provider.api_keys[0],
            protocol=provider.protocol,
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
