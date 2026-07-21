"""Tests for provider profile resolution through unified config."""

from __future__ import annotations

from pathlib import Path

from homemaster.config import ProviderProfileConfig, load_config
from homemaster.config.config import redact_config_value


def test_resolve_provider_profile_prefers_typed_homemaster_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
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


def test_generic_anthropic_environment_does_not_override_homemaster_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ambient-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://ambient.example/v1")
    monkeypatch.setenv("ANTHROPIC_MODEL", "ambient-model")
    path = tmp_path / "homemaster.yaml"
    path.write_text(
        """
        providers:
          default: Mimo
          items:
            - name: Mimo
              api_format: anthropic
              transport: anthropic_sdk
              base_url: https://configured.example/v1
              model: mimo-v2.5
              api_keys: ["configured-key"]
        """,
        encoding="utf-8",
    )

    profile = load_config(path).get_provider()

    assert profile.base_url == "https://configured.example/v1"
    assert profile.model == "mimo-v2.5"
    assert profile.api_keys == ("configured-key",)


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


def test_config_records_file_env_and_limited_cli_provenance(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "homemaster.yaml"
    path.write_text(
        """
        providers:
          default: Mimo
          items:
            - name: Mimo
              api_format: anthropic
              base_url: https://configured.example/v1
              model: configured-model
              api_keys: [configured-key]
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("HOMEMASTER_MIMO_API_KEY", "environment-key")

    config = load_config(path, cli_overrides={"providers.Mimo.model": "cli-model"})

    assert config.get_provider().model == "cli-model"
    assert config.get_provider().api_keys == ("environment-key",)
    assert config.field_source("providers.default") == "file"
    assert config.field_source("providers.Mimo.base_url") == "file"
    assert config.field_source("providers.Mimo.api_keys") == "env"
    assert config.field_source("providers.Mimo.model") == "cli"


def test_default_provider_cli_model_override_targets_selected_default(
    tmp_path: Path,
) -> None:
    path = tmp_path / "homemaster.yaml"
    path.write_text(
        """
        providers:
          default: Mimo
          items:
            - name: Mimo
              api_format: anthropic
              base_url: https://configured.example/v1
              model: configured-model
        """,
        encoding="utf-8",
    )

    config = load_config(path, cli_overrides={"providers.default.model": "cli-model"})

    assert config.get_provider().model == "cli-model"
    assert config.field_source("providers.Mimo.model") == "cli"


def test_provider_specific_environment_overrides_are_typed_and_tracked(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "homemaster.yaml"
    path.write_text(
        """
        providers:
          default: Mimo
          items:
            - name: Mimo
              api_format: anthropic
              base_url: https://configured.example/v1
              model: configured-model
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("HOMEMASTER_MIMO_BASE_URL", "https://env.example/v1/")
    monkeypatch.setenv("HOMEMASTER_MIMO_MODEL", "env-model")
    monkeypatch.setenv("HOMEMASTER_MIMO_AUTH_TYPE", "auth_token")

    config = load_config(path)
    provider = config.get_provider()

    assert provider.base_url == "https://env.example/v1"
    assert provider.model == "env-model"
    assert provider.auth_type == "auth_token"
    assert config.field_source("providers.Mimo.base_url") == "env"
    assert config.field_source("providers.Mimo.model") == "env"
    assert config.field_source("providers.Mimo.auth_type") == "env"


def test_recursive_config_redaction_covers_nested_secrets_and_auth_headers() -> None:
    value = {
        "providers": [{"api_keys": ["secret-one"], "model": "safe"}],
        "nested": {"headers": {"Authorization": "Bearer secret-two"}},
        "plain": "Basic secret-three",
        "database_url": "postgres://user:secret-four@example.test/db",
    }

    redacted = redact_config_value(value)

    encoded = str(redacted)
    assert "secret-one" not in encoded
    assert "secret-two" not in encoded
    assert "secret-three" not in encoded
    assert "secret-four" not in encoded
    assert "safe" in encoded


def test_provider_public_summary_redacts_url_userinfo() -> None:
    provider = ProviderProfileConfig(
        name="private-url",
        api_format="anthropic",
        base_url="https://user:password@example.test/v1",
        model="model",
    )

    summary = str(provider.public_summary())

    assert "user" not in summary
    assert "password" not in summary
    assert "example.test/v1" in summary


def test_invalid_config_error_does_not_echo_secret_input(tmp_path: Path) -> None:
    path = tmp_path / "homemaster.yaml"
    path.write_text(
        """
        providers:
          items:
            - name: Mimo
              api_format: invalid
              base_url: https://example.invalid
              model: model
              api_keys: [do-not-echo-this-secret]
        """,
        encoding="utf-8",
    )

    try:
        load_config(path)
    except Exception as exc:
        assert "do-not-echo-this-secret" not in str(exc)
        assert "providers.0.api_format" not in str(exc)
        assert "providers.items.0.api_format" in str(exc)
    else:
        raise AssertionError("invalid provider API format must fail")


def test_config_rejects_unsafe_project_skill_directory(tmp_path: Path) -> None:
    path = tmp_path / "homemaster.yaml"
    path.write_text("skills:\n  project_dirs: [../outside]\n", encoding="utf-8")

    try:
        load_config(path)
    except Exception as exc:
        assert "safe relative paths" in str(exc)
    else:
        raise AssertionError("unsafe project skill path should fail")
