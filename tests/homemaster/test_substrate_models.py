"""chat_model_from_profile: ProviderProfileConfig -> ChatModelBase."""

from __future__ import annotations

import pytest

from agentscope.model import AnthropicChatModel, OpenAIChatModel
from homemaster.config.config import ProviderProfileConfig
from homemaster.substrate import chat_model_from_profile


def _profile(**overrides) -> ProviderProfileConfig:
    values = {
        "name": "p1",
        "api_format": "anthropic",
        "transport": "anthropic_sdk",
        "base_url": "https://api.example.com",
        "model": "claude-x",
        "api_keys": ("sk-a",),
        "context_window_tokens": 12345,
        "max_output_tokens": 4096,
        "kind": "chat",
    }
    values.update(overrides)
    return ProviderProfileConfig(**values)


def test_anthropic_profile_maps_fields() -> None:
    model = chat_model_from_profile(_profile())
    assert isinstance(model, AnthropicChatModel)
    assert model.model == "claude-x"
    assert model.context_size == 12345
    assert model.parameters.max_tokens == 4096
    assert str(model.credential.base_url).rstrip("/") == "https://api.example.com"
    assert model.credential.api_key.get_secret_value() == "sk-a"


def test_anthropic_auth_token_threads_client_kwargs() -> None:
    model = chat_model_from_profile(_profile(auth_type="auth_token"))
    assert model.client_kwargs.get("auth_token") == "sk-a"


def test_openai_profile_maps_fields() -> None:
    model = chat_model_from_profile(
        _profile(
            api_format="openai",
            transport="openai_sdk",
            model="gpt-x",
            api_keys=("sk-b", "sk-c"),
        )
    )
    assert isinstance(model, OpenAIChatModel)
    assert model.model == "gpt-x"
    assert model.context_size == 12345
    assert model.parameters.max_tokens == 4096
    assert model.credential.api_key.get_secret_value() == "sk-b"


def test_openai_auth_token_rejected() -> None:
    with pytest.raises(ValueError, match="auth_token"):
        chat_model_from_profile(
            _profile(api_format="openai", auth_type="auth_token")
        )


def test_non_chat_kind_rejected() -> None:
    with pytest.raises(ValueError, match="kind"):
        chat_model_from_profile(_profile(kind="embedding"))


def test_missing_api_key_rejected() -> None:
    with pytest.raises(ValueError, match="api_key"):
        chat_model_from_profile(_profile(api_keys=()))


def test_unknown_api_format_rejected() -> None:
    with pytest.raises(ValueError, match="api_format"):
        chat_model_from_profile(_profile(api_format="gemini"))
