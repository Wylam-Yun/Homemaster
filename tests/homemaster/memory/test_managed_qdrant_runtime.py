from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from homemaster.config import HomeMasterConfig
from homemaster.memory.managed_qdrant import (
    ManagedQdrantError,
    ManagedQdrantRuntime,
)


def _config(tmp_path: Path, *, start_timeout_seconds: float = 2.0) -> HomeMasterConfig:
    qdrant_home = tmp_path / "qdrant-home"
    qdrant_home.mkdir(parents=True)
    binary = qdrant_home / "qdrant"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    return HomeMasterConfig(
        memory={
            "data_root": tmp_path / "memory",
            "qdrant": {
                "mode": "managed_local",
                "home": qdrant_home,
                "rest_port": 16333,
                "grpc_port": 16334,
                "start_timeout_seconds": start_timeout_seconds,
                "stop_timeout_seconds": 1.0,
            },
        }
    )


class _FakeService:
    """In-memory stand-in for the served Qdrant: liveness + sentinel data.

    ``alive_pids`` (process-table truth) is tracked separately from
    ``running`` (HTTP API answering) so tests can model a spawned-but-
    still-initializing service.
    """

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root
        self.running = False
        self.pid: int | None = None
        self.alive_pids: set[int] = set()
        self.sentinel: dict[str, Any] | None = None
        self.spawn_calls = 0
        self.stop_calls: list[int] = []

    class _Proc:
        def __init__(self, service: "_FakeService", pid: int) -> None:
            self._service = service
            self.pid = pid

        def poll(self) -> int | None:
            return None if self.pid in self._service.alive_pids else -15

        def wait(self) -> int:
            # wait() reaps, it does not kill: block until the pid leaves the
            # process table (someone else stopped the service).
            import time

            while self.pid in self._service.alive_pids:
                time.sleep(0.001)
            self._service.running = False
            return -15

    def spawn(self) -> "_FakeService._Proc":
        self.spawn_calls += 1
        self.running = True
        self.pid = 40000 + self.spawn_calls
        self.alive_pids.add(self.pid)
        return _FakeService._Proc(self, self.pid)

    async def probe(self) -> str:
        return "qdrant" if self.running else "absent"

    async def read_sentinel(self) -> Mapping[str, Any] | None:
        return self.sentinel

    async def write_sentinel(self, token: str) -> None:
        self.sentinel = {
            "runtime_root": str(self.runtime_root),
            "owner_token": token,
        }

    async def stop(self, pid: int) -> None:
        self.stop_calls.append(pid)
        self.alive_pids.discard(pid)
        self.running = False

    def process_alive(self, pid: int, _start: str) -> bool:
        # Lease files record the *client* pid (this test process); the owner
        # file records the *service* pid — both kinds must count as alive.
        return pid == os.getpid() or pid in self.alive_pids

    def runtime(self, config: HomeMasterConfig, **kwargs: Any) -> ManagedQdrantRuntime:
        kwargs.setdefault("process_spawner", self.spawn)
        kwargs.setdefault("probe", self.probe)
        kwargs.setdefault("sentinel_reader", self.read_sentinel)
        kwargs.setdefault("sentinel_writer", self.write_sentinel)
        kwargs.setdefault("process_probe", self.process_alive)
        kwargs.setdefault("process_stopper", self.stop)
        kwargs.setdefault("poll_interval_seconds", 0.001)
        return ManagedQdrantRuntime(config.memory, **kwargs)


@pytest.mark.asyncio
async def test_first_process_spawns_last_leaser_stops(tmp_path: Path) -> None:
    config = _config(tmp_path)
    service = _FakeService(config.memory.mindmemos_qdrant_service_root / "runtime")
    first = service.runtime(config)
    second = service.runtime(config)

    await first.start()
    assert service.spawn_calls == 1
    assert service.sentinel is not None
    assert service.sentinel["runtime_root"] == str(
        config.memory.mindmemos_qdrant_service_root / "runtime"
    )

    await second.start()
    assert service.spawn_calls == 1  # attached, no second spawn
    leases = list(
        config.memory.mindmemos_qdrant_service_root.joinpath(
            "runtime", "clients"
        ).glob("*.json")
    )
    assert len(leases) == 2

    await first.close()
    assert service.running  # second lease keeps it alive
    await second.close()
    assert not service.running
    assert service.stop_calls == [service.pid]
    # owner file cleaned up after the last leaser stopped the service
    assert not (
        config.memory.mindmemos_qdrant_service_root / "runtime" / "managed-owner.json"
    ).exists()


