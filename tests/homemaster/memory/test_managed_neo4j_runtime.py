from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path

import pytest

from homemaster.config import HomeMasterConfig
from homemaster.memory.managed_neo4j import ManagedNeo4jError, ManagedNeo4jRuntime


def _config(tmp_path: Path, *, start_timeout_seconds: float = 1.0) -> HomeMasterConfig:
    neo4j_home = tmp_path / "neo4j-home"
    java_home = tmp_path / "java-home"
    neo4j_home.joinpath("bin").mkdir(parents=True)
    neo4j_home.joinpath("bin", "neo4j").write_text("#!/bin/sh\n", encoding="utf-8")
    java_home.joinpath("bin").mkdir(parents=True)
    java_home.joinpath("bin", "java").write_text("", encoding="utf-8")
    return HomeMasterConfig(
        memory={
            "data_root": tmp_path / "memory",
            "neo4j": {
                "mode": "managed_local",
                "home": neo4j_home,
                "java_home": java_home,
                "uri": "bolt://127.0.0.1:7687",
                "username": "neo4j",
                "password": "test-password",
                "start_timeout_seconds": start_timeout_seconds,
                "stop_timeout_seconds": 1.0,
            },
        }
    )


@pytest.mark.asyncio
async def test_first_client_starts_and_last_client_stops_managed_neo4j(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ready = False
    commands: list[tuple[str, Mapping[str, str]]] = []

    async def command_runner(action: str, env: Mapping[str, str]) -> None:
        nonlocal ready
        commands.append((action, env))
        ready = action == "start"

    async def readiness_probe() -> bool:
        return ready

    async def service_identity_probe() -> str:
        return "managed-service"

    first = ManagedNeo4jRuntime(
        config.memory,
        command_runner=command_runner,
        readiness_probe=readiness_probe,
        service_identity_probe=service_identity_probe,
        poll_interval_seconds=0.001,
    )
    second = ManagedNeo4jRuntime(
        config.memory,
        command_runner=command_runner,
        readiness_probe=readiness_probe,
        service_identity_probe=service_identity_probe,
        poll_interval_seconds=0.001,
    )

    await first.start()
    await second.start()

    leases = list(config.memory.neo4j_runtime_root.joinpath("clients").glob("*.json"))
    assert len(leases) == 2
    assert [action for action, _env in commands] == ["start"]
    assert commands[0][1]["JAVA_HOME"] == str(config.memory.neo4j.java_home)

    await first.close()
    assert [action for action, _env in commands] == ["start"]
    assert len(list(config.memory.neo4j_runtime_root.joinpath("clients").glob("*.json"))) == 1

    await second.close()
    assert [action for action, _env in commands] == ["start", "stop"]
    assert list(config.memory.neo4j_runtime_root.joinpath("clients").glob("*.json")) == []


@pytest.mark.asyncio
async def test_preexisting_neo4j_is_reused_but_not_stopped(tmp_path: Path) -> None:
    config = _config(tmp_path)
    commands: list[str] = []

    async def command_runner(action: str, _env: Mapping[str, str]) -> None:
        commands.append(action)

    async def readiness_probe() -> bool:
        return True

    runtime = ManagedNeo4jRuntime(
        config.memory,
        command_runner=command_runner,
        readiness_probe=readiness_probe,
        service_identity_probe=lambda: _identity("external-service"),
    )

    await runtime.start()
    await runtime.close()

    assert commands == []


@pytest.mark.asyncio
async def test_start_failure_leaves_no_client_lease(tmp_path: Path) -> None:
    config = _config(tmp_path, start_timeout_seconds=0.01)
    commands: list[str] = []

    async def command_runner(action: str, _env: Mapping[str, str]) -> None:
        commands.append(action)

    async def readiness_probe() -> bool:
        return False

    runtime = ManagedNeo4jRuntime(
        config.memory,
        command_runner=command_runner,
        readiness_probe=readiness_probe,
        service_identity_probe=lambda: _identity("managed-service"),
        poll_interval_seconds=0.001,
    )

    with pytest.raises(ManagedNeo4jError, match="did not become ready"):
        await runtime.start()

    assert commands == ["start", "stop"]
    clients = config.memory.neo4j_runtime_root / "clients"
    assert not clients.exists() or list(clients.glob("*.json")) == []
    assert os.path.exists(config.memory.neo4j.home)


@pytest.mark.asyncio
async def test_command_failure_reports_meaningful_neo4j_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)

    async def readiness_probe() -> bool:
        return False

    def failed_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout=(
                "Starting Neo4j.\n"
                "Lock file has been locked by another process\n"
                "-----------------------------------------------------\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", failed_run)
    runtime = ManagedNeo4jRuntime(config.memory, readiness_probe=readiness_probe)

    with pytest.raises(ManagedNeo4jError, match="Lock file has been locked"):
        await runtime.start()


