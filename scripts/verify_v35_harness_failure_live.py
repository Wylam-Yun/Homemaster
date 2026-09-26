#!/usr/bin/env python3
"""Real isolated-worker failure black box for the V3.5 Harness gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from verify_v35_sigint_live import _descendants, _paths, _start

from homemaster.alfworld import AlfworldHarness
from homemaster.alfworld.backend import BackendReceipt
from homemaster.alfworld.outcomes import AlfworldActionRequest
from homemaster.alfworld.worker_client import AlfworldWorkerError, WorkerThorBackend


def _digest(state: dict[str, Any]) -> str:
    encoded = json.dumps(state, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _receipt(receipt: BackendReceipt | None) -> dict[str, Any] | None:
    if receipt is None:
        return None
    return {
        "operation": receipt.operation,
        "external_return_code": receipt.external_return_code,
        "backend_attempted": receipt.backend_attempted,
        "state": receipt.state,
        "evidence_ref": receipt.evidence_ref,
        "error": receipt.error,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = _paths()
    root = args.output.parent.resolve()
    root.mkdir(parents=True, exist_ok=True)
    client = _start(paths, root, "failure")
    worker_pid = client.worker_pid
    descendants_before = _descendants(worker_pid)
    harness = AlfworldHarness(WorkerThorBackend(client))
    unknown_feedback: Any = None
    forced_response: Any = None
    close_receipt: BackendReceipt | None = None
    errors: list[str] = []
    try:
        reset_receipt = harness.reset(None)
        before_unknown = client.raw_state
        unknown_feedback = harness.execute(
            AlfworldActionRequest(
                tool_name="robot_go_to",
                arguments={"target": "ObjectThatDoesNotExist"},
            )
        )
        after_unknown = client.raw_state

        before_forced = client.raw_state
        try:
            # This bypasses model-facing tools only to force a real worker-side
            # backend error and verify the protocol's non-success receipt.
            forced_response = client._request(
                "act",
                {
                    "kind": "invalid",
                    "tool_name": "robot_manipulate",
                    "tool_args": {"action": "forced_failure"},
                },
                allow_error=True,
            )
        except AlfworldWorkerError as exc:
            errors.append(f"forced request transport failure: {exc}")
        after_forced = client.raw_state
        close_receipt = harness.close()
    except BaseException as exc:
        errors.append(f"run failure: {type(exc).__name__}: {exc}")
        try:
            close_receipt = client.close()
        except BaseException as close_exc:
            errors.append(f"close failure: {type(close_exc).__name__}: {close_exc}")
    descendants_after = _descendants(worker_pid)
    stderr_path = root / "failure.stderr.log"
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""

    forced_payload = None
    if forced_response is not None:
        forced_payload = {
            "request_id": forced_response.request_id,
            "status": forced_response.status,
            "external_return_code": forced_response.external_return_code,
            "backend_attempted": forced_response.backend_attempted,
            "error": forced_response.error,
        }
    payload = {
        "schema": "homemaster-v35-phase3-failure-live-v1",
        "trial_manifest": str(paths["manifest"]),
        "worker_pid": worker_pid,
        "descendants_before": descendants_before,
        "reset": _receipt(reset_receipt) if "reset_receipt" in locals() else None,
        "unknown_target": {
            "feedback": (
                {
                    "action": unknown_feedback.action,
                    "success": unknown_feedback.success,
                    "classification": unknown_feedback.classification,
                    "external_return_code": unknown_feedback.external_return_code,
                    "backend_attempted": unknown_feedback.backend_attempted,
                }
                if unknown_feedback is not None
                else None
            ),
            "before_digest": _digest(before_unknown) if "before_unknown" in locals() else None,
            "after_digest": _digest(after_unknown) if "after_unknown" in locals() else None,
            "state_unchanged": (
                "before_unknown" in locals()
                and "after_unknown" in locals()
                and _digest(before_unknown) == _digest(after_unknown)
            ),
        },
        "forced_backend_failure": {
            "response": forced_payload,
            "before_digest": _digest(before_forced) if "before_forced" in locals() else None,
            "after_digest": _digest(after_forced) if "after_forced" in locals() else None,
            "state_unchanged": (
                "before_forced" in locals()
                and "after_forced" in locals()
                and _digest(before_forced) == _digest(after_forced)
            ),
        },
        "close": _receipt(close_receipt),
        "worker_exit_code": client.worker_exit_code,
        "descendants_after": descendants_after,
        "stderr_path": str(stderr_path),
        "stderr": stderr,
        "errors": errors,
    }
    args.output.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")

    unknown = payload["unknown_target"]
    forced = payload["forced_backend_failure"]
    response = forced["response"]
    passed = (
        unknown["feedback"] is not None
        and unknown["feedback"]["classification"] == "target_unresolved"
        and unknown["feedback"]["external_return_code"] == 64
        and not unknown["feedback"]["backend_attempted"]
        and unknown["state_unchanged"]
        and response is not None
        and response["status"] == "error"
        and response["external_return_code"] != 0
        and response["backend_attempted"]
        and forced["state_unchanged"]
        and payload["close"] is not None
        and payload["close"]["external_return_code"] == 0
        and payload["worker_exit_code"] == 0
        and not descendants_after
        and not errors
        and "Traceback" not in stderr
    )
    print(json.dumps({"status": "PASS" if passed else "FAIL", "output": str(args.output)}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
