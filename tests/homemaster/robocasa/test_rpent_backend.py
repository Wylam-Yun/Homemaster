"""RPentBackend unit tests — worker module stays importable in a bare
interpreter, so lifecycle helpers are exercised without the RPent stack."""

from __future__ import annotations

import pytest

from workers.robocasa_worker.rpent_backend import RPentBackend


class _FakeRpcClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def call(self, method: str) -> None:
        self.calls.append(method)


class _FakeVlaClient:
    def __init__(self, *, swept: bool = False) -> None:
        self._client = _FakeRpcClient()
        self.reset_calls = 0
        self._swept = swept

    def reset_session(self) -> None:
        self.reset_calls += 1
        if self._swept and self.reset_calls == 1:
            raise RuntimeError("session not found")


def test_reset_vla_session_normal_path_skips_register() -> None:
    backend = RPentBackend()
    vla = _FakeVlaClient()
    backend._vla_client = vla

    backend._reset_vla_session()

    assert vla.reset_calls == 1
    assert vla._client.calls == []


def test_reset_vla_session_recovers_idle_swept_session() -> None:
    """vla_server sweeps sessions idle past ``session_timeout_s``; a stale
    session id surfaces as "session not found" and must be re-registered
    before the retrying reset — the recovery that keeps warm-server cell
    reuse alive across the inter-cell finalization gap."""
    backend = RPentBackend()
    vla = _FakeVlaClient(swept=True)
    backend._vla_client = vla

    backend._reset_vla_session()

    assert vla._client.calls == ["session.register"]
    assert vla.reset_calls == 2


def test_reset_vla_session_reraises_unrelated_errors() -> None:
    class _Refused:
        def __init__(self) -> None:
            self._client = _FakeRpcClient()

        def reset_session(self) -> None:
            raise RuntimeError("connection refused")

    backend = RPentBackend()
    refused = _Refused()
    backend._vla_client = refused

    with pytest.raises(RuntimeError, match="connection refused"):
        backend._reset_vla_session()
    assert refused._client.calls == []
