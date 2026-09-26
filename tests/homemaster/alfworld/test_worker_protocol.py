"""Black-box checks for the isolated worker process boundary."""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from homemaster.alfworld import worker_client
from homemaster.alfworld.worker_protocol import WorkerResponse

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKER = REPO_ROOT / "workers" / "alfworld_worker" / "main.py"
PROTOCOL = "homemaster-alfworld-v1"


def _read(process: subprocess.Popen[bytes]) -> dict[str, object]:
    assert process.stdout is not None
    line = process.stdout.readline()
    assert line
    payload = json.loads(line)
    assert isinstance(payload, dict)
    return payload


def _send(process: subprocess.Popen[bytes], request_id: str, operation: str) -> None:
    assert process.stdin is not None
    frame = {
        "protocol": PROTOCOL,
        "request_id": request_id,
        "operation": operation,
        "payload": {},
    }
    process.stdin.write((json.dumps(frame) + "\n").encode())
    process.stdin.flush()


def test_worker_protocol_lifecycle_and_duplicate_request_gate(tmp_path: Path) -> None:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    process = subprocess.Popen(
        [sys.executable, str(WORKER)],
        cwd=WORKER.parent,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        ready = _read(process)
        assert ready["protocol"] == PROTOCOL
        assert ready["status"] == "ready"
        assert ready["external_return_code"] == 0
        assert ready["backend_attempted"] is False

        _send(process, "duplicate", "unsupported")
        rejected = _read(process)
        assert rejected["status"] == "error"
        assert rejected["external_return_code"] != 0
        assert rejected["backend_attempted"] is False

        _send(process, "duplicate", "close")
        duplicate = _read(process)
        assert duplicate["status"] == "error"
        assert duplicate["error"]["code"] == "duplicate_request_id"  # type: ignore[index]

        _send(process, "close-1", "close")
        closed = _read(process)
        assert closed["status"] == "closed"
        assert closed["external_return_code"] == 0
        assert closed["result"]["cleanup_status"] == "succeeded"  # type: ignore[index]
        assert process.wait(timeout=5) == 0
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        assert "Traceback" not in stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_close_consumes_one_bounded_late_response_after_interrupted_action(monkeypatch) -> None:
    class FakeProcess:
        stdin = io.BytesIO()

        def poll(self) -> int | None:
            return None

    client = object.__new__(worker_client.AlfworldWorkerClient)
    client._process = FakeProcess()
    client._closed = False
    client._run_id = "run"
    client._request_timeout_s = 1.0
    client._request_history = []
    client._state = None
    client._raw_state = {}
    client._state_sequence = 0
    client._update_state = lambda _result: None

    responses = iter((WorkerResponse("late-action", "ok", 0, True, {"state": {}}),))

    def read_response(*_args, **_kwargs):
        try:
            return next(responses)
        except StopIteration:
            request_id = json.loads(client._process.stdin.getvalue().splitlines()[-1])["request_id"]
            return WorkerResponse(request_id, "closed", 0, True, {"cleanup_status": "succeeded"})

    monkeypatch.setattr(worker_client, "_read_response", read_response)
    response = client._request_locked("close", {}, allow_prior_responses=True)

    assert response.request_id.startswith("run-")
    assert client._request_history[0]["operation"] == "late_response"
    assert client._request_history[0]["request_id"] == "late-action"


def test_close_defers_sigint_until_worker_receipt_and_exit(tmp_path: Path, monkeypatch) -> None:
    class FakeProcess:
        stdin = io.BytesIO()
        pid = 1234
        returncode = None

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, *, timeout: float) -> int:
            del timeout
            self.returncode = 0
            return 0

    process = FakeProcess()
    stderr_path = tmp_path / "worker.stderr.log"
    stderr_path.write_text("worker closed\n", encoding="utf-8")
    stderr_handle = stderr_path.open("rb")
    client = object.__new__(worker_client.AlfworldWorkerClient)
    client._process = process
    client._stderr_handle = stderr_handle
    client._closed = False
    client._lock = threading.Lock()
    client._request_timeout_s = 1.0
    client._request_history = []
    client._close_sigint_count = 0
    client._run_id = "run"
    client._state = None
    client._raw_state = {}
    client._state_sequence = 0
    client._update_state = lambda _result: None

    def close_request(*_args, **_kwargs):
        signal.raise_signal(signal.SIGINT)
        return WorkerResponse("run-close", "closed", 0, True, {"cleanup_status": "succeeded"})

    monkeypatch.setattr(client, "_request_locked", close_request)
    receipt = client.close()

    assert receipt.external_return_code == 0
    assert receipt.state["sigint_count"] == 1
    assert client.close_sigint_count == 1
    assert process.returncode == 0
