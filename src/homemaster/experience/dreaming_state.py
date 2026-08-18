"""Persistent, scope-locked batches for recoverable MindMemOS dreaming."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DreamingBatch:
    batch_id: str
    project_id: str
    user_id: str
    add_record_ids: tuple[str, ...]
    memory_ids: tuple[str, ...]


class DreamingStateStore:
    def __init__(self, data_root: Path, *, threshold: int = 8) -> None:
        if threshold < 1:
            raise ValueError("dreaming threshold must be positive")
        self._root = data_root / "mindmemos" / "dreaming_state"
        self._threshold = threshold

    @property
    def threshold(self) -> int:
        return self._threshold

    def register(
        self,
        *,
        project_id: str,
        user_id: str,
        add_record_id: str,
        memory_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        ids = tuple(dict.fromkeys(memory_id for memory_id in memory_ids if memory_id))
        if not add_record_id or not ids:
            return self.read(project_id=project_id, user_id=user_id)
        with self._locked(project_id, user_id) as (state, path):
            records = state["pending_add_records"]
            if not any(item["add_record_id"] == add_record_id for item in records):
                records.append(
                    {
                        "add_record_id": add_record_id,
                        "memory_ids": list(ids),
                        "confirmed_at": _now(),
                    }
                )
            state["new_active_memory_count"] = _memory_count(records)
            if state["new_active_memory_count"] >= self._threshold:
                state["pending"] = True
            self._write(path, state)
            return state

    def claim(self, *, project_id: str, user_id: str) -> DreamingBatch | None:
        with self._locked(project_id, user_id) as (state, path):
            inflight = state.get("inflight")
            if inflight is not None:
                owner_pid = int(inflight.get("owner_pid", 0) or 0)
                if owner_pid and _process_is_alive(owner_pid):
                    return None
                state["pending_add_records"] = _merge_records(
                    inflight.get("records", []), state["pending_add_records"]
                )
                state["inflight"] = None
                state["pending"] = True
            if not state["pending"]:
                return None
            records = list(state["pending_add_records"])
            if not records:
                return None
            inflight = {
                "batch_id": uuid.uuid4().hex,
                "owner_pid": os.getpid(),
                "claimed_at": _now(),
                "records": records,
            }
            state["inflight"] = inflight
            state["pending_add_records"] = []
            state["new_active_memory_count"] = 0
            state["last_attempt_at"] = _now()
            self._write(path, state)
            return _batch_from_inflight(project_id, user_id, inflight)

    def complete(self, batch: DreamingBatch, *, outcome: str) -> None:
        with self._locked(batch.project_id, batch.user_id) as (state, path):
            self._require_batch(state, batch)
            state["inflight"] = None
            state["pending"] = state["new_active_memory_count"] >= self._threshold
            state["last_successful_watermark"] = {
                "batch_id": batch.batch_id,
                "add_record_ids": list(batch.add_record_ids),
                "outcome": outcome,
            }
            state["last_success_at"] = _now()
            state["last_error"] = None
            self._write(path, state)

    def fail(self, batch: DreamingBatch, *, error: str) -> None:
        with self._locked(batch.project_id, batch.user_id) as (state, path):
            self._require_batch(state, batch)
            inflight = state["inflight"]
            state["pending_add_records"] = _merge_records(
                inflight["records"], state["pending_add_records"]
            )
            state["new_active_memory_count"] = _memory_count(
                state["pending_add_records"]
            )
            state["inflight"] = None
            state["pending"] = True
            state["last_error"] = error
            self._write(path, state)

    def read(self, *, project_id: str, user_id: str) -> dict[str, Any]:
        with self._locked(project_id, user_id) as (state, _path):
            return json.loads(json.dumps(state))

    @contextmanager
    def _locked(
        self, project_id: str, user_id: str
    ) -> Iterator[tuple[dict[str, Any], Path]]:
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = hashlib.sha256(f"{project_id}\0{user_id}".encode()).hexdigest()
        path = self._root / f"{key}.json"
        lock_path = self._root / f"{key}.lock"
        with lock_path.open("a+", encoding="utf-8") as lock:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                state = self._load(path, project_id, user_id)
                yield state, path
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _load(self, path: Path, project_id: str, user_id: str) -> dict[str, Any]:
        if not path.exists():
            return _new_state(project_id, user_id)
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"dreaming state is unreadable: {path}") from exc
        if state.get("schema_version") != 1 or state.get("scope") != {
            "project_id": project_id,
            "user_id": user_id,
        }:
            raise RuntimeError(f"dreaming state scope/schema mismatch: {path}")
        return state

    @staticmethod
    def _write(path: Path, state: dict[str, Any]) -> None:
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _require_batch(state: dict[str, Any], batch: DreamingBatch) -> None:
        inflight = state.get("inflight")
        if inflight is None or inflight.get("batch_id") != batch.batch_id:
            raise RuntimeError("dreaming batch is no longer authoritative")


class DreamingCoordinator:
    def __init__(
        self,
        *,
        store: DreamingStateStore,
        mindmemos: Any,
        timeout_seconds: float = 300.0,
        event_sink: Any | None = None,
    ) -> None:
        self._store = store
        self._mindmemos = mindmemos
        self._timeout_seconds = timeout_seconds
        self._event_sink = event_sink

    async def register_and_run(
        self,
        *,
        context: Any,
        add_record_id: str,
        memory_ids: tuple[str, ...],
    ) -> str:
        before = self._store.read(
            project_id=context.project_id,
            user_id=context.user_id,
        )
        state = self._store.register(
            project_id=context.project_id,
            user_id=context.user_id,
            add_record_id=add_record_id,
            memory_ids=memory_ids,
        )
        if not before["pending"] and state["pending"]:
            await self._emit(
                "memory.dreaming.threshold_reached",
                context,
                {
                    "request_id": context.request_id,
                    "project_id": context.project_id,
                    "user_id": context.user_id,
                    "threshold": self._store.threshold,
                    "count": state["new_active_memory_count"],
                    "watermark": state["last_successful_watermark"],
                },
            )
        return await self.retry_pending(
            project_id=context.project_id,
            user_id=context.user_id,
            context_template=context,
        )

    async def retry_pending(
        self,
        *,
        project_id: str,
        user_id: str,
        context_template: Any,
    ) -> str:
        batch = self._store.claim(project_id=project_id, user_id=user_id)
        if batch is None:
            return "not_due"
        from mindmemos.typing import MemoryRequestContext

        context = MemoryRequestContext(
            request_id=f"dreaming:{batch.batch_id}",
            account_id=context_template.account_id,
            project_id=project_id,
            api_key_uuid=context_template.api_key_uuid,
            user_id=user_id,
            app_id=context_template.app_id,
            session_id=None,
            agent_id=context_template.agent_id,
        )
        started = time.monotonic()
        await self._emit(
            "memory.dreaming.started",
            context_template,
            {
                "request_id": context.request_id,
                "project_id": project_id,
                "user_id": user_id,
                "batch_id": batch.batch_id,
                "threshold": self._store.threshold,
                "count": len(batch.memory_ids),
                "add_record_ids": list(batch.add_record_ids),
                "memory_ids": list(batch.memory_ids),
            },
        )
        try:
            async with asyncio.timeout(self._timeout_seconds):
                result = await self._mindmemos.dream(
                    seed_add_record_ids=list(batch.add_record_ids),
                    context=context,
                )
                await self._verify_result(result, batch, context)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._store.fail(batch, error=error)
            await self._emit(
                "memory.dreaming.failed",
                context_template,
                {
                    "request_id": context.request_id,
                    "project_id": project_id,
                    "user_id": user_id,
                    "batch_id": batch.batch_id,
                    "duration_ms": (time.monotonic() - started) * 1000,
                    "error": error,
                },
            )
            return "failed"
        self._store.complete(batch, outcome=result.outcome)
        actions = [_action_payload(action) for action in result.actions]
        await self._emit(
            (
                "memory.dreaming.no_action"
                if result.outcome == "no_action"
                else "memory.dreaming.completed"
            ),
            context_template,
            {
                "request_id": context.request_id,
                "project_id": project_id,
                "user_id": user_id,
                "batch_id": batch.batch_id,
                "duration_ms": (time.monotonic() - started) * 1000,
                "action_count": len(actions),
                "actions": actions,
                "add_record_ids": list(batch.add_record_ids),
            },
        )
        return result.outcome

    async def _emit(
        self, event_type: str, context: Any, payload: dict[str, Any]
    ) -> None:
        if self._event_sink is None:
            return
        from homemaster.events.runtime_events import RuntimeEvent

        event = RuntimeEvent(
            type=event_type,
            session_id=str(context.session_id or ""),
            run_id=str(context.request_id),
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

    async def _verify_result(
        self, result: Any, batch: DreamingBatch, context: Any
    ) -> None:
        if result.status != "ok" or result.outcome not in {"actions", "no_action"}:
            raise RuntimeError(result.message or "dreaming pipeline failed")
        if result.errors or any(action.status != "ok" for action in result.actions):
            raise RuntimeError("dreaming returned failed actions")
        for action in result.actions:
            await self._verify_action(action, context)
        records = await self._mindmemos.get_add_records(
            list(batch.add_record_ids), context
        )
        by_id = {record.point_id: record for record in records}
        for add_record_id in batch.add_record_ids:
            record = by_id.get(add_record_id)
            if (
                record is None
                or (record.payload or {}).get("consolidation_status") != "done"
            ):
                raise RuntimeError(
                    f"add record was not consolidated: {add_record_id}"
                )

    async def _verify_action(self, action: Any, context: Any) -> None:
        targets = [
            await self._mindmemos.get_raw(memory_id, context)
            for memory_id in action.target_memory_ids
        ]
        results = [
            await self._mindmemos.get_raw(memory_id, context)
            for memory_id in action.result_memory_ids
        ]
        if action.action == "create":
            valid = bool(results) and all(_status(item) == "active" for item in results)
        elif action.action == "update":
            valid = bool(targets) and all(_status(item) == "active" for item in targets)
        elif action.action == "merge":
            valid = (
                bool(targets)
                and all(_status(item) == "archived" for item in targets)
                and bool(results)
                and all(_status(item) == "active" for item in results)
            )
        elif action.action == "archive":
            valid = bool(targets) and all(
                _status(item) == "archived" for item in targets
            )
            if action.result_memory_ids:
                valid = valid and all(_status(item) == "active" for item in results)
        elif action.action == "link":
            valid = len(action.target_memory_ids) == 2 and bool(action.relationship)
            if valid:
                valid = await self._mindmemos.has_memory_lineage(
                    source_memory_id=action.target_memory_ids[0],
                    target_memory_id=action.target_memory_ids[1],
                    relationship=action.relationship,
                    context=context,
                )
        else:
            valid = False
        if not valid:
            raise RuntimeError(f"dreaming action terminal state failed: {action.action}")


def _new_state(project_id: str, user_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "scope": {"project_id": project_id, "user_id": user_id},
        "new_active_memory_count": 0,
        "pending": False,
        "pending_add_records": [],
        "inflight": None,
        "last_successful_watermark": None,
        "last_attempt_at": None,
        "last_success_at": None,
        "last_error": None,
    }


def _action_payload(action: Any) -> dict[str, Any]:
    dumper = getattr(action, "model_dump", None)
    if callable(dumper):
        return dumper(mode="json")
    return {
        "action": getattr(action, "action", None),
        "status": getattr(action, "status", None),
        "target_memory_ids": list(getattr(action, "target_memory_ids", ())),
        "result_memory_ids": list(getattr(action, "result_memory_ids", ())),
    }


def _merge_records(
    first: list[dict[str, Any]], second: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for record in [*first, *second]:
        merged.setdefault(record["add_record_id"], record)
    return list(merged.values())


def _memory_count(records: list[dict[str, Any]]) -> int:
    return len(
        {
            memory_id
            for record in records
            for memory_id in record.get("memory_ids", [])
            if memory_id
        }
    )


def _batch_from_inflight(
    project_id: str, user_id: str, inflight: dict[str, Any]
) -> DreamingBatch:
    records = inflight["records"]
    return DreamingBatch(
        batch_id=inflight["batch_id"],
        project_id=project_id,
        user_id=user_id,
        add_record_ids=tuple(record["add_record_id"] for record in records),
        memory_ids=tuple(
            dict.fromkeys(
                memory_id
                for record in records
                for memory_id in record.get("memory_ids", [])
            )
        ),
    )


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _status(memory: Any) -> str | None:
    return None if memory is None else getattr(memory, "status", None)


__all__ = ["DreamingBatch", "DreamingCoordinator", "DreamingStateStore"]