@pytest.mark.asyncio
async def test_attacher_survives_starter_exit(tmp_path: Path) -> None:
    """The process that spawned the service may exit while others still run —
    the surviving leaser must be able to stop the service on close."""
    config = _config(tmp_path)
    service = _FakeService(config.memory.mindmemos_qdrant_service_root / "runtime")
    starter = service.runtime(config)
    follower = service.runtime(config)

    await starter.start()
    await follower.start()
    # Starter dies without close() — its lease becomes stale, service stays up.
    starter._lease_path.unlink(missing_ok=True)  # noqa: SLF001

    await follower.close()
    assert not service.running


@pytest.mark.asyncio
async def test_foreign_qdrant_on_port_fails_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    service = _FakeService(config.memory.mindmemos_qdrant_service_root / "runtime")
    # A Qdrant is already up but its sentinel says another memory root.
    service.running = True
    service.sentinel = {
        "runtime_root": "/somewhere/else/runtime",
        "owner_token": "foreign-token",
    }
    runtime = service.runtime(config)

    with pytest.raises(ManagedQdrantError, match="different memory root"):
        await runtime.start()
    assert service.spawn_calls == 0


@pytest.mark.asyncio
async def test_non_qdrant_service_on_port_fails_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    service = _FakeService(config.memory.mindmemos_qdrant_service_root / "runtime")

    async def foreign_probe() -> str:
        return "foreign"

    runtime = ManagedQdrantRuntime(
        config.memory,
        probe=foreign_probe,
        process_spawner=service.spawn,
        poll_interval_seconds=0.001,
    )
    with pytest.raises(ManagedQdrantError, match="non-Qdrant"):
        await runtime.start()
    assert service.spawn_calls == 0


@pytest.mark.asyncio
async def test_unowned_qdrant_with_live_owner_repairs_sentinel(tmp_path: Path) -> None:
    """Crash window: spawner died between service-ready and sentinel write.
    The next starter sees the qdrant answering with no sentinel, but the
    owner file proves our PID started it — repair and attach."""
    config = _config(tmp_path)
    service = _FakeService(config.memory.mindmemos_qdrant_service_root / "runtime")
    service.running = True
    service.pid = 42000
    service.alive_pids.add(42000)
    service.sentinel = None
    # Simulate the crashed starter's owner file (state=starting, live pid).
    runtime_dir = config.memory.mindmemos_qdrant_service_root / "runtime"
    runtime_dir.joinpath("clients").mkdir(parents=True)
    owner = {
        "state": "starting",
        "hostname": __import__("socket").gethostname(),
        "home": str(config.memory.qdrant.home),
        "owner_token": "crashed-token",
        "service_identity": "crashed-token",
        "pid": 42000,
        "process_start": "unknown",
    }
    import json

    (runtime_dir / "managed-owner.json").write_text(json.dumps(owner))

    runtime = service.runtime(config)
    await runtime.start()
    assert service.sentinel is not None
    assert service.sentinel["owner_token"] == "crashed-token"
    await runtime.close()


@pytest.mark.asyncio
async def test_unowned_qdrant_without_owner_fails_closed(tmp_path: Path) -> None:
    """A Qdrant answering with no sentinel and no usable owner file is
    foreign-owned storage we cannot verify — refuse to attach."""
    config = _config(tmp_path)
    service = _FakeService(config.memory.mindmemos_qdrant_service_root / "runtime")
    service.running = True
    service.sentinel = None
    runtime = service.runtime(config)

    with pytest.raises(ManagedQdrantError, match="does not belong"):
        await runtime.start()


