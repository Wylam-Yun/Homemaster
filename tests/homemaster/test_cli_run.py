"""Tests for V1.9 one-shot CLI and the legacy run wrapper."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

from typer.testing import CliRunner

from homemaster.application import RunResult, RunStatus
from homemaster.cli.app import app
from homemaster.cli.run_command import OneShotExecution


def _execution(
    tmp_path: Path,
    *,
    status: RunStatus = RunStatus.REPLIED,
    reply: str = "done",
) -> OneShotExecution:
    run_dir = tmp_path / "run"
    return OneShotExecution(
        result=RunResult(
            run_id="run-one",
            session_id="session-one",
            status=status,
            final_reply=reply,
        ),
        trace_path=run_dir / "runtime_events.jsonl",
        run_dir=run_dir,
    )


def test_legacy_run_wrapper_preserves_labeled_output(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "homemaster.cli.run_command.execute_one_shot",
        lambda **kwargs: _execution(tmp_path, reply="completed"),
    )

    result = CliRunner().invoke(app, ["run", "--utterance", "do it"])

    assert result.exit_code == 0
    assert "assistant: completed" in result.stdout
    assert "status: replied" in result.stdout
    assert "trace:" in result.stdout


def test_legacy_wrapper_forwards_progress_verbose_and_quiet(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def execute(**kwargs):
        captured.update(kwargs)
        return _execution(tmp_path)

    monkeypatch.setattr("homemaster.cli.run_command.execute_one_shot", execute)
    result = CliRunner().invoke(
        app,
        ["run", "--utterance", "do it", "--progress", "--verbose", "--quiet"],
    )

    assert result.exit_code == 0
    assert captured["progress"] is True
    assert captured["verbose"] is True
    assert captured["quiet"] is True


def test_legacy_wrapper_rejects_missing_utterance_and_removed_scenario() -> None:
    missing = CliRunner().invoke(app, ["run"])
    removed = CliRunner().invoke(
        app,
        ["run", "--utterance", "test", "--scenario", "old"],
    )

    assert missing.exit_code == 1
    assert removed.exit_code != 0


def test_top_level_print_supports_text_json_and_stream_json(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "homemaster.cli.run_command.execute_one_shot",
        lambda **kwargs: _execution(tmp_path, reply="answer"),
    )
    runner = CliRunner()

    text = runner.invoke(app, ["-p", "question"])
    structured = runner.invoke(app, ["-p", "question", "--output-format", "json"])
    streamed = runner.invoke(
        app,
        ["-p", "question", "--output-format", "stream-json"],
    )

    assert text.exit_code == structured.exit_code == streamed.exit_code == 0
    assert text.stdout.strip() == "answer"
    assert json.loads(structured.stdout)["type"] == "result"
    assert json.loads(streamed.stdout)["session_id"] == "session-one"


def test_top_level_print_forwards_resume_and_continue(monkeypatch, tmp_path: Path) -> None:
    captured = []

    def execute(**kwargs):
        captured.append(kwargs)
        return _execution(tmp_path)

    monkeypatch.setattr("homemaster.cli.run_command.execute_one_shot", execute)
    runner = CliRunner()
    resumed = runner.invoke(app, ["-p", "next", "--resume", "session-one"])
    continued = runner.invoke(app, ["-p", "next", "--continue"])

    assert resumed.exit_code == continued.exit_code == 0
    assert captured[0]["resume_session_id"] == "session-one"
    assert captured[1]["continue_latest"] is True


def test_top_level_print_forwards_limited_provider_and_model_overrides(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured = {}

    def execute(**kwargs):
        captured.update(kwargs)
        return _execution(tmp_path)

    monkeypatch.setattr("homemaster.cli.run_command.execute_one_shot", execute)

    result = CliRunner().invoke(
        app,
        ["-p", "question", "--provider-name", "Mimo", "--model", "cli-model"],
    )

    assert result.exit_code == 0
    assert captured["provider_name"] == "Mimo"
    assert captured["model"] == "cli-model"


def test_dry_run_resolves_home_profile_without_application_or_external_io(
    monkeypatch,
) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("dry-run created the application")

    monkeypatch.setattr("homemaster.cli.composition.create_application", forbidden)
    result = CliRunner().invoke(
        app,
        ["--dry-run", "-p", "inspect", "--output-format", "json"],
    )

    assert result.exit_code == 0
    preview = json.loads(result.stdout)
    assert preview["entrypoint"] == "model_prompt"
    assert preview["settings"]["profile"] == "home"
    assert preview["mcp_discovery"] == "unknown_until_connect"
    assert preview["external_io"] is False
    assert [tool["name"] for tool in preview["tools"]][:6] == [
        "task_interpreter",
        "memory_retriever",
        "target_grounder",
        "skill_view",
        "robot_go_to",
        "observe",
    ]
    assert {skill["name"] for skill in preview["skills"]} >= {
        "fetch_object",
        "check_object_state",
    }
    assert preview["skill_diagnostics"]["loaded"] >= 2
    assert all("path" not in skill for skill in preview["skills"])


def test_dry_run_applies_limited_model_override_without_external_io() -> None:
    result = CliRunner().invoke(
        app,
        [
            "--dry-run",
            "-p",
            "inspect",
            "--provider-name",
            "Mimo",
            "--model",
            "dry-run-model",
            "--output-format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    preview = json.loads(result.stdout)
    assert preview["settings"]["model"] == "dry-run-model"
    assert preview["config_sources"]["model"] == "cli"
    assert preview["external_io"] is False


def test_home_application_wires_validated_skill_registry_into_run_dependencies(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured = {}

    class FakeApplication:
        class Sessions:
            @staticmethod
            def list_session_ids():
                return []

        session_manager = Sessions()

        async def run(self, request):
            captured.update(request.dependencies)
            return _execution(tmp_path).result

        async def aclose(self):
            return None

    class Bundle:
        application = FakeApplication()
        trace_path = tmp_path / "trace.jsonl"
        run_dir = tmp_path
        skill_registry = object()

    monkeypatch.setattr("homemaster.cli.run_command.load_config", lambda **kwargs: object())
    monkeypatch.setattr(
        "homemaster.cli.run_command.create_home_application",
        lambda **kwargs: Bundle(),
    )

    from homemaster.cli.run_command import execute_one_shot

    execute_one_shot(prompt="inspect")

    assert captured["skill_registry"] is Bundle.skill_registry


def test_top_level_defaults_to_interactive_shell(monkeypatch) -> None:
    calls = []
    app_module = importlib.import_module("homemaster.cli.app")
    monkeypatch.setattr(app_module, "run_interactive_shell", lambda **kwargs: calls.append(kwargs))

    result = CliRunner().invoke(app, [])

    assert result.exit_code == 0
    assert calls == [{"resume_session_id": None, "continue_latest": False}]


def test_result_status_controls_process_exit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "homemaster.cli.run_command.execute_one_shot",
        lambda **kwargs: _execution(tmp_path, status=RunStatus.FAILED),
    )

    result = CliRunner().invoke(app, ["-p", "question"])

    assert result.exit_code == 1
