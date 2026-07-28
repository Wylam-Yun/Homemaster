"""Frozen file memory integration with ContextAssembler."""

from __future__ import annotations

from pathlib import Path

import pytest

from homemaster.agent.context import ContextAssembler
from homemaster.agent.messages import ContentBlock, UserMessage
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState
from homemaster.config import ContextPolicyConfig, MemoryConfig, ProviderProfileConfig
from homemaster.memory.context_service import FrozenMemoryContextService
from homemaster.memory.file_store import FileMemoryOperation, FileMemoryStore


def _services(tmp_path: Path) -> tuple[FileMemoryStore, FrozenMemoryContextService]:
    config = MemoryConfig(
        data_root=tmp_path / "memory-data",
    )
    store = FileMemoryStore(config)
    store.start()
    return store, FrozenMemoryContextService(store)


def _assembler(service: FrozenMemoryContextService | None = None) -> ContextAssembler:
    return ContextAssembler(
        provider=ProviderProfileConfig(
            name="chat",
            api_format="openai",
            base_url="https://chat.example/v1",
            model="chat-model",
            api_keys=["test-key"],
            context_window_tokens=20_000,
        ),
        policy=ContextPolicyConfig(),
        system_prompt="BASE SYSTEM",
        frozen_memory_context=service,
    )


def _session(session_id: str) -> AgentSession:
    session = AgentSession(session_id=session_id)
    session.append(UserMessage(content=[ContentBlock(text="hello")]))
    return session


def _prepare(assembler: ContextAssembler, session: AgentSession):
    return assembler.prepare(
        session=session,
        agent_state=AgentState(run_id="run", session_id=session.session_id),
        task_state_store=None,
        tools=[],
    )


def test_sync_context_freezes_per_session_and_counts_memory_tokens(tmp_path: Path) -> None:
    store, service = _services(tmp_path)
    store.apply("user", [FileMemoryOperation("add", content="concise replies")])
    assembler = _assembler(service)
    session_a = _session("a")
    first_a = _prepare(assembler, session_a)
    baseline = _prepare(_assembler(), _session("baseline"))

    assert first_a.system_prompt.startswith("BASE SYSTEM\n\n# Assistant Identity")
    assert "# User Profile\n\nconcise replies" in first_a.system_prompt
    assert first_a.metrics.estimated_tokens > baseline.metrics.estimated_tokens

    store.apply("memory", [FileMemoryOperation("add", content="decision v2")])
    second_a = _prepare(assembler, session_a)
    first_b = _prepare(assembler, _session("b"))
    assert second_a.system_prompt == first_a.system_prompt
    assert "decision v2" not in second_a.system_prompt
    assert "decision v2" in first_b.system_prompt


@pytest.mark.asyncio
async def test_async_context_uses_the_same_frozen_system_prompt(tmp_path: Path) -> None:
    store, service = _services(tmp_path)
    store.apply("memory", [FileMemoryOperation("add", content="async memory")])
    assembler = _assembler(service)
    session = _session("async-session")

    context = await assembler.aprepare(
        session=session,
        agent_state=AgentState(run_id="run", session_id=session.session_id),
        task_state_store=None,
        tools=[],
    )

    assert "# Assistant Identity" in context.system_prompt
    assert "# Persistent Memory\n\nasync memory" in context.system_prompt
