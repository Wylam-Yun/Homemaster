from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from homemaster.config import load_config
from homemaster.tools.runtime_services import HomeToolServices


def _event(name: str, payload: dict[str, object]) -> bytes:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()


@contextmanager
def _anthropic_server():
    requests: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length", "0"))
            requests.append(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            events = (
                _event(
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": "msg-child",
                            "type": "message",
                            "role": "assistant",
                            "content": [],
                            "model": "child-model",
                            "stop_reason": None,
                            "usage": {"input_tokens": 1, "output_tokens": 0},
                        },
                    },
                ),
                _event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                ),
                _event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "child verified"},
                    },
                ),
                _event("content_block_stop", {"type": "content_block_stop", "index": 0}),
                _event(
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {"output_tokens": 2},
                    },
                ),
                _event("message_stop", {"type": "message_stop"}),
            )
            for event in events:
                self.wfile.write(event)
            self.wfile.flush()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
async def test_default_child_worker_uses_parent_config_and_returns_real_reply(
    tmp_path: Path,
) -> None:
    with _anthropic_server() as (base_url, requests):
        config_path = tmp_path / "homemaster.yaml"
        config_path.write_text(
            f"""
providers:
  default: child
  items:
    - name: child
      api_format: anthropic
      transport: raw_http
      base_url: {base_url}
      model: child-model
      api_keys: [test-key]
runtime_defaults:
  default_provider_name: child
runtime_paths:
  runtime_root: {tmp_path / "runtime"}
observability:
  trace_dir: {tmp_path / "trace"}
  session_dir: {tmp_path / "sessions"}
""",
            encoding="utf-8",
        )
        services = HomeToolServices(
            load_config(config_path),
            state_root=tmp_path / "state",
        )
        try:
            task = await services.tasks.create_agent_task(
                prompt="child prompt",
                description="default worker gate",
                cwd=tmp_path,
            )
            output = ""
            for _ in range(3000):
                current = services.tasks.get_task(task.id)
                assert current is not None
                output = services.tasks.read_task_output(task.id)
                if output.strip():
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("default child worker did not reply")

            payload = json.loads(output.strip())
            assert current.status == "running"
            assert payload["return_code"] == 0
            assert payload["status"] == "replied"
            assert payload["reply"] == "child verified"
            assert requests[0]["model"] == "child-model"
            assert requests[0]["messages"][-1]["content"] == [
                {"type": "text", "text": "child prompt"}
            ]
            stopped = await services.tasks.stop_task(task.id)
            assert stopped.status == "killed"
            assert stopped.return_code is not None
        finally:
            await services.aclose()
