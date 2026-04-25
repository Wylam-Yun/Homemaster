from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from homemaster.llm_client import RawJsonLLMClient, extract_json_payload
from homemaster.runtime import load_provider_config
from homemaster.trace import sanitize_for_log


def test_raw_json_client_reads_mimo_config_and_sends_anthropic_request(tmp_path: Path) -> None:
    config_path = tmp_path / "api_config.json"
    config_path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "name": "Other",
                        "base_url": "https://other.example/anthropic",
                        "model": "other-model",
                        "api_keys": ["other-key"],
                        "protocol": "anthropic",
                    },
                    {
                        "name": "Mimo",
                        "base_url": "https://mimo.example/anthropic",
                        "model": "mimo-v2-pro",
                        "api_keys": ["secret-one"],
                        "protocol": "anthropic",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        requests.append(
            {
                "url": str(request.url),
                "model": body["model"],
                "temperature": body["temperature"],
                "max_tokens": body["max_tokens"],
                "prompt": body["messages"][0]["content"],
            }
        )
        return httpx.Response(
            status_code=200,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "```json\n"
                            '{"task_type":"check_presence","target":"药盒",'
                            '"delivery_target":null,"location_hint":"桌子那边",'
                            '"success_criteria":["确认药盒是否在桌子附近"],'
                            '"needs_clarification":false,'
                            '"clarification_question":null,"confidence":0.9}'
                            "\n```"
                        ),
                    }
                ]
            },
        )

    provider = load_provider_config(config_path, provider_name="Mimo")
    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as http_client:
        client = RawJsonLLMClient(provider, client=http_client)
        response = client.complete_json("prompt body", max_tokens=512, temperature=0.0)

    assert provider.name == "Mimo"
    assert provider.model == "mimo-v2-pro"
    assert response.json_payload["task_type"] == "check_presence"
    assert requests == [
        {
            "url": "https://mimo.example/anthropic/v1/messages",
            "model": "mimo-v2-pro",
            "temperature": 0.0,
            "max_tokens": 512,
            "prompt": "prompt body",
        }
    ]


def test_extract_json_payload_accepts_plain_json() -> None:
    payload = extract_json_payload('{"task_type": "check_presence", "target": "药盒"}')

    assert payload == {"task_type": "check_presence", "target": "药盒"}


def test_sanitize_for_log_redacts_secret_fields() -> None:
    sanitized = sanitize_for_log(
        {
            "api_key": "secret-one",
            "headers": {
                "Authorization": "Bearer secret-one",
                "x-api-key": "secret-one",
            },
            "safe": "visible",
        }
    )
    encoded = json.dumps(sanitized, ensure_ascii=False)

    assert "secret-one" not in encoded
    assert sanitized["safe"] == "visible"
