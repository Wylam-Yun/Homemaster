"""HomeMaster-owned lifecycle for one user-private Neo4j server."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from homemaster.config import MemoryConfig

CommandRunner = Callable[[str, Mapping[str, str]], Awaitable[None]]
ReadinessProbe = Callable[[], Awaitable[bool]]
ProcessProbe = Callable[[int, str], bool]
ServiceIdentityProbe = Callable[[], Awaitable[str | None]]


class ManagedNeo4jError(RuntimeError):
    """Managed Neo4j could not reach a verified lifecycle state."""


class ManagedNeo4jRuntime:
    """Share one private Neo4j server across local HomeMaster processes."""

    def __init__(
        self,
        config: MemoryConfig,
        *,
        command_runner: CommandRunner | None = None,
        readiness_probe: ReadinessProbe | None = None,
        process_probe: ProcessProbe | None = None,
        service_identity_probe: ServiceIdentityProbe | None = None,
        poll_interval_seconds: float = 0.2,
    ) -> None:
        if config.neo4j.mode != "managed_local":
            raise ValueError("ManagedNeo4jRuntime requires memory.neo4j.mode=managed_local")
        self._config = config
        self._neo4j = config.neo4j
        self._runtime_root = config.neo4j_runtime_root
        self._clients_root = self._runtime_root / "clients"
        self._lock_path = self._runtime_root / "manager.lock"
        self._owner_path = self._runtime_root / "managed-owner.json"
        self._lease_path = self._clients_root / f"{os.getpid()}-{uuid4().hex}.json"
        self._command_runner = command_runner
        self._readiness_probe = readiness_probe
        self._process_probe = process_probe or _process_identity_is_alive
        self._service_identity_probe = service_identity_probe
        self._poll_interval_seconds = poll_interval_seconds
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    async def start(self) -> None:
        if self._started:
            return
        self._validate_installation()
        self._prepare_runtime_root()
        async with self._locked():
            self._prune_stale_leases()
            ready = await self._is_ready()
            if not ready:
                owner_token = uuid4().hex
                self._write_owner(state="starting", owner_token=owner_token)
                command_task = asyncio.create_task(self._run_command("start"))
                try:
                    await asyncio.shield(command_task)
                except BaseException:
                    command_succeeded = False
                    try:
                        await command_task
                    except BaseException:
                        pass
                    else:
                        command_succeeded = True
                    if command_succeeded and await self._stop_after_failed_start():
                        self._owner_path.unlink(missing_ok=True)
                    raise
                try:
                    await self._wait_for_ready(
                        expected=True,
                        timeout=self._neo4j.start_timeout_seconds,
                    )
                    service_identity = await self._service_identity()
                    if service_identity is None:
                        raise ManagedNeo4jError(
                            "Neo4j became ready but its service identity could not be verified"
                        )
                except BaseException:
                    if await self._stop_after_failed_start():
                        self._owner_path.unlink(missing_ok=True)
                    raise
                self._write_owner(
                    state="owned",
                    owner_token=owner_token,
                    service_identity=service_identity,
                )
            else:
                await self._validate_or_recover_owner()
            self._write_json(
                self._lease_path,
                {
                    "pid": os.getpid(),
                    "process_start": _process_start_identity(os.getpid()),
                    "hostname": socket.gethostname(),
                },
            )
            self._started = True

    async def close(self) -> None:
        if not self._started:
            return
        self._prepare_runtime_root()
        async with self._locked():
            self._lease_path.unlink(missing_ok=True)
            self._prune_stale_leases()
            if not any(self._clients_root.glob("*.json")) and self._owner_path.is_file():
                owner = self._read_owner()
                if await self._owns_current_service(owner):
                    await self._run_command("stop")
                    await self._wait_for_ready(
                        expected=False,
                        timeout=self._neo4j.stop_timeout_seconds,
                    )
                self._owner_path.unlink(missing_ok=True)
            self._started = False

    def _validate_installation(self) -> None:
        assert self._neo4j.home is not None
        assert self._neo4j.java_home is not None
        neo4j_binary = self._neo4j.home / "bin" / "neo4j"
        java_binary = self._neo4j.java_home / "bin" / "java"
        if not neo4j_binary.is_file():
            raise ManagedNeo4jError(f"Neo4j executable is missing: {neo4j_binary}")
        if not java_binary.is_file():
            raise ManagedNeo4jError(f"Java executable is missing: {java_binary}")

    def _prepare_runtime_root(self) -> None:
        self._clients_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._runtime_root, 0o700)
        os.chmod(self._clients_root, 0o700)

    @asynccontextmanager
    async def _locked(self) -> AsyncIterator[None]:
        import fcntl

        descriptor = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        loop = asyncio.get_running_loop()
        acquired: asyncio.Future[None] = loop.create_future()

        def acquire() -> None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except BaseException as exc:
                loop.call_soon_threadsafe(acquired.set_exception, exc)
            else:
                loop.call_soon_threadsafe(acquired.set_result, None)

        threading.Thread(target=acquire, name="homemaster-neo4j-lock", daemon=True).start()
        try:
            await asyncio.shield(acquired)
        except BaseException:
            await acquired
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            raise
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    async def _validate_or_recover_owner(self) -> None:
        owner = self._read_owner()
        if owner is None:
            return
        if not self._owner_matches_config(owner):
            self._owner_path.unlink(missing_ok=True)
            return
        if owner.get("state") == "starting":
            self._owner_path.unlink(missing_ok=True)
            return
        service_identity = await self._service_identity()
        if service_identity is None:
            self._owner_path.unlink(missing_ok=True)
            return
        if owner.get("state") != "owned" or owner.get("service_identity") != service_identity:
            self._owner_path.unlink(missing_ok=True)

    async def _stop_after_failed_start(self) -> bool:
        try:
            await self._run_command("stop")
            await self._wait_for_ready(
                expected=False,
                timeout=self._neo4j.stop_timeout_seconds,
            )
        except BaseException:
            return False
        return True

    async def _owns_current_service(self, owner: Mapping[str, Any] | None) -> bool:
        if owner is None or owner.get("state") != "owned":
            return False
        if not self._owner_matches_config(owner) or not await self._is_ready():
            return False
        service_identity = await self._service_identity()
        return service_identity is not None and owner.get("service_identity") == service_identity

    def _owner_matches_config(self, owner: Mapping[str, Any]) -> bool:
        return (
            owner.get("hostname") == socket.gethostname()
            and owner.get("neo4j_home") == str(self._neo4j.home)
            and isinstance(owner.get("owner_token"), str)
        )

    def _read_owner(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._owner_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._owner_path.unlink(missing_ok=True)
            return None
        if not isinstance(payload, dict):
            self._owner_path.unlink(missing_ok=True)
            return None
        return payload

    def _write_owner(
        self,
        *,
        state: str,
        owner_token: str,
        service_identity: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "state": state,
            "hostname": socket.gethostname(),
            "neo4j_home": str(self._neo4j.home),
            "owner_token": owner_token,
        }
        if service_identity is not None:
            payload["service_identity"] = service_identity
        self._write_json(self._owner_path, payload)

    def _prune_stale_leases(self) -> None:
        for path in self._clients_root.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                pid = int(payload["pid"])
                process_start = str(payload["process_start"])
                hostname = str(payload["hostname"])
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
                continue
            if hostname != socket.gethostname() or not self._process_probe(pid, process_start):
                path.unlink(missing_ok=True)

    async def _run_command(self, action: str) -> None:
        env = self._command_environment()
        if self._command_runner is not None:
            await self._command_runner(action, env)
            return
        assert self._neo4j.home is not None
        timeout = (
            self._neo4j.start_timeout_seconds
            if action == "start"
            else self._neo4j.stop_timeout_seconds
        )
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                [str(self._neo4j.home / "bin" / "neo4j"), action],
                cwd=self._neo4j.home,
                env=dict(env),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ManagedNeo4jError(f"Neo4j {action} command failed: {type(exc).__name__}") from exc
        if completed.returncode != 0:
            detail = [
                line.strip()
                for line in (completed.stderr or completed.stdout).splitlines()
                if line.strip() and set(line.strip()) != {"-"}
            ]
            message = " | ".join(detail[-6:]) if detail else f"exit code {completed.returncode}"
            raise ManagedNeo4jError(f"Neo4j {action} command failed: {message}")

    def _command_environment(self) -> dict[str, str]:
        assert self._neo4j.home is not None
        assert self._neo4j.java_home is not None
        env = dict(os.environ)
        env["JAVA_HOME"] = str(self._neo4j.java_home)
        env["NEO4J_HOME"] = str(self._neo4j.home)
        env["PATH"] = os.pathsep.join(
            [str(self._neo4j.java_home / "bin"), env.get("PATH", "")]
        )
        return env

    async def _is_ready(self) -> bool:
        if self._readiness_probe is not None:
            return await self._readiness_probe()
        from neo4j import AsyncGraphDatabase

        driver = AsyncGraphDatabase.driver(
            self._neo4j.uri,
            auth=(self._neo4j.username, self._neo4j.password.get_secret_value()),
        )
        try:
            await driver.verify_connectivity()
            return True
        except Exception:
            return False
        finally:
            await driver.close()

    async def _service_identity(self) -> str | None:
        if self._service_identity_probe is not None:
            return await self._service_identity_probe()
        from neo4j import AsyncGraphDatabase

        driver = AsyncGraphDatabase.driver(
            self._neo4j.uri,
            auth=(self._neo4j.username, self._neo4j.password.get_secret_value()),
        )
        try:
            async with driver.session(database=self._neo4j.database) as session:
                result = await session.run("CALL dbms.info() YIELD id RETURN id")
                record = await result.single()
            return str(record["id"]) if record is not None and record.get("id") else None
        except Exception:
            return None
        finally:
            await driver.close()

    async def _wait_for_ready(self, *, expected: bool, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            if (await self._is_ready()) is expected:
                return
            if loop.time() >= deadline:
                state = "ready" if expected else "stopped"
                raise ManagedNeo4jError(f"Neo4j did not become {state} within {timeout:g}s")
            await asyncio.sleep(self._poll_interval_seconds)

    @staticmethod
    def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)


def _process_start_identity(pid: int) -> str:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        return fields[21]
    except (OSError, IndexError):
        return "unknown"


def _process_identity_is_alive(pid: int, expected_start: str) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return _process_start_identity(pid) == expected_start


__all__ = ["ManagedNeo4jError", "ManagedNeo4jRuntime"]
