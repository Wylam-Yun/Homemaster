"""Locate or spawn the long-lived ``homemaster serve`` runtime.

``shell`` and thin-client ``run`` both resolve a server through
:func:`ensure_server`:

* ``HOMEMASTER_SERVER`` (or an explicit ``--server`` value) set → connect only;
  the client never spawns against an explicitly managed endpoint.
* unset → probe ``GET /api/meta`` on ``http://127.0.0.1:8000``; on refusal spawn
  ``python -m homemaster serve --port <free> [--config <cfg>]`` on a free
  loopback port and poll until ready (hermes-style auto-spawn).

Spawned processes are tracked module-wide and terminated via ``atexit`` plus
an explicit :func:`stop_server` for deterministic teardown.
"""

from __future__ import annotations

import atexit
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from homemaster.cli.client import ServerError, ServerUnavailableError

DEFAULT_SERVER_URL = "http://127.0.0.1:8000"
READY_TIMEOUT_S = 15.0
_PROBE_TIMEOUT_S = 1.5
_POLL_INTERVAL_S = 0.2

_spawned: list[subprocess.Popen[Any]] = []
_atexit_installed = False

ProbeFn = Callable[[str], tuple[bool, dict[str, Any] | None]]


def ensure_server(
    server: str | None = None,
    *,
    config_path: str | Path | None = None,
    ready_timeout_s: float = READY_TIMEOUT_S,
    probe: ProbeFn | None = None,
    spawn: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
) -> tuple[str, subprocess.Popen[Any] | None]:
    """Return ``(base_url, proc)`` — ``proc`` is None for a found/explicit server."""

    probe = probe or probe_server
    explicit = (server or os.environ.get("HOMEMASTER_SERVER") or "").strip()
    if explicit:
        base = _normalize_url(explicit)
        alive, _meta = probe(base)
        if not alive:
            raise ServerUnavailableError(
                f"HomeMaster server at {base} is not reachable "
                f"(HOMEMASTER_SERVER is set, so no local server was spawned)"
            )
        return base, None
    alive, _meta = probe(DEFAULT_SERVER_URL)
    if alive:
        return DEFAULT_SERVER_URL, None
    return _spawn_server(
        config_path=config_path,
        ready_timeout_s=ready_timeout_s,
        probe=probe,
        spawn=spawn,
    )


def probe_server(
    base_url: str, *, timeout_s: float = _PROBE_TIMEOUT_S
) -> tuple[bool, dict[str, Any] | None]:
    """True when ``base_url`` answers like a HomeMaster server.

    ``/api/meta`` 200 is the canonical readiness signal; older servers without
    it still count when ``/api/sessions`` answers (200 or a typed 4xx — the
    point is "a HomeMaster HTTP API is listening", not the payload shape).
    """

    try:
        response = httpx.get(f"{base_url}/api/meta", timeout=timeout_s)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
        return False, None
    except OSError:
        return False, None
    if response.status_code == 200:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return True, payload if isinstance(payload, dict) else None
    if response.status_code < 500:
        try:
            sessions = httpx.get(f"{base_url}/api/sessions", timeout=timeout_s)
        except (httpx.HTTPError, OSError):
            return False, None
        if sessions.status_code == 200:
            return True, None
    return False, None


def _normalize_url(value: str) -> str:
    base = value.strip().rstrip("/")
    if not base:
        raise ServerUnavailableError("empty server URL")
    if base.startswith(("ws://", "wss://")):
        base = ("http://" if base.startswith("ws://") else "https://") + base.split("://", 1)[1]
    if not base.startswith(("http://", "https://")):
        base = "http://" + base
    return base


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _spawn_server(
    *,
    config_path: str | Path | None,
    ready_timeout_s: float,
    probe: ProbeFn,
    spawn: Callable[..., subprocess.Popen[Any]],
) -> tuple[str, subprocess.Popen[Any]]:
    global _atexit_installed
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    cmd = [sys.executable, "-m", "homemaster", "serve", "--port", str(port)]
    cfg = config_path or os.environ.get("HOMEMASTER_CONFIG_PATH", "").strip() or None
    if cfg:
        cmd += ["--config", str(cfg)]
    # The child's stderr lands in a temp log: silent in the happy path,
    # diagnosable when the runtime fails to come up.
    log_file = tempfile.NamedTemporaryFile(
        mode="w+b",
        prefix="homemaster-server-",
        suffix=".log",
        delete=False,
    )
    try:
        proc = spawn(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=log_file,
        )
    except OSError as exc:
        log_file.close()
        raise ServerUnavailableError(
            f"could not spawn a local HomeMaster server ({cmd!r}): {exc}"
        ) from exc
    deadline = time.monotonic() + ready_timeout_s
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise ServerUnavailableError(
                    "spawned HomeMaster server exited before becoming ready "
                    f"(exit {proc.returncode}); log: {log_file.name}"
                )
            alive, _meta = probe(base)
            if alive:
                _spawned.append(proc)
                if not _atexit_installed:
                    atexit.register(_terminate_all_spawned)
                    _atexit_installed = True
                return base, proc
            time.sleep(_POLL_INTERVAL_S)
    except BaseException:
        _terminate(proc)
        raise
    _terminate(proc)
    raise ServerUnavailableError(
        f"spawned HomeMaster server did not answer within {ready_timeout_s:.0f}s; "
        f"log: {log_file.name}"
    )


def stop_server(proc: subprocess.Popen[Any] | None) -> None:
    """Terminate one spawned server (no-op for found/explicit servers)."""

    if proc is None:
        return
    if proc in _spawned:
        _spawned.remove(proc)
    _terminate(proc)


def _terminate(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            return
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _terminate_all_spawned() -> None:
    for proc in list(_spawned):
        _terminate(proc)
    _spawned.clear()


__all__ = [
    "DEFAULT_SERVER_URL",
    "READY_TIMEOUT_S",
    "ServerError",
    "ServerUnavailableError",
    "ensure_server",
    "probe_server",
    "stop_server",
]
