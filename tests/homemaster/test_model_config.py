"""Tests for typed model and context configuration."""

from __future__ import annotations

from pathlib import Path

import yaml

from homemaster.config import (
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    ContextPolicyConfig,
    HomeMasterConfig,
    ProviderProfileConfig,
    RuntimeGuardConfig,
    load_config,
)


def test_provider_profile_carries_context_window_and_keys() -> None:
    provider = ProviderProfileConfig(
        name="mimo_v25",
        api_format="anthropic",
        transport="anthropic_sdk",
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
    assert policy.tail_token_ratio == 0.10
    assert policy.protect_first_n == 3
    assert policy.output_reserve_tokens == 8192
    assert policy.safety_buffer_tokens == 13_000


def test_runtime_guard_defaults_allow_unbounded_tool_iterations() -> None:
    guards = RuntimeGuardConfig()

    assert guards.max_tool_iterations is None
    assert guards.max_consecutive_tool_errors == 5
    assert guards.max_no_progress_iterations == 20


def test_example_config_declares_explicit_mimo_context_window() -> None:
    path = Path(__file__).resolve().parents[2] / "config" / "homemaster.example.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    provider = data["providers"]["items"][0]

    assert provider["name"] == "Mimo"
    assert provider["context_window_tokens"] == 1_000_000
    assert provider["max_output_tokens"] is None
    assert data["context"]["output_reserve_tokens"] == 8192


def test_context_window_uses_conservative_fallback_when_not_explicit() -> None:
    provider = ProviderProfileConfig(
        name="mimo_v25",
        api_format="anthropic",
        base_url="https://mimo.example",
        model="MiMo-V2.5",
        api_keys=["secret-one"],
        context_window_tokens=None,
        max_output_tokens=None,
    )

    assert provider.context_window_tokens == DEFAULT_CONTEXT_WINDOW_TOKENS


def test_explicit_context_window_override_wins() -> None:
    provider = ProviderProfileConfig(
        name="custom_256k",
        api_format="anthropic",
        base_url="https://custom.example",
        model="custom-model",
        api_keys=["secret-one"],
        context_window_tokens=256_000,
        max_output_tokens=None,
    )

    assert provider.context_window_tokens == 256_000


def test_unknown_model_context_window_uses_conservative_fallback() -> None:
    provider = ProviderProfileConfig(
        name="unknown",
        api_format="openai",
        base_url="https://custom.example",
        model="unknown-model",
        api_keys=["secret-one"],
        context_window_tokens=None,
        max_output_tokens=None,
    )

    assert provider.context_window_tokens == DEFAULT_CONTEXT_WINDOW_TOKENS


def test_load_config_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "homemaster.yaml"
    path.write_text(
        """
        providers:
          default: mimo_v25
          items:
            - name: mimo_v25
              api_format: anthropic
              transport: anthropic_sdk
              base_url: https://mimo.example
              model: MiMo-V2.5
              api_keys: ["secret-one"]
              context_window_tokens: 1000000
              max_output_tokens: 8192
        runtime:
          max_tool_iterations: null
          max_consecutive_tool_errors: 5
          max_no_progress_iterations: 20
        """,
        encoding="utf-8",
    )

    config = load_config(path)

    assert isinstance(config, HomeMasterConfig)
    assert config.providers.default == "mimo_v25"
    assert config.get_provider("mimo_v25").context_window_tokens == 1_000_000
    assert config.runtime.max_tool_iterations is None


def test_placeholder_api_keys_are_ignored() -> None:
    provider = ProviderProfileConfig(
        name="mimo_v25",
        api_format="anthropic",
        base_url="https://mimo.example",
        model="MiMo-V2.5",
        api_keys=["<your-api-key>"],
        context_window_tokens=1_000_000,
    )

    assert provider.api_keys == ()
