"""Durable compaction artifact tests — append-only transcript + projected views.

Regression coverage for the v35 AgentScope-migration bug: compaction results
were written to the per-round session mirror and discarded on the next
``sync_session()`` overwrite, so every model call past the threshold re-ran
the full summary flow. The durable artifact on ``AgentState`` folds
history once; subsequent prepares project ``head + canonical[fk:]``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import asyncio

import pytest

from homemaster.agent.context import ContextAssembler
from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState
from homemaster.config import ContextPolicyConfig, ProviderProfileConfig
from homemaster.providers.token_estimator import AnthropicTokenEstimator
from homemaster.task_state.store import TaskStateStore


class RecordingSummaryClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def complete(self, messages, *, system_prompt: str = "", **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("summary backend down")
        return AssistantMessage(
            content=[ContentBlock(text=f"summary n{self.calls}: earlier steps done")]
        )


def _assembler(
    *,
    summary_client=None,
    window: int = 100_000,
    threshold_ratio: float = 0.02,
    provider_max_output: int | None = None,
    **policy_kw,
) -> ContextAssembler:
    policy = ContextPolicyConfig(
        compression_threshold_ratio=threshold_ratio,
        output_reserve_tokens=200,
        enabled_providers=("conversation",),
        **policy_kw,
    )
    return ContextAssembler(
        provider=ProviderProfileConfig(
            name="stub",
            protocol="anthropic",
            base_url="https://stub.example",
            model="stub",
            api_keys=["k"],
            context_window_tokens=window,
            max_output_tokens=provider_max_output,
        ),
        policy=policy,
        system_prompt="system prompt",
        summary_client=summary_client,
    )


def _seed_pairs(
    session: AgentSession,
    *,
    pairs: int,
    user_chars: int = 400,
    result_chars: int = 600,
    start: int = 0,
) -> None:
    for index in range(start, start + pairs):
        session.append(
            UserMessage.from_text(f"turn {index} " + "u" * user_chars)
        )
        session.append(
            AssistantMessage(
                tool_calls=[
                    ToolCall(id=f"c{index}", name="robot_go_to", arguments={"to": f"r{index}"})
                ],
                finish_reason="tool_calls",
                usage={"input_tokens": 100 + index},
            )
        )
        session.append(
            ToolResultMessage(
                tool_call_id=f"c{index}",
                name="robot_go_to",
                content=[ContentBlock(text=f"result {index} " + "r" * result_chars)],
            )
        )
    session.append(UserMessage.from_text(f"latest request {start}"))


def _prepare(assembler: ContextAssembler, session: AgentSession, state: AgentState):
    return asyncio.run(assembler.aprepare(
        session=session,
        agent_state=state,
        task_state_store=None,
        tools=[],
    ))


def _text(context) -> str:
    return "\n".join(
        block.text for message in context.messages for block in message.content if block.text
    )


def test_summary_fold_persists_across_rounds() -> None:
    """Core regression gate: after the first fold, later prepares project
    the artifact head + live tail — the summary client must not be called
    again while the view stays under threshold."""
    summary_client = RecordingSummaryClient()
    assembler = _assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    first = _prepare(assembler, session, state)
    assert first.metrics.compaction_triggered is True
    assert summary_client.calls == 1
    artifact = state.compaction
    assert artifact is not None
    assert artifact.first_kept_index > 0
    canonical_len = len(session.messages)

    for round_index in range(3):
        session.append(UserMessage.from_text(f"follow-up {round_index} " + "x" * 200))
        session.append(AssistantMessage(content=[ContentBlock(text="ok " + "y" * 100)]))
        context = _prepare(assembler, session, state)
        assert summary_client.calls == 1, f"round {round_index} re-ran the summary"
        assert context.metrics.compaction_triggered is False
        text = _text(context)
        assert "summary n1: earlier steps done" in text
        assert f"follow-up {round_index}" in text

    # Canonical mirror was never rewritten — append-only growth.
    assert len(session.messages) > canonical_len
    artifact_now = state.compaction
    assert artifact_now is not None
    assert artifact_now.first_kept_index == artifact.first_kept_index


def test_fold_projection_contains_head_and_live_tail() -> None:
    summary_client = RecordingSummaryClient()
    assembler = _assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    _prepare(assembler, session, state)
    artifact = state.compaction
    assert artifact is not None

    head = artifact.head_as_messages()
    assert "CONTEXT COMPACTION" in head[0].content[0].text
    # The protected canonical prefix survives verbatim inside the head.
    canonical = session.messages
    tail = canonical[artifact.first_kept_index :]

    next_view = _prepare(assembler, session, state)
    view_texts = [
        block.text
        for m in next_view.messages
        for block in m.content
        if block.text
    ]
    # Head still leads with the stored summary marker …
    assert "CONTEXT COMPACTION" in next_view.messages[0].content[0].text
    # … and the protected prefix's user text still follows it.
    assert any("turn 0" in t for t in view_texts[: len(head)])
    # The live tail keeps its newest user turn.
    assert "latest request 0" in view_texts[-1] or "latest request 0" in view_texts[-2]
    assert any("latest request 0" in block.text for m in tail for block in m.content)


def test_orphan_drop_shifts_fold_boundary_in_canonical_coords() -> None:
    """repair_tool_pairs drops an orphan assistant (call with no result and
    no content) from the *view* before the fold runs. The artifact boundary
    must still be expressed in CANONICAL coordinates: the same fold position
    on a transcript with one extra dropped element must shift
    first_kept_index by exactly one — otherwise the last folded message
    leaks back into every later projected view."""
    summary_client = RecordingSummaryClient()
    assembler = _assembler(summary_client=summary_client)

    clean = AgentSession(session_id="clean")
    _seed_pairs(clean, pairs=10)
    clean_state = AgentState(run_id="r1", session_id="clean")
    _prepare(assembler, clean, clean_state)
    assert clean_state.compaction is not None
    first_kept_clean = clean_state.compaction.first_kept_index

    dirty = AgentSession(session_id="dirty")
    _seed_pairs(dirty, pairs=10)
    orphan = AssistantMessage(
        tool_calls=[ToolCall(id="orphan-call", name="noop", arguments={})],
        finish_reason="tool_calls",
    )
    dirty._messages.insert(2, orphan)
    dirty_state = AgentState(run_id="r1", session_id="dirty")
    _prepare(assembler, dirty, dirty_state)
    assert dirty_state.compaction is not None

    assert dirty_state.compaction.first_kept_index == first_kept_clean + 1


def test_volatile_fields_do_not_invalidate_artifact() -> None:
    """Merged-Msg bookkeeping (usage/finish_reason/provider_metadata) grows
    every model call; mutating it inside the folded prefix must NOT drop the
    artifact — otherwise the original bug recurs on merged boundaries."""
    summary_client = RecordingSummaryClient()
    assembler = _assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")
    _prepare(assembler, session, state)
    assert summary_client.calls == 1

    folded_assistant = next(
        m for m in session.messages if isinstance(m, AssistantMessage)
    )
    folded_assistant.usage = {"input_tokens": 999_999}
    folded_assistant.finish_reason = "stop"
    folded_assistant.provider_metadata["latency_ms"] = 42

    _prepare(assembler, session, state)
    assert summary_client.calls == 1
    assert state.compaction is not None


def test_prefix_edit_and_insert_invalidate_artifact() -> None:
    summary_client = RecordingSummaryClient()
    assembler = _assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")
    _prepare(assembler, session, state)
    assert summary_client.calls == 1

    # In-place content edit inside the folded prefix → drop + re-fold.
    folded = session.messages[0]
    folded.content[0].text = "edited content"
    _prepare(assembler, session, state)
    assert summary_client.calls == 2

    # Insertion inside the folded prefix shifts the boundary → same handling.
    session._messages.insert(1, UserMessage.from_text("injected mid-prefix"))
    _prepare(assembler, session, state)
    assert summary_client.calls == 3


def test_tail_growth_is_free_until_threshold() -> None:
    """Appending to the canonical tail must not disturb the fold."""
    summary_client = RecordingSummaryClient()
    assembler = _assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")
    _prepare(assembler, session, state)
    artifact = state.compaction
    assert artifact is not None

    session.append(UserMessage.from_text("new user " + "z" * 100))
    _prepare(assembler, session, state)
    assert summary_client.calls == 1
    assert state.compaction.first_kept_index == artifact.first_kept_index


def test_snapshot_round_trip_preserves_artifact() -> None:
    summary_client = RecordingSummaryClient()
    assembler = _assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")
    _prepare(assembler, session, state)
    assert summary_client.calls == 1

    snap = session.to_snapshot_dict(
        agent_state=state,
        task_state_store=TaskStateStore(run_id="r1"),
        model="stub",
        system_prompt="system prompt",
    )
    restored_session, restored_state, _store = AgentSession.from_snapshot_dict(snap)
    assert restored_state.compaction is not None
    assert restored_state.compaction.first_kept_index == state.compaction.first_kept_index

    context = asyncio.run(assembler.aprepare(
        session=restored_session,
        agent_state=restored_state,
        task_state_store=None,
        tools=[],
    ))
    assert summary_client.calls == 1
    assert "summary n1" in _text(context)


def test_snapshot_stripped_image_forces_one_refold() -> None:
    """Snapshots replace folded-prefix image blocks with text placeholders,
    so the prefix hash legitimately misses after resume → exactly one refold."""
    summary_client = RecordingSummaryClient()
    assembler = _assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    session.append(
        AssistantMessage(tool_calls=[ToolCall(id="img", name="observe", arguments={})])
    )
    session.append(
        ToolResultMessage(
            tool_call_id="img",
            name="observe",
            content=[
                ContentBlock(text="observation"),
                ContentBlock(
                    type="image",
                    source={
                        "type": "base64",
                        "media_type": "image/png",
                        "data": (
                            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
                            "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
                        ),
                    },
                ),
            ],
        )
    )
    _seed_pairs(session, pairs=10, start=1)
    state = AgentState(run_id="r1", session_id="s1")
    _prepare(assembler, session, state)
    assert summary_client.calls == 1
    artifact = state.compaction
    assert artifact is not None
    assert artifact.first_kept_index > 2  # image pair is inside the fold

    snap = session.to_snapshot_dict(
        agent_state=state,
        task_state_store=TaskStateStore(run_id="r1"),
        model="stub",
        system_prompt="system prompt",
    )
    restored_session, restored_state, _ = AgentSession.from_snapshot_dict(snap)

    context = asyncio.run(assembler.aprepare(
        session=restored_session,
        agent_state=restored_state,
        task_state_store=None,
        tools=[],
    ))
    assert summary_client.calls == 2  # one refold, not an error loop
    assert restored_state.compaction is not None
    assert context.metrics.compaction_triggered is True


def test_micro_compaction_stays_event_free_and_ephemeral() -> None:
    """Stage-1 hygiene that resolves the overage must not emit a durable
    compaction event or write an artifact — it re-runs as pure view hygiene."""
    summary_client = RecordingSummaryClient()
    assembler = _assembler(summary_client=summary_client, window=100_000, threshold_ratio=0.04)
    session = AgentSession(session_id="s1")
    # Six same-tool results with multi-line payloads; keep_recent=3 → the
    # three oldest shrink to last-10-lines stubs, enough to drop under the
    # threshold without a summary fold.
    for index in range(6):
        session.append(
            AssistantMessage(
                tool_calls=[ToolCall(id=f"t{index}", name="robot_go_to", arguments={})]
            )
        )
        session.append(
            ToolResultMessage(
                tool_call_id=f"t{index}",
                name="robot_go_to",
                content=[
                    ContentBlock(
                        text="\n".join(
                            f"line {index}.{j} " + "x" * 100 for j in range(20)
                        )
                    )
                ],
            )
        )
    session.append(UserMessage.from_text("latest"))
    state = AgentState(run_id="r1", session_id="s1")

    context = _prepare(assembler, session, state)
    assert context.metrics.compaction_triggered is False
    assert context.metrics.compaction_kind == "micro"
    assert state.compaction is None
    assert summary_client.calls == 0
    assert "[navigate]" in _text(context)  # micro-summarized robot_go_to result

    # Re-runs keep the same behavior — no event, no artifact.
    _prepare(assembler, session, state)
    assert summary_client.calls == 0
    assert state.compaction is None


def test_reactive_aggressive_compaction_writes_artifact() -> None:
    summary_client = RecordingSummaryClient()
    assembler = _assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    # Reactive retry path (force_compact="aggressive") folds + persists.
    context = asyncio.run(assembler.aprepare(
        session=session,
        agent_state=state,
        task_state_store=None,
        tools=[],
        force_compact="aggressive",
    ))
    assert context.metrics.compaction_triggered is True
    # metrics carries the trigger so _notify_compaction reports "reactive",
    # not "auto".
    assert context.metrics.compaction_kind == "reactive_summary"
    assert summary_client.calls == 1
    assert state.compaction is not None
    assert state.last_compaction is not None
    assert state.last_compaction.kind == "reactive"


def test_summary_failure_still_folds_deterministically() -> None:
    summary_client = RecordingSummaryClient(fail=True)
    assembler = _assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    context = _prepare(assembler, session, state)
    assert context.metrics.compaction_triggered is True
    assert "Summary unavailable" in _text(context)
    assert "CONTEXT COMPACTION" in _text(context)
    assert state.compaction is not None

    # The placeholder fold is durable — later rounds don't retry the summary.
    session.append(UserMessage.from_text("next " + "x" * 100))
    _prepare(assembler, session, state)
    assert summary_client.calls == 1


def test_second_compaction_stacks_summaries() -> None:
    summary_client = RecordingSummaryClient()
    assembler = _assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")
    _prepare(assembler, session, state)

    # Grow the live tail past the threshold → second fold must stack heads:
    # head = [S2] + protected-prefix(view[:3]) = [S2, S1, m0, m1].
    _seed_pairs(session, pairs=8, start=10, user_chars=1500, result_chars=2200)
    context = _prepare(assembler, session, state)
    assert summary_client.calls == 2
    artifact = state.compaction
    assert artifact is not None
    head_texts = [
        block.text
        for m in artifact.head_as_messages()
        for block in m.content
        if block.text
    ]
    assert "summary n2" in head_texts[0]
    assert "summary n1" in head_texts[1]
    assert "latest request 10" in _text(context)


def test_head_stubs_are_not_rewrapped_each_round() -> None:
    """The durable head is verbatim fold output — the per-prepare hygiene
    must only touch the live canonical tail. microcompact's tool stubs are
    not idempotent (``[navigate] X`` → ``[navigate] [navigate] X``), so a
    whole-view re-run would corrupt both the visible view and any later
    fold's persisted head."""
    summary_client = RecordingSummaryClient()
    assembler = _assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    _prepare(assembler, session, state)
    artifact = state.compaction
    assert artifact is not None
    head_text = "\n".join(
        block.text
        for m in artifact.head_as_messages()
        for block in m.content
        if block.text
    )
    # The protected-prefix tool result was micro-stubbed at fold time —
    # sanity-check the fixture actually exercises the head-stub path.
    assert "[navigate]" in head_text

    # Round 2+ under an applied artifact: head stub stays single-wrapped.
    context = _prepare(assembler, session, state)
    assert "[navigate] [navigate]" not in _text(context)

    # A second fold must not re-summarize stubs either — neither in the
    # outgoing view nor in the persisted head.
    context = asyncio.run(assembler.aprepare(
        session=session,
        agent_state=state,
        task_state_store=None,
        tools=[],
        force_compact="manual",
    ))
    assert "[navigate] [navigate]" not in _text(context)
    head_text = "\n".join(
        block.text
        for m in state.compaction.head_as_messages()
        for block in m.content
        if block.text
    )
    assert "[navigate] [navigate]" not in head_text


