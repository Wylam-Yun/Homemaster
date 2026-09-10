"""Durable, application-owned ALFWorld compile jobs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from homemaster.benchmarking.alfworld.trajectory_memory import AlfworldTrajectoryRecord
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.experience.alfworld_compiler import COMPILER_VERSION, compile_alfworld_trajectory
from homemaster.memory.automatic_recall import build_mindmemos_request_context


class AlfworldCompileJobService:
    def __init__(
        self,
        mindmemos: Any,
        queue: Any,
        *,
        jobs_root: Path,
        event_sink: Any,
        tenant_id: str = "local",
    ) -> None:
        self._mindmemos = mindmemos
        self._queue = queue
        self._jobs_root = jobs_root
        self._event_sink = event_sink
        self._tenant_id = tenant_id

    def enqueue(
        self, memory_id: str, *, session_id: str = "web-memory-management"
    ) -> dict[str, str]:
        memory_id = memory_id.strip()
        if not memory_id:
            raise ValueError("memory_id must not be empty")
        existing = self._find_existing(memory_id)
        if existing is not None:
            return {"job_id": str(existing["job_id"]), "status": str(existing["status"])}
        job_id = str(uuid4())
        job = {
            "schema_version": 1,
            "job_id": job_id,
            "status": "queued",
            "memory_id": memory_id,
            "session_id": session_id,
            "tenant_id": self._tenant_id,
            "compiler_version": COMPILER_VERSION,
        }
        self._write(job_id, job)
        self._emit("memory.experience.compile.queued", job)

        async def work() -> None:
            await self._run(job_id)

        self._queue.enqueue_work(
            job_type="alfworld_compile",
            session_id=session_id,
            work=work,
        )
        return {"job_id": job_id, "status": "queued"}

    async def aenqueue(
        self, memory_id: str, *, session_id: str = "web-memory-management"
    ) -> dict[str, str]:
        """Async variant safe to call from the owner event loop (queue worker)."""

        cleaned = memory_id.strip()
        if not cleaned:
            raise ValueError("memory_id must not be empty")
        existing = self._find_existing(cleaned)
        if existing is not None:
            return {"job_id": str(existing["job_id"]), "status": str(existing["status"])}
        job_id = str(uuid4())
        job = {
            "schema_version": 1,
            "job_id": job_id,
            "status": "queued",
            "memory_id": cleaned,
            "session_id": session_id,
            "tenant_id": self._tenant_id,
            "compiler_version": COMPILER_VERSION,
        }
        self._write(job_id, job)
        await self._aemit("memory.experience.compile.queued", job)

        async def work() -> None:
            await self._run(job_id)

        self._queue.enqueue_work(
            job_type="alfworld_compile",
            session_id=session_id,
            work=work,
        )
        return {"job_id": job_id, "status": "queued"}

    def _find_existing(self, memory_id: str) -> dict[str, Any] | None:
        """Reuse a receipt for this immutable source/compiler pair."""
        if not self._jobs_root.is_dir():
            return None
        matches: list[dict[str, Any]] = []
        for path in self._jobs_root.glob("*.json"):
            try:
                candidate = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                candidate.get("memory_id") == memory_id
                and candidate.get("compiler_version") == COMPILER_VERSION
                and isinstance(candidate.get("job_id"), str)
                and isinstance(candidate.get("status"), str)
            ):
                matches.append(candidate)
        if not matches:
            return None
        return max(matches, key=lambda item: str(item.get("job_id", "")))

    def get(self, job_id: str) -> dict[str, Any] | None:
        path = self._path(job_id.strip())
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    async def _run(self, job_id: str) -> None:
        job = self.get(job_id)
        if job is None:
            raise RuntimeError("compile job receipt is missing")
        job["status"] = "running"
        self._write(job_id, job)
        await self._aemit("memory.experience.compile.started", job)
        try:
            context = build_mindmemos_request_context(
                request_id=f"compile-{job_id}",
                tenant_id=self._tenant_id,
                session_id=str(job["session_id"]),
            )
            raw = await self._mindmemos.get_raw(str(job["memory_id"]), context)
            record = self._record_from_raw(raw)
            observed_hash = _trace_hash(Path(record.source_trace_path))
            derived = compile_alfworld_trajectory(record, source_trace_sha256=observed_hash)
            content = derived.model_dump_json()
            stored = await self._mindmemos.add_derived_experience(
                content,
                source_memory_id=str(job["memory_id"]),
                metadata={
                    "record_kind": derived.record_kind,
                    "outcome": derived.outcome,
                    "is_executable": derived.is_executable,
                    "source_trajectory_id": derived.source_trajectory_id,
                    "source_trace_sha256": derived.source_trace_sha256,
                    "compiler_version": derived.compiler_version,
                    "validation_status": derived.validation_status,
                    "record_json": content,
                },
                context=context,
            )
            job.update(
                {
                    "status": "succeeded",
                    "derived_memory_id": stored.get("memory_id"),
                    "outcome": derived.outcome,
                    "is_executable": derived.is_executable,
                    "source_trace_sha256": derived.source_trace_sha256,
                    "readback_verified": stored.get("readback_verified", True),
                }
            )
            self._write(job_id, job)
            await self._aemit("memory.experience.compile.completed", job)
        except Exception as exc:
            job.update({"status": "failed", "error_code": type(exc).__name__, "error": str(exc)})
            self._write(job_id, job)
            await self._aemit("memory.experience.compile.failed", job)
            raise

    @staticmethod
    def _record_from_raw(raw: Any) -> AlfworldTrajectoryRecord:
        if raw is None or getattr(raw, "status", None) != "active":
            raise ValueError("trajectory source memory is not active")
        metadata = getattr(raw, "metadata", {}) or {}
        if metadata.get("homemaster_memory_type") != "trajectory":
            raise ValueError("memory is not an ALFWorld trajectory")
        record_json = metadata.get("record_json")
        if not isinstance(record_json, str):
            raise ValueError("trajectory source record is missing record_json")
        return AlfworldTrajectoryRecord.model_validate_json(record_json)

    def _path(self, job_id: str) -> Path:
        if not job_id or "/" in job_id or "\\" in job_id:
            raise ValueError("invalid compile job id")
        return self._jobs_root / f"{job_id}.json"

    def _write(self, job_id: str, payload: dict[str, Any]) -> None:
        self._jobs_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._path(job_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def _event(self, event_type: str, job: dict[str, Any]) -> Any:
        return RuntimeEvent(
            type=event_type,
            session_id=str(job.get("session_id", "")),
            run_id=str(job.get("job_id", "")),
            turn_index=None,
            payload={
                "job_id": job.get("job_id"),
                "memory_id": job.get("memory_id"),
                "derived_memory_id": job.get("derived_memory_id"),
                "status": job.get("status"),
                "outcome": job.get("outcome"),
                "is_executable": job.get("is_executable"),
                "compiler_version": job.get("compiler_version"),
                "source_trace_sha256": job.get("source_trace_sha256"),
                "readback_status": "verified" if job.get("readback_verified") else None,
                "error_code": job.get("error_code"),
            },
        )

    def _emit(self, event_type: str, job: dict[str, Any]) -> None:
        emit = getattr(self._event_sink, "emit", None)
        if callable(emit):
            emit(self._event(event_type, job))

    async def _aemit(self, event_type: str, job: dict[str, Any]) -> None:
        aemit = getattr(self._event_sink, "aemit", None)
        if callable(aemit):
            await aemit(self._event(event_type, job))
            return
        emit = getattr(self._event_sink, "emit", None)
        if callable(emit):
            emit(self._event(event_type, job))


def _trace_hash(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"source trace is missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = ["AlfworldCompileJobService"]
