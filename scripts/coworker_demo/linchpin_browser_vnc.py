"""Browser/VNC linchpin helpers and executable gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

from PIL import Image, ImageStat

_CLICK_RECEIPT_KEYS = {
    "action_id",
    "marker",
    "backend_count",
    "dom_count",
    "page_url",
}
_RFB_BANNER = re.compile(rb"RFB 003\.(003|007|008)\n")


@dataclass(frozen=True)
class ClickReceipt:
    action_id: str
    marker: str
    backend_count: int
    dom_count: int
    page_url: str


def parse_click_receipt(text: str) -> ClickReceipt:
    """Parse the browser/backend receipt used by the L1 black-box gate."""

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("click receipt is not valid JSON") from exc
    if not isinstance(raw, dict) or set(raw) != _CLICK_RECEIPT_KEYS:
        raise ValueError("click receipt fields do not match the frozen contract")

    action_id = raw["action_id"]
    marker = raw["marker"]
    page_url = raw["page_url"]
    backend_count = raw["backend_count"]
    dom_count = raw["dom_count"]
    if not all(isinstance(value, str) and value for value in (action_id, marker, page_url)):
        raise ValueError("click receipt string fields must be non-empty")
    if type(backend_count) is not int or type(dom_count) is not int:
        raise ValueError("click receipt counts must be integers")
    if backend_count < 1 or backend_count != dom_count:
        raise ValueError("DOM and backend counts do not prove the same mutation")

    parsed_url = urlparse(page_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
        raise ValueError("click receipt page URL must be HTTP(S)")
    return ClickReceipt(
        action_id=action_id,
        marker=marker,
        backend_count=backend_count,
        dom_count=dom_count,
        page_url=page_url,
    )


def parse_rfb_banner(banner: bytes) -> str:
    """Return a supported RFB protocol version from one exact 12-byte banner."""

    if _RFB_BANNER.fullmatch(banner) is None:
        raise ValueError("invalid or unsupported RFB banner")
    return banner.decode("ascii").strip().removeprefix("RFB ")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_stats(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        grayscale = rgb.convert("L")
        histogram = grayscale.histogram()
        total_pixels = rgb.width * rgb.height
        nonblack_pixels = sum(histogram[17:])
        variance = ImageStat.Stat(grayscale).var[0]
        return {
            "width": rgb.width,
            "height": rgb.height,
            "nonblack_pixels": nonblack_pixels,
            "nonblack_ratio": nonblack_pixels / total_pixels,
            "grayscale_variance": variance,
        }


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _find_display(display_min: int, display_max: int) -> int:
    for number in range(display_min, display_max + 1):
        if Path(f"/tmp/.X11-unix/X{number}").exists():
            continue
        if Path(f"/tmp/.X{number}-lock").exists():
            continue
        return number
    raise RuntimeError("no free X11 display in configured range")


def _receive_rfb_banner(host: str, port: int, timeout_s: float = 5.0) -> str:
    with socket.create_connection((host, port), timeout=timeout_s) as connection:
        connection.settimeout(timeout_s)
        chunks = bytearray()
        while len(chunks) < 12:
            chunk = connection.recv(12 - len(chunks))
            if not chunk:
                break
            chunks.extend(chunk)
    return parse_rfb_banner(bytes(chunks))


class _BackendState:
    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.count = 0
        self.last_action_id: str | None = None
        self._action_ids: set[str] = set()
        self._lock = threading.Lock()

    def increment(self, action_id: str) -> dict[str, Any]:
        with self._lock:
            if action_id in self._action_ids:
                raise ValueError("action_id replayed")
            self._action_ids.add(action_id)
            self.count += 1
            self.last_action_id = action_id
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "marker": self.marker,
            "count": self.count,
            "last_action_id": self.last_action_id,
        }


def _page_html(marker: str) -> bytes:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{marker} ready</title>
  <style>
    html, body {{ margin: 0; width: 100%; height: 100%; font-family: sans-serif; background: #f7f8fa; color: #17202a; }}
    header {{ height: 84px; padding: 22px 36px; box-sizing: border-box; color: white; background: #17202a; font-size: 30px; }}
    main {{ padding: 40px; }}
    #marker {{ font-size: 54px; font-weight: 800; margin: 26px 0; color: #006b5f; }}
    #count {{ font-size: 64px; font-variant-numeric: tabular-nums; }}
    button {{ width: 300px; height: 64px; border: 0; border-radius: 6px; color: white; background: #006b5f; font-size: 24px; cursor: pointer; }}
    button:disabled {{ opacity: .55; }}
    .pulse {{ animation: pulse 1s infinite alternate; }}
    @keyframes pulse {{ from {{ color: #006b5f; }} to {{ color: #b03a2e; }} }}
  </style>
</head>
<body>
  <header>HomeMaster headed-browser linchpin</header>
  <main>
    <div id="marker" class="pulse">{marker}</div>
    <div>Persisted backend count</div>
    <div id="count">0</div>
    <button type="button" data-bid="linchpin-apply">Apply real DOM action</button>
    <pre id="receipt">waiting</pre>
  </main>
  <script>
    window.__linchpinReceipt = null;
    document.querySelector('[data-bid="linchpin-apply"]').addEventListener('click', async (event) => {{
      const button = event.currentTarget;
      button.disabled = true;
      const actionId = crypto.randomUUID();
      const response = await fetch('/api/increment', {{
        method: 'POST',
        headers: {{'content-type': 'application/json'}},
        body: JSON.stringify({{action_id: actionId}}),
      }});
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || 'backend rejected action');
      document.querySelector('#count').textContent = String(payload.count);
      document.querySelector('#receipt').textContent = JSON.stringify(payload);
      document.title = `{marker} count ${{payload.count}}`;
      window.__linchpinReceipt = payload;
      button.disabled = false;
    }});
  </script>
</body>
</html>
""".encode()


