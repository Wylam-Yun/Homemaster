"""Tests for provider profile resolution through unified config."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

import homemaster.config as config_module
from homemaster.channels.impl.feishu import FeishuApiService
from homemaster.config import (
    FeishuChannelConfig,
    GatewayConfig,
    ProviderProfileConfig,
    load_config,
)
from homemaster.config.config import REPO_ROOT, redact_config_value


def test_feishu_config_exposes_only_locked_domain_and_trusted_entry_fields() -> None:
    config = FeishuChannelConfig(enabled=True, domain="lark")

    assert config.domain == "lark"
    assert config.attachment_root.as_posix().endswith("attachments/feishu")
    assert not hasattr(config, "bot_open_id")
    assert not hasattr(config, "bot_names")
    assert not hasattr(config, "group_policy")
    assert not hasattr(config, "principals")

    with pytest.raises(ValueError):
        FeishuChannelConfig(domain="https://attacker.example")


def test_enabled_feishu_requires_no_bot_or_sender_ids() -> None:
    config = FeishuChannelConfig(enabled=True)

    assert config.enabled


def test_feishu_yaml_credentials_are_preferred_secret_safe_and_sanitized(monkeypatch) -> None:
    monkeypatch.setenv("HOMEMASTER_FEISHU_APP_ID", "cli-env")
    monkeypatch.setenv("HOMEMASTER_FEISHU_APP_SECRET", "env-secret")
    config = FeishuChannelConfig(app_id="cli-file", app_secret="file-secret")

    service = FeishuApiService.from_config(config)

    assert service.app_id == "cli-file"
    assert service.credential_source == "file"
    assert "file-secret" in config_module.configured_sensitive_values(
        config_module.HomeMasterConfig(gateway={"feishu": config})
    )
    rendered = (
        repr(config),
        str(config),
        repr(config.model_dump(mode="python")),
        repr(config.model_dump(mode="json")),
        config.model_dump_json(),
        repr(service),
    )
    assert all("file-secret" not in value for value in rendered)
    assert all("env-secret" not in value for value in rendered)


def test_feishu_environment_credentials_remain_pairwise_fallback(monkeypatch) -> None:
    monkeypatch.setenv("HOMEMASTER_FEISHU_APP_ID", "cli-env")
    monkeypatch.setenv("HOMEMASTER_FEISHU_APP_SECRET", "env-secret")

    service = FeishuApiService.from_config(FeishuChannelConfig())

    assert service.app_id == "cli-env"
    assert service.credential_source == "env"
    assert "env-secret" not in repr(service)


def test_feishu_credentials_reject_partial_or_cross_source_pairs(monkeypatch) -> None:
    with pytest.raises(ValueError, match="app_id and app_secret must be configured together"):
        FeishuChannelConfig(app_id="cli-file")
    with pytest.raises(ValueError, match="app_id and app_secret must be configured together"):
        FeishuChannelConfig(app_secret="file-secret")

    monkeypatch.setenv("HOMEMASTER_FEISHU_APP_ID", "cli-env")
    monkeypatch.delenv("HOMEMASTER_FEISHU_APP_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="environment app id and app secret"):
        FeishuApiService.from_config(FeishuChannelConfig())

    monkeypatch.setenv("HOMEMASTER_FEISHU_APP_SECRET", "env-secret")
    service = FeishuApiService.from_config(
        FeishuChannelConfig(app_id="cli-file", app_secret="file-secret")
    )
    assert service.app_id == "cli-file"
    assert service.credential_source == "file"


def test_gateway_has_single_feishu_channel_configuration() -> None:
    config = GatewayConfig(
        enabled=True,
        feishu={"enabled": True},
    )

    assert config.feishu.enabled
    assert not hasattr(config, "telegram")
    with pytest.raises(ValueError, match="Feishu cannot be enabled"):
        GatewayConfig(feishu={"enabled": True})


def test_gateway_extra_and_example_expose_only_feishu() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    gateway_dependencies = pyproject["project"]["optional-dependencies"]["gateway"]
    example = (REPO_ROOT / "config" / "homemaster.example.yaml").read_text(encoding="utf-8")

    assert gateway_dependencies == ["lark-oapi>=1.7.1,<2"]
    assert "python-telegram-bot" not in gateway_dependencies
    assert "  feishu:" in example
    assert "HOMEMASTER_FEISHU_APP_SECRET" in example
    assert "bot_open_id" not in example
    assert "group_policy" not in example
    assert "principals:" not in example
    assert "  telegram:" not in example


def test_configured_sensitive_values_collect_provider_and_mcp_secrets_without_logging(
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
              model: mimo-v2.5
              api_keys: [provider-secret]
        mcp:
          servers:
            local:
              transport: stdio
              command: python
              env:
                MCP_TOKEN: mcp-env-secret
            remote:
              transport: http
              url: https://mcp-user:mcp-password@example.test/mcp
              headers:
                Authorization: Bearer mcp-header-secret
        """,
        encoding="utf-8",
    )
    config = load_config(path)

    assert hasattr(config_module, "configured_sensitive_values")
    values = config_module.configured_sensitive_values(config)

    assert set(values) == {
        "provider-secret",
        "mcp-env-secret",
        "Bearer mcp-header-secret",
        "mcp-user",
        "mcp-password",
    }
    public = json.dumps(
        {
            "providers": [provider.public_summary() for provider in config.providers.items],
            "mcp": config.mcp.public_summary(),
        }
    )
    assert all(value not in public for value in values)


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


def test_extensions_default_disabled_and_duplicate_approvals_fail_closed(
    tmp_path: Path,
) -> None:
    assert load_config(tmp_path / "missing.yaml").extensions.approvals == ()
    path = tmp_path / "homemaster.yaml"
    path.write_text(
        """
extensions:
  approvals:
    - &approval
      manifest_path: extension/manifest.json
      extension_id: example.audit
      version: 1.0.0
      expected_sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    - *approval
""",
        encoding="utf-8",
    )

    try:
        load_config(path)
    except Exception as exc:
        assert "approval ids must be unique" in str(exc)
    else:
        raise AssertionError("duplicate extension approvals must fail")


def test_extension_approval_hash_and_version_are_typed(tmp_path: Path) -> None:
    path = tmp_path / "homemaster.yaml"
    path.write_text(
        """
extensions:
  approvals:
    - manifest_path: extension/manifest.json
      extension_id: Example.Audit
      version: latest
      expected_sha256: not-a-digest
""",
        encoding="utf-8",
    )

    try:
        load_config(path)
    except Exception as exc:
        message = str(exc)
        assert "extensions.approvals.0.extension_id" in message
        assert "extensions.approvals.0.version" in message
        assert "extensions.approvals.0.expected_sha256" in message
    else:
        raise AssertionError("untyped extension approval pins must fail")
