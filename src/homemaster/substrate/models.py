"""Model adapter: HomeMaster ProviderProfileConfig -> AgentScope ChatModelBase.

Dispatch is by ``api_format`` wire family: the anthropic wire
(``anthropic``, ``minimax``) and the OpenAI-compatible wire
(``openai``, ``dashscope``, ``deepseek``, ``moonshot``, ``volcengine``,
``ollama``). HM's ``transport`` axis (raw_http vs SDK) is intentionally
not preserved: the AS model layer replaces HM transports wholesale —
that is the migration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from homemaster.config.config import (
    OPENAI_WIRE_FORMATS,
    ProviderProfileConfig,
)

if TYPE_CHECKING:
    from agentscope.model import ChatModelBase


def _openai_family_model(
    profile: ProviderProfileConfig,
    *,
    key: SecretStr,
    stream: bool,
    timeout_s: float | None,
) -> ChatModelBase:
    """Build an OpenAI-wire AS model for the given api_format.

    All OpenAI-compatible formats (openai/dashscope/deepseek/moonshot/
    volcengine) share ``OpenAIChatModel`` + ``OpenAICredential``: the
    dedicated provider classes add only provider quirks (audio, adaptive
    thinking) that sit outside HM's canonical contract, while the shared
    parser already handles ``reasoning_content`` and carries the vendored
    ``finish_reason``-preservation patch. ``ollama`` keeps its native
    class — it speaks Ollama's own wire, not the OpenAI chat protocol.
    """
    from agentscope.credential import OllamaCredential, OpenAICredential
    from agentscope.model import OllamaChatModel, OpenAIChatModel

    client_kwargs: dict[str, Any] = {}
    if timeout_s is not None:
        client_kwargs["timeout"] = timeout_s
    if profile.api_format == "ollama":
        # OllamaCredential carries only ``host`` — local servers are keyless.
        credential = OllamaCredential(host=profile.base_url)
        return OllamaChatModel(
            credential=credential,
            model=profile.model,
            parameters=OllamaChatModel.Parameters(
                max_tokens=profile.max_output_tokens
            ),
            stream=stream,
            context_size=profile.context_window_tokens,
            client_kwargs=client_kwargs or None,
        )
    credential = OpenAICredential(api_key=key, base_url=profile.base_url)
    return OpenAIChatModel(
        credential=credential,
        model=profile.model,
        parameters=OpenAIChatModel.Parameters(
            max_tokens=profile.max_output_tokens
        ),
        stream=stream,
        context_size=profile.context_window_tokens,
        client_kwargs=client_kwargs or None,
    )


def chat_model_from_profile(
    profile: ProviderProfileConfig,
    *,
    stream: bool = True,
    api_key: str | None = None,
    timeout_s: float | None = None,
) -> ChatModelBase:
    """Build an AgentScope chat model from a HomeMaster provider profile.

    Provider classes are imported lazily so importing ``homemaster.substrate``
    does not pull the whole ``agentscope.model`` tree (which transitively
    loads tts/classifier subpackages).

    ``api_key`` overrides ``profile.api_keys[0]`` (multi-key rotation picks
    the key per attempt). ``timeout_s`` maps to the SDK client's timeout.
    """
    from agentscope.credential import AnthropicCredential
    from agentscope.model import AnthropicChatModel

    if profile.kind != "chat":
        raise ValueError(
            f"provider {profile.name!r} has kind={profile.kind!r}; "
            "only chat providers can back a ChatModelBase"
        )
    raw_key = api_key or (profile.api_keys[0] if profile.api_keys else None)
    if not raw_key and profile.api_format != "ollama":
        raise ValueError(
            f"provider {profile.name!r} has no api_key configured"
        )
    key = SecretStr(raw_key or "ollama")
    if profile.api_format == "anthropic":
        credential = AnthropicCredential(api_key=key, base_url=profile.base_url)
        client_kwargs: dict[str, Any] = {}
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
    if profile.api_format == "minimax":
        # MiniMax's Anthropic-compatible endpoint: shared AnthropicChatModel
        # keeps the vendored stop_reason-preservation patch; the dedicated
        # MiniMaxChatModel only adds adaptive-thinking quirks outside HM's
        # canonical contract.
        credential = AnthropicCredential(api_key=key, base_url=profile.base_url)
        client_kwargs = {"timeout": timeout_s} if timeout_s is not None else None
        parameters = AnthropicChatModel.Parameters(
            max_tokens=profile.max_output_tokens,
        )
        return AnthropicChatModel(
            credential=credential,
            model=profile.model,
            parameters=parameters,
            stream=stream,
            context_size=profile.context_window_tokens,
            client_kwargs=client_kwargs,
        )
    if profile.api_format in OPENAI_WIRE_FORMATS:
        if profile.auth_type == "auth_token":
            raise ValueError(
                f"provider {profile.name!r}: auth_token is not supported by "
                "the OpenAI-compatible AS model"
            )
        return _openai_family_model(
            profile, key=key, stream=stream, timeout_s=timeout_s
        )
    raise ValueError(
        f"provider {profile.name!r}: unsupported api_format "
        f"{profile.api_format!r}"
    )


__all__ = ["chat_model_from_profile"]
