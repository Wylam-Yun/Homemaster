"""Application-owned runtime services for HomeMaster tools."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml
from croniter import croniter

from homemaster.config import HomeMasterConfig, load_config
from homemaster.mcp.types import (
    McpHttpServerConfig,
    McpServerConfig,
    McpStdioServerConfig,
    McpWebSocketServerConfig,
)
from homemaster.tools.task_runtime import (
    BackgroundTaskManager,
    TaskRecord,
    TaskType,
    task_id,
)


@dataclass
class TeamRecord:
    name: str
    description: str = ""
    agents: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


class HomeTaskManager(BackgroundTaskManager):
    """Upstream task lifecycle with an injected Home-owned state directory."""

    def __init__(self, tasks_dir: Path, *, config_path: Path | None = None) -> None:
        super().__init__()
        self.tasks_dir = tasks_dir.expanduser().resolve()
        self.config_path = config_path.expanduser().resolve() if config_path is not None else None
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

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
        if command is None and argv is None:
            raise ValueError("create_shell_task requires either command or argv")
        if command is not None and argv is not None:
            raise ValueError("create_shell_task accepts only one of command or argv")
        task_id_value = task_id(task_type)
        output_path = self.tasks_dir / f"{task_id_value}.log"
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
        self._persist()
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
            argv = [sys.executable, "-m", "homemaster.cli.child_worker"]
            if self.config_path is not None:
                argv.extend(("--config", str(self.config_path)))
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
        updated = replace(record, prompt=prompt)
        if task_type != "local_agent":
            updated.metadata["agent_mode"] = task_type
        self._tasks[record.id] = updated
        await self.write_to_task(record.id, prompt)
        self._persist()
        return updated

    def update_task(
        self,
        task_id: str,
        *,
        description: str | None = None,
        progress: int | None = None,
        status_note: str | None = None,
    ) -> TaskRecord:
        task = super().update_task(
            task_id,
            description=description,
            progress=progress,
            status_note=status_note,
        )
        self._persist()
        return task

    async def stop_task(self, task_id: str) -> TaskRecord:
        task = await super().stop_task(task_id)
        self._persist()
        return task

    async def _watch_process(
        self,
        task_id: str,
        process: asyncio.subprocess.Process,
        generation: int,
    ) -> None:
        await super()._watch_process(task_id, process, generation)
        self._persist()

    def _persist(self) -> None:
        records = []
        for task in sorted(self._tasks.values(), key=lambda item: item.created_at):
            record = asdict(task)
            record["output_file"] = str(task.output_file)
            records.append(record)
        _atomic_json(self.tasks_dir / "tasks.json", records)


class HomeCronStore:
    """Persistent Cron registry rooted under HomeMaster state."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir.expanduser().resolve()
        self.registry_path = self.state_dir / "cron_jobs.json"
        self.pid_path = self.state_dir / "scheduler.pid"

    def load(self) -> list[dict[str, Any]]:
        if not self.registry_path.exists():
            return []
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return payload if isinstance(payload, list) else []

    def save(self, jobs: list[dict[str, Any]]) -> None:
        _atomic_json(self.registry_path, jobs)

    def upsert(self, job: dict[str, Any]) -> dict[str, Any]:
        jobs = [item for item in self.load() if item.get("name") != job.get("name")]
        stored = dict(job)
        stored["next_run"] = croniter(stored["schedule"], time.time()).get_next(float)
        jobs.append(stored)
        self.save(jobs)
        return stored

    def delete(self, name: str) -> None:
        jobs = self.load()
        remaining = [item for item in jobs if item.get("name") != name]
        if len(remaining) == len(jobs):
            raise ValueError(f"No cron job named '{name}'")
        self.save(remaining)

    def toggle(self, name: str, enabled: bool) -> dict[str, Any]:
        jobs = self.load()
        target = next((item for item in jobs if item.get("name") == name), None)
        if target is None:
            raise ValueError(f"No cron job named '{name}'")
        target["enabled"] = enabled
        self.save(jobs)
        return target

    def get(self, name: str) -> dict[str, Any] | None:
        return next((item for item in self.load() if item.get("name") == name), None)

    def scheduler_pid(self) -> int | None:
        try:
            pid = int(self.pid_path.read_text(encoding="ascii").strip())
            os.kill(pid, 0)
        except (FileNotFoundError, ValueError, OSError):
            return None
        return pid


