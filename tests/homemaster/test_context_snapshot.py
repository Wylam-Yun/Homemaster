"""Tests for ContextSnapshot — snapshot generation, stale detection, refresh."""

from __future__ import annotations

from homemaster.agent.state import AgentState
from homemaster.memory.context_snapshot import ContextSnapshot


def test_generate_memory_snapshot_from_records() -> None:
    snap = ContextSnapshot()
    records = [
        {"object_category": "cup", "anchor": {"room_id": "kitchen"}, "belief_state": "verified"},
        {"object_category": "medicine", "anchor": {"room_id": "bedroom"}, "belief_state": "stale"},
    ]
    result = snap.generate_memory_snapshot(records)

    assert "cup" in result.content
    assert "kitchen" in result.content
    assert "medicine" in result.content
    assert result.content_hash  # non-empty
    assert result.generated_at  # non-empty
    assert result.source_versions["object_memory"] == "2"


def test_generate_user_snapshot_empty() -> None:
    snap = ContextSnapshot()
    result = snap.generate_user_snapshot(None)

    assert "No user profile" in result.content
    assert result.content_hash


def test_generate_user_snapshot_with_records() -> None:
    snap = ContextSnapshot()
    records = [
        {"key": "language", "value": "zh-CN"},
        {"key": "volume", "value": "low"},
    ]
    result = snap.generate_user_snapshot(records)

    assert "language" in result.content
    assert "zh-CN" in result.content


def test_refresh_if_stale_generates_when_missing() -> None:
    snap = ContextSnapshot()
    state = AgentState(
        memory_hits=[{"object_category": "cup", "anchor": {"room_id": "kitchen"}}],
    )
    assert state.memory_context_snapshot is None

    updated = snap.refresh_if_stale(state)
    assert updated.memory_context_snapshot is not None
    assert "cup" in updated.memory_context_snapshot


def test_refresh_if_stale_preserves_existing() -> None:
    snap = ContextSnapshot()
    state = AgentState(memory_context_snapshot="existing snapshot")
    updated = snap.refresh_if_stale(state)
    assert updated.memory_context_snapshot == "existing snapshot"


def test_refresh_if_stale_regenerates_on_data_change() -> None:
    """Snapshot must regenerate when memory_hits change (e.g., after update_memory)."""
    snap = ContextSnapshot()
    initial_hits = [
        {"object_category": "cup", "anchor": {"room_id": "kitchen"}, "belief_state": "verified"},
    ]
    state = AgentState(memory_hits=initial_hits)
    state = snap.refresh_if_stale(state)
    first_snapshot = state.memory_context_snapshot
    assert "cup" in first_snapshot

    # Simulate update_memory adding a new record
    state.memory_hits = initial_hits + [
        {"object_category": "book", "anchor": {"room_id": "bedroom"}, "belief_state": "verified"},
    ]
    state = snap.refresh_if_stale(state)
    second_snapshot = state.memory_context_snapshot
    assert "book" in second_snapshot
    assert second_snapshot != first_snapshot


def test_refresh_if_stale_no_change_preserves_snapshot() -> None:
    """Snapshot stays the same when memory_hits haven't changed."""
    snap = ContextSnapshot()
    hits = [
        {"object_category": "cup", "anchor": {"room_id": "kitchen"}, "belief_state": "verified"},
    ]
    state = AgentState(memory_hits=hits)
    state = snap.refresh_if_stale(state)
    first_snapshot = state.memory_context_snapshot

    # Refresh again with same data — snapshot should not change
    state = snap.refresh_if_stale(state)
    assert state.memory_context_snapshot == first_snapshot
