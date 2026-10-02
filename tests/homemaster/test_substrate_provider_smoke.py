"""Phase-1 multi-provider smoke: real HTTP wire through AS model classes.

A threaded localhost server speaks three provider wire protocols
(OpenAI SSE, Anthropic SSE, Ollama NDJSON). Each api_format is run through
``AsLLMClient`` -> ``chat_model_from_profile`` -> real vendored AS model ->
real SDK client -> real HTTP. Asserts normalized text/finish_reason/usage
per provider — not aggregates.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from homemaster.agent.messages import UserMessage
from homemaster.config.config import ProviderProfileConfig
from homemaster.substrate.as_llm_client import AsLLMClient


_OPENAI_SSE = [
    {
        "id": "chatcmpl-fake",
        "object": "chat.completion.chunk",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": "hi"},
                "finish_reason": None,
            }
        ],
    },
    {
        "id": "chatcmpl-fake",
        "object": "chat.completion.chunk",
        "choices": [
            {"index": 0, "delta": {}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": 5,
            "completion_tokens": 2,
            "total_tokens": 7,
        },
    },
]

_ANTHROPIC_SSE = [
    (
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_fake",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "fake",
                "stop_reason": None,
                "usage": {"input_tokens": 5, "output_tokens": 0},
            },
        },
    ),
    (
        "content_block_start",
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
    ),
    (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "hi"},
        },
    ),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    (
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 2},
        },
    ),
    ("message_stop", {"type": "message_stop"}),
]

_OLLAMA_NDJSON = [
    {
        "model": "fake",
        "message": {"role": "assistant", "content": "hi"},
        "done": False,
    },
    {
        "model": "fake",
        "message": {"role": "assistant", "content": ""},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 5,
        "eval_count": 2,
    },
]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        return

    def _sse(self, events: list[tuple[str, dict] | dict]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for item in events:
            if isinstance(item, tuple):
                event, data = item
                frame = f"event: {event}\ndata: {json.dumps(data)}\n\n"
            else:
                frame = f"data: {json.dumps(item)}\n\n"
            self.wfile.write(frame.encode())
            self.wfile.flush()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        if self.path == "/v1/chat/completions":
            self._sse(_OPENAI_SSE)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        elif self.path == "/v1/messages":
            self._sse(_ANTHROPIC_SSE)
        elif self.path == "/api/chat":
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            for item in _OLLAMA_NDJSON:
                self.wfile.write(json.dumps(item).encode() + b"\n")
                self.wfile.flush()
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture(scope="module")
def wire_server() -> Any:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=5)


def _profile(api_format: str, port: int) -> ProviderProfileConfig:
    # OpenAI SDK treats base_url as the API root (must contain /v1);
    # Anthropic SDK appends /v1/messages itself; Ollama appends /api/chat.
    base = f"http://127.0.0.1:{port}"
    if api_format in {"openai", "dashscope", "deepseek", "moonshot", "volcengine"}:
        base += "/v1"
    return ProviderProfileConfig(
        name=f"fake-{api_format}",
        api_format=api_format,
        transport="raw_http",
        base_url=base,
        model="fake-model",
        api_keys=("sk-fake",) if api_format != "ollama" else (),
        kind="chat",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "api_format",
    [
        "openai",
        "dashscope",
        "deepseek",
        "moonshot",
        "volcengine",
        "anthropic",
        "minimax",
        "ollama",
    ],
)
async def test_provider_wire_smoke(api_format: str, wire_server: Any) -> None:
    client = AsLLMClient(_profile(api_format, wire_server.server_port))
    try:
        message = await client.complete(
            [UserMessage.from_text("ping")], system_prompt="sys"
        )
    finally:
        await client.aclose()
    assert message.text == "hi"
    assert message.finish_reason == "stop"
    usage = message.usage or {}
    assert usage.get("input_tokens") == 5
    assert usage.get("output_tokens") == 2
