"""Tests for the long-lived V1.9 interactive application shell."""

from __future__ import annotations

import importlib
import signal
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from homemaster.application import (
    ApplicationSession,
    CompactionResult,
    RunResult,
    RunStatus,
    SessionManager,
    SessionStatus,
)
from homemaster.cli.app import app
from homemaster.cli.confirmation import CliConfirmationHandler
from homemaster.permissions import PermissionMode


class RecordingApplication:
    def __init__(self) -> None:
        self.session_manager = SessionManager()
        self.requests = []
        self.compactions: list[str] = []
        self.closed = 0
        self.session_end_handler = None
        self.close_callback = None

    def session(self, session_id, *, exit_reason="session_end"):
        return ApplicationSession(self, session_id, exit_reason)

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
        if self.close_callback is not None:
            await self.close_callback()


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
    )
    bundle.composition_kwargs = None

    def create(**kwargs):
        bundle.composition_kwargs = kwargs
        return bundle

    monkeypatch.setattr(module, "compose_application", create)
    monkeypatch.setattr(
        module,
        "run_doctor",
        lambda live=False, config_path=None: SimpleNamespace(has_failures=False),
    )
    return application, bundle


def _install_session_end_handler(bundle, handler=None):
    """Install a V3.8 session-end archive handler.

    The handler is synchronous (the real ``_archive_session_end`` does durable
    store I/O inline) and returns a receipt whose ``job_id`` the shell echoes
    as ``Queued session finalization``, or ``None`` when no boundary existed.
    """
    calls = []

    if handler is None:

        def handler(session_id, exit_reason):
            calls.append((session_id, exit_reason))
            return SimpleNamespace(job_id=f"archive-{len(calls)}", status="archived")

    bundle.application.session_end_handler = handler
    return calls


def test_shell_exits_and_closes_owned_application_once(monkeypatch, tmp_path) -> None:
    application, _ = _install_shell(monkeypatch, tmp_path)

    result = CliRunner().invoke(app, ["shell", "--local"], input="/exit\n")

    assert result.exit_code == 0
    assert application.closed == 1


def test_shell_reuses_one_application_and_session_across_turns(monkeypatch, tmp_path) -> None:
    application, bundle = _install_shell(monkeypatch, tmp_path)

    result = CliRunner().invoke(app, ["shell", "--local"], input="first\nsecond\n/exit\n")

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


def test_shell_confirm_mode_injects_handler_and_removes_tool_auto(monkeypatch, tmp_path) -> None:
    application, bundle = _install_shell(monkeypatch, tmp_path)

    result = CliRunner().invoke(
        app,
        ["shell", "--local", "--permission-mode", "confirm"],
        input="first\n/exit\n",
    )

    assert result.exit_code == 0
    assert bundle.composition_kwargs["permission_mode"] is PermissionMode.DEFAULT
    assert isinstance(bundle.composition_kwargs["confirmation_handler"], CliConfirmationHandler)
    assert "tool.auto" not in application.requests[0].permission_subject.capabilities


def test_shell_full_auto_keeps_default_subject_capabilities(monkeypatch, tmp_path) -> None:
    application, bundle = _install_shell(monkeypatch, tmp_path)

    result = CliRunner().invoke(app, ["shell", "--local"], input="first\n/exit\n")

    assert result.exit_code == 0
    assert bundle.composition_kwargs["permission_mode"] is PermissionMode.FULL_AUTO
    assert bundle.composition_kwargs["confirmation_handler"] is None
    assert "tool.auto" in application.requests[0].permission_subject.capabilities


def test_shell_sends_json_and_ticket_text_to_normal_agent(monkeypatch, tmp_path) -> None:
    application, _ = _install_shell(monkeypatch, tmp_path)
    utterances = [
        "检查 tmp/manual_experience_test.json",
        "检查 /tmp/manual_experience_test.json",
        "执行这个 ticket 变更单 /tmp/manual_experience_test.json",
    ]

    result = CliRunner().invoke(
        app,
        ["shell", "--local"],
        input="\n".join([*utterances, "/exit", ""]),
    )

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
        ["shell", "--local"],
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

    result = CliRunner().invoke(app, ["shell", "--local"], input="first\n/new\nsecond\n/exit\n")

    assert result.exit_code == 0
    assert application.requests[0].session_id != application.requests[1].session_id
    assert application.requests[1].resume is False
    assert application.closed == 1


def test_shell_archives_old_and_current_sessions_on_new_and_exit(
    monkeypatch, tmp_path
) -> None:
    application, bundle = _install_shell(monkeypatch, tmp_path)
    calls = _install_session_end_handler(bundle)

    result = CliRunner().invoke(app, ["shell", "--local"], input="first\n/new\nsecond\n/exit\n")

    assert result.exit_code == 0
    assert [item[1] for item in calls] == ["new_session", "user_exit"]
    assert calls[0][0] != calls[1][0]
    assert result.stdout.count("Queued session finalization") == 2
    assert application.closed == 1


def test_shell_session_close_runs_archive_handler_before_application_close(
    monkeypatch, tmp_path
) -> None:
    application, bundle = _install_shell(monkeypatch, tmp_path)
    events = []

    def handler(session_id, exit_reason):
        events.append(("archive", session_id, exit_reason))
        return SimpleNamespace(job_id="archive-1", status="archived")

    bundle.application.session_end_handler = handler

    async def record_close():
        events.append(("application_close",))

    application.close_callback = record_close

    result = CliRunner().invoke(app, ["shell", "--local"], input="first\n/exit\n")

    assert result.exit_code == 0
    assert events[0] == ("archive", application.requests[0].session_id, "user_exit")
    assert events[-1] == ("application_close",)


