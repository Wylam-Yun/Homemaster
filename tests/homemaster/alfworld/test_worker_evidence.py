from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from homemaster.alfworld.benchmark.worker_adapter import WorkerAlfworldAdapter
from homemaster.alfworld.gateway import CleanupResult


def test_worker_evidence_records_close_receipt_and_cleanup(tmp_path: Path) -> None:
    stderr_path = tmp_path / "worker.stderr.log"
    stderr_path.write_text("worker ready\n", encoding="utf-8")
    adapter = object.__new__(WorkerAlfworldAdapter)
    adapter.client = SimpleNamespace(
        worker_pid=123,
        worker_exit_code=0,
        request_history=[
            {
                "operation": "close",
                "request_id": "close-1",
                "external_return_code": 0,
            }
        ],
        raw_state={"won": True},
        stderr_path=stderr_path,
    )

    output = tmp_path / "worker.json"
    adapter.write_worker_evidence(
        output,
        CleanupResult(status="succeeded", evidence_ref="worker:123:close"),
    )

    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["close"] == {
        "status": "succeeded",
        "evidence_ref": "worker:123:close",
        "request": {
            "operation": "close",
            "request_id": "close-1",
            "external_return_code": 0,
        },
    }
    assert evidence["cleanup"] == {"worker_exited": True}
    assert evidence["stderr"] == "worker ready\n"
