"""Tests for typed model and context configuration."""

from __future__ import annotations

import json
from pathlib import Path

from homemaster.config.model_config import (
    ContextPolicyConfig,
    HomeMasterConfig,
    ProviderProfileConfig,
    RuntimeGuardConfig,
    load_model_config,
)
from homemaster.config.model_profiles import resolve_context_window_tokens


def test_provider_profile_carries_context_window_and_keys() -> None:
    provider = ProviderProfileConfig(
        name="mimo_v25",
        protocol="anthropic",
        base_url="https://mimo.example",
        model="MiMo-V2.5",
        api_keys=["secret-one"],
        context_window_tokens=1_000_000,
        max_output_tokens=8192,
    )

    assert provider.name == "mimo_v25"
    assert provider.context_window_tokens == 1_000_000
    assert provider.max_output_tokens == 8192
    assert provider.api_keys == ("secret-one",)


def test_context_policy_defaults_match_v15_spec() -> None:
    policy = ContextPolicyConfig()

    assert policy.auto_compact_enabled is True
    assert policy.compression_threshold_ratio == 0.50
    assert policy.recent_tail_ratio == 0.20
    assert policy.output_reserve_tokens == 8192
    assert policy.preserve_recent_agent_steps == 20
    assert policy.preserve_recent_user_turns == 3
    assert policy.safety_buffer_tokens == 13_000


def test_runtime_guard_defaults_allow_unbounded_tool_iterations() -> None:
    guards = RuntimeGuardConfig()

    assert guards.max_tool_iterations is None
    assert guards.max_consecutive_tool_errors == 5
    assert guards.max_no_progress_iterations == 20


def test_example_config_does_not_cap_mimo_output_tokens() -> None:
    path = Path(__file__).resolve().parents[2] / "config" / "homemaster.example.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    provider = data["providers"]["items"][0]

    assert provider["name"] == "mimo_v25"
    assert provider["context_window_tokens"] is None
    assert provider["max_output_tokens"] is None
    assert data["context"]["output_reserve_tokens"] == 8192


def test_mimo_context_window_is_resolved_from_model_profile() -> None:
    provider = ProviderProfileConfig(
        name="mimo_v25",
        protocol="anthropic",
        base_url="https://mimo.example",
        model="MiMo-V2.5",
        api_keys=["secret-one"],
        context_window_tokens=None,
        max_output_tokens=None,
    )

    assert resolve_context_window_tokens(provider) == 1_000_000


def test_explicit_context_window_override_wins() -> None:
    provider = ProviderProfileConfig(
        name="custom_256k",
        protocol="anthropic",
        base_url="https://custom.example",
        model="custom-model",
        api_keys=["secret-one"],
        context_window_tokens=256_000,
        max_output_tokens=None,
    )

    assert resolve_context_window_tokens(provider) == 256_000


def test_unknown_model_context_window_uses_conservative_fallback() -> None:
    provider = ProviderProfileConfig(
        name="unknown",
        protocol="openai",
        base_url="https://custom.example",
        model="unknown-model",
        api_keys=["secret-one"],
        context_window_tokens=None,
        max_output_tokens=None,
    )

    assert resolve_context_window_tokens(provider) == 200_000


def test_load_model_config_from_json(tmp_path: Path) -> None:
    path = tmp_path / "homemaster.json"
    path.write_text(
        """
        {
          "providers": {
            "default": "mimo_v25",
            "items": [
              {
                "name": "mimo_v25",
                "protocol": "anthropic",
                "base_url": "https://mimo.example",
                "model": "MiMo-V2.5",
                "api_keys": ["secret-one"],
                "context_window_tokens": 1000000,
                "max_output_tokens": 8192
              }
            ]
          },
          "runtime": {
            "max_tool_iterations": null,
            "max_consecutive_tool_errors": 5,
            "max_no_progress_iterations": 20
          }
        }
        """,
        encoding="utf-8",
    )

    config = load_model_config(path)

    assert isinstance(config, HomeMasterConfig)
    assert config.providers.default == "mimo_v25"
    assert config.get_provider("mimo_v25").context_window_tokens == 1_000_000
    assert config.runtime.max_tool_iterations is None
