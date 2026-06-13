"""Tests for system prompt delivery through transport layer."""

from __future__ import annotations

import httpx

from homemaster.agent.messages import ContentBlock, UserMessage
from homemaster.providers.mimo_transport import MimoTransport


def _make_transport(protocol: str) -> MimoTransport:
    return MimoTransport(
        base_url="https://example.invalid",
        model="m",
        api_key="secret",
        protocol=protocol,
        http_client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
    )


def test_anthropic_payload_includes_system_prompt() -> None:
    transport = _make_transport("anthropic")

    payload = transport._build_request_payload(
        [UserMessage(content=[ContentBlock(text="hello")])],
        tools=None,
        system_prompt="You are HomeMaster.",
    )

    assert payload["system"] == "You are HomeMaster."
    assert payload["messages"][0]["role"] == "user"


def test_openai_payload_prepends_system_message() -> None:
    transport = _make_transport("openai")

    payload = transport._build_request_payload(
        [UserMessage(content=[ContentBlock(text="hello")])],
        tools=None,
        system_prompt="You are HomeMaster.",
    )

    assert payload["messages"][0] == {"role": "system", "content": "You are HomeMaster."}
    assert payload["messages"][1]["role"] == "user"


def test_empty_system_prompt_is_omitted_anthropic() -> None:
    transport = _make_transport("anthropic")

    payload = transport._build_request_payload(
        [UserMessage(content=[ContentBlock(text="hello")])],
        tools=None,
        system_prompt="",
    )

    assert "system" not in payload


def test_empty_system_prompt_is_omitted_openai() -> None:
    transport = _make_transport("openai")

    payload = transport._build_request_payload(
        [UserMessage(content=[ContentBlock(text="hello")])],
        tools=None,
        system_prompt="",
    )

    assert payload["messages"][0]["role"] == "user"
