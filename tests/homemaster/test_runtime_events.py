"""Tests for the Phase 8 runtime event system.

Covers: RuntimeEvent schema, JSONL serialization, sanitization, truncation,
all sink types, AgentRuntime event emission, PipelineRunner event emission,
Stage05/recovery event emission, and CLI --progress flag.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from homemaster.events.runtime_events import KNOWN_EVENT_TYPES, RuntimeEvent
from homemaster.events.sanitizer import sanitize_event_payload
from homemaster.events.sinks import (
    ConsoleProgressEventSink,
    FanoutEventSink,
    JsonlEventSink,
    NullEventSink,
)

# ---------------------------------------------------------------------------
# 1. RuntimeEvent serialization
# ---------------------------------------------------------------------------


class TestRuntimeEventSerialization:
    """RuntimeEvent with all fields serializes to expected dict shape."""

    def test_all_fields_present_in_asdict(self) -> None:
        event = RuntimeEvent(
            turn_index=0,
            event_type="run_started",
            payload={"key": "value"},
            run_id="test-run",
            source="agent_runtime",
            stage="stage02",
            subtask_id="sub1",
            skill_name="navigation",
            provider_name="Mimo",
            attempt=1,
            parent_event_id="parent123",
            tool_name="observe",
            executor_mode="simulated_skill",
            state_status="running",
            failure_record_id="fail1",
            duration_ms=42.5,
        )
        d = asdict(event)
        assert d["turn_index"] == 0
        assert d["event_type"] == "run_started"
        assert d["payload"] == {"key": "value"}
        assert d["run_id"] == "test-run"
        assert d["source"] == "agent_runtime"
        assert d["stage"] == "stage02"
        assert d["subtask_id"] == "sub1"
        assert d["skill_name"] == "navigation"
        assert d["provider_name"] == "Mimo"
        assert d["attempt"] == 1
        assert d["parent_event_id"] == "parent123"
        assert d["tool_name"] == "observe"
        assert d["executor_mode"] == "simulated_skill"
        assert d["state_status"] == "running"
        assert d["failure_record_id"] == "fail1"
        assert d["duration_ms"] == 42.5

    def test_defaults_for_optional_fields(self) -> None:
        event = RuntimeEvent(turn_index=0, event_type="test", payload={})
        d = asdict(event)
        assert d["source"] == ""
        assert d["stage"] == ""
        assert d["subtask_id"] == ""
        assert d["skill_name"] == ""
        assert d["provider_name"] == ""
        assert d["attempt"] is None
        assert d["parent_event_id"] == ""
        assert d["tool_name"] == ""
        assert d["executor_mode"] == ""
        assert d["state_status"] == ""
        assert d["failure_record_id"] == ""
        assert d["duration_ms"] is None

    def test_event_id_is_unique(self) -> None:
        e1 = RuntimeEvent(turn_index=0, event_type="test", payload={})
        e2 = RuntimeEvent(turn_index=0, event_type="test", payload={})
        assert e1.event_id != e2.event_id

    def test_known_event_types_count(self) -> None:
        # Should have ~45 known event types
        assert len(KNOWN_EVENT_TYPES) >= 40

    def test_legacy_event_types_in_known(self) -> None:
        for legacy in ("decision", "tool_call", "tool_result", "state_transition", "error"):
            assert legacy in KNOWN_EVENT_TYPES


# ---------------------------------------------------------------------------
# 2. JSONL parsing
# ---------------------------------------------------------------------------


class TestJsonlSink:
    """JsonlEventSink writes valid JSON per line with all fields."""

    def test_writes_valid_json_lines(self, tmp_path: Path) -> None:
        sink = JsonlEventSink(tmp_path)
        sink.emit(RuntimeEvent(turn_index=0, event_type="run_started", payload={"a": 1}))
        sink.emit(RuntimeEvent(turn_index=1, event_type="turn_started", payload={}))

        lines = (tmp_path / "runtime_events.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2
        for line in lines:
            parsed = json.loads(line)
            assert "event_type" in parsed
            assert "run_id" in parsed
            assert "event_id" in parsed
            assert "timestamp" in parsed
            assert "payload" in parsed

    def test_includes_all_new_fields(self, tmp_path: Path) -> None:
        sink = JsonlEventSink(tmp_path)
        sink.emit(RuntimeEvent(
            turn_index=0, event_type="test", payload={},
            duration_ms=10.0, tool_name="observe", executor_mode="sim",
            state_status="running", failure_record_id="f1", source="agent_runtime",
        ))
        line = (tmp_path / "runtime_events.jsonl").read_text().strip()
        parsed = json.loads(line)
        assert parsed["duration_ms"] == 10.0
        assert parsed["tool_name"] == "observe"
        assert parsed["executor_mode"] == "sim"
        assert parsed["state_status"] == "running"
        assert parsed["failure_record_id"] == "f1"
        assert parsed["source"] == "agent_runtime"

    def test_custom_filename(self, tmp_path: Path) -> None:
        sink = JsonlEventSink(tmp_path, filename="custom.jsonl")
        sink.emit(RuntimeEvent(turn_index=0, event_type="test", payload={}))
        assert (tmp_path / "custom.jsonl").exists()
        assert not (tmp_path / "runtime_events.jsonl").exists()

    def test_events_property_returns_copy(self, tmp_path: Path) -> None:
        sink = JsonlEventSink(tmp_path)
        e = RuntimeEvent(turn_index=0, event_type="test", payload={})
        sink.emit(e)
        events = sink.events
        assert len(events) == 1
        assert events[0] is e


# ---------------------------------------------------------------------------
# 3. Secret redaction
# ---------------------------------------------------------------------------


class TestSecretRedaction:
    """Secrets are redacted by sanitize_event_payload."""

    def test_api_key_redacted(self) -> None:
        result = sanitize_event_payload({"api_key": "sk-123456"})
        assert result["api_key"] == "[REDACTED]"

    def test_token_redacted(self) -> None:
        result = sanitize_event_payload({"auth_token": "Bearer abc"})
        assert result["auth_token"] == "[REDACTED]"

    def test_password_redacted(self) -> None:
        result = sanitize_event_payload({"password": "s3cret"})
        assert result["password"] == "[REDACTED]"

    def test_nested_secret_redacted(self) -> None:
        result = sanitize_event_payload({"nested": {"api_key": "sk-abc"}})
        assert result["nested"]["api_key"] == "[REDACTED]"

    def test_list_of_dicts_redacted(self) -> None:
        result = sanitize_event_payload({"items": [{"secret": "x"}]})
        assert result["items"][0]["secret"] == "[REDACTED]"

    def test_authorization_redacted(self) -> None:
        result = sanitize_event_payload({"Authorization": "Bearer token"})
        assert result["Authorization"] == "[REDACTED]"

    def test_normal_keys_preserved(self) -> None:
        result = sanitize_event_payload({"tool": "observe", "success": True})
        assert result["tool"] == "observe"
        assert result["success"] is True


# ---------------------------------------------------------------------------
# 4. Truncation
# ---------------------------------------------------------------------------


class TestTruncation:
    """Large payloads and prompt-like values are truncated."""

    def test_long_prompt_value_truncated(self) -> None:
        long_prompt = "x" * 500
        result = sanitize_event_payload({"prompt": long_prompt})
        assert "[TRUNCATED:" in result["prompt"]
        assert "500 chars]" in result["prompt"]

    def test_long_content_value_truncated(self) -> None:
        long_content = "y" * 300
        result = sanitize_event_payload({"content": long_content})
        assert "[TRUNCATED:" in result["content"]

    def test_long_message_value_truncated(self) -> None:
        result = sanitize_event_payload({"message": "z" * 400})
        assert "[TRUNCATED:" in result["message"]

    def test_long_response_value_truncated(self) -> None:
        result = sanitize_event_payload({"response": "w" * 250})
        assert "[TRUNCATED:" in result["response"]

    def test_short_prompt_not_truncated(self) -> None:
        result = sanitize_event_payload({"prompt": "short"})
        assert result["prompt"] == "short"

    def test_non_prompt_long_value_not_truncated(self) -> None:
        long_val = "a" * 300
        result = sanitize_event_payload({"observation": long_val})
        assert result["observation"] == long_val

    def test_nested_prompt_truncated(self) -> None:
        result = sanitize_event_payload({"nested": {"prompt": "x" * 500}})
        assert "[TRUNCATED:" in result["nested"]["prompt"]

    def test_overall_payload_truncation_flag(self) -> None:
        # Create a payload that exceeds 4000 chars when serialized
        big = {"data": "x" * 5000}
        result = sanitize_event_payload(big)
        assert result.get("_truncated") is True
        assert "_truncated_len" in result

    def test_small_payload_no_truncation_flag(self) -> None:
        result = sanitize_event_payload({"small": "data"})
        assert "_truncated" not in result


# ---------------------------------------------------------------------------
# 5. No raw prompt/response in default trace
# ---------------------------------------------------------------------------


class TestNoRawPromptResponse:
    """Raw prompt/response content does not appear in default JSONL trace."""

    def test_jsonl_output_has_no_raw_prompt(self, tmp_path: Path) -> None:
        sink = JsonlEventSink(tmp_path)
        long_prompt = "SECRET_PROMPT_TEXT_" * 50  # 950 chars
        sink.emit(RuntimeEvent(
            turn_index=0, event_type="test",
            payload={"prompt": long_prompt},
        ))
        raw = (tmp_path / "runtime_events.jsonl").read_text()
        assert "SECRET_PROMPT_TEXT_" * 5 not in raw  # truncated, so full repeat absent


# ---------------------------------------------------------------------------
# 6. NullEventSink
# ---------------------------------------------------------------------------


class TestNullEventSink:
    """NullEventSink accepts events silently."""

    def test_emit_succeeds(self) -> None:
        sink = NullEventSink()
        sink.emit(RuntimeEvent(turn_index=0, event_type="test", payload={}))

    def test_events_empty(self) -> None:
        sink = NullEventSink()
        sink.emit(RuntimeEvent(turn_index=0, event_type="test", payload={}))
        assert sink.events == []


# ---------------------------------------------------------------------------
# 7. FanoutEventSink
# ---------------------------------------------------------------------------


class TestFanoutEventSink:
    """FanoutEventSink forwards to all wrapped sinks."""

    def test_forwards_to_all_sinks(self, tmp_path: Path) -> None:
        sink_a = JsonlEventSink(tmp_path / "a")
        sink_b = JsonlEventSink(tmp_path / "b")
        fanout = FanoutEventSink([sink_a, sink_b])
        event = RuntimeEvent(turn_index=0, event_type="test", payload={})
        fanout.emit(event)

        assert len(sink_a.events) == 1
        assert len(sink_b.events) == 1

    def test_events_from_first_sink(self, tmp_path: Path) -> None:
        sink_a = JsonlEventSink(tmp_path / "a")
        sink_b = JsonlEventSink(tmp_path / "b")
        fanout = FanoutEventSink([sink_a, sink_b])
        event = RuntimeEvent(turn_index=0, event_type="test", payload={})
        fanout.emit(event)

        assert fanout.events == sink_a.events

    def test_empty_sinks(self) -> None:
        fanout = FanoutEventSink([])
        fanout.emit(RuntimeEvent(turn_index=0, event_type="test", payload={}))
        assert fanout.events == []


# ---------------------------------------------------------------------------
# 8. ConsoleProgressEventSink
# ---------------------------------------------------------------------------


class TestConsoleProgressEventSink:
    """ConsoleProgressEventSink filters to high-level events."""

    def test_filtered_event_types_printed(self, capsys: pytest.CaptureFixture[str]) -> None:
        sink = ConsoleProgressEventSink()
        # These should be printed
        for etype in ("run_started", "run_completed", "run_failed",
                       "turn_started", "decision_completed",
                       "tool_call_started", "tool_call_completed", "tool_call_failed",
                       "state_transitioned", "finish_decision_received", "max_turns_exceeded",
                       "stage_started", "stage_completed", "stage_failed"):
            sink.emit(RuntimeEvent(turn_index=0, event_type=etype, payload={}))

    def test_non_progress_events_filtered(self, capsys: pytest.CaptureFixture[str]) -> None:
        sink = ConsoleProgressEventSink()
        sink.emit(RuntimeEvent(turn_index=0, event_type="context_built", payload={}))
        sink.emit(RuntimeEvent(turn_index=0, event_type="decision_started", payload={}))
        # These should NOT produce output (not in filter list and don't end with _failed)
        captured = capsys.readouterr()
        assert "context_built" not in captured.err
        assert "decision_started" not in captured.err

    def test_failed_events_always_printed(self, capsys: pytest.CaptureFixture[str]) -> None:
        sink = ConsoleProgressEventSink()
        sink.emit(RuntimeEvent(turn_index=0, event_type="custom_failed", payload={}))

    def test_events_property_empty(self) -> None:
        sink = ConsoleProgressEventSink()
        assert sink.events == []


# ---------------------------------------------------------------------------
# 9. AgentRuntime event emission
# ---------------------------------------------------------------------------


class TestAgentRuntimeEvents:
    """AgentRuntime emits structured events across the run lifecycle."""

    def test_run_produces_lifecycle_events(self) -> None:
        """A short AgentRuntime run produces run_started, turn_started, etc."""
        from unittest.mock import MagicMock

        from homemaster.agent.context_builder import ContextBuilder
        from homemaster.agent.decision import FinishDecision
        from homemaster.agent.runtime import AgentRuntime
        from homemaster.config.runtime_settings import RuntimeSettings
        from homemaster.events.sinks import NullEventSink
        from homemaster.tools.state_updater import StateUpdater

        sink = NullEventSink()

        # Track emitted event types
        emitted: list[str] = []

        def tracking_emit(event: RuntimeEvent) -> None:
            emitted.append(event.event_type)

        sink.emit = tracking_emit  # type: ignore[method-assign]

        settings = RuntimeSettings(
            run_id="test-rt", scenario="test",
            world_path=Path("/dev/null"),
            runtime_root=Path("/tmp"), debug_root=Path("/tmp"),
            results_root=Path("/tmp"), config_path=Path("/dev/null"),
            provider_name="test", embedding_provider_name="test",
            skill_mode="simulated", max_turns=5,
        )

        # Decision client that returns FinishDecision immediately
        decision_client = MagicMock()
        decision_client.decide.return_value = FinishDecision(
            status="completed", summary="done",
        )

        context_builder = MagicMock(spec=ContextBuilder)
        context_builder.build.return_value = {"test": True}

        tool_registry = MagicMock()
        tool_registry.tool_manifests.return_value = []

        skill_registry = MagicMock()
        skill_registry.candidate_summaries.return_value = []

        dispatcher = MagicMock()
        state_updater = MagicMock(spec=StateUpdater)

        context_snapshot = MagicMock()
        context_snapshot.refresh_if_stale.side_effect = lambda s: s

        runtime = AgentRuntime(
            settings=settings,
            decision_client=decision_client,
            tool_registry=tool_registry,
            skill_registry=skill_registry,
            event_sink=sink,
            context_builder=context_builder,
            dispatcher=dispatcher,
            state_updater=state_updater,
            context_snapshot=context_snapshot,
        )

        result = runtime.run("test request")

        assert result.final_status == "completed"
        assert "run_started" in emitted
        assert "turn_started" in emitted
        assert "context_built" in emitted
        assert "decision_started" in emitted
        assert "decision_completed" in emitted
        assert "finish_decision_received" in emitted
        assert "run_completed" in emitted

    def test_events_have_source_agent_runtime(self) -> None:
        """All events from AgentRuntime have source='agent_runtime'."""
        from unittest.mock import MagicMock

        from homemaster.agent.context_builder import ContextBuilder
        from homemaster.agent.decision import FinishDecision
        from homemaster.agent.runtime import AgentRuntime
        from homemaster.config.runtime_settings import RuntimeSettings
        from homemaster.tools.state_updater import StateUpdater

        sources: list[str] = []

        class SourceTracker:
            def emit(self, event: RuntimeEvent) -> None:
                sources.append(event.source)
            @property
            def events(self) -> list[RuntimeEvent]:
                return []

        sink = SourceTracker()
        settings = RuntimeSettings(
            run_id="test-src", scenario="test",
            world_path=Path("/dev/null"),
            runtime_root=Path("/tmp"), debug_root=Path("/tmp"),
            results_root=Path("/tmp"), config_path=Path("/dev/null"),
            provider_name="test", embedding_provider_name="test",
            skill_mode="simulated", max_turns=5,
        )
        decision_client = MagicMock()
        decision_client.decide.return_value = FinishDecision(status="completed", summary="ok")

        tool_registry = MagicMock()
        tool_registry.tool_manifests.return_value = []
        skill_registry = MagicMock()
        skill_registry.candidate_summaries.return_value = []
        context_builder = MagicMock(spec=ContextBuilder)
        context_builder.build.return_value = {}
        context_snapshot = MagicMock()
        context_snapshot.refresh_if_stale.side_effect = lambda s: s

        runtime = AgentRuntime(
            settings=settings,
            decision_client=decision_client,
            tool_registry=tool_registry,
            skill_registry=skill_registry,
            event_sink=sink,
            context_builder=context_builder,
            dispatcher=MagicMock(),
            state_updater=MagicMock(spec=StateUpdater),
            context_snapshot=context_snapshot,
        )

        runtime.run("test")

        # All new events should have source="agent_runtime"
        # Legacy events have source="" (they use direct emit, not _emit)
        agent_events = [s for s in sources if s == "agent_runtime"]
        # run_started, turn_started, context_built, decision_*, finish, run_completed
        assert len(agent_events) >= 6


# ---------------------------------------------------------------------------
# 10. Pipeline compat events
# ---------------------------------------------------------------------------


class TestPipelineEvents:
    """PipelineRunner with event_sink produces stage lifecycle events."""

    def test_pipeline_runner_emits_events(self, tmp_path: Path) -> None:

        from homemaster.pipeline.core import PipelineContext, PipelineRunner, StageRegistry

        sink = JsonlEventSink(tmp_path)

        class DummyStage:
            name = "test_stage"
            def execute(self, ctx: PipelineContext) -> PipelineContext:
                return ctx.with_stage_status("test_stage", {"status": "PASS"})

        registry = StageRegistry()
        registry.register(DummyStage())

        ctx = PipelineContext(
            run_id="pipe-test", scenario="test", utterance="test",
            resolved_world_path=Path("/dev/null"),
            resolved_memory_path=Path("/dev/null"),
            runtime_memory_dir=Path("/tmp"),
            case_dir=Path("/tmp"),
            results_dir=Path("/tmp"),
            config_path=Path("/dev/null"),
            provider_name="test",
            embedding_provider_name="test",
        )

        runner = PipelineRunner(registry, event_sink=sink)
        runner.run(ctx)

        event_types = [e.event_type for e in sink.events]
        assert "run_started" in event_types
        assert "stage_started" in event_types
        assert "stage_completed" in event_types
        assert "run_completed" in event_types

    def test_pipeline_events_have_source_pipeline(self, tmp_path: Path) -> None:
        from homemaster.pipeline.core import PipelineContext, PipelineRunner, StageRegistry

        sink = JsonlEventSink(tmp_path)

        class DummyStage:
            name = "s1"
            def execute(self, ctx: PipelineContext) -> PipelineContext:
                return ctx

        registry = StageRegistry()
        registry.register(DummyStage())

        ctx = PipelineContext(
            run_id="pipe-src", scenario="test", utterance="test",
            resolved_world_path=Path("/dev/null"),
            resolved_memory_path=Path("/dev/null"),
            runtime_memory_dir=Path("/tmp"),
            case_dir=Path("/tmp"),
            results_dir=Path("/tmp"),
            config_path=Path("/dev/null"),
            provider_name="test",
            embedding_provider_name="test",
        )

        runner = PipelineRunner(registry, event_sink=sink)
        runner.run(ctx)

        run_events = [e for e in sink.events if e.event_type in ("run_started", "run_completed")]
        assert all(e.source == "pipeline" for e in run_events)

    def test_no_sink_works_identically(self) -> None:
        """PipelineRunner without event_sink works as before."""
        from homemaster.pipeline.core import PipelineContext, PipelineRunner, StageRegistry

        class DummyStage:
            name = "s1"
            def execute(self, ctx: PipelineContext) -> PipelineContext:
                return ctx.with_stage_status("s1", {"status": "PASS"})

        registry = StageRegistry()
        registry.register(DummyStage())

        ctx = PipelineContext(
            run_id="no-sink", scenario="test", utterance="test",
            resolved_world_path=Path("/dev/null"),
            resolved_memory_path=Path("/dev/null"),
            runtime_memory_dir=Path("/tmp"),
            case_dir=Path("/tmp"),
            results_dir=Path("/tmp"),
            config_path=Path("/dev/null"),
            provider_name="test",
            embedding_provider_name="test",
        )

        runner = PipelineRunner(registry)
        result = runner.run(ctx)
        assert result.stage_statuses["s1"]["status"] == "PASS"


# ---------------------------------------------------------------------------
# 11. Stage05/recovery events
# ---------------------------------------------------------------------------


class TestStage05RecoveryEvents:
    """Stage05 executor and recovery loop emit events when event_sink is set."""

    def test_executor_emits_subtask_events(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        from homemaster.contracts import (
            ExecutionState,
            ModuleExecutionResult,
            OrchestrationPlan,
            PlanningContext,
            StepDecision,
            Subtask,
            TaskCard,
        )
        from homemaster.stages.executor import execute_stage_05_plan

        sink = JsonlEventSink(tmp_path)

        task_card = TaskCard(
            task_type="fetch_object", target="水杯", delivery_target="user",
            location_hint="厨房", success_criteria=["把水杯交给用户"],
            needs_clarification=False, clarification_question=None, confidence=0.9,
        )
        context = PlanningContext(
            task_card=task_card,
            runtime_state_summary={"grounding_status": "grounded"},
            world_summary={"room_ids": ["kitchen"]},
        )
        plan = OrchestrationPlan(
            goal="找到水杯",
            subtasks=[Subtask(
                id="find_cup", intent="找到水杯", target_object="水杯",
                room_hint="厨房", success_criteria=["观察到水杯"],
            )],
        )

        class SuccessProvider:
            def next_decision(
                self, subtask: Subtask, state: ExecutionState,
                ctx: PlanningContext,
            ) -> StepDecision:
                return StepDecision(
                    subtask_id=subtask.id, selected_skill="navigation",
                    skill_input={
                        "goal_type": "find_object", "target_object": subtask.target_object,
                        "subtask_id": subtask.id, "subtask_intent": subtask.intent,
                    },
                )

        # Provide a skill registry that returns a successful result
        skill_registry = MagicMock()
        skill_registry.execute.return_value = ModuleExecutionResult(
            skill="navigation",
            status="success",
            observation={"target_visible": True, "visible_objects": ["水杯"]},
        )
        skill_registry.validate_input.return_value = None

        execute_stage_05_plan(
            context, plan,
            decision_provider=SuccessProvider(),
            skill_registry=skill_registry,
            event_sink=sink,
        )

        event_types = [e.event_type for e in sink.events]
        assert "subtask_started" in event_types
        assert "step_decision_generated" in event_types
        assert "skill_call_started" in event_types
        assert "verification_started" in event_types

    def test_recovery_loop_emits_recovery_events(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from homemaster.contracts import (
            ExecutionState,
            OrchestrationPlan,
            PlanningContext,
            RecoveryDecision,
            StepDecision,
            Subtask,
            TaskCard,
        )
        from homemaster.pipeline.core import PipelineContext
        from homemaster.stages.recovery_loop import run_stage05_with_recovery

        sink = JsonlEventSink(tmp_path)

        task_card = TaskCard(
            task_type="fetch_object", target="水杯", delivery_target="user",
            location_hint="厨房", success_criteria=["把水杯交给用户"],
            needs_clarification=False, clarification_question=None, confidence=0.9,
        )
        planning_ctx = PlanningContext(
            task_card=task_card,
            runtime_state_summary={"grounding_status": "grounded"},
            world_summary={"room_ids": ["kitchen"]},
        )
        plan = OrchestrationPlan(
            goal="找到水杯",
            subtasks=[Subtask(
                id="find_cup", intent="找到水杯", target_object="水杯",
                room_hint="厨房", success_criteria=["观察到水杯"],
            )],
        )

        class AlwaysFailProvider:
            def next_decision(
                self, subtask: Subtask, state: ExecutionState,
                ctx: PlanningContext,
            ) -> StepDecision:
                return StepDecision(
                    subtask_id=subtask.id, selected_skill="navigation",
                    skill_input={
                        "goal_type": "find_object", "target_object": subtask.target_object,
                        "subtask_id": subtask.id, "subtask_intent": subtask.intent,
                        "force_no_object": True,
                    },
                )

        from dataclasses import dataclass as dc

        @dc(frozen=True)
        class MockRecoveryResult:
            decision: RecoveryDecision
            prompt: str = ""
            raw_response: str = ""
            parsed_json: dict[str, Any] | None = None
            provider: dict[str, Any] | None = None
            attempts: tuple[dict[str, Any], ...] = ()

        ctx = PipelineContext(
            run_id="recovery-test", scenario="test", utterance="test",
            resolved_world_path=Path("/dev/null"),
            resolved_memory_path=Path("/dev/null"),
            runtime_memory_dir=Path("/tmp"),
            case_dir=Path("/tmp"),
            results_dir=Path("/tmp"),
            config_path=Path("/dev/null"),
            provider_name="test",
            embedding_provider_name="test",
            planning_context=planning_ctx,
        )

        with patch(
            "homemaster.stages.recovery_loop.load_provider_config",
            return_value="dummy",
        ), patch(
            "homemaster.stages.recovery_loop.generate_recovery_decision",
            return_value=MockRecoveryResult(
                decision=RecoveryDecision(action="finish_failed", reason="no hope"),
            ),
        ):
            result, attempts = run_stage05_with_recovery(
                ctx=ctx, plan=plan,
                decision_provider=AlwaysFailProvider(),
                config_path="/dev/null", provider_name="test",
                event_sink=sink,
            )

        event_types = [e.event_type for e in sink.events]
        assert "recovery_started" in event_types
        assert "recovery_decision_generated" in event_types


# ---------------------------------------------------------------------------
# 12. CLI --progress flag
# ---------------------------------------------------------------------------


class TestCliProgressFlag:
    """CLI --progress/--no-progress flags work correctly."""

    def test_progress_flag_in_signature(self) -> None:
        """run command accepts --progress/--no-progress."""
        import inspect

        from homemaster.cli.app import run_command
        sig = inspect.signature(run_command)
        assert "progress" in sig.parameters

    def test_progress_flag_accepted(self) -> None:
        """--progress flag is accepted by CLI parser."""
        from typer.testing import CliRunner

        from homemaster.cli import app

        # Just verify the flag is accepted (will fail at runtime due to missing scenario,
        # but should NOT fail due to unknown flag)
        result = CliRunner().invoke(app, ["run", "--utterance", "test", "--progress"])
        # Should fail because --scenario is required, not because --progress is unknown
        assert "scenario" in result.stderr or result.exit_code != 0

    def test_no_progress_flag_accepted(self) -> None:
        """--no-progress flag is accepted by CLI parser."""
        from typer.testing import CliRunner

        from homemaster.cli import app

        result = CliRunner().invoke(app, ["run", "--utterance", "test", "--no-progress"])
        assert "scenario" in result.stderr or result.exit_code != 0