@pytest.mark.asyncio
async def test_spawn_exit_during_startup_surfaces_stderr(tmp_path: Path) -> None:
    config = _config(tmp_path)

    class DeadProc:
        pid = 43000

        def poll(self) -> int | None:
            return 2

        def wait(self) -> int:
            return 2

    service = _FakeService(config.memory.mindmemos_qdrant_service_root / "runtime")
    service.pid = 43000

    runtime = ManagedQdrantRuntime(
        config.memory,
        process_spawner=lambda: DeadProc(),
        probe=service.probe,
        sentinel_reader=service.read_sentinel,
        sentinel_writer=service.write_sentinel,
        process_probe=service.process_alive,
        poll_interval_seconds=0.001,
    )
    with pytest.raises(ManagedQdrantError, match="exited during startup"):
        await runtime.start()
    assert not (
        config.memory.mindmemos_qdrant_service_root / "runtime" / "managed-owner.json"
    ).exists()


@pytest.mark.asyncio
async def test_managed_local_requires_home(tmp_path: Path) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="qdrant.home"):
        HomeMasterConfig(memory={"qdrant": {"mode": "managed_local"}})


def test_process_probe_uses_pid_start_identity(tmp_path: Path) -> None:
    """The default probe must distinguish a live pid from a recycled one —
    it compares a real start-time identity (/proc on Linux, ps on macOS)
    so a stale lease for a reused pid is pruned rather than trusted."""
    from homemaster.memory.managed_qdrant import _pid_alive
    from homemaster.memory.managed_neo4j import (
        _process_identity_is_alive,
        _process_start_identity,
    )

    pid = os.getpid()
    start = _process_start_identity(pid)
    assert _pid_alive(pid)
    assert _process_identity_is_alive(pid, start)
    assert not _process_identity_is_alive(pid, "definitely-not-our-start")


@pytest.mark.asyncio
async def test_same_event_loop_clients_start_concurrently(tmp_path: Path) -> None:
    """Two start() calls racing on one loop must serialize on the manager
    lock — exactly one spawn, two leases."""
    import asyncio

    config = _config(tmp_path)
    service = _FakeService(config.memory.mindmemos_qdrant_service_root / "runtime")
    first = service.runtime(config)
    second = service.runtime(config)

    await asyncio.gather(first.start(), second.start())
    assert service.spawn_calls == 1
    leases = list(
        config.memory.mindmemos_qdrant_service_root.joinpath(
            "runtime", "clients"
        ).glob("*.json")
    )
    assert len(leases) == 2
    await asyncio.gather(first.close(), second.close())
    assert not service.running


@pytest.mark.asyncio
async def test_starting_owner_with_live_pid_is_promoted_to_owned(
    tmp_path: Path,
) -> None:
    """Crash window: starter died between the sentinel claim and the
    ``owned`` write. The next process must promote the record (pid+start
    and the sentinel token both prove ownership), not delete it —
    otherwise the service can never be stopped again."""
    import json
    import socket

    config = _config(tmp_path)
    runtime_root = config.memory.mindmemos_qdrant_service_root / "runtime"
    service = _FakeService(runtime_root)
    service.running = True
    service.pid = 45000
    service.alive_pids.add(45000)
    service.sentinel = {
        "runtime_root": str(runtime_root),
        "owner_token": "tok-promote",
    }
    runtime_root.joinpath("clients").mkdir(parents=True)
    (runtime_root / "managed-owner.json").write_text(
        json.dumps(
            {
                "state": "starting",
                "hostname": socket.gethostname(),
                "home": str(config.memory.qdrant.home),
                "rest_port": 16333,
                "owner_token": "tok-promote",
                "service_identity": "tok-promote",
                "pid": 45000,
                "process_start": "real-start",
            }
        )
    )

    runtime = service.runtime(config)
    await runtime.start()
    owner = json.loads((runtime_root / "managed-owner.json").read_text())
    assert owner["state"] == "owned"

    await runtime.close()
    assert not service.running