def test_session_mirror_is_never_written() -> None:
    summary_client = RecordingSummaryClient()
    assembler = _assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    before = [m.model_dump(mode="json") for m in session.messages]
    _prepare(assembler, session, state)
    after = [m.model_dump(mode="json") for m in session.messages]
    assert before == after


@pytest.mark.asyncio
async def test_agentscope_native_compress_stays_fenced() -> None:
    """The on_compress_context fence must keep short-circuiting — the durable
    artifact design depends on canonical context being append-only."""
    from homemaster.substrate.middleware_runtime import ContextAssemblyMiddleware

    middleware = ContextAssemblyMiddleware(handle=SimpleNamespace())
    called = False

    async def _next(**kwargs):
        nonlocal called
        called = True

    result = await middleware.on_compress_context(
        agent=None,
        input_kwargs={},
        next_handler=_next,
    )
    assert result is None
    assert called is False


@pytest.mark.asyncio
async def test_async_prepare_matches_sync_durability() -> None:
    summary_client = RecordingSummaryClient()
    assembler = _assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    first = await assembler.aprepare(
        session=session, agent_state=state, task_state_store=None, tools=[]
    )
    assert first.metrics.compaction_triggered is True
    assert summary_client.calls == 1

    session.append(UserMessage.from_text("again " + "x" * 100))
    second = await assembler.aprepare(
        session=session, agent_state=state, task_state_store=None, tools=[]
    )
    assert summary_client.calls == 1
    assert second.metrics.compaction_triggered is False
    assert "summary n1" in _text(second)


