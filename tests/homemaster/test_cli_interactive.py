"""Tests for the long-lived V1.9 interactive application shell."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from homemaster.application import (
    CompactionResult,
    RunResult,
    RunStatus,
    SessionManager,
    SessionStatus,
)
from homemaster.cli.app import app


class RecordingApplication:
    def __init__(self) -> None:
        self.session_manager = SessionManager()
        self.requests = []
        self.compactions: list[str] = []
        self.closed = 0

    async def run(self, request):
        self.requests.append(request)
        try:
            runtime = self.session_manager.get(request.session_id)
        except KeyError:
            runtime = await self.session_manager.open_or_resume(request.session_id)
        runtime.revision += 1
        return RunResult(
            run_id=f"run-{len(self.requests)}",
            session_id=runtime.session.session_id,
            status=RunStatus.REPLIED,
            final_reply=f"reply-{len(self.requests)}",
        )

    async def compact(self, session_id):
        self.compactions.append(session_id)
        runtime = self.session_manager.get(session_id)
        runtime.revision += 1
        return CompactionResult(
            session_id=session_id,
            generation=runtime.generation,
            revision=runtime.revision,
            triggered=True,
            kind="manual_summary",
        )

    def status(self, session_id):
        runtime = self.session_manager.get(session_id)
        return SessionStatus(
            session_id=session_id,
            generation=runtime.generation,
            revision=runtime.revision,
            status="waiting_user",
            active=False,
            cancellation_requested=False,
            task_status=None,
            environment_ref="home-test",
        )

    def cancel(self, session_id):
        del session_id
        return True

    async def aclose(self):
        self.closed += 1


def _install_shell(monkeypatch, tmp_path: Path):
    module = importlib.import_module("homemaster.cli.interactive_shell")
    application = RecordingApplication()
    bundle = SimpleNamespace(
        application=application,
        config=SimpleNamespace(
            runtime=SimpleNamespace(max_tool_iterations=None),
            memory=SimpleNamespace(data_root=tmp_path / "memory"),
        ),
        trace_path=tmp_path / "runtime_events.jsonl",
        skill_registry=object(),
        mindmemos=None,
    )
    monkeypatch.setattr(module, "create_home_application", lambda **kwargs: bundle)
    monkeypatch.setattr(
        module,
        "run_doctor",
        lambda live=False: SimpleNamespace(has_failures=False),
    )
    return application, bundle


def _install_finalizer(monkeypatch, bundle):
    module = importlib.import_module("homemaster.cli.interactive_shell")
    calls = []

    class RecordingFinalizer:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        async def finalize(self, session_id, exit_reason):
            calls.append((session_id, exit_reason))
            return SimpleNamespace(
                status="completed",
                operations=(),
                collected_events=3,
                excluded_transport_deltas=2,
                rendered_messages=4,
                duration_ms=4.0,
                error=None,
            )

    bundle.mindmemos = object()
    monkeypatch.setattr(module, "SessionFinalizer", RecordingFinalizer)
    return calls


def test_shell_exits_and_closes_owned_application_once(monkeypatch, tmp_path) -> None:
    application, _ = _install_shell(monkeypatch, tmp_path)

    result = CliRunner().invoke(app, ["shell"], input="/exit\n")

    assert result.exit_code == 0
    assert application.closed == 1


def test_shell_reuses_one_application_and_session_across_turns(monkeypatch, tmp_path) -> None:
    application, bundle = _install_shell(monkeypatch, tmp_path)

    result = CliRunner().invoke(app, ["shell"], input="first\nsecond\n/exit\n")

    assert result.exit_code == 0
    assert [request.text for request in application.requests] == ["first", "second"]
    assert application.requests[0].session_id == application.requests[1].session_id
    assert application.requests[0].resume is False
    assert application.requests[1].resume is True
    assert application.requests[0].dependencies["skill_registry"] is bundle.skill_registry
    assert application.requests[0].run_policy.max_tool_iterations is None
    assert "Assistant: reply-1" in result.stdout
    assert "Assistant: reply-2" in result.stdout
    assert application.closed == 1


def test_shell_sends_json_and_ticket_text_to_normal_agent(monkeypatch, tmp_path) -> None:
    application, _ = _install_shell(monkeypatch, tmp_path)
    utterances = [
        "检查 tmp/manual_experience_test.json",
        "检查 /tmp/manual_experience_test.json",
        "执行这个 ticket 变更单 /tmp/manual_experience_test.json",
    ]

    result = CliRunner().invoke(app, ["shell"], input="\n".join([*utterances, "/exit", ""]))

    assert result.exit_code == 0
    assert [request.text for request in application.requests] == utterances
    assert "Invalid change ticket" not in result.stdout


def test_shell_compact_and_status_use_typed_application_controls(
    monkeypatch,
    tmp_path,
) -> None:
    application, _ = _install_shell(monkeypatch, tmp_path)

    result = CliRunner().invoke(
        app,
        ["shell"],
        input="first\n/compact\n/status\n/exit\n",
    )

    assert result.exit_code == 0
    assert application.compactions == [application.requests[0].session_id]
    assert "kind=manual_summary" in result.stdout
    assert "revision=2" in result.stdout
    assert "Status: waiting_user" in result.stdout


def test_shell_new_resets_resume_policy_without_rebuilding_application(
    monkeypatch,
    tmp_path,
) -> None:
    application, _ = _install_shell(monkeypatch, tmp_path)

    result = CliRunner().invoke(app, ["shell"], input="first\n/new\nsecond\n/exit\n")

    assert result.exit_code == 0
    assert application.requests[0].session_id != application.requests[1].session_id
    assert application.requests[1].resume is False
    assert application.closed == 1


def test_shell_finalizes_old_and_current_sessions(monkeypatch, tmp_path) -> None:
    application, bundle = _install_shell(monkeypatch, tmp_path)
    calls = _install_finalizer(monkeypatch, bundle)

    result = CliRunner().invoke(app, ["shell"], input="first\n/new\nsecond\n/exit\n")

    assert result.exit_code == 0
    assert [item[1] for item in calls if item[0] != "init"] == [
        "new_session",
        "user_exit",
    ]
    assert result.stdout.count("Vanilla Add completed: 0 operations") == 2
    assert application.closed == 1


def test_shell_events_reports_application_trace(monkeypatch, tmp_path) -> None:
    _, bundle = _install_shell(monkeypatch, tmp_path)

    result = CliRunner().invoke(app, ["shell"], input="/events\n/exit\n")

    assert result.exit_code == 0
    assert f"Trace: {bundle.trace_path}" in result.stdout


def test_shell_help_describes_typed_controls(monkeypatch, tmp_path) -> None:
    _install_shell(monkeypatch, tmp_path)

    result = CliRunner().invoke(app, ["shell"], input="/help\n/exit\n")

    assert result.exit_code == 0
    assert "/compact: persist an immediate context compaction." in result.stdout
    assert "/status: show typed application session status." in result.stdout
