from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path

from typer.testing import CliRunner

from homemaster.cli.app import app


@dataclass(frozen=True)
class FakeSummary:
    run_id: str = "run-1"

    @property
    def episodes(self) -> list[object]:
        return [object()]

    @property
    def success_rate(self) -> float:
        return 1.0

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_scored_episodes": 1,
            "agent_success_rate_on_valid": 1.0,
            "average_steps": 3.0,
            "episode_count": 1,
            "formal_score_available": True,
            "harness_invalid_episodes": 0,
            "harness_valid_coverage": 1.0,
            "run_id": self.run_id,
            "success_rate": 1.0,
            "total_invalid_actions": 0,
        }


@dataclass(frozen=True)
class FakeTasksetResult:
    taskset_id: str = "taskset-1"
    floorplan: int = 1
    difficulty: str = "easy"
    chain_success: bool = True
    success_rate: float = 1.0
    chain_completed_count: int = 1
    subtasks: tuple[object, ...] = (object(),)


@dataclass(frozen=True)
class FakeTasksetSummary:
    run_id: str = "taskset-run-1"
    taskset_results: tuple[FakeTasksetResult, ...] = (FakeTasksetResult(),)

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_scored_tasksets": 1,
            "agent_success_rate_on_valid": 1.0,
            "formal_score_available": True,
            "harness_invalid_tasksets": 0,
            "harness_valid_coverage": 1.0,
            "not_run_subtasks": 0,
            "total_tasksets": 1,
        }


def test_benchmark_alfworld_cli_invokes_handler(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured = {}

    def fake_handle(**kwargs):
        captured.update(kwargs)
        return FakeSummary()

    app_module = importlib.import_module("homemaster.cli.app")
    monkeypatch.setattr(app_module, "handle_benchmark_alfworld", fake_handle)

    result = CliRunner().invoke(
        app,
        [
            "benchmark-alfworld",
            "--alfworld-root",
            str(tmp_path / "alfworld"),
            "--alfworld-config",
            str(tmp_path / "base_config.yaml"),
            "--trace-root",
            str(tmp_path / "traces"),
            "--episodes",
            "1",
            "--memory-mode",
            "disabled",
            "--max-invalid-actions",
            "100",
            "--max-env-steps",
            "50",
            "--max-tool-iterations",
            "300",
            "--observation-mode",
            "textual_debug",
        ],
    )

    assert result.exit_code == 0
    assert captured["episodes"] == 1
    assert captured["memory_mode"] == "disabled"
    assert captured["max_env_steps"] == 50
    assert captured["max_tool_iterations"] == 300
    assert captured["provider_name"] is None
    assert captured["observation_mode"] == "textual_debug"
    assert "success_rate: 1.000" in result.stdout
    assert "agent_success_rate_on_valid: 1.000" in result.stdout
    assert "harness_valid_coverage: 1.000" in result.stdout
    assert "formal_score_available: true" in result.stdout


def test_benchmark_alfworld_help_exposes_key_options() -> None:
    result = CliRunner().invoke(app, ["benchmark-alfworld", "--help"])

    assert result.exit_code == 0
    assert "--alfworld-root" in result.stdout
    assert "--max-env-steps" in result.stdout
    assert "--max-invalid-actions" in result.stdout
    assert "--memory-mode" in result.stdout
    assert "--observation-mode" in result.stdout


def test_benchmark_alfworld_taskset_cli_reports_coverage_and_score_gate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_module = importlib.import_module("homemaster.cli.app")
    monkeypatch.setattr(
        app_module,
        "handle_benchmark_alfworld_taskset",
        lambda **_kwargs: FakeTasksetSummary(),
    )

    result = CliRunner().invoke(
        app,
        [
            "benchmark-alfworld-taskset",
            "--taskset-config",
            str(tmp_path / "tasksets.yaml"),
            "--alfworld-root",
            str(tmp_path / "alfworld"),
            "--alfworld-config",
            str(tmp_path / "base_config.yaml"),
        ],
    )

    assert result.exit_code == 0
    assert "agent_success_rate_on_valid: 1.000" in result.stdout
    assert "harness_valid_coverage: 1.000" in result.stdout
    assert "formal_score_available: true" in result.stdout
    assert "not_run_subtasks: 0" in result.stdout
