"""Tests for task_runner use_agent_runtime=True opt-in path.

Uses FakeMimoDecisionClient to verify the AgentRuntime path works
without live LLM calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homemaster.agent.decision import AgentDecision, FinishDecision, ToolCallDecision
from homemaster.task_runner import run_homemaster_task


class FakeMimoDecisionClient:
    """Offline test double. Returns decisions from a fixed list."""

    def __init__(self, decisions: list[AgentDecision]) -> None:
        self._decisions = list(decisions)
        self._index = 0

    def decide(self, **kwargs: Any) -> AgentDecision:
        if self._index >= len(self._decisions):
            return FinishDecision(status="failed", summary="no more decisions")
        d = self._decisions[self._index]
        self._index += 1
        return d


def _setup_scenario(tmp_path: Path) -> None:
    """Create minimal scenario files for task_runner."""
    scenario_root = tmp_path / "data" / "scenarios" / "test_scenario"
    scenario_root.mkdir(parents=True)

    world = {"rooms": [{"id": "kitchen", "objects": [{"category": "cup"}]}]}
    (scenario_root / "world.json").write_text(
        json.dumps(world, ensure_ascii=False), encoding="utf-8"
    )

    memory = {"records": [{"object_category": "cup", "anchor": {"room_id": "kitchen"}}]}
    (scenario_root / "memory.json").write_text(
        json.dumps(memory, ensure_ascii=False), encoding="utf-8"
    )


def test_task_runner_agent_runtime_returns_result(tmp_path: Path) -> None:
    """use_agent_runtime=True path returns HomeMasterRunResult."""
    _setup_scenario(tmp_path)

    from unittest.mock import MagicMock, patch

    decisions = [
        ToolCallDecision(tool="navigate", arguments={"room_hint": "kitchen"}),
        FinishDecision(status="completed", summary="done"),
    ]
    fake_client = FakeMimoDecisionClient(decisions)
    fake_config = MagicMock()

    # Patch at the source module (imports inside _run_agent_runtime)
    with patch(
        "homemaster.providers.mimo_decision_client.LiveMimoDecisionClient",
        return_value=fake_client,
    ), patch(
        "homemaster.task_runner.validate_runtime_services", return_value=[]
    ), patch(
        "homemaster.runtime.load_provider_config", return_value=fake_config,
    ), patch(
        "homemaster.task_runner.REPO_ROOT", tmp_path,
    ):
        result = run_homemaster_task(
            utterance="test request",
            scenario="test_scenario",
            runtime_memory_root=tmp_path / "runs",
            debug_root=tmp_path / "debug",
            results_root=tmp_path / "results",
            run_id="test-agent-001",
            use_agent_runtime=True,
        )

    assert result.run_id == "test-agent-001"
    assert result.final_status in {"completed", "failed"}
    assert "agent_runtime" in result.stage_statuses


def test_task_runner_agent_runtime_with_failed_status(tmp_path: Path) -> None:
    """AgentRuntime path returns failed status when model decides failure."""
    _setup_scenario(tmp_path)

    from unittest.mock import MagicMock, patch

    decisions = [
        FinishDecision(status="failed", summary="could not find object"),
    ]
    fake_client = FakeMimoDecisionClient(decisions)
    fake_config = MagicMock()

    with patch(
        "homemaster.providers.mimo_decision_client.LiveMimoDecisionClient",
        return_value=fake_client,
    ), patch(
        "homemaster.task_runner.validate_runtime_services", return_value=[]
    ), patch(
        "homemaster.runtime.load_provider_config", return_value=fake_config,
    ), patch(
        "homemaster.task_runner.REPO_ROOT", tmp_path,
    ):
        result = run_homemaster_task(
            utterance="find nonexistent",
            scenario="test_scenario",
            runtime_memory_root=tmp_path / "runs",
            debug_root=tmp_path / "debug",
            results_root=tmp_path / "results",
            run_id="test-agent-002",
            use_agent_runtime=True,
        )

    assert result.final_status == "failed"