@pytest.mark.asyncio
async def test_orphaned_spawn_adopted_via_listener_pid(tmp_path: Path) -> None:
    """Crash window: starter was killed between spawn and the pid
    write-back — owner says ``starting`` with no pid, service answers but
    is unclaimed. Adopt only when the port listener resolves to the
    configured binary."""
    import json
    import socket

    config = _config(tmp_path)
    runtime_root = config.memory.mindmemos_qdrant_service_root / "runtime"
    service = _FakeService(runtime_root)
    service.running = True
    service.pid = 46000
    service.alive_pids.add(46000)
    service.sentinel = None
    runtime_root.joinpath("clients").mkdir(parents=True)
    (runtime_root / "managed-owner.json").write_text(
        json.dumps(
            {
                "state": "starting",
                "hostname": socket.gethostname(),
                "home": str(config.memory.qdrant.home),
                "rest_port": 16333,
                "owner_token": "tok-orphan",
                "service_identity": "tok-orphan",
            }
        )
    )

    runtime = service.runtime(
        config,
        listener_pid=lambda port: 46000 if port == 16333 else None,
        binary_checker=lambda pid, binary: pid == 46000,
    )
    await runtime.start()
    assert service.spawn_calls == 0
    assert service.sentinel is not None
    assert service.sentinel["owner_token"] == "tok-orphan"
    owner = json.loads((runtime_root / "managed-owner.json").read_text())
    assert owner["state"] == "owned"
    assert owner["pid"] == 46000
    await runtime.close()
    assert not service.running