def _unfolded_assembler(**kw) -> ContextAssembler:
    """High threshold — the seeded session stays unfolded, so the full-view
    heuristic is visibly larger than any small usage anchor."""
    return _assembler(threshold_ratio=0.5, **kw)


def test_usage_anchor_replaces_full_view_estimate() -> None:
    """opencode/pi model: once the provider's real input_tokens land, the
    next prepare costs anchor + delta — not a full re-estimate of the view."""
    summary_client = RecordingSummaryClient()
    assembler = _unfolded_assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    first = _prepare(assembler, session, state)
    heuristic_tokens = first.metrics.estimated_tokens
    assert heuristic_tokens > 2000  # unfolded full-view estimate — sanity
    assert state.pending_view is not None
    assert state.pending_view.canonical_len == len(session.messages)
    assert state.pending_view.artifact_key == ""  # no fold happened

    # The provider reports the real cost of the sent view — far below the
    # char-heuristic full-view estimate.
    state.note_view_usage(500)
    assert state.usage_anchor is not None
    assert state.usage_anchor.input_tokens == 500

    session.append(UserMessage.from_text("follow up"))
    context = _prepare(assembler, session, state)
    # anchor(500) + tiny delta ≈ 500 → padded ≈ 670 ≪ full heuristic.
    assert context.metrics.estimated_tokens < heuristic_tokens // 3


