from __future__ import annotations

import json
import multiprocessing
import threading
from pathlib import Path
from queue import Empty
from typing import Any

import pytest

import homemaster.application.session as session_module
from homemaster.agent.messages import AssistantMessage, UserMessage
from homemaster.application.session import (
    SessionConflictError,
    SessionFileBackend,
    SessionManager,
)


def _payload(generation: int, text: str) -> dict[str, Any]:
    return {
        "session_generation": generation,
        "messages": [UserMessage.from_text(text).model_dump(mode="json")],
    }


def _process_save(root: str, ready: Any, start: Any, output: Any) -> None:
    backend = SessionFileBackend(Path(root))
    ready.put(True)
    start.wait()
    try:
        revision = backend.save("shared", _payload(1, "writer"), expected_revision=0)
        output.put(("saved", revision))
    except SessionConflictError:
        output.put(("conflict", None))


def test_backend_uses_revision_first_pointer_last_and_cas(tmp_path, monkeypatch) -> None:
    backend = SessionFileBackend(tmp_path)
    assert backend.save("s1", _payload(1, "old"), expected_revision=0) == 1
    real_write = session_module._atomic_write_json

    def fail_pointer(path: Path, payload: dict[str, Any]) -> None:
        if path.name == "latest.json":
            raise RuntimeError("pointer crash")
        real_write(path, payload)

    monkeypatch.setattr(session_module, "_atomic_write_json", fail_pointer)
    with pytest.raises(RuntimeError, match="pointer crash"):
        backend.save("s1", _payload(2, "orphan"), expected_revision=1)

    snapshot = backend.load("s1")
    assert snapshot.revision == 1
    assert snapshot.payload["messages"][0]["content"][0]["text"] == "old"

    monkeypatch.setattr(session_module, "_atomic_write_json", real_write)
    assert backend.save("s1", _payload(3, "new"), expected_revision=1) == 3
    with pytest.raises(SessionConflictError, match="expected=1, current=3"):
        backend.save("s1", _payload(4, "stale"), expected_revision=1)


def test_crash_before_revision_publication_keeps_old_snapshot(tmp_path, monkeypatch) -> None:
    backend = SessionFileBackend(tmp_path)
    backend.save("s1", _payload(1, "old"), expected_revision=0)
    real_write = session_module._atomic_write_json

    def fail_revision(path: Path, payload: dict[str, Any]) -> None:
        if path.parent.name == "revisions":
            raise RuntimeError("revision crash")
        real_write(path, payload)

    monkeypatch.setattr(session_module, "_atomic_write_json", fail_revision)
    with pytest.raises(RuntimeError, match="revision crash"):
        backend.save("s1", _payload(2, "new"), expected_revision=1)

    assert backend.load("s1").revision == 1


def test_thread_writers_are_locked_and_stale_writer_is_rejected(tmp_path) -> None:
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def writer() -> None:
        backend = SessionFileBackend(tmp_path)
        barrier.wait()
        try:
            backend.save("shared", _payload(1, "thread"), expected_revision=0)
            outcomes.append("saved")
        except SessionConflictError:
            outcomes.append("conflict")

    threads = [threading.Thread(target=writer) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(outcomes) == ["conflict", "saved"]


def test_process_writers_are_locked_and_stale_writer_is_rejected(tmp_path) -> None:
    context = multiprocessing.get_context("fork")
    ready = context.Queue()
    output = context.Queue()
    start = context.Event()
    processes = [
        context.Process(target=_process_save, args=(str(tmp_path), ready, start, output))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for _ in processes:
        ready.get(timeout=5)
    start.set()
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0
    try:
        outcomes = [output.get(timeout=1)[0] for _ in processes]
    except Empty as exc:
        raise AssertionError("writer process did not report an outcome") from exc
    assert sorted(outcomes) == ["conflict", "saved"]


@pytest.mark.asyncio
async def test_manager_snapshot_excludes_live_resources_and_resumes_cleanly(tmp_path) -> None:
    manager = SessionManager(session_root=tmp_path)
    runtime = await manager.open_or_resume("persisted", environment_ref="backend-a")
    runtime.session.append(UserMessage.from_text("hello"))
    runtime.session.append(AssistantMessage())
    runtime.canonical_evidence_refs = ("evidence/run/1",)
    runtime.backend = object()
    runtime.provider_client = object()
    runtime.tool_view = object()
    runtime.browser = object()

    await manager.save("persisted")
    encoded = json.dumps(SessionFileBackend(tmp_path).load("persisted").payload)

    assert "provider_client" not in encoded
    assert "tool_view" not in encoded
    assert "browser" not in encoded
    assert "evidence/run/1" in encoded

    resumed_manager = SessionManager(session_root=tmp_path)
    resumed = await resumed_manager.resume("persisted")
    assert len(resumed.session.messages) == 1
    assert resumed.environment_ref == "backend-a"
    assert SessionFileBackend(tmp_path).list_session_ids() == ("persisted",)
    assert "## user\nhello" in SessionFileBackend(tmp_path).export_markdown("persisted")


def test_missing_session_read_does_not_create_session_directories(tmp_path: Path) -> None:
    backend = SessionFileBackend(tmp_path)

    with pytest.raises(FileNotFoundError):
        backend.load("missing-session")

    assert tuple(tmp_path.iterdir()) == ()