def test_shell_new_runs_archive_synchronously_before_next_run(
    monkeypatch, tmp_path
) -> None:
    """V3.8 closes a session with a fast durable archive transaction, not a
    background finalization cascade, so the next run starts after it."""
    application, bundle = _install_shell(monkeypatch, tmp_path)
    events = []

    def handler(session_id, exit_reason):
        events.append(("archive", session_id, exit_reason))
        return SimpleNamespace(job_id="archive-1", status="archived")

    bundle.application.session_end_handler = handler
    original_run = application.run

    async def recording_run(request):
        if application.requests:
            events.append(("next_run", request.session_id))
        return await original_run(request)

    application.run = recording_run

    result = CliRunner().invoke(app, ["shell", "--local"], input="first\n/new\nsecond\n/exit\n")

    assert result.exit_code == 0
    names = [event[0] for event in events]
    assert names.index("archive") < names.index("next_run")
    assert events[0][2] == "new_session"
    assert "Queued session finalization" in result.stdout
    assert application.closed == 1


def test_shell_ignores_sigint_during_session_close(monkeypatch, tmp_path) -> None:
    application, bundle = _install_shell(monkeypatch, tmp_path)
    events = []

    def handler(session_id, exit_reason):
        events.append((session_id, exit_reason, "started"))
        signal.raise_signal(signal.SIGINT)
        signal.raise_signal(signal.SIGINT)
        events.append((session_id, exit_reason, "completed"))
        return SimpleNamespace(job_id="archive-1", status="archived")

    bundle.application.session_end_handler = handler

    result = CliRunner().invoke(app, ["shell", "--local"], input="first\n/exit\n")

    assert result.exit_code == 0
    close_events = [event for event in events if len(event) == 3]
    assert [event[2] for event in close_events] == ["started", "completed"]
    assert application.closed == 1
    assert result.stdout.count("Finalization in progress; Ctrl+C ignored") == 1


def test_shell_archive_command_calls_session_end_handler(monkeypatch, tmp_path) -> None:
    application, bundle = _install_shell(monkeypatch, tmp_path)
    calls = _install_session_end_handler(bundle)

    result = CliRunner().invoke(
        app, ["shell", "--local"], input="first\n/archive\n/exit\n"
    )

    assert result.exit_code == 0
    archive_calls = [call for call in calls if call[1] == "archived"]
    assert archive_calls == [(application.requests[0].session_id, "archived")]
    assert f"Archival queued for session {application.requests[0].session_id}" in result.stdout
    assert application.closed == 1


def test_shell_archive_reports_unavailable_without_memory_surface(
    monkeypatch, tmp_path
) -> None:
    application, _ = _install_shell(monkeypatch, tmp_path)

    result = CliRunner().invoke(
        app, ["shell", "--local"], input="first\n/archive\n/exit\n"
    )

    assert result.exit_code == 0
    assert "Archive unavailable: memory.mode is not 'full'." in result.stdout


def test_shell_handles_run_cancellation_after_session_close(monkeypatch, tmp_path) -> None:
    application, bundle = _install_shell(monkeypatch, tmp_path)
    _install_session_end_handler(bundle)

    async def interrupt_second_run(request):
        if not application.requests:
            return await RecordingApplication.run(application, request)
        application.requests.append(request)
        raise KeyboardInterrupt

    application.run = interrupt_second_run

    result = CliRunner().invoke(
        app,
        ["shell", "--local"],
        input="first\n/new\nsecond\n/exit\n",
    )

    assert result.exit_code == 0
    assert "Run cancelled." in result.stdout
    assert "Goodbye" in result.stdout
    assert application.closed == 1


def test_shell_ignores_sigint_during_application_close(monkeypatch, tmp_path) -> None:
    application, _ = _install_shell(monkeypatch, tmp_path)
    close_events = []

    async def interrupting_close():
        application.closed += 1
        close_events.append("started")
        signal.raise_signal(signal.SIGINT)
        close_events.append("completed")

    application.aclose = interrupting_close

    result = CliRunner().invoke(app, ["shell", "--local"], input="/exit\n")

    assert result.exit_code == 0
    assert close_events == ["started", "completed"]
    assert application.closed == 1
    assert result.stdout.count("Shutdown in progress; Ctrl+C ignored") == 1


def test_shell_events_reports_application_trace(monkeypatch, tmp_path) -> None:
    _, bundle = _install_shell(monkeypatch, tmp_path)

    result = CliRunner().invoke(app, ["shell", "--local"], input="/events\n/exit\n")

    assert result.exit_code == 0
    assert f"Trace: {bundle.trace_path}" in result.stdout


def test_shell_help_describes_typed_controls(monkeypatch, tmp_path) -> None:
    _install_shell(monkeypatch, tmp_path)

    result = CliRunner().invoke(app, ["shell", "--local"], input="/help\n/exit\n")

    assert result.exit_code == 0
    assert "/compact: persist an immediate context compaction." in result.stdout
    assert "/status: show typed application session status." in result.stdout
