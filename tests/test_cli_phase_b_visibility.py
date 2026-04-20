from __future__ import annotations

from typer.testing import CliRunner

from task_brain.cli import app
from task_brain.planner import DeterministicHighLevelPlanner

_RUNNER = CliRunner()
_CHECK_MEDICINE_INSTRUCTION = "去桌子那边看看药盒是不是还在。"


def test_cli_fallback_is_visible_when_api_key_missing(monkeypatch) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    result = _RUNNER.invoke(
        app,
        [
            "run",
            "--scenario",
            "check_medicine_success",
            "--instruction",
            _CHECK_MEDICINE_INSTRUCTION,
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "final_status: success" in result.stdout
    assert "planner_fallback_diagnostics:" in result.stdout
    assert "auth_missing_key" in result.stdout
    assert "call_llm_planner" in result.stdout
    assert "llm_planner_error" in result.stdout
    assert "llm_planner_fallback" in result.stdout


def test_cli_fallback_trace_order_is_stable_when_llm_fails(monkeypatch) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    result = _RUNNER.invoke(
        app,
        [
            "run",
            "--scenario",
            "check_medicine_success",
            "--instruction",
            _CHECK_MEDICINE_INSTRUCTION,
        ],
    )

    assert result.exit_code == 0, result.stdout
    call_index = result.stdout.find("call_llm_planner")
    error_index = result.stdout.find("llm_planner_error")
    fallback_index = result.stdout.find("llm_planner_fallback")
    assert call_index != -1
    assert error_index != -1
    assert fallback_index != -1
    assert call_index < error_index < fallback_index


def test_cli_llm_success_path_has_no_fallback_diagnostics(monkeypatch) -> None:
    def _success_generate(self, context):  # noqa: ANN001
        return DeterministicHighLevelPlanner().generate(context)

    monkeypatch.setattr("task_brain.planner.LLMHighLevelPlanner.generate", _success_generate)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    result = _RUNNER.invoke(
        app,
        [
            "run",
            "--scenario",
            "check_medicine_success",
            "--instruction",
            _CHECK_MEDICINE_INSTRUCTION,
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "final_status: success" in result.stdout
    assert "call_llm_planner" in result.stdout
    assert "llm_planner_error" not in result.stdout
    assert "llm_planner_fallback" not in result.stdout
    assert "planner_fallback_diagnostics:" not in result.stdout


def test_cli_llm_success_keeps_trace_order_constraints(monkeypatch) -> None:
    def _success_generate(self, context):  # noqa: ANN001
        return DeterministicHighLevelPlanner().generate(context)

    monkeypatch.setattr("task_brain.planner.LLMHighLevelPlanner.generate", _success_generate)

    result = _RUNNER.invoke(
        app,
        [
            "run",
            "--scenario",
            "check_medicine_success",
            "--instruction",
            _CHECK_MEDICINE_INSTRUCTION,
        ],
    )

    assert result.exit_code == 0, result.stdout
    retrieve_index = result.stdout.find("retrieve_memory")
    generate_index = result.stdout.find("generate_plan")
    final_verify_index = result.stdout.find("final_task_verification")
    respond_index = result.stdout.find("respond_with_trace")
    assert retrieve_index != -1
    assert generate_index != -1
    assert final_verify_index != -1
    assert respond_index != -1
    assert retrieve_index < generate_index
    assert final_verify_index < respond_index
