"""HomeMaster-owned background task process lifecycle."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal
from uuid import uuid4

log = logging.getLogger(__name__)

TaskType = Literal["local_bash", "local_agent", "remote_agent", "in_process_teammate", "dream"]
TaskStatus = Literal["pending", "running", "completed", "failed", "killed"]
CompletionListener = Callable[["TaskRecord"], Awaitable[None] | None]


@dataclass
class TaskRecord:
    id: str
    type: TaskType
    status: TaskStatus
    description: str
    cwd: str
    output_file: Path
    command: str | None = None
    prompt: str | None = None
    created_at: float = 0.0
    started_at: float | None = None
    ended_at: float | None = None
    return_code: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] | None = None
    argv: list[str] | None = None


class BackgroundTaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._waiters: dict[str, asyncio.Task[None]] = {}
        self._output_locks: dict[str, asyncio.Lock] = {}
        self._input_locks: dict[str, asyncio.Lock] = {}
        self._generations: dict[str, int] = {}
        self._completion_listeners: dict[str, CompletionListener] = {}

    async def create_shell_task(
        self,
        *,
        command: str | None = None,
        description: str,
        cwd: str | Path,
        task_type: TaskType = "local_bash",
        env: dict[str, str] | None = None,
        argv: list[str] | None = None,
    ) -> TaskRecord:
        if (command is None) == (argv is None):
            raise ValueError("create_shell_task requires exactly one of command or argv")
        task_id_value = task_id(task_type)
        output_path = Path(cwd).resolve() / f".{task_id_value}.log"
        record = TaskRecord(
            id=task_id_value,
            type=task_type,
            status="running",
            description=description,
            cwd=str(Path(cwd).resolve()),
            output_file=output_path,
            command=command,
            created_at=time.time(),
            started_at=time.time(),
            env=dict(env) if env is not None else None,
            argv=list(argv) if argv is not None else None,
        )
        output_path.write_text("", encoding="utf-8")
        self._tasks[task_id_value] = record
        self._output_locks[task_id_value] = asyncio.Lock()
        self._input_locks[task_id_value] = asyncio.Lock()
        await self._start_process(task_id_value)
        return record

    async def create_agent_task(
        self,
        *,
        prompt: str,
        description: str,
        cwd: str | Path,
        task_type: TaskType = "local_agent",
        model: str | None = None,
        api_key: str | None = None,
        command: str | None = None,
        env: dict[str, str] | None = None,
        argv: list[str] | None = None,
    ) -> TaskRecord:
        del api_key
        if command is None and argv is None:
            argv = ["python", "-m", "homemaster.cli.child_worker"]
            if model:
                argv.extend(("--model", model))
        record = await self.create_shell_task(
            command=command,
            description=description,
            cwd=cwd,
            task_type=task_type,
            env=env,
            argv=argv,
        )
        record.prompt = prompt
        if task_type != "local_agent":
            record.metadata["agent_mode"] = task_type
        await self.write_to_task(record.id, prompt)
        return record

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    def list_tasks(self, *, status: TaskStatus | None = None) -> list[TaskRecord]:
        tasks = list(self._tasks.values())
        if status is not None:
            tasks = [item for item in tasks if item.status == status]
        return sorted(tasks, key=lambda item: item.created_at, reverse=True)

    def update_task(
        self,
        task_id: str,
        *,
        description: str | None = None,
        progress: int | None = None,
        status_note: str | None = None,
    ) -> TaskRecord:
        task = self._require_task(task_id)
        if description is not None and description.strip():
            task.description = description.strip()
        if progress is not None:
            task.metadata["progress"] = str(progress)
        if status_note is not None:
            note = status_note.strip()
            if note:
                task.metadata["status_note"] = note
            else:
                task.metadata.pop("status_note", None)
        return task

    async def stop_task(self, task_id: str) -> TaskRecord:
        task = self._require_task(task_id)
        process = self._processes.get(task_id)
        if process is None:
            if task.status in {"completed", "failed", "killed"}:
                return task
            raise ValueError(f"Task {task_id} is not running")
        task.status = "killed"
        await _terminate_process(process)
        waiter = self._waiters.get(task_id)
        if waiter is not None:
            await waiter
        return task

    async def write_to_task(self, task_id: str, data: str) -> None:
        task = self._require_task(task_id)
        payload = _encode_worker_payload(data)
        async with self._input_locks[task_id]:
            process = await self._ensure_writable_process(task)
            assert process.stdin is not None
            process.stdin.write(payload)
            try:
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                process = await self._restart_agent_task(task)
                assert process.stdin is not None
                process.stdin.write(payload)
                await process.stdin.drain()

    def read_task_output(self, task_id: str, *, max_bytes: int = 12000) -> str:
        content = self._require_task(task_id).output_file.read_text(
            encoding="utf-8", errors="replace"
        )
        return content[-max_bytes:] if len(content) > max_bytes else content

    def register_completion_listener(self, listener: CompletionListener) -> Callable[[], None]:
        listener_id = uuid4().hex
        self._completion_listeners[listener_id] = listener

        def unregister() -> None:
            self._completion_listeners.pop(listener_id, None)

        return unregister

    async def _start_process(self, task_id: str) -> asyncio.subprocess.Process:
        task = self._require_task(task_id)
        generation = self._generations.get(task_id, 0) + 1
        self._generations[task_id] = generation
        env = {**os.environ, **task.env} if task.env else None
        kwargs = {
            "cwd": task.cwd,
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.STDOUT,
            "env": env,
        }
        if os.name == "posix":
            kwargs["start_new_session"] = True
        if task.argv is not None:
            process = await asyncio.create_subprocess_exec(*task.argv, **kwargs)
        else:
            assert task.command is not None
            process = await asyncio.create_subprocess_shell(task.command, **kwargs)
        self._processes[task_id] = process
        self._waiters[task_id] = asyncio.create_task(
            self._watch_process(task_id, process, generation)
        )
        return process

    async def _watch_process(
        self,
        task_id: str,
        process: asyncio.subprocess.Process,
        generation: int,
    ) -> None:
        reader = asyncio.create_task(self._copy_output(task_id, process))
        return_code = await process.wait()
        await reader
        await _close_stdin(process)
        if self._generations.get(task_id) != generation:
            return
        task = self._tasks[task_id]
        task.return_code = return_code
        if task.status != "killed":
            task.status = "completed" if return_code == 0 else "failed"
        task.ended_at = time.time()
        await self._notify_completion_listeners(task)
        self._processes.pop(task_id, None)
        self._waiters.pop(task_id, None)

    async def _copy_output(self, task_id: str, process: asyncio.subprocess.Process) -> None:
        if process.stdout is None:
            return
        while chunk := await process.stdout.read(4096):
            async with self._output_locks[task_id]:
                with self._tasks[task_id].output_file.open("ab") as handle:
                    handle.write(chunk)

    async def _ensure_writable_process(self, task: TaskRecord) -> asyncio.subprocess.Process:
        process = self._processes.get(task.id)
        if process is not None and process.stdin is not None and process.returncode is None:
            return process
        if task.type not in {"local_agent", "remote_agent", "in_process_teammate"}:
            raise ValueError(f"Task {task.id} does not accept input")
        return await self._restart_agent_task(task)

    async def _restart_agent_task(self, task: TaskRecord) -> asyncio.subprocess.Process:
        waiter = self._waiters.get(task.id)
        if waiter is not None and not waiter.done():
            await waiter
        restart_count = int(task.metadata.get("restart_count", "0")) + 1
        task.metadata["restart_count"] = str(restart_count)
        task.metadata["status_note"] = (
            "Task restarted; prior interactive context was not preserved."
        )
        task.status = "running"
        task.started_at = time.time()
        task.ended_at = None
        task.return_code = None
        return await self._start_process(task.id)

    async def _notify_completion_listeners(self, task: TaskRecord) -> None:
        snapshot = replace(task, metadata=dict(task.metadata))
        for listener_id, listener in list(self._completion_listeners.items()):
            try:
                result = listener(snapshot)
                if result is not None:
                    await result
            except Exception:
                log.exception("Task completion listener %s failed", listener_id)

    def _require_task(self, task_id: str) -> TaskRecord:
        task = self._tasks.get(task_id)
        if task is None:
            raise ValueError(f"No task found with ID: {task_id}")
        return task

    async def aclose(self) -> None:
        processes = list(self._processes.values())
        waiters = list(self._waiters.values())
        for process in processes:
            if process.returncode is None:
                await _terminate_process(process, force=True)
        if waiters:
            await asyncio.gather(*waiters, return_exceptions=True)
        self._processes.clear()
        self._waiters.clear()


async def _terminate_process(
    process: asyncio.subprocess.Process,
    *,
    force: bool = False,
) -> None:
    if process.returncode is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass
    elif force:
        process.kill()
    else:
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except TimeoutError:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.returncode is None:
            process.kill()
        await process.wait()


def task_id(task_type: TaskType) -> str:
    prefix = {
        "local_bash": "b",
        "local_agent": "a",
        "remote_agent": "r",
        "in_process_teammate": "t",
        "dream": "d",
    }[task_type]
    return f"{prefix}{uuid4().hex[:8]}"


def _encode_worker_payload(data: str) -> bytes:
    stripped = data.rstrip("\n")
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        parsed = None
    framed = (
        stripped
        if isinstance(parsed, dict) and isinstance(parsed.get("text"), str)
        else json.dumps({"text": stripped}, ensure_ascii=False)
        if "\n" in stripped or "\r" in stripped
        else stripped
    )
    return (framed + "\n").encode()


async def _close_stdin(process: asyncio.subprocess.Process) -> None:
    if process.stdin is None or process.stdin.is_closing():
        return
    process.stdin.close()
    try:
        await process.stdin.wait_closed()
    except (BrokenPipeError, ConnectionResetError):
        pass


__all__ = ["BackgroundTaskManager", "TaskRecord", "TaskStatus", "TaskType", "task_id"]
