"""Session ownership, generation fencing, and durable revision snapshots."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from homemaster.agent.messages import Message
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState
from homemaster.task_state.models import TaskStatus
from homemaster.task_state.store import TaskStateStore


class SessionError(RuntimeError):
    """Base error for session ownership and persistence failures."""


class SessionConflictError(SessionError):
    """Raised when a stale writer attempts to publish a session revision."""


class SessionGenerationError(SessionError):
    """Raised when an old run attempts to write current session state."""


@runtime_checkable
class SessionBackend(Protocol):
    def save(
        self,
        session_id: str,
        payload: dict[str, Any],
        *,
        expected_revision: int | None,
    ) -> int: ...

    def load(self, session_id: str) -> SessionSnapshot: ...

    def list_session_ids(self) -> tuple[str, ...]: ...

    def export_markdown(self, session_id: str) -> str: ...


class CancellationSource:
    def __init__(self) -> None:
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True


@dataclass(frozen=True)
class CompactionRequest:
    generation: int
    kind: str


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: str
    revision: int
    generation: int
    environment_ref: str | None
    payload: dict[str, Any]


@dataclass
class SessionRuntime:
    session: AgentSession
    agent_state: AgentState
    task_state_store: TaskStateStore
    environment_ref: str | None = None
    canonical_evidence_refs: tuple[str, ...] = ()
    generation: int = 0
    revision: int = 0

    def __post_init__(self) -> None:
        if self.session.session_id != self.agent_state.session_id and self.agent_state.session_id:
            raise ValueError("session and agent state ids differ")
        if not self.agent_state.session_id:
            self.agent_state.session_id = self.session.session_id
        self.canonical_evidence_refs = _validated_evidence_refs(
            self.canonical_evidence_refs
        )
        self.turn_lock = asyncio.Lock()
        self.active_task: asyncio.Task[Any] | None = None
        self.cancellation: CancellationSource | None = None
        self.last_result: Any = None
        self._compaction_request: CompactionRequest | None = None
        self._observation_reset: Callable[[str], Any] | None = None
        self._needs_observe = True

    def set_observation_reset(self, callback: Callable[[str], Any] | None) -> None:
        self._observation_reset = callback
        if callback is not None and self._needs_observe:
            callback("session requires a fresh observe")

    def mark_observed(self, generation: int) -> None:
        self.assert_generation(generation)
        self._needs_observe = False

    def rebind_environment(self, environment_ref: str | None) -> None:
        self.environment_ref = environment_ref
        self._needs_observe = True
        if self._observation_reset is not None:
            self._observation_reset("session environment rebind requires a fresh observe")

    def assert_generation(self, generation: int) -> None:
        if generation != self.generation:
            raise SessionGenerationError(
                f"stale session generation {generation}; current={self.generation}"
            )

    def request_compaction(self, generation: int, kind: str = "manual") -> None:
        self.assert_generation(generation)
        if not kind.strip():
            raise ValueError("compaction kind must be non-empty")
        self._compaction_request = CompactionRequest(generation=generation, kind=kind)

    def consume_compaction(self, generation: int) -> str | None:
        self.assert_generation(generation)
        request = self._compaction_request
        if request is None or request.generation != generation:
            return None
        self._compaction_request = None
        return request.kind

    def cancel(self, generation: int) -> bool:
        self.assert_generation(generation)
        if self.cancellation is None:
            return False
        self.cancellation.cancel()
        task = self.active_task
        self.generation += 1
        self._compaction_request = None
        if task is not None and not task.done():
            task.cancel()
        return True


class SessionFileBackend:
    """Revisioned JSON backend with a CAS latest pointer.

    Immutable revisions are written and fsynced before the small latest pointer
    is replaced.  Readers validate the pointer and fall back to the newest
    complete revision when a crash leaves a torn pointer behind.
    """

    _locks: dict[Path, threading.Lock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        session_id: str,
        payload: dict[str, Any],
        *,
        expected_revision: int | None,
    ) -> int:
        _validate_session_id(session_id)
        lock = self._lock_for(session_id)
        with lock, self._file_lock(session_id):
            current = self._committed_snapshot(session_id)
            current_revision = current.revision if current is not None else 0
            if expected_revision is not None and current_revision != expected_revision:
                raise SessionConflictError(
                    "stale session revision: "
                    f"expected={expected_revision}, current={current_revision}"
                )
            revision = max(current_revision, self._highest_revision(session_id)) + 1
            stored = dict(payload)
            stored["snapshot_revision"] = revision
            stored["session_id"] = session_id
            revision_path = self._revision_path(session_id, revision)
            _atomic_write_json(revision_path, stored)
            pointer = {
                "schema_version": "homemaster-v1.9-session-pointer-v1",
                "session_id": session_id,
                "revision": revision,
                "revision_path": revision_path.name,
            }
            _atomic_write_json(self._latest_path(session_id), pointer)
            return revision

    def load(self, session_id: str) -> SessionSnapshot:
        _validate_session_id(session_id)
        committed = self._committed_snapshot(session_id)
        if committed is not None:
            return committed
        candidates = sorted(self._revisions_dir(session_id).glob("*.json"), reverse=True)
        for path in candidates:
            snapshot = self._read_revision(session_id, path)
            if snapshot is not None:
                return snapshot
        raise FileNotFoundError(f"no complete session revision for {session_id}")

    def list_session_ids(self) -> tuple[str, ...]:
        sessions: list[tuple[float, str]] = []
        for path in self.root.iterdir():
            if not path.is_dir():
                continue
            try:
                snapshot = self.load(path.name)
                saved_at = float(snapshot.payload.get("saved_at", 0))
            except (FileNotFoundError, OSError, TypeError, ValueError):
                continue
            sessions.append((saved_at, snapshot.session_id))
        return tuple(session_id for _, session_id in sorted(sessions, reverse=True))

    def export_markdown(self, session_id: str) -> str:
        snapshot = self.load(session_id)
        lines = [f"# HomeMaster Session {snapshot.session_id}"]
        for message in snapshot.payload.get("messages", []):
            if not isinstance(message, dict):
                continue
            text = "\n".join(
                str(block.get("text"))
                for block in message.get("content", [])
                if isinstance(block, dict) and block.get("text")
            )
            if text:
                lines.append(f"\n## {message.get('role', 'unknown')}\n{text}")
        return "\n".join(lines)

    def _read_latest(self, session_id: str) -> dict[str, Any] | None:
        path = self._latest_path(session_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _committed_snapshot(self, session_id: str) -> SessionSnapshot | None:
        pointer = self._read_latest(session_id)
        if pointer is None:
            return None
        try:
            if pointer.get("session_id") != session_id:
                return None
            revision = int(pointer["revision"])
            if revision <= 0:
                return None
        except (KeyError, TypeError, ValueError):
            return None
        return self._read_revision(session_id, self._revision_path(session_id, revision))

    def _read_revision(self, session_id: str, path: Path) -> SessionSnapshot | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("session_id") != session_id:
                return None
            revision = int(payload.get("snapshot_revision", 0))
            if revision <= 0 or path.stem != f"{revision:020d}":
                return None
            return SessionSnapshot(
                session_id=session_id,
                revision=revision,
                generation=int(payload.get("session_generation", 0)),
                environment_ref=_optional_text(payload.get("environment_ref")),
                payload=payload,
            )
        except (OSError, ValueError, TypeError):
            return None

    def _highest_revision(self, session_id: str) -> int:
        highest = 0
        for path in self._revisions_dir(session_id).glob("*.json"):
            try:
                highest = max(highest, int(path.stem))
            except ValueError:
                continue
        return highest

    def _lock_for(self, session_id: str) -> threading.Lock:
        key = self._session_dir(session_id)
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    @contextlib.contextmanager
    def _file_lock(self, session_id: str) -> Iterator[None]:
        lock_path = self._session_dir(session_id) / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+")
        try:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except ImportError:
                pass
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except ImportError:
                pass
            handle.close()

    def _revisions_dir(self, session_id: str) -> Path:
        path = self._session_dir(session_id) / "revisions"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _revision_path(self, session_id: str, revision: int) -> Path:
        return self._revisions_dir(session_id) / f"{revision:020d}.json"

    def _latest_path(self, session_id: str) -> Path:
        path = self._session_dir(session_id) / "latest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _session_dir(self, session_id: str) -> Path:
        _validate_session_id(session_id)
        return self.root / session_id


class SessionManager:
    """Own session state while keeping active resources out of snapshots."""

    def __init__(
        self,
        *,
        session_root: Path | None = None,
        backend: SessionBackend | None = None,
    ) -> None:
        if session_root is not None and backend is not None:
            raise ValueError("configure session_root or backend, not both")
        if backend is not None and not isinstance(backend, SessionBackend):
            raise TypeError("backend must implement SessionBackend")
        self._sessions: dict[str, SessionRuntime] = {}
        self._backend = (
            backend
            if backend is not None
            else SessionFileBackend(session_root) if session_root is not None else None
        )
        self._manager_lock = asyncio.Lock()

    @property
    def sessions(self) -> tuple[SessionRuntime, ...]:
        return tuple(self._sessions.values())

    async def open_or_resume(
        self,
        session_id: str | None = None,
        *,
        environment_ref: str | None = None,
        resume: bool = False,
        continuous_taskset: bool = False,
    ) -> SessionRuntime:
        if session_id is not None and not session_id.strip():
            raise ValueError("session_id must be non-empty")
        if session_id is not None:
            _validate_session_id(session_id)
        if continuous_taskset and session_id is None:
            raise ValueError("continuous taskset requires an explicit session id")
        async with self._manager_lock:
            if session_id is not None and session_id in self._sessions:
                runtime = self._sessions[session_id]
                if not (resume or continuous_taskset):
                    raise SessionConflictError(
                        "existing sessions require resume=True or continuous_taskset=True"
                    )
                if environment_ref is not None:
                    runtime.rebind_environment(environment_ref)
                return runtime
            if resume:
                if session_id is None or self._backend is None:
                    raise ValueError("resume requires explicit session id and session backend")
                runtime = self._runtime_from_snapshot(self._backend.load(session_id))
                if environment_ref is not None:
                    runtime.rebind_environment(environment_ref)
            else:
                actual_id = session_id or f"session-{uuid.uuid4().hex[:12]}"
                session = AgentSession(actual_id)
                runtime = SessionRuntime(
                    session=session,
                    agent_state=AgentState(session_id=actual_id, run_id=""),
                    task_state_store=TaskStateStore(run_id=actual_id),
                    environment_ref=environment_ref,
                )
            self._sessions[runtime.session.session_id] = runtime
            return runtime

    @asynccontextmanager
    async def turn(self, session_id: str) -> Any:
        runtime = self._sessions.get(session_id)
        if runtime is None:
            raise KeyError(session_id)
        async with runtime.turn_lock:
            runtime.generation += 1
            generation = runtime.generation
            runtime.agent_state.turn_index += 1
            runtime.cancellation = CancellationSource()
            active_task = asyncio.current_task()
            runtime.active_task = active_task
            try:
                yield runtime, generation, runtime.cancellation
            finally:
                if runtime.active_task is active_task:
                    runtime.active_task = None
                    runtime.cancellation = None

    def apply(
        self,
        session_id: str,
        generation: int,
        mutation: Callable[[SessionRuntime], Any],
    ) -> Any:
        runtime = self.get(session_id)
        runtime.assert_generation(generation)
        result = mutation(runtime)
        runtime.assert_generation(generation)
        return result

    def append_message(self, session_id: str, generation: int, message: Message) -> None:
        self.apply(session_id, generation, lambda runtime: runtime.session.append(message))

    def commit_final_result(
        self,
        session_id: str,
        generation: int,
        result: Any,
        *,
        status: str | None = None,
    ) -> None:
        def commit(runtime: SessionRuntime) -> None:
            runtime.last_result = result
            if status is not None:
                runtime.agent_state.status = status  # type: ignore[assignment]

        self.apply(session_id, generation, commit)

    def request_compaction(
        self,
        session_id: str,
        generation: int,
        kind: str = "manual",
    ) -> None:
        self.get(session_id).request_compaction(generation, kind)

    def cancel(self, session_id: str) -> bool:
        runtime = self.get(session_id)
        return runtime.cancel(runtime.generation)

    async def save(
        self,
        session_id: str,
        *,
        model: str = "",
        system_prompt: str = "",
        expected_revision: int | None = None,
        generation: int | None = None,
    ) -> int:
        if self._backend is None:
            raise ValueError("session backend is not configured")
        runtime = self._sessions[session_id]
        write_generation = runtime.generation if generation is None else generation
        runtime.assert_generation(write_generation)
        payload = _snapshot_payload(runtime, model=model, system_prompt=system_prompt)
        revision = self._backend.save(
            session_id,
            payload,
            expected_revision=runtime.revision if expected_revision is None else expected_revision,
        )
        runtime.assert_generation(write_generation)
        runtime.revision = revision
        return revision

    async def resume(
        self,
        session_id: str,
        *,
        environment_ref: str | None = None,
    ) -> SessionRuntime:
        return await self.open_or_resume(
            session_id,
            environment_ref=environment_ref,
            resume=True,
        )

    def get(self, session_id: str) -> SessionRuntime:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"unknown session: {session_id}") from exc

    def _runtime_from_snapshot(self, snapshot: SessionSnapshot) -> SessionRuntime:
        session, agent_state, task_state = AgentSession.from_snapshot_dict(snapshot.payload)
        if task_state.snapshot is not None and task_state.snapshot.status is TaskStatus.PAUSED:
            task_state.update_status(TaskStatus.ACTIVE)
        runtime = SessionRuntime(
            session=session,
            agent_state=agent_state,
            task_state_store=task_state,
            environment_ref=snapshot.environment_ref,
            canonical_evidence_refs=tuple(
                _validated_evidence_refs(snapshot.payload.get("canonical_evidence_refs", ()))
            ),
            generation=snapshot.generation,
            revision=snapshot.revision,
        )
        return runtime


def _snapshot_payload(
    runtime: SessionRuntime,
    *,
    model: str,
    system_prompt: str,
) -> dict[str, Any]:
    payload = runtime.session.to_snapshot_dict(
        agent_state=runtime.agent_state,
        task_state_store=runtime.task_state_store,
        model=model,
        system_prompt=system_prompt,
    )
    payload.update(
        {
            "session_generation": runtime.generation,
            "environment_ref": runtime.environment_ref,
            "canonical_evidence_refs": list(runtime.canonical_evidence_refs),
            "session_status": runtime.agent_state.status,
        }
    )
    return payload


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        _fsync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _validate_session_id(session_id: str) -> None:
    if (
        not isinstance(session_id, str)
        or not session_id.strip()
        or session_id in {".", ".."}
        or "/" in session_id
        or "\\" in session_id
        or "\x00" in session_id
    ):
        raise ValueError("session_id must be a non-empty path-safe identifier")


def _validated_evidence_refs(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("canonical_evidence_refs must be a list of strings")
    refs = tuple(value)
    if any(not isinstance(item, str) or not item.strip() for item in refs):
        raise ValueError("canonical_evidence_refs must contain non-empty strings")
    if len(refs) != len(set(refs)):
        raise ValueError("canonical_evidence_refs must be unique")
    return refs


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "CancellationSource",
    "CompactionRequest",
    "SessionConflictError",
    "SessionBackend",
    "SessionError",
    "SessionFileBackend",
    "SessionGenerationError",
    "SessionManager",
    "SessionRuntime",
    "SessionSnapshot",
]
