"""Tests for runtime provider profile resolution."""

from __future__ import annotations

from pathlib import Path

from homemaster.config.resolution import resolve_provider_profile


def test_resolve_provider_profile_prefers_typed_homemaster_config(tmp_path: Path) -> None:
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
                "base_url": "https://typed.example/v1",
                "model": "MiMo-V2.5",
                "api_keys": ["typed-key"],
                "context_window_tokens": 1000000,
                "max_output_tokens": 8192
              }
            ]
          }
        }
        """,
        encoding="utf-8",
    )

    profile = resolve_provider_profile(config_path=path)

    assert profile.name == "mimo_v25"
    assert profile.base_url == "https://typed.example/v1"
    assert profile.api_keys == ("typed-key",)
    assert profile.context_window_tokens == 1_000_000


def test_resolve_provider_profile_reads_api_config(tmp_path: Path) -> None:
    path = tmp_path / "api_config.json"
    path.write_text(
        """
        {
          "providers": [
            {
              "name": "Mimo",
              "protocol": "anthropic",
              "base_url": "https://api-config.example/v1",
              "model": "api-config-model",
              "api_keys": ["api-config-key"],
              "context_window_tokens": 200000,
              "max_output_tokens": 4096
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    profile = resolve_provider_profile(config_path=path, provider_name="Mimo")

    assert profile.name == "Mimo"
    assert profile.base_url == "https://api-config.example/v1"
    assert profile.api_keys == ("api-config-key",)
    assert profile.context_window_tokens == 200_000
