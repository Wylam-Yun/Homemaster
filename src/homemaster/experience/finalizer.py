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
    """Resumable session finalizer scheduled by the interactive shell."""

    def __init__(
        self,
        *,
        trace_path: Path,
        data_root: Path,
        mindmemos: Any,
        dreaming_coordinator: Any | None = None,
        event_sink: Any | None = None,
    ) -> None:
        self._trace_path = trace_path
        self._jobs_root = data_root / "experience_jobs"
        self._mindmemos = mindmemos
        self._dreaming_coordinator = dreaming_coordinator
        self._event_sink = event_sink

    async def finalize(self, session_id: str, exit_reason: str) -> FinalizeResult:
        started = time.monotonic()
        job: dict[str, Any] | None = None
        job_path: Path | None = None
        current_phase: str | None = None
        context: Any | None = None
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
            job = self._read_json(job_path)
            if job and job.get("status") == "completed":
                return FinalizeResult(
                    session_id=session_id,
                    status="already_completed",
                    collected_events=len(events),
                    excluded_transport_deltas=excluded,
                    rendered_messages=len(messages),
                )
            job_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            if job is None:
                job = {
                    "schema_version": 2,
                    "job_id": job_id,
                    "status": "pending",
                    "session_id": session_id,
                    "input_hash": input_hash,
                    "extractor_version": _EXTRACTOR_VERSION,
                    "add": {"status": "pending"},
                    "implicit_feedback": {"status": "pending"},
                    "dreaming_counter": {"status": "pending"},
                    "dreaming": {"status": "not_due"},
                }
                self._write_json(job_path, job)
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
            if job["add"]["status"] != "completed":
                current_phase = "add"
                recorded = await self._mindmemos.add_vanilla(
                    messages,
                    context,
                    metadata={
                        "source_type": "homemaster_session_experience",
                        "source_session_id": session_id,
                        "input_hash": input_hash,
                        "extractor_version": _EXTRACTOR_VERSION,
                    },
                )
                add_record_id = getattr(recorded, "add_record_id", None)
                add_result = getattr(recorded, "result", None)
                if not add_record_id or add_result is None or add_result.status != "ok":
                    raise RuntimeError(
                        "MindMemOS vanilla add returned no successful record receipt"
                    )
                operations = tuple(
                    ExperienceOperation(
                        operation=str(item.operation),
                        memory_id=item.memory_id,
                        memory_type=item.mem_type,
                        content=str(item.content or ""),
                        related_memory_ids=tuple(item.related_memory_ids),
                    )
                    for item in add_result.memories
                )
                active_memory_ids = await self._verified_active_adds(operations, context)
                job["add"] = {
                    "status": "completed",
                    "add_record_id": add_record_id,
                    "operations": [asdict(item) for item in operations],
                    "active_memory_ids": list(active_memory_ids),
                }
                self._write_json(job_path, job)
            operations = tuple(
                ExperienceOperation(
                    operation=item["operation"],
                    memory_id=item.get("memory_id"),
                    memory_type=item.get("memory_type"),
                    content=item.get("content", ""),
                    related_memory_ids=tuple(item.get("related_memory_ids", ())),
                )
                for item in job["add"].get("operations", [])
            )
            if job["implicit_feedback"]["status"] != "completed":
                current_phase = "implicit_feedback"
                feedback_started = time.monotonic()
                await self._emit(
                    "memory.feedback.implicit.started",
                    session_id=session_id,
                    run_id=job_id,
                    payload={
                        "request_id": job_id,
                        "project_id": context.project_id,
                        "user_id": context.user_id,
                    },
                )
                implicit = await self._mindmemos.feedback_implicit(context)
                await self._verify_feedback_result(implicit, context)
                actions = [
                    action.model_dump(mode="json") for action in implicit.actions
                ]
                job["implicit_feedback"] = {
                    "status": "completed",
                    "actions": actions,
                }
                self._write_json(job_path, job)
                await self._emit(
                    "memory.feedback.implicit.completed",
                    session_id=session_id,
                    run_id=job_id,
                    payload={
                        "request_id": job_id,
                        "project_id": context.project_id,
                        "user_id": context.user_id,
                        "duration_ms": (time.monotonic() - feedback_started) * 1000,
                        "action_count": len(actions),
                        "actions": actions,
                    },
                )
            if job["dreaming_counter"]["status"] != "completed":
                current_phase = "dreaming_counter"
                if self._dreaming_coordinator is None:
                    dreaming_outcome = "not_due"
                else:
                    dreaming_outcome = await self._dreaming_coordinator.register_and_run(
                        context=context,
                        add_record_id=job["add"]["add_record_id"],
                        memory_ids=tuple(job["add"].get("active_memory_ids", ())),
                    )
                job["dreaming_counter"] = {"status": "completed"}
                job["dreaming"] = {"status": dreaming_outcome}
                self._write_json(job_path, job)
            elif (
                self._dreaming_coordinator is not None
                and job["dreaming"].get("status") == "failed"
            ):
                current_phase = "dreaming"
                outcome = await self._dreaming_coordinator.retry_pending(
                    project_id=context.project_id,
                    user_id=context.user_id,
                    context_template=context,
                )
                job["dreaming"] = {"status": outcome}
                self._write_json(job_path, job)
            if job["dreaming"].get("status") == "failed":
                current_phase = "dreaming"
                raise RuntimeError("dreaming remains pending after a failed attempt")
            job["status"] = "completed"
            job.pop("failed_phase", None)
            self._write_json(job_path, job)
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
            error = f"{type(exc).__name__}: {exc}"
            if job is not None and job_path is not None and current_phase is not None:
                job["status"] = "failed"
                job["failed_phase"] = current_phase
                phase = dict(job.get(current_phase, {}))
                phase.update({"status": "failed", "error": error})
                job[current_phase] = phase
                try:
                    self._write_json(job_path, job)
                except Exception:
                    logger.exception("failed to persist finalization failure receipt")
            if current_phase == "implicit_feedback" and context is not None:
                await self._emit(
                    "memory.feedback.implicit.failed",
                    session_id=session_id,
                    run_id=str(context.request_id),
                    payload={
                        "request_id": context.request_id,
                        "project_id": context.project_id,
                        "user_id": context.user_id,
                        "duration_ms": (time.monotonic() - started) * 1000,
                        "error": error,
                    },
                )
            logger.exception("session experience finalization failed")
            return FinalizeResult(
                session_id=session_id,
                status="failed",
                duration_ms=(time.monotonic() - started) * 1000,
                error=error,
            )

    async def _emit(
        self,
        event_type: str,
        *,
        session_id: str,
        run_id: str,
        payload: dict[str, Any],
    ) -> None:
        if self._event_sink is None:
            return
        from homemaster.events.runtime_events import RuntimeEvent

        event = RuntimeEvent(
            type=event_type,
            session_id=session_id,
            run_id=run_id,
            turn_index=None,
            payload=payload,
        )
        try:
            aemit = getattr(self._event_sink, "aemit", None)
            if callable(aemit):
                await aemit(event)
                return
            emitted = self._event_sink.emit(event)
            if hasattr(emitted, "__await__"):
                await emitted
        except Exception:
            return

    async def _verified_active_adds(
        self, operations: tuple[ExperienceOperation, ...], context: Any
    ) -> tuple[str, ...]:
        memory_ids: list[str] = []
        for operation in operations:
            if operation.operation != "add" or not operation.memory_id:
                continue
            raw = await self._mindmemos.get_raw(operation.memory_id, context)
            if raw is None or getattr(raw, "status", None) != "active":
                raise RuntimeError(
                    f"vanilla add raw memory verification failed: {operation.memory_id}"
                )
            memory_ids.append(operation.memory_id)
        return tuple(dict.fromkeys(memory_ids))

    async def _verify_feedback_result(self, result: Any, context: Any) -> None:
        if result.status != "ok":
            raise RuntimeError(result.message or "implicit feedback failed")
        for action in result.actions:
            if action.status != "ok":
                raise RuntimeError(f"implicit feedback action failed: {action.action}")
            if action.action == "noop":
                continue
            if action.action == "add":
                raw = await self._mindmemos.get_raw(action.result_memory_id, context)
                valid = raw is not None and getattr(raw, "status", None) == "active"
            elif action.action == "delete":
                raw = await self._mindmemos.get_raw(action.target_memory_id, context)
                valid = raw is not None and getattr(raw, "status", None) == "archived"
            elif action.action == "update":
                old = await self._mindmemos.get_raw(action.target_memory_id, context)
                new = await self._mindmemos.get_raw(action.result_memory_id, context)
                valid = bool(
                    old is not None
                    and getattr(old, "status", None) == "archived"
                    and new is not None
                    and getattr(new, "status", None) == "active"
                    and await self._mindmemos.has_memory_lineage(
                        source_memory_id=action.result_memory_id,
                        target_memory_id=action.target_memory_id,
                        relationship="DERIVED_FROM",
                        context=context,
                    )
                )
            else:
                valid = False
            if not valid:
                raise RuntimeError(
                    f"implicit feedback terminal verification failed: {action.action}"
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
            timestamp = _timestamp_millis(event.get("timestamp"))
            if event_type == "runtime.turn_started":
                text = payload.get("user_text")
                if isinstance(text, str) and text.strip():
                    messages.append(
                        DialogueMessage(
                            role="user",
                            content=text.strip(),
                            timestamp=timestamp,
                        )
                    )
            elif event_type == "assistant.thinking":
                text = payload.get("thinking")
                if isinstance(text, str) and text.strip():
                    messages.append(
                        DialogueMessage(
                            role="assistant",
                            content=f"[thinking]\n{text.strip()}",
                            timestamp=timestamp,
                        )
                    )
            elif event_type == "assistant.reply":
                text = payload.get("reply")
                if isinstance(text, str) and text.strip():
                    messages.append(
                        DialogueMessage(
                            role="assistant",
                            content=text.strip(),
                            timestamp=timestamp,
                        )
                    )
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
                messages.append(
                    DialogueMessage(
                        role="tool",
                        content="\n".join(parts),
                        timestamp=timestamp,
                    )
                )
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
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temp.unlink(missing_ok=True)


def _timestamp_millis(value: object) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return int(parsed.timestamp() * 1000)