@pytest.mark.asyncio
async def test_orphaned_spawn_without_verifiable_listener_fails_closed(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    runtime_root = config.memory.mindmemos_qdrant_service_root / "runtime"
    service = _FakeService(runtime_root)
    service.running = True
    service.sentinel = None
    runtime_root.joinpath("clients").mkdir(parents=True)
    import json
    import socket

    (runtime_root / "managed-owner.json").write_text(
        json.dumps(
            {
                "state": "starting",
                "hostname": socket.gethostname(),
                "home": str(config.memory.qdrant.home),
                "rest_port": 16333,
                "owner_token": "tok-orphan",
                "service_identity": "tok-orphan",
            }
        )
    )
    runtime = service.runtime(config, listener_pid=lambda port: None)
    with pytest.raises(ManagedQdrantError, match="pid was lost"):
        await runtime.start()
    assert service.spawn_calls == 0


@pytest.mark.asyncio
async def test_absent_with_live_starting_owner_waits_instead_of_spawning(
    tmp_path: Path,
) -> None:
    """pid alive + port absent = service still initializing after the
    starter died. The next process must wait for it, not double-spawn."""
    import json
    import socket

    config = _config(tmp_path)
    runtime_root = config.memory.mindmemos_qdrant_service_root / "runtime"
    service = _FakeService(runtime_root)
    service.alive_pids.add(47000)  # process alive but not listening yet
    runtime_root.joinpath("clients").mkdir(parents=True)
    (runtime_root / "managed-owner.json").write_text(
        json.dumps(
            {
                "state": "starting",
                "hostname": socket.gethostname(),
                "home": str(config.memory.qdrant.home),
                "rest_port": 16333,
                "owner_token": "tok-init",
                "service_identity": "tok-init",
                "pid": 47000,
                "process_start": "real-start",
            }
        )
    )
    probe_calls = 0

    started_up = {"v": False}

    async def delayed_probe() -> str:
        nonlocal probe_calls
        probe_calls += 1
        if probe_calls >= 3 and not started_up["v"]:
            started_up["v"] = True
            service.running = True
        return "qdrant" if service.running else "absent"

    runtime = service.runtime(config, probe=delayed_probe)
    await runtime.start()
    assert service.spawn_calls == 0
    assert service.sentinel is not None
    assert service.sentinel["owner_token"] == "tok-init"
    await runtime.close()


@pytest.mark.asyncio
async def test_unresponsive_port_fails_closed(tmp_path: Path) -> None:
    """A bound-but-hung port must never look 'absent' — spawning over it
    would race the wedged service for the storage dir."""
    config = _config(tmp_path)
    service = _FakeService(config.memory.mindmemos_qdrant_service_root / "runtime")

    async def wedged_probe() -> str:
        return "unresponsive"

    runtime = ManagedQdrantRuntime(
        config.memory,
        process_spawner=service.spawn,
        probe=wedged_probe,
        poll_interval_seconds=0.001,
    )
    with pytest.raises(ManagedQdrantError, match="accepts connections"):
        await runtime.start()
    assert service.spawn_calls == 0


@pytest.mark.asyncio
async def test_stop_escalates_sigterm_then_sigkill_with_identity(
    tmp_path: Path,
) -> None:
    """Stop must re-verify pid+start before each signal and escalate to
    SIGKILL when the TERM grace expires."""
    import signal

    config = _config(tmp_path)
    service = _FakeService(config.memory.mindmemos_qdrant_service_root / "runtime")
    signals: list[signal.Signals] = []
    killed = {"kill": False}

    def killer(pid: int, sig: signal.Signals) -> None:
        signals.append(sig)
        if sig == signal.SIGKILL:
            killed["kill"] = True
            service.alive_pids.discard(pid)
            service.running = False

    runtime = service.runtime(
        config, process_stopper=None, process_killer=killer
    )
    await runtime.start()
    await runtime.close()
    assert signal.SIGTERM in signals
    assert signal.SIGKILL in signals
    assert killed["kill"]


@pytest.mark.asyncio
async def test_dead_owned_owner_is_adopted_via_listener_pid(
    tmp_path: Path,
) -> None:
    """Sentinel ours + owner pid dead (service restarted elsewhere) —
    adopt the listener when it provably runs the configured binary so the
    last leaser can still stop the service."""
    import json
    import socket

    config = _config(tmp_path)
    runtime_root = config.memory.mindmemos_qdrant_service_root / "runtime"
    service = _FakeService(runtime_root)
    service.running = True
    service.pid = 49000
    service.alive_pids.add(49000)
    service.sentinel = {
        "runtime_root": str(runtime_root),
        "owner_token": "tok-dead",
    }
    runtime_root.joinpath("clients").mkdir(parents=True)
    (runtime_root / "managed-owner.json").write_text(
        json.dumps(
            {
                "state": "owned",
                "hostname": socket.gethostname(),
                "home": str(config.memory.qdrant.home),
                "rest_port": 16333,
                "owner_token": "tok-dead",
                "service_identity": "tok-dead",
                "pid": 999999,
                "process_start": "dead-start",
            }
        )
    )

    runtime = service.runtime(
        config,
        listener_pid=lambda port: 49000,
        binary_checker=lambda pid, binary: pid == 49000,
    )
    await runtime.start()
    owner = json.loads((runtime_root / "managed-owner.json").read_text())
    assert owner["state"] == "owned"
    assert owner["pid"] == 49000
    await runtime.close()
    assert not service.running


@pytest.mark.asyncio
async def test_close_does_not_kill_service_with_dead_owner_pid(
    tmp_path: Path,
) -> None:
    """Owner pid dead/recycled while *some* Qdrant still answers: the last
    leaser must not signal a foreign process — release the lease and drop
    the owner record instead."""
    config = _config(tmp_path)
    runtime_root = config.memory.mindmemos_qdrant_service_root / "runtime"
    service = _FakeService(runtime_root)
    runtime = service.runtime(config)
    await runtime.start()
    # The recorded service pid dies (or is recycled) before close while a
    # Qdrant still answers — the owner record can no longer prove the
    # listener is ours, so close must release without signalling.
    owner = runtime._read_owner()  # noqa: SLF001
    service.alive_pids.discard(int(owner["pid"]))
    service.alive_pids.add(48000)
    service.pid = 48000
    await runtime.close()
    # Nothing signalled, owner file dropped, lease gone.
    assert service.stop_calls == []
    assert not (runtime_root / "managed-owner.json").exists()
