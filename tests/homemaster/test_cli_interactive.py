"""Tests for the long-lived V1.9 interactive application shell."""

from __future__ import annotations

import asyncio
import importlib
import signal
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


class RecordingMemoryQueue:
    def __init__(self, calls) -> None:
        self.calls = calls
        self.tasks = []
        self.tail = None

    def enqueue_work(self, *, job_type, session_id, work):
        self.calls.append(("queue", "queued", session_id, job_type))
        previous = self.tail

        async def run_work():
            if previous is not None:
                await previous
            self.calls.append(("queue", "processing", session_id, job_type))
            await work()
            self.calls.append(("queue", "completed", session_id, job_type))

        task = asyncio.get_event_loop().create_task(run_work())
        self.tasks.append(task)
        self.tail = task
        return SimpleNamespace(job_id=f"finalize-{len(self.tasks)}", status="accepted")

    async def wait_idle(self):
        if self.tasks:
            await asyncio.gather(*self.tasks)
        self.calls.append(("queue", "idle"))


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
        memory_add_queue=None,
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
    if bundle.memory_add_queue is None:
        bundle.memory_add_queue = RecordingMemoryQueue(calls)
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
    assert [item[1] for item in calls if item[0] not in {"init", "queue"}] == [
        "new_session",
        "user_exit",
    ]
    assert result.stdout.count("Vanilla Add completed: 0 operations") == 2
    assert application.closed == 1


def test_shell_enqueues_session_finalizer_before_exit_drain(
    monkeypatch, tmp_path
) -> None:
    _, bundle = _install_shell(monkeypatch, tmp_path)
    calls = _install_finalizer(monkeypatch, bundle)

    result = CliRunner().invoke(app, ["shell"], input="first\n/exit\n")

    assert result.exit_code == 0
    ordered = [item for item in calls if item[0] != "init"]
    assert ordered[0][0:2] == ("queue", "queued")
    assert ordered[1][0:2] == ("queue", "processing")
    assert ordered[2][1] == "user_exit"
    assert ordered[-1] == ("queue", "idle")


def test_shell_new_runs_next_task_before_old_finalization_completes(
    monkeypatch, tmp_path
) -> None:
    application, bundle = _install_shell(monkeypatch, tmp_path)
    module = importlib.import_module("homemaster.cli.interactive_shell")
    events = []

    class DelayedFinalizer:
        def __init__(self, **kwargs):
            del kwargs

        async def finalize(self, session_id, exit_reason):
            events.append(("finalize_started", session_id, exit_reason))
            await asyncio.sleep(0.02)
            events.append(("finalize_completed", session_id, exit_reason))
            return SimpleNamespace(
                status="completed",
                operations=(),
                collected_events=0,
                excluded_transport_deltas=0,
                rendered_messages=0,
                duration_ms=20.0,
                error=None,
            )

    original_run = application.run

    async def recording_run(request):
        if application.requests:
            events.append(("next_run", request.session_id))
        return await original_run(request)

    application.run = recording_run
    bundle.mindmemos = object()
    bundle.memory_add_queue = RecordingMemoryQueue(events)
    monkeypatch.setattr(module, "SessionFinalizer", DelayedFinalizer)

    result = CliRunner().invoke(app, ["shell"], input="first\n/new\nsecond\n/exit\n")

    assert result.exit_code == 0
    names = [event[0] for event in events]
    assert names.index("next_run") < names.index("finalize_completed")
    assert result.stdout.index("New session created.") < result.stdout.index(
        "Vanilla Add completed"
    )
    assert application.closed == 1


def test_shell_ignores_sigint_during_session_finalization(
    monkeypatch, tmp_path
) -> None:
    application, bundle = _install_shell(monkeypatch, tmp_path)
    module = importlib.import_module("homemaster.cli.interactive_shell")
    events = []

    class InterruptingFinalizer:
        def __init__(self, **kwargs):
            del kwargs

        async def finalize(self, session_id, exit_reason):
            events.append((session_id, exit_reason, "started"))
            signal.raise_signal(signal.SIGINT)
            signal.raise_signal(signal.SIGINT)
            events.append((session_id, exit_reason, "completed"))
            return SimpleNamespace(
                status="completed",
                operations=(),
                collected_events=0,
                excluded_transport_deltas=0,
                rendered_messages=0,
                duration_ms=1.0,
                error=None,
            )

    bundle.mindmemos = object()
    bundle.memory_add_queue = RecordingMemoryQueue(events)
    monkeypatch.setattr(module, "SessionFinalizer", InterruptingFinalizer)

    result = CliRunner().invoke(app, ["shell"], input="first\n/exit\n")

    assert result.exit_code == 0
    finalizer_events = [event for event in events if len(event) == 3]
    assert [event[2] for event in finalizer_events] == ["started", "completed"]
    assert application.closed == 1
    assert result.stdout.count("Finalization in progress; Ctrl+C ignored") == 1


def test_shell_restores_sigint_after_finalization_for_run_cancellation(
    monkeypatch, tmp_path
) -> None:
    application, bundle = _install_shell(monkeypatch, tmp_path)
    _install_finalizer(monkeypatch, bundle)

    async def interrupt_second_run(request):
        if not application.requests:
            return await RecordingApplication.run(application, request)
        application.requests.append(request)
        signal.raise_signal(signal.SIGINT)
        await asyncio.sleep(0)

    application.run = interrupt_second_run

    result = CliRunner().invoke(
        app,
        ["shell"],
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

    result = CliRunner().invoke(app, ["shell"], input="/exit\n")

    assert result.exit_code == 0
    assert close_events == ["started", "completed"]
    assert application.closed == 1
    assert result.stdout.count("Shutdown in progress; Ctrl+C ignored") == 1


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
