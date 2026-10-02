"""Model adapter: HomeMaster ProviderProfileConfig -> AgentScope ChatModelBase.

Supports ``api_format`` ``anthropic`` and ``openai`` for Phase 0/1. HM's
``transport`` axis (raw_http vs SDK) is intentionally not preserved: the AS
model layer replaces HM transports wholesale — that is the migration.
"""

from __future__ import annotations

from pydantic import SecretStr

from agentscope.credential import AnthropicCredential, OpenAICredential
from agentscope.model import AnthropicChatModel, ChatModelBase, OpenAIChatModel

from homemaster.config.config import ProviderProfileConfig


def chat_model_from_profile(
    profile: ProviderProfileConfig,
    *,
    stream: bool = True,
) -> ChatModelBase:
    """Build an AgentScope chat model from a HomeMaster provider profile."""
    if profile.kind != "chat":
        raise ValueError(
            f"provider {profile.name!r} has kind={profile.kind!r}; "
            "only chat providers can back a ChatModelBase"
        )
    if not profile.api_keys:
        raise ValueError(
            f"provider {profile.name!r} has no api_key configured"
        )
    api_key = SecretStr(profile.api_keys[0])
    if profile.api_format == "anthropic":
        credential = AnthropicCredential(api_key=api_key, base_url=profile.base_url)
        client_kwargs: dict = {}
        if profile.auth_type == "auth_token":
            # AsyncAnthropic accepts auth_token; keep HM's auth axis.
            client_kwargs["auth_token"] = profile.api_keys[0]
        parameters = AnthropicChatModel.Parameters(
            max_tokens=profile.max_output_tokens,
        )
        return AnthropicChatModel(
            credential=credential,
            model=profile.model,
            parameters=parameters,
            stream=stream,
            context_size=profile.context_window_tokens,
            client_kwargs=client_kwargs or None,
        )
    if profile.api_format == "openai":
        if profile.auth_type == "auth_token":
            raise ValueError(
                f"provider {profile.name!r}: auth_token is not supported by "
                "the OpenAI-compatible AS model"
            )
        credential = OpenAICredential(api_key=api_key, base_url=profile.base_url)
        parameters = OpenAIChatModel.Parameters(
            max_tokens=profile.max_output_tokens,
        )
        return OpenAIChatModel(
            credential=credential,
            model=profile.model,
            parameters=parameters,
            stream=stream,
            context_size=profile.context_window_tokens,
        )
    raise ValueError(
        f"provider {profile.name!r}: unsupported api_format "
        f"{profile.api_format!r}"
    )


__all__ = ["chat_model_from_profile"]