@pytest.mark.asyncio
async def test_starting_owner_marker_reuses_service_without_claiming_it(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ready = True
    commands: list[str] = []

    async def command_runner(action: str, _env: Mapping[str, str]) -> None:
        nonlocal ready
        commands.append(action)
        ready = action == "start"

    async def readiness_probe() -> bool:
        return ready

    runtime = ManagedNeo4jRuntime(
        config.memory,
        command_runner=command_runner,
        readiness_probe=readiness_probe,
        service_identity_probe=lambda: _identity("recovered-service"),
        poll_interval_seconds=0.001,
    )
    runtime._prepare_runtime_root()
    runtime._write_json(
        runtime._owner_path,
        {
            "state": "starting",
            "hostname": socket.gethostname(),
            "neo4j_home": str(config.memory.neo4j.home),
            "owner_token": "crashed-owner",
        },
    )

    await runtime.start()
    await runtime.close()

    assert commands == []
    assert not runtime._owner_path.exists()


@pytest.mark.asyncio
async def test_cancellation_waits_for_start_outcome_and_stops_started_service(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    ready = False
    entered = asyncio.Event()
    release = asyncio.Event()
    commands: list[str] = []

    async def command_runner(action: str, _env: Mapping[str, str]) -> None:
        nonlocal ready
        if action == "start":
            entered.set()
            await release.wait()
        commands.append(action)
        ready = action == "start"

    async def readiness_probe() -> bool:
        return ready

    runtime = ManagedNeo4jRuntime(
        config.memory,
        command_runner=command_runner,
        readiness_probe=readiness_probe,
        service_identity_probe=lambda: _identity("managed-service"),
        poll_interval_seconds=0.001,
    )
    start_task = asyncio.create_task(runtime.start())
    await entered.wait()
    start_task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await start_task

    assert commands == ["start", "stop"]
    assert not runtime._owner_path.exists()


@pytest.mark.asyncio
async def test_uncertain_start_failure_keeps_non_owning_intent_marker(tmp_path: Path) -> None:
    config = _config(tmp_path)

    async def command_runner(action: str, _env: Mapping[str, str]) -> None:
        assert action == "start"
        raise TimeoutError("command outcome unknown")

    async def readiness_probe() -> bool:
        return False

    runtime = ManagedNeo4jRuntime(
        config.memory,
        command_runner=command_runner,
        readiness_probe=readiness_probe,
        service_identity_probe=lambda: _identity("managed-service"),
    )

    with pytest.raises(TimeoutError, match="outcome unknown"):
        await runtime.start()

    owner = runtime._read_owner()
    assert owner is not None
    assert owner["state"] == "starting"


@pytest.mark.asyncio
async def test_cancellation_preserves_intent_when_compensating_stop_fails(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    ready = False
    entered = asyncio.Event()
    release = asyncio.Event()
    commands: list[str] = []

    async def command_runner(action: str, _env: Mapping[str, str]) -> None:
        nonlocal ready
        commands.append(action)
        if action == "start":
            entered.set()
            await release.wait()
            ready = True
            return
        raise RuntimeError("stop failed")

    async def readiness_probe() -> bool:
        return ready

    runtime = ManagedNeo4jRuntime(
        config.memory,
        command_runner=command_runner,
        readiness_probe=readiness_probe,
        service_identity_probe=lambda: _identity("managed-service"),
        poll_interval_seconds=0.001,
    )
    start_task = asyncio.create_task(runtime.start())
    await entered.wait()
    start_task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await start_task

    assert commands == ["start", "stop"]
    owner = runtime._read_owner()
    assert owner is not None
    assert owner["state"] == "starting"


@pytest.mark.asyncio
async def test_stale_owner_marker_never_stops_replacement_service(tmp_path: Path) -> None:
    config = _config(tmp_path)
    commands: list[str] = []

    async def command_runner(action: str, _env: Mapping[str, str]) -> None:
        commands.append(action)

    async def readiness_probe() -> bool:
        return True

    runtime = ManagedNeo4jRuntime(
        config.memory,
        command_runner=command_runner,
        readiness_probe=readiness_probe,
        service_identity_probe=lambda: _identity("replacement-service"),
    )
    runtime._prepare_runtime_root()
    runtime._write_json(
        runtime._owner_path,
        {
            "state": "owned",
            "hostname": socket.gethostname(),
            "neo4j_home": str(config.memory.neo4j.home),
            "owner_token": "stale-owner",
            "service_identity": "old-service",
        },
    )

    await runtime.start()
    await runtime.close()

    assert commands == []
    assert not runtime._owner_path.exists()


@pytest.mark.asyncio
async def test_file_lock_acquisition_never_blocks_event_loop_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fcntl

    config = _config(tmp_path)
    event_loop_thread = threading.get_ident()
    original_flock = fcntl.flock

    def guarded_flock(descriptor: int, operation: int) -> None:
        if operation & fcntl.LOCK_EX and threading.get_ident() == event_loop_thread:
            raise AssertionError("blocking flock acquired on event-loop thread")
        original_flock(descriptor, operation)

    monkeypatch.setattr(fcntl, "flock", guarded_flock)
    runtime = ManagedNeo4jRuntime(
        config.memory,
        command_runner=lambda _action, _env: _nothing(),
        readiness_probe=lambda: _ready(),
        service_identity_probe=lambda: _identity("external-service"),
    )

    await runtime.start()
    await runtime.close()


@pytest.mark.asyncio
async def test_same_event_loop_clients_can_start_concurrently(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ready = False
    commands: list[str] = []

    async def command_runner(action: str, _env: Mapping[str, str]) -> None:
        nonlocal ready
        await asyncio.sleep(0.01)
        commands.append(action)
        ready = action == "start"

    async def readiness_probe() -> bool:
        return ready

    runtimes = [
        ManagedNeo4jRuntime(
            config.memory,
            command_runner=command_runner,
            readiness_probe=readiness_probe,
            service_identity_probe=lambda: _identity("managed-service"),
            poll_interval_seconds=0.001,
        )
        for _ in range(2)
    ]

    await asyncio.wait_for(
        asyncio.gather(*(runtime.start() for runtime in runtimes)), timeout=1
    )
    await asyncio.wait_for(
        asyncio.gather(*(runtime.close() for runtime in runtimes)), timeout=1
    )

    assert commands == ["start", "stop"]


async def _identity(value: str) -> str:
    return value


async def _ready() -> bool:
    return True


async def _nothing() -> None:
    return None
