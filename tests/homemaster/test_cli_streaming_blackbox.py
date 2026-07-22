"""Subprocess gates for the real CLI against a blocked loopback SSE provider."""

from __future__ import annotations

import json
import os
import select
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class _SseState:
    def __init__(self) -> None:
        self.first_delta_sent = threading.Event()
        self.release = threading.Event()
        self.request_received = threading.Event()
        self.allow_response = threading.Event()
        self.allow_response.set()
        self.second_request = threading.Event()
        self._request_count = 0
        self._lock = threading.Lock()

    def next_request(self) -> int:
        with self._lock:
            current = self._request_count
            self._request_count += 1
            return current


def _event(name: str, payload: dict[str, object]) -> bytes:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()


@contextmanager
def _blocked_anthropic_server(
    *,
    first_text: str = "hello ",
    second_text: str = "world",
    fail_after_first: bool = False,
    tool_then_text: bool = False,
    gate_first_response: bool = False,
    tool_name: str = "observe",
) -> Iterator[tuple[str, _SseState]]:
    state = _SseState()
    if gate_first_response:
        state.allow_response.clear()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802
            request_index = state.next_request()
            length = int(self.headers.get("content-length", "0"))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("connection", "close")
            self.end_headers()
            if tool_then_text and request_index == 0:
                state.request_received.set()
                if not state.allow_response.wait(20):
                    return
                tool_events = [
                    _event(
                        "message_start",
                        {
                            "type": "message_start",
                            "message": {
                                "id": "msg_tool",
                                "type": "message",
                                "role": "assistant",
                                "content": [],
                                "model": "blackbox-model",
                                "stop_reason": None,
                                "stop_sequence": None,
                                "usage": {"input_tokens": 1, "output_tokens": 0},
                            },
                        },
                    ),
                    _event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {
                                "type": "tool_use",
                                "id": "call_observe",
                                "name": tool_name,
                                "input": {},
                            },
                        },
                    ),
                    _event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "input_json_delta", "partial_json": "{}"},
                        },
                    ),
                    _event("content_block_stop", {"type": "content_block_stop", "index": 0}),
                    _event(
                        "message_delta",
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                            "usage": {"output_tokens": 1},
                        },
                    ),
                    _event("message_stop", {"type": "message_stop"}),
                ]
                for item in tool_events:
                    self.wfile.write(item)
                self.wfile.flush()
                return
            if tool_then_text and request_index == 1:
                state.second_request.set()
            initial = [
                _event(
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": "msg_test",
                            "type": "message",
                            "role": "assistant",
                            "content": [],
                            "model": "blackbox-model",
                            "stop_reason": None,
                            "stop_sequence": None,
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
                        "delta": {"type": "text_delta", "text": first_text},
                    },
                ),
            ]
            for item in initial:
                self.wfile.write(item)
            self.wfile.flush()
            state.first_delta_sent.set()
            if not state.release.wait(20):
                return
            if fail_after_first:
                self.wfile.write(
                    _event(
                        "error",
                        {
                            "type": "error",
                            "error": {
                                "type": "api_error",
                                "message": "api_key=provider-failure-secret",
                            },
                        },
                    )
                )
                self.wfile.flush()
                return
            final = [
                _event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": second_text},
                    },
                ),
                _event(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": 0},
                ),
                _event(
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {"output_tokens": 2},
                    },
                ),
                _event("message_stop", {"type": "message_stop"}),
            ]
            for item in final:
                self.wfile.write(item)
            self.wfile.flush()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", state
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _start_cli(base_url: str, output_format: str) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env.update(
        {
            "HOMEMASTER_MIMO_API_KEY": "blackbox-provider-secret",
            "HOMEMASTER_MIMO_BASE_URL": base_url,
            "HOMEMASTER_MIMO_MODEL": "blackbox-model",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "homemaster.cli",
            "-p",
            "say hello",
            "--output-format",
            output_format,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


@pytest.mark.parametrize("output_format", ["text", "stream-json", "json"])
def test_real_cli_first_byte_contract(output_format: str) -> None:
    with _blocked_anthropic_server() as (base_url, state):
        process = _start_cli(base_url, output_format)
        assert process.stdout is not None
        try:
            assert state.first_delta_sent.wait(60), (
                process.stderr.read().decode(errors="replace")
                if process.poll() is not None and process.stderr is not None
                else "CLI did not reach the loopback provider"
            )
            ready, _, _ = select.select([process.stdout], [], [], 2)
            if output_format == "json":
                assert ready == []
                prefix = b""
            else:
                assert ready == [process.stdout]
                prefix = os.read(process.stdout.fileno(), 4096)
                assert process.poll() is None
            state.release.set()
            stdout, stderr = process.communicate(timeout=20)
            stdout = prefix + stdout
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    assert process.returncode == 0, stderr.decode(errors="replace")
    assert b"blackbox-provider-secret" not in stdout + stderr
    assert b"\x1b[" not in stdout
    if output_format == "text":
        assert stdout.decode() == "hello world"
    elif output_format == "stream-json":
        rows = [json.loads(line) for line in stdout.splitlines()]
        assert rows[0] == {"type": "assistant_delta", "text": "hello "}
        assert rows[-1]["type"] == "result"
        assert rows[-1]["final_reply"] == "hello world"
        assert sum(row["type"] == "result" for row in rows) == 1
    else:
        result = json.loads(stdout)
        assert result["type"] == "result"
        assert result["final_reply"] == "hello world"


def test_real_stream_json_partial_failure_has_one_error_and_terminal_result() -> None:
    with _blocked_anthropic_server(fail_after_first=True) as (base_url, state):
        process = _start_cli(base_url, "stream-json")
        assert process.stdout is not None
        assert state.first_delta_sent.wait(60)
        ready, _, _ = select.select([process.stdout], [], [], 2)
        assert ready == [process.stdout]
        prefix = os.read(process.stdout.fileno(), 4096)
        assert process.poll() is None
        state.release.set()
        stdout, stderr = process.communicate(timeout=20)
        stdout = prefix + stdout

    rows = [json.loads(line) for line in stdout.splitlines()]
    assert process.returncode == 1
    assert rows[0] == {"type": "assistant_delta", "text": "hello "}
    assert [row["type"] for row in rows].count("error") == 1
    assert [row["type"] for row in rows].count("assistant_complete") == 0
    assert rows[-1]["type"] == "result"
    assert rows[-1]["status"] == "failed"
    assert b"provider-failure-secret" not in stdout + stderr


def test_real_stream_json_sigint_during_provider_wait_is_clean_cancellation() -> None:
    with _blocked_anthropic_server() as (base_url, state):
        process = _start_cli(base_url, "stream-json")
        assert process.stdout is not None
        assert state.first_delta_sent.wait(60)
        ready, _, _ = select.select([process.stdout], [], [], 2)
        assert ready == [process.stdout]
        prefix = os.read(process.stdout.fileno(), 4096)
        process.send_signal(signal.SIGINT)
        state.release.set()
        stdout, stderr = process.communicate(timeout=20)
        stdout = prefix + stdout

    rows = [json.loads(line) for line in stdout.splitlines()]
    assert process.returncode == 130
    assert rows[0] == {"type": "assistant_delta", "text": "hello "}
    assert rows[-2] == {"type": "status", "message": "run cancelled"}
    assert rows[-1]["type"] == "result"
    assert rows[-1]["status"] == "cancelled"
    assert all(row["type"] != "assistant_complete" for row in rows)
    assert b"Traceback" not in stderr


def test_real_stream_json_redacts_split_config_secret_path_and_url() -> None:
    with _blocked_anthropic_server(
        first_text="safe blackbox-provider-",
        second_text="secret /home/user/private https://host/path?token=value",
    ) as (base_url, state):
        process = _start_cli(base_url, "stream-json")
        assert state.first_delta_sent.wait(60)
        state.release.set()
        stdout, stderr = process.communicate(timeout=20)

    assert process.returncode == 0, stderr.decode(errors="replace")
    combined = stdout + stderr
    assert b"blackbox-provider-secret" not in combined
    assert b"/home/user/private" not in combined
    assert b"token=value" not in combined
    rows = [json.loads(line) for line in stdout.splitlines()]
    assert rows[-1]["type"] == "result"


def test_real_stream_json_tool_failure_is_one_failed_completion() -> None:
    with _blocked_anthropic_server(
        tool_then_text=True,
        tool_name="not_a_registered_tool",
    ) as (base_url, state):
        process = _start_cli(base_url, "stream-json")
        assert state.second_request.wait(60)
        assert state.first_delta_sent.wait(60)
        state.release.set()
        stdout, stderr = process.communicate(timeout=20)

    rows = [json.loads(line) for line in stdout.splitlines()]
    failed_tools = [row for row in rows if row["type"] == "tool_completed"]
    assert process.returncode == 0, stderr.decode(errors="replace")
    assert len(failed_tools) == 1
    assert failed_tools[0]["is_error"] is True
    assert all(row["type"] != "error" for row in rows)
    assert rows[-1]["type"] == "result"


def _tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _capture_pane(session: str) -> str:
    return _tmux("capture-pane", "-p", "-S", "-", "-t", session).stdout


def _wait_until(predicate, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise AssertionError("timed out waiting for terminal state")


@pytest.mark.skipif(not os.path.exists("/usr/bin/tmux"), reason="tmux is required")
def test_real_interactive_rich_final_screen_via_tmux() -> None:
    session = f"hm-rich-{uuid.uuid4().hex[:10]}"
    with _blocked_anthropic_server(
        tool_then_text=True,
        gate_first_response=True,
    ) as (base_url, state):
        env = os.environ.copy()
        env.update(
            {
                "HOMEMASTER_MIMO_API_KEY": "blackbox-provider-secret",
                "HOMEMASTER_MIMO_BASE_URL": base_url,
                "HOMEMASTER_MIMO_MODEL": "blackbox-model",
                "TERM": "xterm-256color",
            }
        )
        command = shlex.join(
            [
                "env",
                f"HOMEMASTER_MIMO_API_KEY={env['HOMEMASTER_MIMO_API_KEY']}",
                f"HOMEMASTER_MIMO_BASE_URL={env['HOMEMASTER_MIMO_BASE_URL']}",
                f"HOMEMASTER_MIMO_MODEL={env['HOMEMASTER_MIMO_MODEL']}",
                f"TERM={env['TERM']}",
                sys.executable,
                "-m",
                "homemaster.cli",
            ]
        )
        try:
            subprocess.run(
                ["tmux", "new-session", "-d", "-x", "100", "-y", "30", "-s", session, command],
                check=True,
                env=env,
            )
            _wait_until(lambda: "homemaster>" in _capture_pane(session))
            _tmux("send-keys", "-t", session, "say hello", "Enter")
            assert state.request_received.wait(60), _capture_pane(session)
            _wait_until(lambda: "Model working" in _capture_pane(session), timeout=10)
            state.allow_response.set()
            assert state.second_request.wait(60), _capture_pane(session)
            _wait_until(lambda: "observe" in _capture_pane(session), timeout=10)
            assert state.first_delta_sent.wait(60), _capture_pane(session)
            _wait_until(lambda: "hello" in _capture_pane(session), timeout=10)
            state.release.set()
            _wait_until(lambda: _capture_pane(session).count("homemaster>") >= 2)
            final_screen = _capture_pane(session)
            assert "observe" in final_screen
            assert final_screen.count("hello world") == 1
            assert "Model working" not in final_screen
            _tmux("send-keys", "-t", session, "/exit", "Enter")
            _wait_until(
                lambda: _tmux("has-session", "-t", session, check=False).returncode != 0,
                timeout=20,
            )
        finally:
            _tmux("kill-session", "-t", session, check=False)