def test_anchor_delta_covers_only_new_tail() -> None:
    summary_client = RecordingSummaryClient()
    assembler = _unfolded_assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    _prepare(assembler, session, state)
    state.note_view_usage(500)
    baseline = _prepare(assembler, session, state).metrics.estimated_tokens

    # A big append must be priced on top of the anchor.
    session.append(UserMessage.from_text("big " + "x" * 4000))
    grown = _prepare(assembler, session, state).metrics.estimated_tokens
    assert grown > baseline + 1000  # ~1000 tokens of new text, padded 4/3


def test_anchor_recounts_merged_tail_message() -> None:
    """AgentScope merges reply rounds into the last Msg — canonical length
    is unchanged but the tail message grew. tail_key detects it and the
    grown message is re-counted into the delta."""
    summary_client = RecordingSummaryClient()
    assembler = _unfolded_assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    _prepare(assembler, session, state)
    state.note_view_usage(500)
    baseline = _prepare(assembler, session, state).metrics.estimated_tokens

    # In-place growth with no append — only detectable via the tail key.
    session.messages[-1].content[0].text += " " + "y" * 4000
    grown = _prepare(assembler, session, state).metrics.estimated_tokens
    assert grown > baseline + 1000


def test_new_fold_invalidates_anchor() -> None:
    """After a fold the sent view no longer decomposes as anchor+delta —
    the next prepare must fall back to a full-view estimate until new
    usage lands."""
    summary_client = RecordingSummaryClient()
    assembler = _unfolded_assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    _prepare(assembler, session, state)
    # Absurdly large anchor: if the next prepare still trusted it, the
    # reported estimate would stay ≥ padded(5000) ≈ 6.7k.
    state.note_view_usage(5000)
    anchored = _prepare(assembler, session, state).metrics.estimated_tokens
    assert anchored > 5000  # sanity: anchored path reports the anchor

    # Manual compact → new artifact key → anchor stale → full heuristic.
    asyncio.run(assembler.aprepare(
        session=session,
        agent_state=state,
        task_state_store=None,
        tools=[],
        force_compact="manual",
    ))
    fallback = _prepare(assembler, session, state).metrics.estimated_tokens
    assert fallback < 3000  # full heuristic of the small folded view

    # … and the next real usage re-anchors on the folded view.
    state.note_view_usage(5000)
    reanchored = _prepare(assembler, session, state).metrics.estimated_tokens
    assert reanchored > 5000