class HomeTeamRegistry:
    """Application-owned team registry with a durable Home state snapshot."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir.expanduser().resolve()
        self.path = self.state_dir / "teams.json"
        self._teams: dict[str, TeamRecord] = {}

    def create_team(self, name: str, description: str = "") -> TeamRecord:
        if name in self._teams:
            raise ValueError(f"Team '{name}' already exists")
        team = TeamRecord(name=name, description=description)
        self._teams[name] = team
        self._persist()
        return team

    def delete_team(self, name: str) -> None:
        if name not in self._teams:
            raise ValueError(f"Team '{name}' does not exist")
        del self._teams[name]
        self._persist()

    def add_agent(self, name: str, task_id: str) -> None:
        team = self._teams.get(name)
        if team is None:
            team = self.create_team(name)
        if task_id not in team.agents:
            team.agents.append(task_id)
            self._persist()

    def list_teams(self) -> list[TeamRecord]:
        return sorted(self._teams.values(), key=lambda item: item.name)

    def _persist(self) -> None:
        _atomic_json(self.path, [asdict(team) for team in self.list_teams()])


class HomePlanModeService:
    """Session-scoped permission mode state."""

    def __init__(self) -> None:
        self._plan_sessions: set[str] = set()

    def set(self, session_id: str, enabled: bool) -> None:
        if enabled:
            self._plan_sessions.add(session_id)
        else:
            self._plan_sessions.discard(session_id)

    def enabled(self, session_id: str) -> bool:
        return session_id in self._plan_sessions


class HomeConfigService:
    """Validate and atomically update the actual HomeMaster YAML file."""

    def __init__(self, config: HomeMasterConfig) -> None:
        self.config = config
        self.path = config.config_path

    def show(self) -> str:
        payload = self.config.model_dump(mode="json")
        gateway = payload.get("gateway")
        if isinstance(gateway, dict) and isinstance(gateway.get("feishu"), dict):
            gateway["feishu"]["app_secret"] = (
                self.config.gateway.feishu.app_secret.get_secret_value()
            )
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)

    def set(self, key: str, raw_value: str) -> Path:
        if self.path is None:
            raise ValueError("HomeMaster config path is unavailable")
        if self.path.exists():
            payload = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        else:
            payload = self.config.model_dump(mode="json")
        if not isinstance(payload, dict):
            raise ValueError("HomeMaster config must be a mapping")
        target: dict[str, Any] = payload
        parts = key.split(".")
        for part in parts[:-1]:
            child = target.get(part)
            if not isinstance(child, dict):
                raise ValueError(f"Unknown config key: {key}")
            target = child
        if parts[-1] not in target:
            raise ValueError(f"Unknown config key: {key}")
        target[parts[-1]] = yaml.safe_load(raw_value)
        HomeMasterConfig.model_validate(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
        self.config = load_config(self.path)
        return self.path

    def set_mcp_auth(
        self,
        server_name: str,
        mode: str,
        value: str,
        key: str | None,
    ) -> McpServerConfig:
        config = self.config.mcp.servers.get(server_name)
        if config is None:
            raise ValueError(f"Unknown MCP server: {server_name}")
        if isinstance(config, McpStdioServerConfig):
            if mode not in {"env", "bearer"}:
                raise ValueError("stdio MCP auth supports env or bearer modes")
            target = key or "MCP_AUTH_TOKEN"
            env = dict(config.env)
            env[target] = f"Bearer {value}" if mode == "bearer" else value
            updated: McpServerConfig = config.model_copy(update={"env": env})
            field = "env"
            serialized = env
        elif isinstance(config, (McpHttpServerConfig, McpWebSocketServerConfig)):
            if mode not in {"header", "bearer"}:
                raise ValueError("http/ws MCP auth supports header or bearer modes")
            target = key or "Authorization"
            headers = dict(config.headers)
            headers[target] = (
                f"Bearer {value}" if mode == "bearer" and target == "Authorization" else value
            )
            updated = config.model_copy(update={"headers": headers})
            field = "headers"
            serialized = headers
        else:  # pragma: no cover - discriminated config is exhaustive
            raise ValueError("Unsupported MCP server config type")
        self.set(
            f"mcp.servers.{server_name}.{field}",
            json.dumps(serialized, ensure_ascii=False),
        )
        return updated


class HomeToolServices:
    """All mutable services owned by one Home application."""

    def __init__(self, config: HomeMasterConfig, *, state_root: Path | None = None) -> None:
        root = (state_root or Path("~/.homemaster")).expanduser().resolve()
        self.root = root
        self.tasks = HomeTaskManager(root / "tasks", config_path=config.config_path)
        self.cron = HomeCronStore(root / "cron")
        self.teams = HomeTeamRegistry(root / "teams")
        self.plan_mode = HomePlanModeService()
        self.config = HomeConfigService(config)
        self.agent_tasks: dict[str, str] = {}

    async def aclose(self) -> None:
        await self.tasks.aclose()


__all__ = [
    "HomeConfigService",
    "HomeCronStore",
    "HomeToolServices",
    "HomePlanModeService",
    "HomeTaskManager",
    "HomeTeamRegistry",
]
