"""HomeMaster-owned lifecycle for one user-private Qdrant service.

Mirrors :class:`ManagedNeo4jRuntime`: ``memory.qdrant.mode=managed_local``
makes the first HomeMaster process spawn the configured ``qdrant`` binary,
later processes attach to the already-running service, and the last lease
holder stops it. Coordination lives in ``mindmemos/qdrant_service/runtime``
(manager lock + ``managed-owner.json`` + per-client lease files), exactly
like the Neo4j runtime — no docker, no root, no systemd unit required.

Two failure classes are distinguished explicitly:

- *absent* — nothing on the loopback port: spawn our own binary.
- *foreign* — something answers on the port but is not verifiably the
  service this memory root owns (non-Qdrant HTTP, or a Qdrant serving a
  different storage root). Fail closed rather than silently attaching to
  somebody else's vector store.

Ownership is verified two ways: the owner file records the spawned server
PID plus its process-start identity, and a sentinel point inside the
Qdrant data itself records ``runtime_root`` — surviving the case where the
owner file is lost while the service keeps running.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from homemaster.config import MemoryConfig
from homemaster.memory.managed_neo4j import (
    _process_identity_is_alive,
    _process_start_identity,
)

ProcessSpawner = Callable[[], "subprocess.Popen[bytes]"]
Probe = Callable[[], Awaitable[str]]
SentinelReader = Callable[[], Awaitable[Mapping[str, Any] | None]]
SentinelWriter = Callable[[str], Awaitable[None]]
ProcessProbe = Callable[[int, str], bool]
ProcessStopper = Callable[[int], Awaitable[None]]
ProcessKiller = Callable[[int, signal.Signals], None]
ListenerPid = Callable[[int], "int | None"]

_SENTINEL_COLLECTION = "_homemaster_runtime"
_SENTINEL_POINT_ID = 1

# Probe states:
#   "absent"       — nothing accepts connections on the loopback port
#   "qdrant"       — a Qdrant HTTP API answers on the port
#   "foreign"      — something answers but it is not a Qdrant API
#   "unresponsive" — the port is bound (connect succeeds) but the HTTP API
#                    does not answer within the probe timeout — e.g. a wedged
#                    or still-initializing service. Never treated as absent.
_ABSENT = "absent"
_QDRANT = "qdrant"
_FOREIGN = "foreign"
_UNRESPONSIVE = "unresponsive"


class ManagedQdrantError(RuntimeError):
    """Managed Qdrant could not reach a verified lifecycle state."""


class ManagedQdrantRuntime:
    """Share one private Qdrant service across local HomeMaster processes."""

    def __init__(
        self,
        config: MemoryConfig,
        *,
        process_spawner: ProcessSpawner | None = None,
        probe: Probe | None = None,
        sentinel_reader: SentinelReader | None = None,
        sentinel_writer: SentinelWriter | None = None,
        process_probe: ProcessProbe | None = None,
        process_stopper: ProcessStopper | None = None,
        process_killer: ProcessKiller | None = None,
        listener_pid: ListenerPid | None = None,
        binary_checker: Callable[[int, Path], bool] | None = None,
        poll_interval_seconds: float = 0.2,
    ) -> None:
        if config.qdrant.mode != "managed_local":
            raise ValueError("ManagedQdrantRuntime requires memory.qdrant.mode=managed_local")
        self._config = config
        self._qdrant = config.qdrant
        self._service_root = config.mindmemos_qdrant_service_root
        self._runtime_root = self._service_root / "runtime"
        self._storage_path = self._service_root / "storage"
        self._logs_root = self._runtime_root / "logs"
        self._clients_root = self._runtime_root / "clients"
        self._lock_path = self._runtime_root / "manager.lock"
        self._owner_path = self._runtime_root / "managed-owner.json"
        self._lease_path = self._clients_root / f"{os.getpid()}-{uuid4().hex}.json"
        self._rest_url = f"http://127.0.0.1:{self._qdrant.rest_port}"
        self._spawner = process_spawner
        self._probe = probe
        self._sentinel_reader = sentinel_reader
        self._sentinel_writer = sentinel_writer
        self._process_probe = process_probe or _process_identity_is_alive
        self._process_stopper = process_stopper
        self._process_killer = process_killer or os.killpg
        self._listener_pid = listener_pid or _listener_pid
        self._pid_is_our_binary = binary_checker or _pid_is_our_binary
        self._poll_interval_seconds = poll_interval_seconds
        self._spawned: subprocess.Popen[bytes] | None = None
        self._reaper_task: asyncio.Task[int] | None = None
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    async def start(self) -> None:
        if self._started:
            return
        binary = self._validate_installation()
        self._prepare_runtime_root()
        async with self._locked():
            self._prune_stale_leases()
            state = await self._probe_port()
            if state == _ABSENT:
                owner = self._read_owner()
                if (
                    owner is not None
                    and self._owner_matches_config(owner)
                    and self._owner_process_alive(owner)
                ):
                    # A previous starter died after spawning but before the
                    # service came up — its recorded pid is still alive, so
                    # wait for it rather than spawning a competitor. Keep the
                    # owner file on timeout: it is the only record of that pid.
                    await self._wait_until_listening(timeout=self._qdrant.start_timeout_seconds)
                    state = await self._probe_port()
            if state == _ABSENT:
                self._owner_path.unlink(missing_ok=True)
                owner_token = uuid4().hex
                # Record intent BEFORE spawn: a crash between spawn and the
                # pid write-back below is then recoverable via the
                # ``starting`` marker plus listener-pid verification.
                self._write_owner(state="starting", owner_token=owner_token)
                try:
                    self._spawned = self._spawn(binary)
                    # Record the server PID immediately: if this process dies
                    # between ready and the sentinel write, the next starter
                    # can still prove the running service is ours and repair.
                    self._write_owner(
                        state="starting",
                        owner_token=owner_token,
                        pid=self._spawned.pid,
                        process_start=_process_start_identity(self._spawned.pid),
                    )
                    await self._wait_until_ready_or_dead(
                        self._spawned,
                        timeout=self._qdrant.start_timeout_seconds,
                    )
                    # Stamp ownership inside the served data itself so later
                    # processes can tell "our storage" from a foreign Qdrant
                    # even if the owner file was lost.
                    await self._claim_sentinel(owner_token)
                    # The service could have died post-readiness with a
                    # foreign Qdrant rebinding the port before the claim —
                    # fail closed rather than trusting that storage.
                    if self._spawned.poll() is not None:
                        raise ManagedQdrantError(
                            "Qdrant died after readiness was confirmed; "
                            "the service now on the port is not verifiably "
                            "ours"
                        )
                except BaseException:
                    if self._spawned is not None:
                        await self._terminate_spawned(self._spawned)
                    self._owner_path.unlink(missing_ok=True)
                    raise
                self._write_owner(
                    state="owned",
                    owner_token=owner_token,
                    pid=self._spawned.pid,
                    process_start=_process_start_identity(self._spawned.pid),
                )
            elif state == _QDRANT:
                sentinel = await self._read_sentinel()
                identity = self._identity_from_sentinel(sentinel)
                if identity is not None:
                    await self._validate_or_recover_owner(identity)
                    if not self._owner_path.is_file():
                        # Sentinel says the storage is ours but the owner
                        # record is unusable (dead/stale pid) — adopt the
                        # listener when the port owner provably runs the
                        # configured binary, else attach lease-only.
                        adopted = self._listener_pid(self._qdrant.rest_port)
                        if adopted is not None and self._pid_is_our_binary(adopted, binary):
                            self._write_owner(
                                state="owned",
                                owner_token=identity,
                                pid=adopted,
                                process_start=_process_start_identity(adopted),
                            )
                else:
                    if sentinel is not None:
                        raise ManagedQdrantError(
                            f"port {self._qdrant.rest_port} serves a Qdrant that "
                            "belongs to a different memory root "
                            f"({sentinel.get('runtime_root')!r}); set "
                            "memory.qdrant.rest_port to a free port or stop "
                            "that service"
                        )
                    owner = self._read_owner()
                    if (
                        owner is not None
                        and self._owner_matches_config(owner)
                        and self._owner_process_alive(owner)
                    ):
                        # We spawned it but crashed before the sentinel write
                        # — reclaim and repair rather than fail.
                        await self._claim_sentinel(str(owner["owner_token"]))
                        if owner.get("state") != "owned":
                            self._write_owner(
                                state="owned",
                                owner_token=str(owner["owner_token"]),
                                pid=int(owner["pid"]),
                                process_start=str(owner["process_start"]),
                            )
                    elif (
                        owner is not None
                        and owner.get("state") == "starting"
                        and self._owner_matches_config(owner)
                    ):
                        # Starter died between spawn and the pid write-back —
                        # adopt the listener only when the port owner provably
                        # runs the configured binary.
                        adopted = self._listener_pid(self._qdrant.rest_port)
                        if adopted is not None and self._pid_is_our_binary(adopted, binary):
                            token = str(owner["owner_token"])
                            await self._claim_sentinel(token)
                            self._write_owner(
                                state="owned",
                                owner_token=token,
                                pid=adopted,
                                process_start=_process_start_identity(adopted),
                            )
                        else:
                            raise ManagedQdrantError(
                                f"port {self._qdrant.rest_port} serves a "
                                "Qdrant whose owner record marks it as "
                                "starting but whose pid was lost — possibly a "
                                "HomeMaster-spawned service we can no longer "
                                "verify. Stop it manually or change "
                                "memory.qdrant.rest_port"
                            )
                    else:
                        raise ManagedQdrantError(
                            f"port {self._qdrant.rest_port} serves a Qdrant that "
                            "does not belong to this memory root; stop it or "
                            "change memory.qdrant.rest_port"
                        )
            elif state == _UNRESPONSIVE:
                raise ManagedQdrantError(
                    f"port {self._qdrant.rest_port} accepts connections but "
                    "does not answer the Qdrant API — a hung service may "
                    "hold it; investigate or change memory.qdrant.rest_port"
                )
            else:
                raise ManagedQdrantError(
                    f"port {self._qdrant.rest_port} is already bound by a "
                    "non-Qdrant service; change memory.qdrant.rest_port"
                )
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
                if owner is not None and await self._owns_current_service(owner):
                    pid = owner.get("pid")
                    if isinstance(pid, int):
                        await self._stop_service(pid, str(owner.get("process_start") or "unknown"))
                self._owner_path.unlink(missing_ok=True)
            self._started = False
        if self._spawned is not None:
            # Reap the spawn handle if the service is gone; when other leases
            # still hold it the detached child keeps running — park a reaper
            # task so it does not turn into a zombie when it eventually exits.
            proc = self._spawned
            self._spawned = None
            if proc.poll() is None:
                self._reaper_task = asyncio.ensure_future(asyncio.to_thread(proc.wait))
            else:
                proc.wait()

    # -- installation / filesystem -------------------------------------------------

    def _validate_installation(self) -> Path:
        home = self._qdrant.home
        if home is None:
            raise ManagedQdrantError(
                "managed_local Qdrant requires memory.qdrant.home pointing at "
                "the qdrant installation directory or binary"
            )
        candidates = [home] if home.is_file() else [home / "qdrant", home / "bin" / "qdrant"]
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        raise ManagedQdrantError(f"Qdrant executable is missing under memory.qdrant.home: {home}")

    def _prepare_runtime_root(self) -> None:
        self._clients_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._logs_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._storage_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._service_root, 0o700)
        os.chmod(self._runtime_root, 0o700)
        os.chmod(self._clients_root, 0o700)
        os.chmod(self._logs_root, 0o700)
        os.chmod(self._storage_path, 0o700)

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

        threading.Thread(target=acquire, name="homemaster-qdrant-lock", daemon=True).start()
        try:
            cancellation: asyncio.CancelledError | None = None
            while True:
                try:
                    await asyncio.shield(acquired)
                    break
                except asyncio.CancelledError as exc:
                    cancellation = exc
                    if not acquired.done():
                        continue
                    break
            if cancellation is not None:
                raise cancellation
        except BaseException:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            raise
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    # -- spawn / stop ---------------------------------------------------------------

    def _spawn(self, binary: Path) -> subprocess.Popen[bytes]:
        if self._spawner is not None:
            return self._spawner()
        env = dict(os.environ)
        env.update(
            {
                "QDRANT__STORAGE__STORAGE_PATH": str(self._storage_path),
                "QDRANT__SERVICE__HOST": "127.0.0.1",
                "QDRANT__SERVICE__HTTP_PORT": str(self._qdrant.rest_port),
                "QDRANT__SERVICE__GRPC_PORT": str(self._qdrant.grpc_port),
                "QDRANT__TELEMETRY_DISABLED": "true",
                "QDRANT__LOG_LEVEL": "info",
            }
        )
        stdout_path = self._logs_root / "qdrant.out.log"
        stderr_path = self._logs_root / "qdrant.err.log"
        stdout = stdout_path.open("ab")
        stderr = stderr_path.open("ab")
        try:
            return subprocess.Popen(
                [str(binary)],
                cwd=str(self._service_root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
        except BaseException:
            stdout.close()
            stderr.close()
            raise

    async def _wait_until_ready_or_dead(
        self,
        proc: subprocess.Popen[bytes],
        *,
        timeout: float,
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            returncode = proc.poll()
            if returncode is not None:
                raise ManagedQdrantError(
                    f"Qdrant exited during startup with code {returncode}: " + self._stderr_tail()
                )
            if await self._probe_port() == _QDRANT:
                return
            if loop.time() >= deadline:
                raise ManagedQdrantError(
                    f"Qdrant did not become ready within {timeout:g}s: " + self._stderr_tail()
                )
            await asyncio.sleep(self._poll_interval_seconds)

    async def _wait_until_listening(self, *, timeout: float) -> None:
        """Wait until *anything* answers the port — used when a recorded
        service pid is still alive but the HTTP API has not come up yet."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            if await self._probe_port() != _ABSENT:
                return
            if loop.time() >= deadline:
                raise ManagedQdrantError(
                    f"recorded Qdrant pid is alive but the service did not "
                    f"listen within {timeout:g}s: " + self._stderr_tail()
                )
            await asyncio.sleep(self._poll_interval_seconds)

    async def _wait_for_absent(self, *, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            if await self._probe_port() == _ABSENT:
                return
            if loop.time() >= deadline:
                raise ManagedQdrantError(f"Qdrant did not stop within {timeout:g}s")
            await asyncio.sleep(self._poll_interval_seconds)

    async def _terminate_spawned(self, proc: subprocess.Popen[bytes]) -> None:
        try:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
            await asyncio.to_thread(proc.wait)
        except (OSError, ProcessLookupError):
            pass

    async def _stop_service(self, pid: int, process_start: str) -> None:
        if self._process_stopper is not None:
            await self._process_stopper(pid)
            await self._wait_for_absent(timeout=self._qdrant.stop_timeout_seconds)
            return
        # ``start_new_session`` made the spawned service its own process
        # group leader, so pgid == pid reaches the whole group. Re-check the
        # pid+start identity immediately before each signal — if the service
        # already exited and the pid was recycled, signalling must not reach
        # an unrelated process group.
        try:
            if self._process_probe(pid, process_start):
                self._process_killer(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        loop = asyncio.get_running_loop()
        deadline = loop.time() + min(self._qdrant.stop_timeout_seconds, 10.0)
        while loop.time() < deadline:
            if not self._process_probe(pid, process_start):
                break
            await asyncio.sleep(self._poll_interval_seconds)
        try:
            if self._process_probe(pid, process_start):
                self._process_killer(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        await self._wait_for_absent(timeout=self._qdrant.stop_timeout_seconds)
        if self._spawned is not None and self._spawned.pid == pid:
            await asyncio.to_thread(self._spawned.wait)

    def _stderr_tail(self) -> str:
        path = self._logs_root / "qdrant.err.log"
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 16384))
                text = handle.read().decode("utf-8", errors="replace")
        except OSError:
            return "no stderr log captured"
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return " | ".join(lines[-6:]) if lines else "no stderr output"

    # -- HTTP probes / sentinel -------------------------------------------------------

    async def _probe_port(self) -> str:
        """Classify what listens on the loopback REST port.

        ``ConnectError`` (nothing bound) maps to absent; every other
        transport failure (timeouts, resets on a bound socket) maps to
        ``unresponsive`` — a hung service must never look "absent", or the
        spawn path would try to bind over it.
        """
        if self._probe is not None:
            return await self._probe()
        import httpx

        try:
            async with httpx.AsyncClient(
                base_url=self._rest_url, timeout=2.0, trust_env=False
            ) as client:
                response = await client.get("/collections")
        except httpx.ConnectError:
            return _ABSENT
        except httpx.HTTPError:
            return _UNRESPONSIVE
        if response.status_code != 200:
            return _FOREIGN
        try:
            body = response.json()
        except ValueError:
            return _FOREIGN
        result = body.get("result") if isinstance(body, dict) else None
        if isinstance(result, dict) and isinstance(result.get("collections"), list):
            return _QDRANT
        return _FOREIGN

    async def _read_sentinel(self) -> Mapping[str, Any] | None:
        if self._sentinel_reader is not None:
            return await self._sentinel_reader()
        import httpx

        try:
            async with httpx.AsyncClient(
                base_url=self._rest_url, timeout=2.0, trust_env=False
            ) as client:
                response = await client.get(
                    f"/collections/{_SENTINEL_COLLECTION}/points/{_SENTINEL_POINT_ID}"
                )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        result = body.get("result") if isinstance(body, dict) else None
        payload = result.get("payload") if isinstance(result, dict) else None
        return payload if isinstance(payload, dict) else None

    async def _service_identity(self) -> str | None:
        """Return the owner token when the served storage is verifiably ours."""
        return self._identity_from_sentinel(await self._read_sentinel())

    def _identity_from_sentinel(self, sentinel: Mapping[str, Any] | None) -> str | None:
        if sentinel is None:
            return None
        if sentinel.get("runtime_root") != str(self._runtime_root):
            return None
        token = sentinel.get("owner_token")
        return str(token) if token else None

    async def _claim_sentinel(self, owner_token: str) -> None:
        if self._sentinel_writer is not None:
            await self._sentinel_writer(owner_token)
            return
        import httpx

        async with httpx.AsyncClient(
            base_url=self._rest_url, timeout=5.0, trust_env=False
        ) as client:
            create = await client.put(
                f"/collections/{_SENTINEL_COLLECTION}",
                json={"vectors": {"size": 1, "distance": "Dot"}},
            )
            if create.status_code not in (200, 409):
                # Some versions answer 400 for "collection already exists" —
                # confirm existence before failing the claim.
                exists = await client.get(f"/collections/{_SENTINEL_COLLECTION}")
                if exists.status_code != 200:
                    raise ManagedQdrantError(
                        f"could not create ownership sentinel: HTTP {create.status_code}"
                    )
            upsert = await client.put(
                f"/collections/{_SENTINEL_COLLECTION}/points",
                params={"wait": "true"},
                json={
                    "points": [
                        {
                            "id": _SENTINEL_POINT_ID,
                            "vector": [0.0],
                            "payload": {
                                "runtime_root": str(self._runtime_root),
                                "owner_token": owner_token,
                            },
                        }
                    ]
                },
            )
            if upsert.status_code != 200:
                raise ManagedQdrantError(
                    f"could not write ownership sentinel: HTTP {upsert.status_code}"
                )
        sentinel = await self._read_sentinel()
        if (
            sentinel is None
            or sentinel.get("runtime_root") != str(self._runtime_root)
            or sentinel.get("owner_token") != owner_token
        ):
            raise ManagedQdrantError(
                "ownership sentinel readback mismatch — refusing to trust "
                "the service on port "
                f"{self._qdrant.rest_port}"
            )

    # -- owner file / leases -----------------------------------------------------------

    async def _validate_or_recover_owner(self, service_identity: str) -> None:
        owner = self._read_owner()
        if owner is None:
            return
        if (
            owner.get("service_identity") == service_identity
            and self._owner_matches_config(owner)
            and self._owner_process_alive(owner)
        ):
            if owner.get("state") != "owned":
                # Starter died between the sentinel claim and the owned
                # write — pid+start plus the sentinel token both prove it is
                # ours, so promote the record rather than orphan the service
                # forever (nobody could ever stop it again otherwise).
                self._write_owner(
                    state="owned",
                    owner_token=str(owner["owner_token"]),
                    pid=int(owner["pid"]),
                    process_start=str(owner["process_start"]),
                )
            return
        self._owner_path.unlink(missing_ok=True)

    async def _owns_current_service(self, owner: Mapping[str, Any] | None) -> bool:
        if owner is None or owner.get("state") != "owned":
            return False
        if not self._owner_matches_config(owner) or not self._owner_process_alive(owner):
            return False
        if await self._probe_port() != _QDRANT:
            return False
        service_identity = await self._service_identity()
        return service_identity is not None and owner.get("service_identity") == service_identity

    def _owner_matches_config(self, owner: Mapping[str, Any]) -> bool:
        return (
            owner.get("hostname") == socket.gethostname()
            and owner.get("home") == str(self._qdrant.home)
            and owner.get("rest_port") in (None, self._qdrant.rest_port)
            and isinstance(owner.get("owner_token"), str)
        )

    def _owner_process_alive(self, owner: Mapping[str, Any]) -> bool:
        pid = owner.get("pid")
        process_start = owner.get("process_start")
        if not isinstance(pid, int) or process_start is None:
            return False
        return self._process_probe(pid, str(process_start))

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
        pid: int | None = None,
        process_start: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "state": state,
            "hostname": socket.gethostname(),
            "home": str(self._qdrant.home),
            "rest_port": self._qdrant.rest_port,
            "owner_token": owner_token,
            "service_identity": owner_token,
        }
        if pid is not None:
            payload["pid"] = pid
        if process_start is not None:
            payload["process_start"] = process_start
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

    @staticmethod
    def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(dict(payload), ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _pid_is_our_binary(pid: int, binary: Path) -> bool:
    """Verify the listener's executable resolves to the configured qdrant
    binary — the only identity proof left when the owner record lost its
    pid."""
    resolved = binary.resolve()
    try:
        exe = Path(f"/proc/{pid}/exe").resolve()
        if exe == resolved:
            return True
    except OSError:
        pass
    try:
        result = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    argv0 = result.stdout.strip().split(" ", 1)[0]
    if not argv0:
        return False
    try:
        return Path(argv0).resolve() == resolved
    except OSError:
        return False


def _listener_pid(port: int) -> int | None:
    """Best-effort lookup of the single process listening on a TCP port.

    Used only in the crashed-starter recovery path (spawn → pid write-back
    window); returns None when zero or several pids hold the socket —
    ambiguous listeners must fail closed, not guess.
    """
    pids = _listener_pids_from_proc(port) or _listener_pids_from_lsof(port)
    return next(iter(pids), None) if len(pids) == 1 else None


def _listener_pids_from_proc(port: int) -> set[int]:
    inodes: set[str] = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10 or fields[3] != "0A":  # LISTEN state
                continue
            try:
                if int(fields[1].rsplit(":", 1)[-1], 16) == port:
                    inodes.add(fields[9])
            except ValueError:
                continue
    if not inodes:
        return set()
    pids: set[int] = set()
    try:
        processes = [p for p in Path("/proc").iterdir() if p.name.isdigit()]
    except OSError:
        return set()
    for process in processes:
        try:
            descriptors = list((process / "fd").iterdir())
        except OSError:
            continue
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except OSError:
                continue
            if target.startswith("socket:[") and target[8:-1] in inodes:
                pids.add(int(process.name))
                break
    return pids


def _listener_pids_from_lsof(port: int) -> set[int]:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode != 0:
        return set()
    return {
        int(line[1:])
        for line in result.stdout.splitlines()
        if line.startswith("p") and line[1:].isdigit()
    }


__all__ = ["ManagedQdrantError", "ManagedQdrantRuntime"]
