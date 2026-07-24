"""Tests for V1.9 one-shot CLI and the legacy run wrapper."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

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


def test_live_print_delegates_stdout_to_execution_without_post_run_echo(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured = {}

    def execute(**kwargs):
        captured.update(kwargs)
        print("live-answer", end="")
        return SimpleNamespace(
            result=_execution(tmp_path, reply="live-answer").result,
            live_rendered=True,
        )

    monkeypatch.setattr("homemaster.cli.run_command.execute_one_shot", execute)

    result = CliRunner().invoke(app, ["-p", "question", "--output-format", "text"])

    assert result.exit_code == 0
    assert captured.get("output_format") is not None
    assert str(captured["output_format"]) == "text"
    assert result.stdout == "live-answer"


def test_stream_json_fatal_error_emits_one_raw_error_and_no_result(monkeypatch) -> None:
    def fail(**_kwargs):
        raise RuntimeError("api_key=top-secret")

    monkeypatch.setattr("homemaster.cli.run_command.execute_one_shot", fail)

    result = CliRunner().invoke(
        app,
        ["-p", "question", "--output-format", "stream-json"],
    )

    assert result.exit_code == 1
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert rows == [
        {
            "type": "error",
            "message": "api_key=top-secret",
            "recoverable": False,
        }
    ]
    assert "top-secret" in result.stdout
    assert all(row["type"] != "result" for row in rows)


def test_stream_json_composition_error_preserves_bare_configured_literal(monkeypatch) -> None:
    secret = "bare-configured-literal"
    monkeypatch.setattr(
        "homemaster.cli.run_command.load_config",
        lambda **_kwargs: SimpleNamespace(providers=object()),
    )

    def fail_composition(**_kwargs):
        raise RuntimeError(f"composition exposed {secret}")

    monkeypatch.setattr(
        "homemaster.cli.run_command.create_home_application",
        fail_composition,
    )

    result = CliRunner().invoke(
        app,
        ["-p", "question", "--output-format", "stream-json"],
    )

    assert result.exit_code == 1
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert rows == [
        {
            "type": "error",
            "message": f"composition exposed {secret}",
            "recoverable": False,
        }
    ]
    assert secret in result.stdout


def test_stream_json_close_failure_emits_no_premature_result(monkeypatch, tmp_path: Path) -> None:
    secret = "close-configured-literal"

    class Application:
        session_manager = SimpleNamespace(list_session_ids=lambda: [])

        async def run(self, _request):
            return _execution(tmp_path, reply="done").result

        async def aclose(self):
            raise RuntimeError(f"close exposed {secret}")

    bundle = SimpleNamespace(
        application=Application(),
        trace_path=tmp_path / "trace.jsonl",
        run_dir=tmp_path,
        skill_registry=object(),
    )
    monkeypatch.setattr(
        "homemaster.cli.run_command.load_config",
        lambda **_kwargs: SimpleNamespace(providers=object()),
    )
    monkeypatch.setattr(
        "homemaster.cli.run_command.create_home_application",
        lambda **_kwargs: bundle,
    )

    result = CliRunner().invoke(
        app,
        ["-p", "question", "--output-format", "stream-json"],
    )

    assert result.exit_code == 1
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert rows == [
        {
            "type": "error",
            "message": f"close exposed {secret}",
            "recoverable": False,
        }
    ]
    assert secret in result.stdout


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
    assert preview["mcp_discovery"] == "not_configured"
    assert preview["external_io"] is False
    tool_names = [tool["name"] for tool in preview["tools"]]
    assert tool_names[:6] == [
        "bash",
        "brief",
        "sleep",
        "tool_search",
        "todo_write",
        "notebook_edit",
    ]
    assert {"robot_go_to", "browser_navigate", "task_planner"} <= set(tool_names)
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
            captured["request"] = request
            return _execution(tmp_path).result

        async def aclose(self):
            return None

    class Bundle:
        application = FakeApplication()
        trace_path = tmp_path / "trace.jsonl"
        run_dir = tmp_path
        skill_registry = object()

    monkeypatch.setattr(
        "homemaster.cli.run_command.load_config",
        lambda **kwargs: SimpleNamespace(runtime=SimpleNamespace(max_tool_iterations=None)),
    )
    monkeypatch.setattr(
        "homemaster.cli.run_command.create_home_application",
        lambda **kwargs: Bundle(),
    )

    from homemaster.cli.run_command import execute_one_shot

    execute_one_shot(prompt="inspect")

    request = captured["request"]
    assert request.dependencies["skill_registry"] is Bundle.skill_registry
    assert request.run_policy.max_tool_iterations is None


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
