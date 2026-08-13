from __future__ import annotations

import asyncio
import json

import pytest

from homemaster.application.session import SessionConflictError, SessionManager


@pytest.mark.asyncio
async def test_new_session_starts_with_recall_required() -> None:
    manager = SessionManager()
    runtime = await manager.open_or_resume("new-recall")

    assert runtime.require_recall is True
    async with manager.turn("new-recall") as (_, generation, _):
        assert runtime.consume_recall(generation) is True
        assert runtime.consume_recall(generation) is False
        runtime.require_recall_after_compaction(generation)
        assert runtime.require_recall is True


@pytest.mark.asyncio
async def test_recall_latch_round_trips_snapshot(tmp_path) -> None:
    manager = SessionManager(session_root=tmp_path)
    runtime = await manager.open_or_resume("persist-recall")
    async with manager.turn("persist-recall") as (_, generation, _):
        assert runtime.consume_recall(generation) is True
        await manager.save("persist-recall", generation=generation)

    restored = await SessionManager(session_root=tmp_path).resume("persist-recall")
    assert restored.require_recall is False


@pytest.mark.asyncio
async def test_required_recall_latch_round_trips_snapshot(tmp_path) -> None:
    manager = SessionManager(session_root=tmp_path)
    runtime = await manager.open_or_resume("persist-required-recall")
    await manager.save("persist-required-recall", generation=runtime.generation)

    restored = await SessionManager(session_root=tmp_path).resume("persist-required-recall")

    assert restored.require_recall is True


def test_blank_run_request_is_rejected_before_automatic_recall() -> None:
    from homemaster.application.contracts import RunRequest

    with pytest.raises(ValueError, match="run text must be non-empty"):
        RunRequest(text="   ")


@pytest.mark.asyncio
async def test_legacy_snapshot_without_recall_latch_restores_false(tmp_path) -> None:
    manager = SessionManager(session_root=tmp_path)
    runtime = await manager.open_or_resume("legacy-recall")
    await manager.save("legacy-recall", generation=runtime.generation)
    revision_path = (
        tmp_path / "legacy-recall" / "revisions" / "00000000000000000001.json"
    )
    payload = json.loads(revision_path.read_text(encoding="utf-8"))
    payload.pop("require_recall")
    revision_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    restored = await SessionManager(session_root=tmp_path).resume("legacy-recall")

    assert restored.require_recall is False


@pytest.mark.asyncio
async def test_default_sessions_are_new_and_resume_requires_explicit_id(tmp_path) -> None:
    manager = SessionManager(session_root=tmp_path)

    first = await manager.open_or_resume()
    second = await manager.open_or_resume()

    assert first.session.session_id != second.session.session_id
    with pytest.raises(ValueError, match="resume requires explicit session id"):
        await manager.open_or_resume(resume=True)


@pytest.mark.asyncio
async def test_only_explicit_continuous_taskset_reuses_active_session() -> None:
    manager = SessionManager()
    first = await manager.open_or_resume("taskset-1", continuous_taskset=True)

    shared = await manager.open_or_resume("taskset-1", continuous_taskset=True)

    assert shared is first
    with pytest.raises(SessionConflictError, match="existing sessions require"):
        await manager.open_or_resume("taskset-1")


@pytest.mark.asyncio
async def test_same_session_turns_serialize_while_distinct_sessions_overlap() -> None:
    manager = SessionManager()
    first = await manager.open_or_resume("first")
    second = await manager.open_or_resume("second")
    entered: list[str] = []
    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def hold(session_id: str, label: str) -> None:
        async with manager.turn(session_id):
            entered.append(label)
            if label == "first-a":
                first_entered.set()
                await release_first.wait()

    first_task = asyncio.create_task(hold(first.session.session_id, "first-a"))
    await first_entered.wait()
    blocked_same = asyncio.create_task(hold(first.session.session_id, "first-b"))
    overlapping_other = asyncio.create_task(hold(second.session.session_id, "second"))
    await asyncio.wait_for(overlapping_other, timeout=1)

    assert entered == ["first-a", "second"]
    assert not blocked_same.done()
    release_first.set()
    await asyncio.gather(first_task, blocked_same)
    assert entered == ["first-a", "second", "first-b"]


@pytest.mark.asyncio
async def test_compaction_request_is_bound_to_exact_session_generation() -> None:
    manager = SessionManager()
    runtime = await manager.open_or_resume("compact")

    async with manager.turn("compact") as (_, generation, _):
        manager.request_compaction("compact", generation, "manual")
        assert runtime.consume_compaction(generation) == "manual"
        assert runtime.consume_compaction(generation) is None


@pytest.mark.asyncio
async def test_queued_turn_rebinds_only_after_it_acquires_the_turn_lock() -> None:
    manager = SessionManager()
    runtime = await manager.open_or_resume("shared")
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first() -> None:
        async with manager.turn("shared", environment_ref="backend-a"):
            first_entered.set()
            await release_first.wait()

    async def second() -> None:
        async with manager.turn("shared", environment_ref="backend-b"):
            second_entered.set()

    first_task = asyncio.create_task(first())
    await first_entered.wait()
    second_task = asyncio.create_task(second())
    await asyncio.sleep(0.05)

    assert runtime.environment_ref == "backend-a"
    assert second_entered.is_set() is False
    release_first.set()
    await asyncio.gather(first_task, second_task)
    assert runtime.environment_ref == "backend-b"
