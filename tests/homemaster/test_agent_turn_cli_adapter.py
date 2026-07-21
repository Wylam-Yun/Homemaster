"""Tests for thin CLI-facing application compatibility wrappers."""

from __future__ import annotations

import ast
from pathlib import Path

from homemaster.agent.session import AgentSession
from homemaster.agent.turn import compact_agent_context, run_agent_turn, run_single_turn
from homemaster.application import CompactionResult, RunResult, RunStatus, SessionManager


class RecordingApplication:
    def __init__(self) -> None:
        self.session_manager = SessionManager()
        self.requests = []
        self.compactions: list[str] = []

    async def run(self, request):
        self.requests.append(request)
        session_id = request.session_id or "generated-session"
        return RunResult(
            run_id="run-recorded",
            session_id=session_id,
            status=RunStatus.REPLIED,
            final_reply="recorded reply",
        )

    async def compact(self, session_id):
        self.compactions.append(session_id)
        runtime = self.session_manager.get(session_id)
        return CompactionResult(
            session_id=session_id,
            generation=runtime.generation,
            revision=runtime.revision,
            triggered=False,
            kind="none",
        )


def test_run_single_turn_only_submits_typed_application_request() -> None:
    application = RecordingApplication()

    result = run_single_turn(
        application=application,  # type: ignore[arg-type]
        utterance="hello",
        session_id="session-one",
        provider_name="provider-one",
    )

    request = application.requests[0]
    assert result.final_reply == "recorded reply"
    assert request.text == "hello"
    assert request.session_id == "session-one"
    assert request.profile == "home"
    assert request.provider_name == "provider-one"
    assert request.resume is False


def test_run_agent_turn_attaches_legacy_session_and_submits_resume() -> None:
    application = RecordingApplication()
    session = AgentSession("legacy-session")

    result = run_agent_turn(
        session,
        "continue",
        application=application,  # type: ignore[arg-type]
    )

    request = application.requests[0]
    assert result.status == "replied"
    assert request.session_id == "legacy-session"
    assert request.resume is True
    assert application.session_manager.get("legacy-session").session is session


def test_compact_wrapper_forwards_to_exact_application_session() -> None:
    application = RecordingApplication()
    session = AgentSession("compact-session")

    result = compact_agent_context(
        session,
        application=application,  # type: ignore[arg-type]
    )

    assert application.compactions == ["compact-session"]
    assert result.status == "noop"
    assert result.compaction_triggered is False


def test_turn_module_has_no_runtime_composition_imports_or_constructors() -> None:
    source_path = Path(__file__).parents[2] / "src/homemaster/agent/turn.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert not imported.intersection(
        {
            "homemaster.agent.generic_runtime",
            "homemaster.domain.tool_registry",
            "homemaster.providers.llm_client",
            "homemaster.tools.dispatcher",
        }
    )
    assert not calls.intersection(
        {
            "AgentRuntime",
            "GenericAgentRuntime",
            "LLMClient",
            "ToolDispatcher",
            "build_home_tool_registry",
            "create_application",
        }
    )