def _handler_factory(state: _BackendState, request_log: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _write(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
            self.send_response(status.value)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            self.send_header("cache-control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            self._write(status, "application/json", json.dumps(payload, sort_keys=True).encode())

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                self._write(HTTPStatus.OK, "text/html; charset=utf-8", _page_html(state.marker))
                return
            if self.path == "/api/state":
                self._json(HTTPStatus.OK, state.snapshot())
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/increment":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("content-length", "0"))
                payload = json.loads(self.rfile.read(length))
                if set(payload) != {"action_id"} or not isinstance(payload["action_id"], str):
                    raise ValueError("invalid payload")
                snapshot = state.increment(payload["action_id"])
            except (ValueError, json.JSONDecodeError) as exc:
                self._json(HTTPStatus.CONFLICT, {"error": str(exc)})
                return
            self._json(HTTPStatus.OK, snapshot)

        def log_message(self, format_string: str, *args: object) -> None:
            with request_log.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "client": self.client_address[0],
                            "message": format_string % args,
                            "path": self.path,
                            "timestamp_ns": time.time_ns(),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )

    return Handler


def _wait_for_vnc(process: subprocess.Popen[str], display: int, port: int, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    last_error = "not attempted"
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(f"Xtigervnc exited before ready: {return_code}")
        if Path(f"/tmp/.X11-unix/X{display}").exists():
            try:
                return _receive_rfb_banner("127.0.0.1", port, timeout_s=1.0)
            except (OSError, ValueError) as exc:
                last_error = str(exc)
        time.sleep(0.1)
    raise RuntimeError(f"Xtigervnc did not become ready: {last_error}")


def _listener_evidence(port: int) -> dict[str, Any]:
    completed = subprocess.run(
        ["/usr/bin/ss", "-H", "-ltnp"],
        text=True,
        capture_output=True,
        check=False,
    )
    suffix = f":{port}"
    matching = []
    allowed = True
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) < 4 or not fields[3].endswith(suffix):
            continue
        matching.append(line)
        local_address = fields[3][: -len(suffix)].strip("[]")
        if local_address not in {"127.0.0.1", "::1"}:
            allowed = False
    return {
        "return_code": completed.returncode,
        "matching_listeners": matching,
        "loopback_only": bool(matching) and allowed,
        "stderr": completed.stderr,
    }


def _load_mac_proof(path: Path, marker: str, artifact_dir: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    screenshot_name = payload.get("screenshot_file")
    screenshot = artifact_dir / str(screenshot_name)
    required = {
        "marker": marker,
        "marker_confirmed": True,
        "rfb_version": "003.008",
        "screen_sharing_open_returncode": 0,
        "screencapture_returncode": 0,
        "tesseract_returncode": 0,
        "tunnel_alive_at_capture": True,
    }
    checks = {key: payload.get(key) == value for key, value in required.items()}
    checks["screenshot_exists"] = screenshot.is_file()
    checks["screenshot_hash"] = checks["screenshot_exists"] and _sha256(screenshot) == payload.get(
        "screenshot_sha256"
    )
    payload["validation_checks"] = checks
    payload["valid"] = all(checks.values())
    return payload


def _run_remote(args: argparse.Namespace) -> int:
    from playwright.sync_api import sync_playwright

    artifact_dir = args.artifact_dir.resolve()
    artifact_dir.mkdir(parents=True, exist_ok=False)
    release_file = artifact_dir / "release"
    mac_proof_path = artifact_dir / "mac_proof.json"
    marker = f"COWORKERL1{uuid.uuid4().hex[:8].upper()}"
    display_number = _find_display(args.display_min, args.display_max)
    display = f":{display_number}"
    vnc_port = _find_free_port()
    state = _BackendState(marker)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        _handler_factory(state, artifact_dir / "http_requests.jsonl"),
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    http_port = int(server.server_address[1])
    page_url = f"http://127.0.0.1:{http_port}/"

    vnc_stdout = (artifact_dir / "xtigervnc.stdout.log").open("w", encoding="utf-8")
    vnc_stderr = (artifact_dir / "xtigervnc.stderr.log").open("w", encoding="utf-8")
    vnc_command = [
        args.tigervnc_executable,
        display,
        "-geometry",
        "1920x1080",
        "-depth",
        "24",
        "-rfbport",
        str(vnc_port),
        "-interface",
        "127.0.0.1",
        "-localhost",
        "yes",
        "-SecurityTypes",
        "None",
        "-ac",
        "-nolisten",
        "tcp",
        "-br",
    ]
    vnc_process = subprocess.Popen(
        vnc_command,
        text=True,
        stdout=vnc_stdout,
        stderr=vnc_stderr,
    )
    result: dict[str, Any] = {
        "schema_version": 1,
        "pass": False,
        "marker": marker,
        "display": display,
        "vnc_port": vnc_port,
        "page_url": page_url,
        "vnc_command": vnc_command,
        "vnc_pid": vnc_process.pid,
    }
    browser = None
    try:
        rfb_version = _wait_for_vnc(vnc_process, display_number, vnc_port, args.startup_timeout_s)
        environment = os.environ.copy()
        environment["DISPLAY"] = display
        xdpyinfo = subprocess.run(
            ["/usr/bin/xdpyinfo"],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        (artifact_dir / "xdpyinfo.stdout.log").write_text(xdpyinfo.stdout, encoding="utf-8")
        (artifact_dir / "xdpyinfo.stderr.log").write_text(xdpyinfo.stderr, encoding="utf-8")

        playwright = sync_playwright().start()
        try:
            browser = playwright.chromium.launch(
                executable_path=args.chrome_executable,
                headless=False,
                env=environment,
                args=[
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--window-position=640,0",
                    "--window-size=1280,720",
                ],
            )
            context = browser.new_context(viewport={"width": 1280, "height": 720})
            page = context.new_page()
            page.goto(page_url, wait_until="networkidle", timeout=20_000)
            locator = page.locator('[data-bid="linchpin-apply"]')
            if locator.count() != 1 or not locator.is_visible() or not locator.is_enabled():
                raise RuntimeError("data-bid click target is not unique, visible and enabled")
            locator.click(timeout=20_000)
            page.wait_for_function("window.__linchpinReceipt !== null", timeout=20_000)
            browser_receipt = page.evaluate("window.__linchpinReceipt")
            dom_count = int(page.locator("#count").inner_text())
            with urlopen(f"http://127.0.0.1:{http_port}/api/state", timeout=5) as response:
                backend_state = json.loads(response.read())
                backend_status = response.status
            click_receipt = parse_click_receipt(
                json.dumps(
                    {
                        "action_id": browser_receipt["last_action_id"],
                        "marker": browser_receipt["marker"],
                        "backend_count": backend_state["count"],
                        "dom_count": dom_count,
                        "page_url": page.url,
                    }
                )
            )
            screenshot = artifact_dir / "browser.png"
            page.screenshot(path=str(screenshot), full_page=True)
            screenshot_stats = _image_stats(screenshot)

            xwininfo = subprocess.run(
                ["/usr/bin/xwininfo", "-root", "-tree"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            (artifact_dir / "xwininfo.stdout.log").write_text(xwininfo.stdout, encoding="utf-8")
            (artifact_dir / "xwininfo.stderr.log").write_text(xwininfo.stderr, encoding="utf-8")
            listeners = _listener_evidence(vnc_port)
            remote_checks = {
                "rfb_handshake": rfb_version == "003.008",
                "xdpyinfo": xdpyinfo.returncode == 0,
                "backend_http_status": backend_status == 200,
                "backend_count": backend_state["count"] == 1,
                "backend_action_id": backend_state["last_action_id"] == click_receipt.action_id,
                "dom_count": click_receipt.dom_count == 1,
                "marker": click_receipt.marker == marker,
                "x11_window": xwininfo.returncode == 0 and marker in xwininfo.stdout,
                "screenshot_nonblank": screenshot_stats["nonblack_ratio"] > 0.25
                and screenshot_stats["grayscale_variance"] > 100,
                "loopback_only": listeners["return_code"] == 0 and listeners["loopback_only"],
            }
            result.update(
                {
                    "rfb_version": rfb_version,
                    "xdpyinfo_returncode": xdpyinfo.returncode,
                    "browser_receipt": click_receipt.__dict__,
                    "backend_state": backend_state,
                    "browser_screenshot": str(screenshot),
                    "browser_screenshot_sha256": _sha256(screenshot),
                    "browser_screenshot_stats": screenshot_stats,
                    "xwininfo_returncode": xwininfo.returncode,
                    "listeners": listeners,
                    "remote_checks": remote_checks,
                    "remote_pass": all(remote_checks.values()),
                }
            )
            _atomic_json(artifact_dir / "remote_ready.json", result)
            print(
                "LINCHPIN_READY "
                + json.dumps(
                    {
                        "artifact_dir": str(artifact_dir),
                        "display": display,
                        "marker": marker,
                        "page_url": page_url,
                        "vnc_port": vnc_port,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

            deadline = time.monotonic() + args.hold_timeout_s
            while not release_file.exists() and time.monotonic() < deadline:
                if vnc_process.poll() is not None:
                    raise RuntimeError("Xtigervnc exited while waiting for Mac/L2 proof")
                time.sleep(0.2)
            if not release_file.exists():
                raise TimeoutError("release file was not created before hold timeout")
            if args.require_mac_proof:
                mac_proof = _load_mac_proof(mac_proof_path, marker, artifact_dir)
                result["mac_proof"] = mac_proof
                result["pass"] = bool(result["remote_pass"] and mac_proof["valid"])
            else:
                result["mac_proof"] = {"required": False, "status": "deferred_by_user"}
                result["pass"] = bool(result["remote_pass"])
        finally:
            if browser is not None:
                browser.close()
            playwright.stop()
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        if vnc_process.poll() is None:
            vnc_process.terminate()
        try:
            vnc_returncode = vnc_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            vnc_process.kill()
            vnc_returncode = vnc_process.wait(timeout=5)
        vnc_stdout.close()
        vnc_stderr.close()
        result["vnc_returncode"] = vnc_returncode
        result["vnc_cleanup_accepted"] = vnc_returncode in {0, -15}
        result["pass"] = bool(result.get("pass") and result["vnc_cleanup_accepted"])
        _atomic_json(artifact_dir / "result.json", result)
        print("LINCHPIN_RESULT " + json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["pass"] else 1


def _normalize_ocr(text: str) -> str:
    return "".join(character for character in text.upper() if character.isalnum())


def _submit_screen_sharing_password() -> subprocess.CompletedProcess[str]:
    script = """
tell application "System Events"
  tell process "Screen Sharing"
    if (count of windows) is 0 then return "no-window"
    tell window 1
      if (count of text fields) is 0 then return "no-dialog"
      if (count of buttons) < 2 then return "no-connect-button"
      set value of text field 1 to "linchpin"
      click button 2
      return "submitted"
    end tell
  end tell
end tell
"""
    return subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        text=True,
        capture_output=True,
        check=False,
    )


def _run_mac_capture(args: argparse.Namespace) -> int:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    screenshot = output_dir / "screen-sharing.png"
    rfb_version = _receive_rfb_banner("127.0.0.1", args.port, timeout_s=5.0)
    tunnel_alive = True
    try:
        os.kill(args.tunnel_pid, 0)
    except OSError:
        tunnel_alive = False
    opened = subprocess.run(
        ["/usr/bin/open", "-a", "Screen Sharing", f"vnc://127.0.0.1:{args.port}"],
        text=True,
        capture_output=True,
        check=False,
    )
    deadline = time.monotonic() + args.viewer_wait_s
    password_attempts: list[dict[str, Any]] = []
    activated: subprocess.CompletedProcess[str] | None = None
    captured: subprocess.CompletedProcess[str] | None = None
    ocr: subprocess.CompletedProcess[str] | None = None
    marker_confirmed = False
    while time.monotonic() < deadline and not marker_confirmed:
        time.sleep(0.5)
        password_submission = _submit_screen_sharing_password()
        password_attempts.append(
            {
                "returncode": password_submission.returncode,
                "stdout": password_submission.stdout.strip(),
                "stderr": password_submission.stderr,
            }
        )
        time.sleep(1)
        activated = subprocess.run(
            ["/usr/bin/osascript", "-e", 'tell application "Screen Sharing" to activate'],
            text=True,
            capture_output=True,
            check=False,
        )
        captured = subprocess.run(
            ["/usr/sbin/screencapture", "-x", "-t", "png", str(screenshot)],
            text=True,
            capture_output=True,
            check=False,
        )
        if captured.returncode != 0 or not screenshot.is_file():
            continue
        ocr = subprocess.run(
            [args.tesseract_executable, str(screenshot), "stdout", "--psm", "11"],
            text=True,
            capture_output=True,
            check=False,
        )
        marker_confirmed = _normalize_ocr(args.marker) in _normalize_ocr(ocr.stdout)
    if activated is None or captured is None or ocr is None or not screenshot.is_file():
        raise RuntimeError("Screen Sharing did not produce a screenshot and OCR attempt")
    proof = {
        "schema_version": 1,
        "marker": args.marker,
        "marker_confirmed": marker_confirmed,
        "rfb_version": rfb_version,
        "screen_sharing_open_returncode": opened.returncode,
        "screen_sharing_open_stderr": opened.stderr,
        "password_attempts": password_attempts,
        "screen_sharing_activate_returncode": activated.returncode,
        "screen_sharing_activate_stderr": activated.stderr,
        "screencapture_returncode": captured.returncode,
        "screencapture_stderr": captured.stderr,
        "tesseract_returncode": ocr.returncode,
        "ocr_text": ocr.stdout,
        "tunnel_alive_at_capture": tunnel_alive,
        "screenshot_file": screenshot.name,
        "screenshot_sha256": _sha256(screenshot),
        "screenshot_stats": _image_stats(screenshot),
    }
    _atomic_json(output_dir / "mac_proof.json", proof)
    print(json.dumps(proof, sort_keys=True))
    required = (
        marker_confirmed
        and rfb_version == "003.008"
        and opened.returncode == 0
        and captured.returncode == 0
        and ocr.returncode == 0
        and tunnel_alive
    )
    return 0 if required else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)

    remote = subparsers.add_parser("remote", help="run the remote browser/VNC gate")
    remote.add_argument("--artifact-dir", type=Path, required=True)
    remote.add_argument("--chrome-executable", default="/usr/bin/google-chrome")
    remote.add_argument("--tigervnc-executable", default="/usr/bin/Xtigervnc")
    remote.add_argument("--display-min", type=int, default=120)
    remote.add_argument("--display-max", type=int, default=159)
    remote.add_argument("--startup-timeout-s", type=float, default=20.0)
    remote.add_argument("--hold-timeout-s", type=float, default=240.0)
    remote.add_argument("--require-mac-proof", action="store_true")

    mac_capture = subparsers.add_parser("mac-capture", help="capture Mac Screen Sharing proof")
    mac_capture.add_argument("--port", type=int, required=True)
    mac_capture.add_argument("--marker", required=True)
    mac_capture.add_argument("--tunnel-pid", type=int, required=True)
    mac_capture.add_argument("--output-dir", type=Path, required=True)
    mac_capture.add_argument("--viewer-wait-s", type=float, default=20.0)
    mac_capture.add_argument("--tesseract-executable", default=shutil.which("tesseract"))
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.mode == "remote":
        return _run_remote(args)
    if not args.tesseract_executable:
        raise SystemExit("tesseract executable is required for mac-capture")
    return _run_mac_capture(args)


if __name__ == "__main__":
    sys.exit(main())
