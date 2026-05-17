"""Tests for MimoDecisionClient protocol and LiveMimoDecisionClient."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = str(Path(__file__).resolve().parents[2] / "src")


def _run(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env={**__import__("os").environ, "PYTHONPATH": _REPO_ROOT},
    )


def test_live_client_fail_fast_without_key() -> None:
    """LiveMimoDecisionClient must fail fast if provider has no valid config."""
    script = '''
from homemaster.providers.mimo_decision_client import LiveMimoDecisionClient
from homemaster.runtime import ProviderConfig

# Provider with empty api_keys — should fail on construction or decide()
try:
    config = ProviderConfig(
        name="test",
        base_url="http://localhost",
        model="test",
        api_keys=(),
        protocol="openai",
    )
    client = LiveMimoDecisionClient(config)
    # If construction succeeds, decide() should fail
    decision = client.decide(context={}, tools=[], settings=None)
    print(f"UNEXPECTED_SUCCESS: {decision}")
except Exception as e:
    print(f"FAIL_FAST: {type(e).__name__}: {e}")
'''
    result = _run(script)
    # Should fail fast, not return a deterministic fallback
    assert "FAIL_FAST" in result.stdout or result.returncode != 0, (
        f"Expected fail fast, got: {result.stdout}\n{result.stderr}"
    )


def test_protocol_interface() -> None:
    """MimoDecisionClient protocol must have decide() method."""
    from homemaster.providers.mimo_decision_client import MimoDecisionClient

    assert hasattr(MimoDecisionClient, "decide")
