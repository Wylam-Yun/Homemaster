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
            "average_steps": 3.0,
            "episode_count": 1,
            "run_id": self.run_id,
            "success_rate": 1.0,
            "total_invalid_actions": 0,
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
            "--max-output-tokens",
            "0",
        ],
    )

    assert result.exit_code == 0
    assert captured["episodes"] == 1
    assert captured["memory_mode"] == "disabled"
    assert captured["max_env_steps"] == 50
    assert captured["max_tool_iterations"] == 300
    assert captured["max_output_tokens"] == 0
    assert "success_rate: 1.000" in result.stdout


def test_benchmark_alfworld_help_exposes_key_options() -> None:
    result = CliRunner().invoke(app, ["benchmark-alfworld", "--help"])

    assert result.exit_code == 0
    assert "--alfworld-root" in result.stdout
    assert "--max-env-steps" in result.stdout
    assert "--max-output-tokens" in result.stdout
    assert "--max-invalid-actions" in result.stdout
    assert "--memory-mode" in result.stdout
