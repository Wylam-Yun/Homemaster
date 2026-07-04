"""Tests for provider profile resolution through unified config."""

from __future__ import annotations

from pathlib import Path

from homemaster.config import load_config


def test_resolve_provider_profile_prefers_typed_homemaster_config(tmp_path: Path) -> None:
    path = tmp_path / "homemaster.yaml"
    path.write_text(
        """
        providers:
          default: mimo_v25
          items:
            - name: mimo_v25
              api_format: anthropic
              transport: anthropic_sdk
              base_url: https://typed.example/v1
              model: MiMo-V2.5
              api_keys: ["typed-key"]
              context_window_tokens: 1000000
              max_output_tokens: 8192
        """,
        encoding="utf-8",
    )

    profile = load_config(path).get_provider()

    assert profile.name == "mimo_v25"
    assert profile.base_url == "https://typed.example/v1"
    assert profile.api_keys == ("typed-key",)
    assert profile.context_window_tokens == 1_000_000


def test_resolve_provider_profile_rejects_legacy_flat_provider_list(tmp_path: Path) -> None:
    path = tmp_path / "legacy_flat_providers.yaml"
    path.write_text(
        """
        providers:
          - name: Mimo
            api_format: anthropic
            base_url: https://legacy.example/v1
            model: legacy-model
            api_keys: ["legacy-key"]
            context_window_tokens: 200000
            max_output_tokens: 4096
        """,
        encoding="utf-8",
    )

    try:
        load_config(path).get_provider("Mimo")
    except Exception as exc:
        assert "ProviderConfigSection" in str(exc)
    else:
        raise AssertionError("legacy flat provider list should not be accepted")