def test_anchor_respects_corrupt_coverage() -> None:
    """If canonical appears shorter than the anchored coverage (foreign
    session/replay), the anchor must not be trusted."""
    summary_client = RecordingSummaryClient()
    assembler = _unfolded_assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    _prepare(assembler, session, state)
    state.note_view_usage(500)
    # Simulate a stale anchor covering more canonical than exists.
    assert state.usage_anchor is not None
    state.usage_anchor.canonical_len = len(session.messages) + 100
    fallback = _prepare(assembler, session, state).metrics.estimated_tokens
    assert fallback > 3000  # full heuristic of the unfolded view


def test_tools_change_invalidates_anchor() -> None:
    """A changed tool set alters both schema tokens and the projection of
    covered tool-call messages — the anchor must be dropped, not patched."""
    summary_client = RecordingSummaryClient()
    assembler = _unfolded_assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    _prepare(assembler, session, state)
    state.note_view_usage(5000)
    anchored = _prepare(assembler, session, state).metrics.estimated_tokens
    assert anchored > 5000

    tools = [
        {
            "name": "robot_go_to",
            "description": "Move the robot",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    fallback = asyncio.run(assembler.aprepare(
        session=session,
        agent_state=state,
        task_state_store=None,
        tools=tools,
    )).metrics.estimated_tokens
    assert fallback < anchored  # full heuristic, not anchored 5000+


def test_prelude_change_delta_adjusts_anchor() -> None:
    """Automatic memory recall rewrites the prelude most rounds — the
    anchored estimate tracks the delta instead of pinning the stale
    prelude share or dropping the anchor."""
    summary_client = RecordingSummaryClient()
    assembler = _unfolded_assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    _prepare(assembler, session, state)
    state.note_view_usage(500)
    baseline = _prepare(assembler, session, state).metrics.estimated_tokens

    # A recalled-memory prelude arrives: +~1000 tokens of fixed share.
    assembler.bind_automatic_memory_context("memory " + "m" * 4000)
    grown = _prepare(assembler, session, state).metrics.estimated_tokens
    assert grown > baseline + 1000
    assert grown < baseline + 2000  # delta applied, anchor retained

    # … and removing it shrinks the estimate back symmetrically.
    assembler.bind_automatic_memory_context(None)
    shrunk = _prepare(assembler, session, state).metrics.estimated_tokens
    assert shrunk < grown - 1000


@pytest.mark.asyncio
async def test_record_usage_promotes_pending_view() -> None:
    """The runtime wiring: a ModelCallEndEvent's usage lands on the pending
    coverage from the prepare that built the request — anchor promoted with
    the real input token count (cache-read included)."""
    from homemaster.substrate.runtime import AsAgentRuntime

    summary_client = RecordingSummaryClient()
    assembler = _unfolded_assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    await assembler.aprepare(
        session=session,
        agent_state=state,
        task_state_store=None,
        tools=[],
    )
    assert state.pending_view is not None
    assert state.usage_anchor is None

    emitted: list[str] = []

    async def _emit(kind: str, **kwargs) -> None:
        emitted.append(kind)

    await AsAgentRuntime._record_usage(
        state,
        {"input_tokens": 300, "cache_read_input_tokens": 200, "output_tokens": 40},
        api_format="anthropic",
        emit=_emit,
    )
    assert state.usage_anchor is not None
    assert state.usage_anchor.input_tokens == 500  # uncached + cache-read
    assert state.usage_anchor.canonical_len == len(session.messages)
    # Promotion consumes the pending coverage — a stray second event must
    # not re-anchor.
    assert state.pending_view is None

    # OpenAI-family: input_tokens already includes the cached share —
    # adding cache_read would double-count every cache hit.
    await assembler.aprepare(
        session=session,
        agent_state=state,
        task_state_store=None,
        tools=[],
    )
    await AsAgentRuntime._record_usage(
        state,
        {"input_tokens": 300, "cache_read_input_tokens": 200, "output_tokens": 40},
        api_format="openai",
        emit=_emit,
    )
    assert state.usage_anchor.input_tokens == 300

    # Zero-usage calls (errored provider) must not clobber the anchor.
    await AsAgentRuntime._record_usage(
        state, {"input_tokens": 0, "output_tokens": 0}, emit=_emit
    )
    assert state.usage_anchor.input_tokens == 300


@pytest.mark.asyncio
async def test_reactive_compact_prepare_fingerprints_wire_tools() -> None:
    """The reactive retry path must fingerprint the SAME tool serialization
    the retried request sends (the AS function envelope from
    input_kwargs["tools"]) — not the HM registry schema — or the usage
    anchor written by the compaction prepare dies on the next assemble."""
    from homemaster.substrate.middleware_runtime import ContextAssemblyMiddleware

    captured: dict = {}

    class SpyAssembler:
        async def aprepare(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                metrics=None, messages=[], system_prompt="", tools=kwargs.get("tools")
            )

    wire_tools = [
        {
            "type": "function",
            "function": {"name": "robot_go_to", "parameters": {"type": "object"}},
        }
    ]
    middleware = ContextAssemblyMiddleware(handle=SimpleNamespace())
    middleware._assembler = SpyAssembler()
    middleware._handle = SimpleNamespace(
        sync_session=lambda: None,
        session=AgentSession(session_id="s1"),
        agent_state=AgentState(run_id="r1", session_id="s1"),
        task_state_store=None,
    )
    await middleware._compact(tools=wire_tools)
    assert captured["tools"] is wire_tools


def test_mid_list_canonical_mutation_busts_anchor() -> None:
    """prefix_key (hermes base_prefix_fp equivalent): a mid-list rewrite —
    strip, splice, external edit — that tail_key cannot see must fail the
    anchor closed into a full-view estimate."""
    summary_client = RecordingSummaryClient()
    assembler = _unfolded_assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    _prepare(assembler, session, state)
    state.note_view_usage(500)
    anchored = _prepare(assembler, session, state).metrics.estimated_tokens
    assert anchored < 1000

    # Mutate a mid-list message (not the last) — e.g. the image-strip
    # path rewrites canonical content without changing length.
    session.messages[5].content[0].text += " MUTATED"
    fallback = _prepare(assembler, session, state).metrics.estimated_tokens
    assert fallback > 3000  # full-view heuristic restored


def test_estimate_messages_counts_reasoning_and_tool_calls() -> None:
    """pi/openclaw parity: reasoning_content (ThinkingBlock on the wire)
    and tool_call arguments both price into the delta — they were blind
    spots that made assistant-heavy rounds under-count systematically."""
    estimator = AnthropicTokenEstimator()
    msg = AssistantMessage(
        content=[ContentBlock(text="short")],
        reasoning_content="think " + "t" * 4000,
        tool_calls=[
            ToolCall(id="c1", name="robot_go_to", arguments={"to": "x" * 4000})
        ],
    )
    plain = AssistantMessage(content=[ContentBlock(text="short")])
    counted = estimator.estimate_messages([msg])
    baseline = estimator.estimate_messages([plain])
    # ~4000 chars reasoning + ~4000 chars args ≈ ~2000 tokens total.
    assert counted - baseline > 1500


def test_estimate_text_non_ascii_density() -> None:
    """hermes' byte-level corrective: CJK ~1 token/codepoint, non-ASCII
    non-CJK by UTF-8 bytes/4, ASCII chars/4."""
    estimator = AnthropicTokenEstimator()
    ascii_400 = estimator.estimate_text("a" * 400)
    cjk_400 = estimator.estimate_text("汉" * 400)
    cyrillic_400 = estimator.estimate_text("Ж" * 400)  # 2 UTF-8 bytes/char
    assert ascii_400 == 100
    assert cjk_400 == 400  # ~1 token/codepoint, not chars/2
    assert cyrillic_400 == 200  # 800 bytes / 4 — not chars/4


# ---------------------------------------------------------------------------
# Real-shape replay: payload sizes/fields mirror a captured live session
# (reasoning ~1KB/turn, tool results ~1-3KB, mid-session user turns at
# gw-session positions [6, 8] — latest_user_index gates the fold boundary).
# ---------------------------------------------------------------------------

_REAL_REASONING = (
    "The user asked me to check the workspace layout first. I should list the "
    "top-level directories, then inspect src/homemaster for the module "
    "boundaries before touching anything. Last time I skipped the config "
    "package and missed that providers live under src/homemaster/providers — "
    "this time read config/config.py first so provider resolution is known. "
    "The task spans two checkouts; keep paths absolute and never cd blindly. "
    "Comparing context.py with compact.py: the assembler owns prepare-time "
    "projection while compact.py holds pure split/repair helpers — the "
    "duplication risk is in the estimation paths, not the split logic. "
    "session.messages is only a mirror now; anything that rewrites it gets "
    "overwritten by sync_session from the engine transcript, so evidence "
    "about compaction has to come from AgentState, not the session. I will "
    "verify each candidate overlap by reading both implementations fully "
    "rather than grepping for similar names — name similarity alone has "
    "produced false positives before. Also worth checking whether the "
    "middleware path duplicates the direct prepare path or delegates to it; "
    "if it duplicates, that is the redundancy the user is asking about. "
    "Next step: read the middleware_runtime prepare wrapper and compare its "
    "assembly steps against ContextAssembler.prepare line by line, then "
    "check whether memory_recall builds its query from the projected view "
    "or the raw mirror — that distinction decides whether summaries reach "
    "recall. The last tool result listed directories under src/; the deeper "
    "modules worth comparing are context assembly, state serialization, and "
    "the substrate message converters, in that order. "
)  # ~2KB reasoning block — real sessions carry 0.3-1.2k+ per turn; sized up
    # so conversation mass survives micro (tool results stub away, text doesn't)

_REAL_LS_RESULT = (
    "apps\nartifacts\nCHANGELOG.md\nCLAUDE.md\nconfig\ndata\ndocs\nexamples\n"
    "homemaster\nhomemaster.egg-info\nopenharness\nplan\nscripts\nsrc\ntests\n"
    "third_party\ntools\nuv.lock\nworklog\n"
    + "src/homemaster/agent/\nsrc/homemaster/application/\nsrc/homemaster/substrate/\n"
    + "src/homemaster/providers/\nsrc/homemaster/memory/\nsrc/homemaster/tools/\n"
) * 3  # ~1KB real ls/find-shaped payload


def _real_shape_session() -> list:
    """Multi-turn session shaped like a captured gateway run: interleaved
    user turns, reasoning-bearing assistants, KB-scale tool results."""
    msgs = [UserMessage.from_text(
        "帮我看看这个项目有没有代码冗余的情况 /Users/wylam/Documents/workspace/HomeMaster "
        "重点看 agent/context.py 和 substrate/runtime.py 两条链路，先给结论再给证据。"
    )]
    for turn in range(1, 20):
        if turn in (6, 8):  # mid-session user turns — real gw cadence
            msgs.append(UserMessage.from_text(
                "继续，另外把 token 估算相关的也列出来" if turn == 6 else "runtime.py 也看"
            ))
        msgs.append(AssistantMessage(
            reasoning_content=_REAL_REASONING + f"turn {turn} specifics.",
            tool_calls=[ToolCall(
                id=f"call_{turn:04d}", name="list_directory" if turn % 3 else "read_file",
                arguments={"path": f"/ws/src/homemaster/mod_{turn}", "depth": 2},
            )],
        ))
        msgs.append(ToolResultMessage(
            tool_call_id=f"call_{turn:04d}",
            name="list_directory" if turn % 3 else "read_file",
            content=[ContentBlock(text=_REAL_LS_RESULT + f"\n# turn {turn}")],
        ))
    return msgs


def test_real_shape_session_folds_once_and_stays_folded() -> None:
    """Replay a real-shaped multi-turn session: auto fold fires once on
    real payloads, the artifact persists across later rounds and a
    snapshot round-trip, and the summary model saw exactly the folded
    prefix (real mid-history content in, live tail out)."""
    sc = RecordingSummaryClient()
    sc.seen = []  # capture summary inputs
    orig = sc.complete
    sc.complete = lambda messages, **kw: (sc.seen.append(messages), orig(messages, **kw))[1]

    # window=24000 → threshold=min(12000, 24000-200-13000)=10800. The
    # real-shape view is ~13k raw/~17.3k padded — folds; post-micro the
    # reasoning mass still exceeds the gate (tool stubs can't absorb
    # text), so the summary path actually runs; anchored ~6.3k stays
    # under — no re-fold.
    assembler = _assembler(summary_client=sc, window=24_000, threshold_ratio=0.5)
    session = AgentSession(session_id="real-shape")
    msgs = _real_shape_session()
    session.replace_messages(msgs)
    state = AgentState(run_id="r1", session_id="real-shape")

    ctx = _prepare(assembler, session, state)
    assert ctx.metrics.compaction_kind == "summary"
    art = state.compaction
    assert art is not None and art.first_kept_index > 0
    # Real gate: fold boundary cannot cross the latest user turn (idx 16)
    # — real sessions only fold the span before it.
    assert art.first_kept_index <= 17
    head_text = str(art.head_messages[0].get("content"))
    assert "CONTEXT COMPACTION" in head_text

    # Summary input is two sections: "# Messages To Compact" (the folded
    # span) + "# Recent Tail Reference" (kept tail shown as context —
    # reasoning_content is deliberately never serialized to the summarizer).
    seen_text = json.dumps([m.model_dump(mode="json") for m in sc.seen[0]],
                           ensure_ascii=False, default=str)
    compacted_section, _, tail_section = seen_text.partition(
        "# Recent Tail Reference"
    )
    assert "call_0004" in compacted_section  # folded mid-history reaches summarizer
    assert "call_0019" in tail_section  # live tail shown as reference
    assert "call_0019" not in compacted_section  # tail is never folded away

    # Later rounds on real appends: anchored, no re-fold.
    state.note_view_usage(4321)
    for _ in range(3):
        tail = msgs[-1]
        msgs.append(ToolResultMessage.model_validate(tail.model_dump()))
        msgs[-1].tool_call_id = f"call_extra_{len(msgs)}"
        session.replace_messages(msgs)
        ctx = _prepare(assembler, session, state)
        assert ctx.metrics.compaction_triggered is False
        state.note_view_usage(4321)
    assert sc.calls == 1, "summary must fire exactly once across rounds"

    # Snapshot round-trip keeps artifact + anchor on real shapes.
    restored = AgentState.model_validate(
        json.loads(json.dumps(state.model_dump(mode="json")))
    )
    assert restored.compaction is not None
    assert restored.usage_anchor is not None and restored.usage_anchor.prefix_key
    ctx = _prepare(assembler, session, restored)
    assert ctx.metrics.compaction_triggered is False
    assert sc.calls == 1


def _seed_single_instruction(session: AgentSession, *, rounds: int = 12) -> None:
    """One user instruction followed by agentic reasoning/tool rounds —
    the ALFWorld/CLI shape the old pin could never fold."""
    session.append(UserMessage.from_text("do the chore: find the mug, heat it"))
    for index in range(rounds):
        session.append(
            AssistantMessage(
                reasoning_content=(
                    f"planning step {index}: " + "think about objects " * 60
                ),
                tool_calls=[
                    ToolCall(
                        id=f"sc{index}", name="navigate", arguments={"to": f"wp{index}"}
                    )
                ],
                finish_reason="tool_calls",
                usage={"input_tokens": 500 + index},
            )
        )
        session.append(
            ToolResultMessage(
                tool_call_id=f"sc{index}",
                name="navigate",
                content=[ContentBlock(text=f"moved {index} " + "m" * 100)],
            )
        )


def test_single_instruction_session_folds() -> None:
    """hermes-style bounded pin: an instruction already inside the protected
    prefix stays verbatim, so the fold must proceed — the old unconditional
    pin emptied `older` and blocked summary compaction forever."""
    summary_client = RecordingSummaryClient()
    assembler = _assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_single_instruction(session)
    state = AgentState(run_id="r1", session_id="s1")

    context = _prepare(assembler, session, state)
    assert context.metrics.compaction_triggered is True
    assert summary_client.calls == 1
    assert state.compaction is not None, "durable artifact must be written"
    # The instruction survives verbatim via the protected prefix.
    assert "do the chore" in _text(context)

    session.append(AssistantMessage(content=[ContentBlock(text="done " + "d" * 50)]))
    context2 = _prepare(assembler, session, state)
    assert summary_client.calls == 1, "no re-fold after the durable artifact"
    assert context2.metrics.compaction_triggered is False


def test_latest_user_still_pinned_when_foldable() -> None:
    """A latest user message that would actually be folded still pulls the
    cut back — only the already-protected case stops pinning."""
    summary_client = RecordingSummaryClient()
    assembler = _assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    session.append(UserMessage.from_text("first task"))
    for index in range(3):
        session.append(
            AssistantMessage(
                reasoning_content="r" * 1500,
                tool_calls=[ToolCall(id=f"p{index}", name="go", arguments={})],
            )
        )
        session.append(
            ToolResultMessage(
                tool_call_id=f"p{index}",
                name="go",
                content=[ContentBlock(text="done")],
            )
        )
    session.append(UserMessage.from_text("SECOND TASK: now clean the table"))
    for index in range(8):
        session.append(
            AssistantMessage(
                reasoning_content=f"after second task {index} " + "r" * 1500,
                tool_calls=[ToolCall(id=f"q{index}", name="go", arguments={})],
            )
        )
        session.append(
            ToolResultMessage(
                tool_call_id=f"q{index}",
                name="go",
                content=[ContentBlock(text="done")],
            )
        )
    state = AgentState(run_id="r1", session_id="s1")

    context = _prepare(assembler, session, state)
    assert context.metrics.compaction_triggered is True
    text = _text(context)
    assert "SECOND TASK: now clean the table" in text
    assert "first task" in text  # protected prefix


class _LengthTruncatedClient(RecordingSummaryClient):
    def complete(self, messages, *, system_prompt: str = "", **kwargs):
        self.calls += 1
        return AssistantMessage(
            content=[ContentBlock(text="## partial sum")],
            finish_reason="length",
        )


def test_truncated_summary_is_not_persisted() -> None:
    """pi-style: stopReason=='length' must not commit a partial checkpoint —
    fold proceeds behind the deterministic fallback instead."""
    summary_client = _LengthTruncatedClient()
    assembler = _assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    context = _prepare(assembler, session, state)
    assert context.metrics.compaction_triggered is True
    assert summary_client.calls == 1
    text = _text(context)
    assert "## partial sum" not in text
    assert "Summary unavailable" in text

    session.append(UserMessage.from_text("next turn " + "x" * 50))
    context2 = _prepare(assembler, session, state)
    assert context2.metrics.compaction_triggered is False
    assert summary_client.calls == 1, "fallback artifact must also be durable"


def test_truncated_summary_aborts_sync_when_configured() -> None:
    summary_client = _LengthTruncatedClient()
    assembler = _assembler(
        summary_client=summary_client, abort_on_summary_failure=True
    )
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    with pytest.raises(RuntimeError, match="finish=length"):
        _prepare(assembler, session, state)


@pytest.mark.asyncio
async def test_truncated_summary_falls_back_async() -> None:
    summary_client = _LengthTruncatedClient()
    assembler = _assembler(summary_client=summary_client)
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    context = await assembler.aprepare(
        session=session, agent_state=state, task_state_store=None, tools=[]
    )
    assert context.metrics.compaction_triggered is True
    assert "## partial sum" not in _text(context)
    assert "Summary unavailable" in _text(context)


@pytest.mark.asyncio
async def test_truncated_summary_aborts_when_configured() -> None:
    summary_client = _LengthTruncatedClient()
    assembler = _assembler(
        summary_client=summary_client, abort_on_summary_failure=True
    )
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    with pytest.raises(RuntimeError, match="finish=length"):
        await assembler.aprepare(
            session=session, agent_state=state, task_state_store=None, tools=[]
        )


class _KwargsRecordingClient(RecordingSummaryClient):
    def complete(self, messages, *, system_prompt: str = "", **kwargs):
        self.last_kwargs = kwargs
        return super().complete(messages, system_prompt=system_prompt, **kwargs)


def test_summary_uses_dedicated_output_budget() -> None:
    """opencode-style dedicated summary budget — the call must not inherit
    the per-request output_reserve_tokens sliver."""
    summary_client = _KwargsRecordingClient()
    assembler = _assembler(
        summary_client=summary_client, summary_max_output_tokens=7777
    )
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    context = _prepare(assembler, session, state)
    assert context.metrics.compaction_triggered is True
    assert summary_client.last_kwargs["max_output_tokens"] == 7777
    assert summary_client.last_kwargs["max_output_tokens"] != 200


def test_summary_budget_clamps_to_provider_cap() -> None:
    """pi rule: a dedicated budget larger than the model's declared max
    must clamp — asking the wire for more than the model can emit just
    errors the call into the fallback path."""
    summary_client = _KwargsRecordingClient()
    assembler = _assembler(
        summary_client=summary_client,
        summary_max_output_tokens=40960,
        provider_max_output=8192,
    )
    session = AgentSession(session_id="s1")
    _seed_pairs(session, pairs=10)
    state = AgentState(run_id="r1", session_id="s1")

    context = _prepare(assembler, session, state)
    assert context.metrics.compaction_triggered is True
    assert summary_client.last_kwargs["max_output_tokens"] == 8192
