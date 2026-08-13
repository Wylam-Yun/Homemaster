"""Build one task-trace snapshot and submit it to MindMemOS Vanilla Add."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("homemaster.experience")

_EXTRACTOR_VERSION = "experience-v2"


@dataclass(frozen=True)
class TaskTraceEnvelope:
    session_id: str
    started_at: str | None
    ended_at: str
    exit_reason: str
    events: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ExperienceOperation:
    operation: str
    memory_id: str | None
    memory_type: str | None
    content: str
    related_memory_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class FinalizeResult:
    session_id: str
    status: str
    collected_events: int = 0
    excluded_transport_deltas: int = 0
    rendered_messages: int = 0
    duration_ms: float = 0.0
    operations: tuple[ExperienceOperation, ...] = ()
    error: str | None = None


class SessionFinalizer:
    """Lightweight, synchronous session finalizer owned by the interactive shell."""

    def __init__(self, *, trace_path: Path, data_root: Path, mindmemos: Any) -> None:
        self._trace_path = trace_path
        self._jobs_root = data_root / "experience_jobs"
        self._mindmemos = mindmemos

    async def finalize(self, session_id: str, exit_reason: str) -> FinalizeResult:
        started = time.monotonic()
        try:
            events, excluded = self._collect(session_id)
            if not events:
                return FinalizeResult(session_id=session_id, status="empty")
            envelope = self._build_envelope(session_id, exit_reason, events)
            messages = self._render_messages(envelope)
            rendered = [
                {"role": message.role, "content": message.content}
                for message in messages
            ]
            input_bytes = json.dumps(
                rendered, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            input_hash = "sha256:" + hashlib.sha256(input_bytes).hexdigest()
            job_id = hashlib.sha256(
                f"{session_id}\0{input_hash}\0{_EXTRACTOR_VERSION}".encode()
            ).hexdigest()
            job_dir = self._jobs_root / job_id
            job_path = job_dir / "job.json"
            completed = self._read_json(job_path)
            if completed and completed.get("status") == "completed":
                return FinalizeResult(
                    session_id=session_id,
                    status="already_completed",
                    collected_events=len(events),
                    excluded_transport_deltas=excluded,
                    rendered_messages=len(messages),
                )
            job_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._write_json(
                job_path,
                {
                    "job_id": job_id,
                    "status": "pending",
                    "session_id": session_id,
                    "input_hash": input_hash,
                    "extractor_version": _EXTRACTOR_VERSION,
                },
            )
            from mindmemos.typing import MemoryRequestContext

            context = MemoryRequestContext(
                request_id=job_id,
                account_id="local",
                project_id="local",
                api_key_uuid="embedded-local",
                user_id="local",
                app_id="homemaster",
                session_id=session_id,
                agent_id="homemaster",
            )
            result = await self._mindmemos.add_vanilla(
                messages,
                context,
                metadata={
                    "source_type": "homemaster_session_experience",
                    "source_session_id": session_id,
                    "input_hash": input_hash,
                    "extractor_version": _EXTRACTOR_VERSION,
                },
            )
            operations = tuple(
                ExperienceOperation(
                    operation=str(item.operation),
                    memory_id=item.memory_id,
                    memory_type=item.mem_type,
                    content=str(item.content or ""),
                    related_memory_ids=tuple(item.related_memory_ids),
                )
                for item in result.memories
            )
            self._write_json(
                job_path,
                {
                    "job_id": job_id,
                    "status": "completed",
                    "session_id": session_id,
                    "input_hash": input_hash,
                    "extractor_version": _EXTRACTOR_VERSION,
                    "operations": [asdict(item) for item in operations],
                },
            )
            return FinalizeResult(
                session_id=session_id,
                status="completed",
                collected_events=len(events),
                excluded_transport_deltas=excluded,
                rendered_messages=len(messages),
                duration_ms=(time.monotonic() - started) * 1000,
                operations=operations,
            )
        except Exception as exc:
            logger.exception("session experience finalization failed")
            return FinalizeResult(
                session_id=session_id,
                status="failed",
                duration_ms=(time.monotonic() - started) * 1000,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _collect(self, session_id: str) -> tuple[list[dict[str, Any]], int]:
        events: list[dict[str, Any]] = []
        excluded = 0
        if not self._trace_path.exists():
            return events, excluded
        with self._trace_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("session_id") != session_id:
                    continue
                if event.get("type") == "transport.delta":
                    excluded += 1
                    continue
                events.append(event)
        return events, excluded

    @staticmethod
    def _build_envelope(
        session_id: str, exit_reason: str, events: list[dict[str, Any]]
    ) -> TaskTraceEnvelope:
        return TaskTraceEnvelope(
            session_id=session_id,
            started_at=events[0].get("timestamp"),
            ended_at=datetime.now(UTC).isoformat(),
            exit_reason=exit_reason,
            events=tuple(events),
        )

    @staticmethod
    def _render_messages(envelope: TaskTraceEnvelope) -> list[Any]:
        from mindmemos.typing import DialogueMessage

        messages: list[Any] = []
        for event in envelope.events:
            event_type = event.get("type")
            payload = event.get("payload") or {}
            if event_type == "runtime.turn_started":
                text = payload.get("user_text")
                if isinstance(text, str) and text.strip():
                    messages.append(DialogueMessage(role="user", content=text.strip()))
            elif event_type == "assistant.thinking":
                text = payload.get("thinking")
                if isinstance(text, str) and text.strip():
                    messages.append(
                        DialogueMessage(role="assistant", content=f"[thinking]\n{text.strip()}")
                    )
            elif event_type == "assistant.reply":
                text = payload.get("reply")
                if isinstance(text, str) and text.strip():
                    messages.append(DialogueMessage(role="assistant", content=text.strip()))
            elif event_type in {"tool.call_completed", "tool.call_failed"}:
                name = event.get("name") or "tool"
                status = "failed" if event_type == "tool.call_failed" else "success"
                arguments = payload.get("args")
                result = payload.get("result")
                parts = [f"tool: {name}", f"status: {status}"]
                if arguments not in (None, {}, ""):
                    parts.append(
                        "arguments: "
                        + json.dumps(arguments, ensure_ascii=False, sort_keys=True)
                    )
                if result not in (None, ""):
                    parts.append(f"result:\n{result}")
                messages.append(DialogueMessage(role="tool", content="\n".join(parts)))
        messages.append(
            DialogueMessage(role="system", content=f"Session ended: {envelope.exit_reason}")
        )
        return messages

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
