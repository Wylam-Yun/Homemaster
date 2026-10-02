"""Model adapter: HomeMaster ProviderProfileConfig -> AgentScope ChatModelBase.

Supports ``api_format`` ``anthropic`` and ``openai`` for Phase 0/1. HM's
``transport`` axis (raw_http vs SDK) is intentionally not preserved: the AS
model layer replaces HM transports wholesale — that is the migration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr

from homemaster.config.config import ProviderProfileConfig

if TYPE_CHECKING:
    from agentscope.model import ChatModelBase


def chat_model_from_profile(
    profile: ProviderProfileConfig,
    *,
    stream: bool = True,
    api_key: str | None = None,
    timeout_s: float | None = None,
) -> "ChatModelBase":
    """Build an AgentScope chat model from a HomeMaster provider profile.

    Provider classes are imported lazily so importing ``homemaster.substrate``
    does not pull the whole ``agentscope.model`` tree (which transitively
    loads tts/classifier subpackages).

    ``api_key`` overrides ``profile.api_keys[0]`` (multi-key rotation picks
    the key per attempt). ``timeout_s`` maps to the SDK client's timeout.
    """
    from agentscope.credential import AnthropicCredential, OpenAICredential
    from agentscope.model import AnthropicChatModel, OpenAIChatModel

    if profile.kind != "chat":
        raise ValueError(
            f"provider {profile.name!r} has kind={profile.kind!r}; "
            "only chat providers can back a ChatModelBase"
        )
    raw_key = api_key or (profile.api_keys[0] if profile.api_keys else None)
    if not raw_key:
        raise ValueError(
            f"provider {profile.name!r} has no api_key configured"
        )
    key = SecretStr(raw_key)
    if profile.api_format == "anthropic":
        credential = AnthropicCredential(api_key=key, base_url=profile.base_url)
        client_kwargs: dict = {}
        if profile.auth_type == "auth_token":
            # AsyncAnthropic accepts auth_token; keep HM's auth axis.
            client_kwargs["auth_token"] = raw_key
        if timeout_s is not None:
            client_kwargs["timeout"] = timeout_s
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
        credential = OpenAICredential(api_key=key, base_url=profile.base_url)
        client_kwargs = {"timeout": timeout_s} if timeout_s is not None else None
        parameters = OpenAIChatModel.Parameters(
            max_tokens=profile.max_output_tokens,
        )
        return OpenAIChatModel(
            credential=credential,
            model=profile.model,
            parameters=parameters,
            stream=stream,
            context_size=profile.context_window_tokens,
            client_kwargs=client_kwargs,
        )
    raise ValueError(
        f"provider {profile.name!r}: unsupported api_format "
        f"{profile.api_format!r}"
    )


__all__ = ["chat_model_from_profile"]
