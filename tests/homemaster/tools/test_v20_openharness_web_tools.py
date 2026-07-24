"""External HTTP gates for the V2.0 OpenHarness web-tool adapters."""

from __future__ import annotations

import hashlib
import threading
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from homemaster.tools.contracts import ToolExecutionStatus
from tests.homemaster.tools.universal_harness import execute, registry

_BODY = b"first line\nsecond line\n"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/content")
            self.end_headers()
            return
        if self.path == "/content":
            self._send(200, _BODY)
            return
        if self.path == "/too-large":
            self._send(200, b"x" * 501)
            return
        if self.path == "/encoded":
            self._send(200, _BODY, extra_headers={"Content-Encoding": "gzip"})
            return
        if self.path.startswith("/search"):
            body = (
                b'<a class="result__a" href="https://example.test/one">First result</a>'
                b'<div class="result__snippet">A useful snippet.</div>'
            )
            self._send(200, body, content_type="text/html; charset=UTF-8")
            return
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        del format, args

    def _send(
        self,
        status: int,
        body: bytes,
        *,
        content_type: str = "text/plain; charset=utf-8",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def _http_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


async def _execute(profile, root: Path, name: str, arguments: dict[str, object]):
    return await execute(
        profile,
        root,
        name,
        arguments,
        capabilities=("tool.read", "tool.mutate", "tool.auto", "network.http"),
    )


def test_universal_registry_registers_web_tools() -> None:
    names = set(registry().all_names())

    assert {"web_fetch", "web_search"} <= names


@pytest.mark.asyncio
async def test_web_fetch_follows_redirect_and_records_independent_identity_bytes(
    tmp_path: Path,
) -> None:
    profile = registry()
    with _http_server() as server:
        result = await _execute(profile, tmp_path, "web_fetch", {"url": f"{server}/redirect"})

        request = urllib.request.Request(
            f"{server}/content",
            headers={"Accept-Encoding": "identity"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            independently_observed = response.read()

    metadata = result.data["metadata"]
    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data["content"] == _BODY.decode("utf-8")
    assert result.data["summary"] == result.text
    assert "External content" in result.text
    assert metadata["url"].endswith("/content")
    assert metadata["status_code"] == 200
    assert metadata["raw_byte_count"] == len(_BODY)
    assert metadata["raw_sha256"] == hashlib.sha256(_BODY).hexdigest()
    assert metadata["complete"] is True
    assert independently_observed == _BODY
    assert hashlib.sha256(independently_observed).hexdigest() == metadata["raw_sha256"]


@pytest.mark.asyncio
async def test_web_fetch_rejects_encoded_and_oversized_responses(tmp_path: Path) -> None:
    profile = registry()
    with _http_server() as server:
        encoded = await _execute(profile, tmp_path, "web_fetch", {"url": f"{server}/encoded"})
        too_large = await _execute(
            profile,
            tmp_path,
            "web_fetch",
            {"url": f"{server}/too-large", "max_chars": 500},
        )

    assert encoded.status is ToolExecutionStatus.FAILURE
    assert encoded.error is not None
    assert encoded.error.code == "unsupported_content_encoding"
    assert encoded.backend_attempted is True
    assert too_large.status is ToolExecutionStatus.FAILURE
    assert too_large.error is not None
    assert too_large.error.code == "response_too_large"
    assert too_large.backend_attempted is True


@pytest.mark.asyncio
async def test_web_fetch_rejects_embedded_credentials_before_network_io(tmp_path: Path) -> None:
    profile = registry()

    result = await _execute(
        profile,
        tmp_path,
        "web_fetch",
        {"url": "http://user:password@127.0.0.1:9/content"},
    )

    assert result.status is ToolExecutionStatus.FAILURE
    assert result.error is not None
    assert result.error.code == "invalid_url"
    assert result.backend_attempted is False


@pytest.mark.asyncio
async def test_web_search_parses_real_http_response(tmp_path: Path) -> None:
    profile = registry()
    with _http_server() as server:
        result = await _execute(
            profile,
            tmp_path,
            "web_search",
            {"query": "home tools", "search_url": f"{server}/search", "max_results": 1},
        )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data["query"] == "home tools"
    assert result.data["results"] == (
        {
            "title": "First result",
            "url": "https://example.test/one",
            "snippet": "A useful snippet.",
        },
    )
    assert result.data["metadata"]["status_code"] == 200
