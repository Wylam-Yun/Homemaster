"""RoboCasa benchmark runner tests with fake adapter/worker/transport."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from homemaster.agent.messages import ToolCall
from homemaster.providers.types import TransportDelta
from homemaster.robocasa.benchmark.runner import RoboCasaBenchmarkRunner
from homemaster.robocasa.types import (
    RoboCasaBenchmarkConfig,
    RoboCasaEnvState,
)
from tests.homemaster.as_testkit import as_provider


def _provider_config(tmp_path: Path) -> Path:
    path = tmp_path / "homemaster.json"
    path.write_text(
        """
        {
          "memory": {"enabled": true, "data_root": "./memory"},
          "providers": {
            "default": "mimo_v25",
            "items": [
              {
                "name": "mimo_v25",
                "protocol": "anthropic",
                "base_url": "https://mimo.example/anthropic",
                "model": "mimo-v2.5",
                "api_keys": ["test-key"],
                "context_window_tokens": null,
                "max_output_tokens": null
              }
            ]
          }
        }
        """,
        encoding="utf-8",
    )
    return path


def _manifest(tmp_path: Path, tasks: list[str], seeds: list[int]) -> Path:
    path = tmp_path / "target50_v2.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1.0,
                "protocol_id": "robocasa-harness-vla-v2",
                "environment_split": "target",
                "success_source": "state.success",
                "planner_reference": {"model": "gpt-5.5"},
                "runtime_protocol": {"rldx_max_chunks": 40},
                "splits": {
                    "atomic": {
                        "task_count": len(tasks),
                        "seeds": seeds,
                        "timeout_seconds": 60,
                        "tasks": tasks,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


class FakeWorker:
    """Stand-in for RoboCasaWorkerClient (spawned lazily by the runner)."""

    def __init__(self) -> None:
        self.worker_exit_code: int | None = None
        self.closed = False
        self.terminated = False

    def close(self) -> dict[str, Any]:
        self.closed = True
        return {"closed": True}

    def terminate(self) -> None:
        self.terminated = True


class FakeAdapter:
    """Borrowed-environment adapter scripted to succeed after N skills."""

    backend_id = "fake:robocasa"

    def __init__(self, succeed_after: int = 2) -> None:
        self._succeed_after = succeed_after
        self.skill_calls = 0
        self.ensure_calls: list[dict[str, Any]] = []
        self.state = RoboCasaEnvState(
            task_name="",
            split="target",
            seed=0,
            task_language="",
            success=False,
        )
        self.closed = False

    @property
    def current_state(self) -> RoboCasaEnvState:
        return self.state

    def ensure_cell(self, **kwargs: Any) -> dict[str, Any]:
        self.ensure_calls.append(kwargs)
        task_name = kwargs["task_name"]
        self.state = RoboCasaEnvState(
            task_name=task_name,
            split=kwargs["split"],
            seed=kwargs["seed"],
            task_language=f"Task: {task_name}",
            success=False,
        )
        self.skill_calls = 0
        return {"task_language": f"Task: {task_name}"}

    def observe(self, *, cameras: Any = None) -> dict[str, Any]:
        return {"state": self.state.to_model_visible_dict(), "images": []}

    def invoke(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if operation in {"skill", "arm"}:
            self.skill_calls += 1
            if self.skill_calls >= self._succeed_after:
                self.state = RoboCasaEnvState(
                    task_name=self.state.task_name,
                    split=self.state.split,
                    seed=self.state.seed,
                    task_language=self.state.task_language,
                    success=True,
                    step_index=self.state.step_index,
                )
        return {
            "primitive": operation,
            "result": {"status": "ok"},
            "errored": False,
            "step_index": self.state.step_index + 1,
            "state": self.state.to_model_visible_dict(),
        }

    def check_success(self) -> dict[str, Any]:
        return {
            "success": self.state.success,
            "state": self.state.to_model_visible_dict(),
        }

    def image_artifacts(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    def bind_application_run(self, run_id: str, generation: int) -> None:
        pass

    async def screenshot(self) -> bytes:
        return b"png"

    def close(self) -> dict[str, Any]:
        self.closed = True
        return {"closed": True}

    def terminate(self) -> None:
        pass


class SkillTransport:
    """Emits N ``robocasa_skill`` calls, then replies with text."""

    def __init__(self, skill_calls: int) -> None:
        self._remaining = skill_calls
        self.call_count = 0

    async def stream(
        self,
        messages: Any,
        tools: Any = None,
        *,
        system_prompt: str = "",
        event_sink: Any = None,
        run_id: str = "",
        session_id: str = "",
        turn_index: Any = None,
        iteration: Any = None,
        attempt_sink: Any = None,
        model_attempt_id: str = "attempt",
        **kwargs: Any,
    ):
        self.call_count += 1
        if self._remaining > 0:
            index = self._remaining
            self._remaining -= 1
            yield TransportDelta(
                type="transport.delta",
                tool_call_delta=ToolCall(
                    id=f"skill_{index}", name="robocasa_skill", arguments={}
                ),
            )
            yield TransportDelta(type="transport.delta", finish_reason="tool_calls")
        else:
            yield TransportDelta(type="transport.delta", text_delta="Done.")
            yield TransportDelta(type="transport.delta", finish_reason="stop")


def _config(tmp_path: Path, manifest: Path) -> RoboCasaBenchmarkConfig:
    return RoboCasaBenchmarkConfig(
        rpent_root=tmp_path / "rpent",
        task_manifest=manifest,
        trace_root=tmp_path / "traces",
        memory_mode="disabled",
        max_tool_iterations=10,
        provider_config=_provider_config(tmp_path),
    )


def test_runner_two_cells_summary(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, ["OpenDrawer"], seeds=[1, 2])
    adapter = FakeAdapter(succeed_after=2)
    worker = FakeWorker()
    transport = SkillTransport(skill_calls=100)
    runner = RoboCasaBenchmarkRunner(
        config=_config(tmp_path, manifest),
        transport_factory=lambda: as_provider(transport),
        adapter_factory=lambda _config: adapter,
        worker_factory=lambda workdir, log_path: worker,
    )

    summary = runner.run()

    assert len(summary.cells) == 2
    assert all(cell.success for cell in summary.cells)
    assert all(cell.classification == "agent_success" for cell in summary.cells)
    assert summary.success_rate == 1.0
    assert summary.task_weighted_success_rate == 1.0
    # one worker spans both cells
    assert len(adapter.ensure_calls) == 2
    # result.json + summary.json artifacts exist
    summary_path = runner.run_dir / "summary.json"
    assert summary_path.is_file()
    data = json.loads(summary_path.read_text())
    assert data["cell_count"] == 2
    for cell in summary.cells:
        assert cell.result_path is not None and cell.result_path.is_file()
        result = json.loads(cell.result_path.read_text())
        assert result["success_source"] == "state.success"
        assert result["planner"]["backend"] == "homemaster"
        assert result["success"] is True


def test_runner_env_failure_is_not_success(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, ["OpenDrawer"], seeds=[1])
    adapter = FakeAdapter(succeed_after=999)  # never succeeds
    worker = FakeWorker()
    # one skill call, then the model replies without env success
    transport = SkillTransport(skill_calls=1)
    runner = RoboCasaBenchmarkRunner(
        config=_config(tmp_path, manifest),
        transport_factory=lambda: as_provider(transport),
        adapter_factory=lambda _config: adapter,
        worker_factory=lambda workdir, log_path: worker,
    )

    summary = runner.run()

    cell = summary.cells[0]
    assert cell.success is False
    assert cell.classification == "agent_model_failure"
    assert cell.score_eligible is True
    assert summary.success_rate == 0.0


def test_runner_setup_failure_is_infrastructure(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, ["OpenDrawer"], seeds=[1])

    class FailingAdapter(FakeAdapter):
        def ensure_cell(self, **kwargs: Any) -> dict[str, Any]:
            raise RuntimeError("env_server failed to start")

    worker = FakeWorker()
    runner = RoboCasaBenchmarkRunner(
        config=_config(tmp_path, manifest),
        transport_factory=lambda: as_provider(SkillTransport(1)),
        adapter_factory=lambda _config: FailingAdapter(),
        worker_factory=lambda workdir, log_path: worker,
    )

    summary = runner.run()

    cell = summary.cells[0]
    assert cell.success is False
    assert cell.classification == "infrastructure_error"
    assert cell.score_eligible is False
    assert worker.terminated is True


def test_classification_maps_real_runtime_error_codes() -> None:
    from homemaster.robocasa.benchmark import runner as runner_module
    from homemaster.robocasa.types import EpisodeOutcome

    def classify(error_code: str | None) -> str:
        return runner_module._classification(
            success=False, outcome=EpisodeOutcome(), error_code=error_code
        )

    # real substrate codes → provider failure (score-ineligible)
    assert classify("transport_error") == "provider_failure"
    assert classify("provider_protocol_error") == "provider_failure"
    assert classify("reply_error") == "provider_failure"
    # user interrupt is a cancellation, not an agent failure
    assert classify("user_interrupted") == "cancelled"
    # budget exhaustion = score-eligible agent failure (Target50 semantics)
    assert classify("deadline_exceeded") == "agent_model_failure"
    assert classify("max_tool_iterations_exceeded") == "agent_model_failure"
    assert classify("benchmark_invalid_action_limit") == "agent_model_failure"
    assert classify(None) == "agent_model_failure"


def test_runner_task_and_seed_filtering(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path, ["OpenDrawer", "CloseFridge"], seeds=[1, 2])
    adapter = FakeAdapter(succeed_after=1)
    worker = FakeWorker()
    config = _config(tmp_path, manifest)
    config = dataclasses.replace(config, tasks=("OpenDrawer",), seeds=(2,))
    runner = RoboCasaBenchmarkRunner(
        config=config,
        transport_factory=lambda: as_provider(SkillTransport(5)),
        adapter_factory=lambda _config: adapter,
        worker_factory=lambda workdir, log_path: worker,
    )

    summary = runner.run()

    assert len(summary.cells) == 1
    assert summary.cells[0].cell.task_name == "OpenDrawer"
    assert summary.cells[0].cell.seed == 2
