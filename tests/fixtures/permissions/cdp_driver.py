"""Minimal standard-library Chrome DevTools Protocol driver.

Used by the V3.4 acceptance verifier for the real-browser round: navigate
the served console, operate the approval card, screenshot each state, and
read back visible text. No third-party packages: HTTP via urllib, the
WebSocket handshake/framing via socket + hashlib. Chrome itself is the
only external dependency (verified present on hkust4).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import select
import socket
import struct
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def launch_chrome(
    executable: str, remote_port: int, user_data_dir: Path, extra_args: list[str] | None = None
) -> subprocess.Popen:
    user_data_dir.mkdir(parents=True, exist_ok=True)
    args = [
        executable, "--headless=new", "--no-sandbox", "--disable-gpu",
        "--hide-scrollbars", "--window-size=1280,900",
        f"--remote-debugging-port={remote_port}",
        f"--user-data-dir={str(user_data_dir)}",
        "about:blank",
    ]
    args.extend(extra_args or [])
    return subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def debugger_page_ws(port: int, timeout_s: float = 30.0) -> str:
    deadline = time.time() + timeout_s
    last_error = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as resp:
                targets = json.loads(resp.read().decode("utf-8"))
            for target in targets:
                if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                    return str(target["webSocketDebuggerUrl"])
            last_error = "no page target yet"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.3)
    raise RuntimeError(f"chrome debugger not ready: {last_error}")


class CDPError(RuntimeError):
    pass


class CDP:
    """Blocking CDP session over one page target."""

    def __init__(self, ws_url: str) -> None:
        self._sock = self._handshake(ws_url)
        self._next_id = 0
        self._buffer = b""

    @staticmethod
    def _handshake(ws_url: str) -> socket.socket:
        assert ws_url.startswith("ws://"), ws_url
        host_port, _, path = ws_url[5:].partition("/")
        host, _, port_text = host_port.partition(":")
        port = int(port_text or 80)
        key = base64.b64encode(os.urandom(16)).decode()
        sock = socket.create_connection((host, port), timeout=10)
        request = (
            f"GET /{path} HTTP/1.1\r\nHost: {host_port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode())
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = sock.recv(4096)
            if not chunk:
                raise CDPError("handshake closed")
            header += chunk
        lines = header.decode("latin1").split("\r\n")
        if "101" not in lines[0]:
            raise CDPError(f"handshake rejected: {lines[0]}")
        accept = ""
        for line in lines[1:]:
            if line.lower().startswith("sec-websocket-accept:"):
                accept = line.split(":", 1)[1].strip()
        expect = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode()).digest()
        ).decode()
        if accept != expect:
            raise CDPError("handshake accept mismatch")
        return sock

    def _send_text(self, payload: str) -> None:
        raw = payload.encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        length = len(raw)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(raw))
        self._sock.sendall(bytes(header) + masked)

    def _recv_frame(self, timeout: float) -> tuple[int, bytes]:
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise CDPError("cdp receive timeout")
            ready, _, _ = select.select([self._sock], [], [], remaining)
            if not ready:
                raise CDPError("cdp receive timeout")
            chunk = self._sock.recv(65536)
            if not chunk:
                raise CDPError("cdp connection closed")
            self._buffer += chunk
            frame = self._try_parse()
            if frame is not None:
                return frame

    def _try_parse(self) -> tuple[int, bytes] | None:
        data = self._buffer
        if len(data) < 2:
            return None
        first, second = data[0], data[1]
        opcode = first & 0x0F
        fin = bool(first & 0x80)
        masked = bool(second & 0x80)
        length = second & 0x7F
        offset = 2
        if length == 126:
            if len(data) < 4:
                return None
            (length,) = struct.unpack("!H", data[2:4])
            offset = 4
        elif length == 127:
            if len(data) < 10:
                return None
            (length,) = struct.unpack("!Q", data[2:10])
            offset = 10
        if masked:
            if len(data) < offset + 4:
                return None
            mask = data[offset:offset + 4]
            offset += 4
        if len(data) < offset + length:
            return None
        payload = data[offset:offset + length]
        self._buffer = data[offset + length:]
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode == 0x9:  # ping
            self._send_frame(0xA, payload)
            return self._try_parse()
        if opcode == 0x8:
            raise CDPError("cdp connection closed by peer")
        if opcode == 0x0:  # continuation
            opcode2, more = self._try_parse() or (0x0, b"")
            return opcode2, payload + more
        if not fin:
            opcode2, more = self._recv_continuation()
            return opcode, payload + more
        return opcode, payload

    def _recv_continuation(self) -> tuple[int, bytes]:
        parts = b""
        while True:
            opcode, payload = self._recv_frame(30.0)
            parts += payload
            if opcode != 0x0:
                return opcode, parts

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def call(self, method: str, params: dict[str, Any] | None = None,
             timeout: float = 30.0) -> Any:
        self._next_id += 1
        call_id = self._next_id
        self._send_text(json.dumps({"id": call_id, "method": method,
                                    "params": params or {}}))
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise CDPError(f"cdp call {method} timeout")
            opcode, payload = self._recv_frame(remaining)
            if opcode != 0x1:
                continue
            try:
                message = json.loads(payload.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            if message.get("id") != call_id:
                continue
            if "error" in message:
                raise CDPError(f"cdp {method} failed: {message['error']}")
            return message.get("result")

    def evaluate(self, expression: str, await_promise: bool = False) -> Any:
        result = self.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
        })
        payload = result.get("result", {})
        if payload.get("subtype") == "error":
            raise CDPError(f"page js error: {payload.get('description')}")
        return payload.get("value")

    def navigate(self, url: str) -> None:
        self.call("Page.navigate", {"url": url})

    def reload(self) -> None:
        self.call("Page.reload")

    def screenshot(self, path: Path) -> None:
        result = self.call("Page.captureScreenshot", {"format": "png"})
        path.write_bytes(base64.b64decode(result["data"]))

    def wait_for(self, js_condition: str, timeout_s: float = 60.0) -> None:
        deadline = time.time() + timeout_s
        last: Any = None
        while time.time() < deadline:
            try:
                last = self.evaluate(f"!!({js_condition})")
            except CDPError:
                last = False
            if last is True:
                return
            time.sleep(0.4)
        raise CDPError(f"wait_for timeout: {js_condition} (last={last!r})")

    def body_text(self) -> str:
        return str(self.evaluate("document.body ? document.body.innerText : ''"))

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()
