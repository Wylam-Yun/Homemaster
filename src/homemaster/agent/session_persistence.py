"""Session snapshot persistence helpers."""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from homemaster.agent.messages import AssistantMessage, Message
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.events.trace import json_compatible_copy
from homemaster.task_state.models import TaskStatus
from homemaster.task_state.store import TaskStateStore


@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    path: Path
    created_at: float
    saved_at: float
    status: str
    iteration_index: int
    total_tokens: int
    message_count: int


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically with fsync + replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def save_snapshot(
    *,
    session: AgentSession,
    agent_state: AgentState,
    task_state_store: TaskStateStore,
    path: Path,
    model: str,
    system_prompt: str,
    strip_images: bool = True,
) -> None:
    """Save a consistent session snapshot to disk."""

    original_messages = session.messages
    snapshot_messages = original_messages
    if (
        snapshot_messages
        and isinstance(snapshot_messages[-1], AssistantMessage)
        and snapshot_messages[-1].tool_calls
    ):
        snapshot_messages = snapshot_messages[:-1]
    if snapshot_messages is not original_messages:
        temporary = AgentSession(session.session_id)
        temporary.replace_messages(snapshot_messages)
        source = temporary
    else:
        source = session
    payload = source.to_snapshot_dict(
        agent_state=agent_state,
        task_state_store=task_state_store,
        model=model,
        system_prompt=system_prompt,
        strip_images=strip_images,
    )
    atomic_write_json(path, payload)


def load_session_json(path: Path) -> dict[str, Any]:
    """Load a session JSON payload from disk."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"invalid session JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"session JSON must be an object: {path}")
    return payload


def session_snapshot_path(session_root: Path, session_id: str) -> Path:
    """Return the snapshot path for a session id or direct session path."""

    expanded = session_root.expanduser()
    candidate = Path(session_id).expanduser()
    if candidate.is_file():
        return candidate
    if candidate.is_dir():
        return candidate / "session.json"
    return expanded / session_id / "session.json"


def find_latest_session_snapshot(session_root: Path) -> Path:
    """Return the newest session snapshot under session_root."""

    snapshots = list(session_root.expanduser().glob("*/session.json"))
    if not snapshots:
        raise FileNotFoundError(f"no sessions found under {session_root.expanduser()}")
    return max(snapshots, key=lambda path: path.stat().st_mtime)


def list_sessions(session_root: Path) -> list[SessionInfo]:
    """List readable session snapshots, newest first."""

    infos: list[SessionInfo] = []
    for snapshot_path in session_root.expanduser().glob("*/session.json"):
        try:
            payload = load_session_json(snapshot_path)
        except ValueError:
            continue
        info = _session_info_from_payload(snapshot_path, payload)
        infos.append(info)
    return sorted(infos, key=lambda item: item.saved_at, reverse=True)


def resume_session(path: Path) -> tuple[AgentSession, AgentState, TaskStateStore]:
    """Load session, agent state, and task state. Paused tasks become active."""

    session, agent_state, task_state_store = AgentSession.from_snapshot_dict(
        load_session_json(path)
    )
    snapshot = task_state_store.snapshot
    if snapshot is not None and snapshot.status == TaskStatus.PAUSED:
        task_state_store.update_status(TaskStatus.ACTIVE)
    return session, agent_state, task_state_store


def _session_info_from_payload(path: Path, payload: dict[str, Any]) -> SessionInfo:
    agent_state = payload.get("agent_state") if isinstance(payload.get("agent_state"), dict) else {}
    usage = (
        agent_state.get("provider_usage")
        if isinstance(agent_state.get("provider_usage"), dict)
        else {}
    )
    return SessionInfo(
        session_id=str(payload.get("session_id") or path.parent.name),
        path=path,
        created_at=float(payload.get("created_at") or 0),
        saved_at=float(payload.get("saved_at") or path.stat().st_mtime),
        status=str(agent_state.get("status") or "unknown"),
        iteration_index=int(agent_state.get("iteration_index") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
        message_count=len(payload.get("messages") or []),
    )


class SessionPersistenceManager:
    """Own the per-session trace, message log, and resumable snapshot files."""

    def __init__(
        self,
        *,
        session: AgentSession,
        agent_state: AgentState,
        task_state_store: TaskStateStore,
        session_root: Path,
        model: str,
        system_prompt: str,
        strip_images: bool = True,
        trace_rotation_max_mb: int = 100,
    ) -> None:
        self.session = session
        self.agent_state = agent_state
        self.task_state_store = task_state_store
        self.session_dir = session_root.expanduser() / session.session_id
        self.model = model
        self.system_prompt = system_prompt
        self.strip_images = strip_images
        self.trace_rotation_max_bytes = max(1, trace_rotation_max_mb) * 1024 * 1024
        self._events: list[RuntimeEvent] = []
        self.session_dir.mkdir(parents=True, exist_ok=True)
        for path in (self.trace_path, self.messages_path):
            path.touch(exist_ok=True)

    @property
    def trace_path(self) -> Path:
        return self.session_dir / "trace.jsonl"

    @property
    def messages_path(self) -> Path:
        return self.session_dir / "messages.jsonl"

    @property
    def snapshot_path(self) -> Path:
        return self.session_dir / "session.json"

    @property
    def events(self) -> list[RuntimeEvent]:
        return list(self._events)

    def emit(self, event: RuntimeEvent) -> None:
        """Append a full debug event payload without rewriting values."""

        self._events.append(event)
        self._rotate_trace_if_needed()
        entry = asdict(event)
        entry["payload"] = json_compatible_copy(event.payload)
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def append_message(self, message: Message) -> None:
        """Append user-visible dialogue to messages.jsonl."""

        if message.role == "user":
            content = _message_text(message)
        elif message.role == "assistant" and message.text:
            content = message.text
        else:
            return
        entry = {
            "ts": time.time(),
            "role": message.role,
            "content": content,
        }
        with self.messages_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def save_snapshot(self) -> None:
        save_snapshot(
            session=self.session,
            agent_state=self.agent_state,
            task_state_store=self.task_state_store,
            path=self.snapshot_path,
            model=self.model,
            system_prompt=self.system_prompt,
            strip_images=self.strip_images,
        )

    def _rotate_trace_if_needed(self) -> None:
        if not self.trace_path.exists():
            return
        if self.trace_path.stat().st_size < self.trace_rotation_max_bytes:
            return
        index = 1
        while (self.session_dir / f"trace.{index}.jsonl").exists():
            index += 1
        os.replace(self.trace_path, self.session_dir / f"trace.{index}.jsonl")


def _message_text(message: Message) -> str:
    return "\n".join(block.text for block in message.content if block.text)
