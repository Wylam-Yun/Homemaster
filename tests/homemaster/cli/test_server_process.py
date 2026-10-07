"""Tests for server discovery/spawn lifecycle (ensure_server)."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from homemaster.cli.client import ServerUnavailableError
from homemaster.cli.server_process import (
    DEFAULT_SERVER_URL,
    ensure_server,
    stop_server,
)


class FakeProc:
    """Popen-shaped stub: alive until terminate/kill lands."""

    def __init__(self) -> None:
        self.args: list[str] | None = None
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def _prober(alive_urls: set[str]):
    def probe(url: str) -> tuple[bool, dict[str, Any] | None]:
        return (url in alive_urls, {"version": "test"} if url in alive_urls else None)

    return probe


def test_explicit_server_is_connect_only_and_never_spawns(monkeypatch) -> None:
    spawned = []
    monkeypatch.setenv("HOMEMASTER_SERVER", "http://remote.test:9000")

    def spawn(*args: Any, **kwargs: Any) -> FakeProc:
        spawned.append((args, kwargs))
        return FakeProc()

    url, proc = ensure_server(
        probe=_prober({"http://remote.test:9000"}), spawn=spawn
    )
    assert url == "http://remote.test:9000"
    assert proc is None
    assert spawned == []


def test_explicit_server_unreachable_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("HOMEMASTER_SERVER", "http://remote.test:9000")
    with pytest.raises(ServerUnavailableError):
        ensure_server(probe=_prober(set()), spawn=lambda *a, **k: FakeProc())


def test_default_server_probe_hit_skips_spawn(monkeypatch) -> None:
    monkeypatch.delenv("HOMEMASTER_SERVER", raising=False)
    spawned = []
    url, proc = ensure_server(
        probe=_prober({DEFAULT_SERVER_URL}),
        spawn=lambda *a, **k: spawned.append(a) or FakeProc(),
    )
    assert url == DEFAULT_SERVER_URL
    assert proc is None
    assert spawned == []


def test_spawn_uses_python_m_homemaster_serve_on_free_port(monkeypatch) -> None:
    monkeypatch.delenv("HOMEMASTER_SERVER", raising=False)
    monkeypatch.delenv("HOMEMASTER_CONFIG_PATH", raising=False)
    procs: list[FakeProc] = []
    spawned_cmds: list[list[str]] = []

    def spawn(cmd, **kwargs):
        spawned_cmds.append(list(cmd))
        proc = FakeProc()
        procs.append(proc)
        return proc

    def probe(url: str):
        # The first probe is the default-server check (miss); the spawn target
        # answers immediately so ensure_server returns it.
        return (url != DEFAULT_SERVER_URL, None)

    url, proc = ensure_server(probe=probe, spawn=spawn)
    assert proc is procs[0]
    cmd = spawned_cmds[0]
    assert cmd[:3] == [sys.executable, "-m", "homemaster"]
    assert "serve" in cmd
    assert "--port" in cmd
    assert int(cmd[cmd.index("--port") + 1]) > 0
    assert url == f"http://127.0.0.1:{cmd[cmd.index('--port') + 1]}"


def test_spawn_forwards_config_path(monkeypatch) -> None:
    monkeypatch.delenv("HOMEMASTER_SERVER", raising=False)
    monkeypatch.delenv("HOMEMASTER_CONFIG_PATH", raising=False)
    cmds: list[list[str]] = []

    def spawn(cmd, **kwargs):
        cmds.append(list(cmd))
        return FakeProc()

    ensure_server(
        config_path="/tmp/hm.yaml",
        probe=lambda url: (url != DEFAULT_SERVER_URL, None),
        spawn=spawn,
    )
    cmd = cmds[0]
    assert cmd[cmd.index("--config") + 1] == "/tmp/hm.yaml"


def test_spawn_uses_homemaster_config_path_env(monkeypatch) -> None:
    monkeypatch.delenv("HOMEMASTER_SERVER", raising=False)
    monkeypatch.setenv("HOMEMASTER_CONFIG_PATH", "/tmp/env-cfg.yaml")
    cmds: list[list[str]] = []

    def spawn(cmd, **kwargs):
        cmds.append(list(cmd))
        return FakeProc()

    ensure_server(
        probe=lambda url: (url != DEFAULT_SERVER_URL, None),
        spawn=spawn,
    )
    cmd = cmds[0]
    assert cmd[cmd.index("--config") + 1] == "/tmp/env-cfg.yaml"


def test_spawn_timeout_raises_unavailable(monkeypatch) -> None:
    monkeypatch.delenv("HOMEMASTER_SERVER", raising=False)

    with pytest.raises(ServerUnavailableError, match="did not answer"):
        ensure_server(
            probe=lambda url: (False, None),
            spawn=lambda *a, **k: FakeProc(),
            ready_timeout_s=0.3,
        )


def test_spawn_child_exit_raises_with_log_hint(monkeypatch) -> None:
    monkeypatch.delenv("HOMEMASTER_SERVER", raising=False)

    def spawn(*a: Any, **k: Any) -> FakeProc:
        proc = FakeProc()
        proc.returncode = 3
        return proc

    with pytest.raises(ServerUnavailableError, match="exit"):
        ensure_server(
            probe=lambda url: (False, None),
            spawn=spawn,
            ready_timeout_s=5.0,
        )


def test_stop_server_terminates_spawned_proc(monkeypatch) -> None:
    monkeypatch.delenv("HOMEMASTER_SERVER", raising=False)
    procs: list[FakeProc] = []

    def spawn(cmd, **kwargs):
        proc = FakeProc()
        procs.append(proc)
        return proc

    _url, proc = ensure_server(
        probe=lambda url: (url != DEFAULT_SERVER_URL, None),
        spawn=spawn,
    )
    assert proc is not None
    stop_server(proc)
    assert procs[0].terminated is True
