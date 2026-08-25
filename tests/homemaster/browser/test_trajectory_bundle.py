from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from homemaster.browser.trajectory_bundle import (
    TrajectoryBundleError,
    materialize_trajectory_bundle,
    verify_trajectory_bundle,
)


def _event(
    event_type: str,
    call_id: str,
    name: str,
    *,
    arguments: dict | None = None,
    data: dict | None = None,
) -> dict:
    payload = (
        {"arguments": arguments or {}} if event_type.endswith("started") else {"data": data or {}}
    )
    return {
        "type": event_type,
        "session_id": "session-1",
        "run_id": "run-1",
        "turn_index": 0,
        "payload": payload,
        "tool_call_id": call_id,
        "name": name,
        "timestamp": "2026-08-24T00:00:00+00:00",
        "event_id": f"event-{call_id}-{event_type}",
        "duration_ms": 12.5 if not event_type.endswith("started") else None,
        "gateway_generation": None,
    }


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _rewrite_manifest_artifact(output: Path, relative: str) -> None:
    artifact = output / relative
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][relative] = {
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "byte_count": artifact.stat().st_size,
    }
    _write_json(manifest_path, manifest)


def test_materialized_bundle_preserves_exact_tool_lifecycle_and_hashes(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    browser_dir = run_dir / "browser" / "run-1"
    screenshot_dir = browser_dir / "screenshots"
    screenshot_dir.mkdir(parents=True)
    events = []
    protocol_blocked = _event(
        "tool.call_completed",
        "blocked-without-start",
        "browser_click",
        data={
            "status": "protocol_blocked",
            "backend_attempted": False,
            "error_code": "browser_inspect_required",
        },
    )
    protocol_blocked["payload"]["is_error"] = False
    events.append(protocol_blocked)
    calls = [
        ("1", "load_skill", {"name": "change-ticket-executor"}),
        ("2", "browser_inspect", {}),
        ("3", "browser_click", {"element_id": "change-link"}),
        ("4", "browser_click", {"element_id": "e1"}),
        ("5", "terminal", {"command": "grep -Fxq ..."}),
        ("6", "browser_click", {"element_id": "asset-link"}),
        ("7", "browser_click", {"element_id": "e2"}),
    ]
    for call_id, name, arguments in calls:
        data = {"success": True, "evidence_ref": f"evidence:{call_id}"}
        if call_id == "3":
            data["url_after"] = "http://test/ops/change"
        elif call_id == "6":
            data["url_after"] = "http://test/ops/asset-check"
        events.extend(
            [
                _event("tool.call_started", call_id, name, arguments=arguments),
                _event(
                    "tool.call_completed",
                    call_id,
                    name,
                    data=data,
                ),
            ]
        )
    (run_dir / "runtime_events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    (browser_dir / "browser_actions.jsonl").write_text('{"outcome":"success"}\n')
    (browser_dir / "browser_trace.zip").write_bytes(b"trace")
    (browser_dir / "video.webm").write_bytes(b"video")
    (screenshot_dir / "observe-0001.png").write_bytes(b"png")
    ticket = tmp_path / "ticket.json"
    _write_json(ticket, {"ticket_id": "OPS202608220001"})
    terminal = tmp_path / "terminal.json"
    _write_json(terminal, {"exit_code": 0, "stdout": "CONFIG_VERSION_OK\n"})
    final_state = tmp_path / "final-state.json"
    _write_json(
        final_state,
        {
            "fixture": {"before_version": "0.9.0", "after_version": "1.0.0"},
            "asset": {
                "hostname": "fixture-node-01",
                "status": "running",
                "version": "1.0.0",
            },
            "evidence_records": {"precheck": "WSO-before", "postcheck": "WSO-after"},
        },
    )
    output = tmp_path / "ops-runs" / "run-1" / "deterministic"

    result = materialize_trajectory_bundle(
        run_dir=run_dir,
        output_dir=output,
        ticket_path=ticket,
        terminal_verification_path=terminal,
        final_state_path=final_state,
        repository_commits={"homemaster": "abc", "ant-design-pro": "def"},
    )

    assert result == output
    rows = [json.loads(line) for line in (output / "trajectory.jsonl").read_text().splitlines()]
    assert [row["sequence"] for row in rows] == list(range(1, 8))
    assert [row["sop_stage"] for row in rows] == [
        "framework",
        "check_before_change",
        "check_before_change",
        "change_implement",
        "change_implement",
        "change_implement",
        "change_verified",
    ]
    verify_trajectory_bundle(output)

    (output / "browser_actions.jsonl").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(TrajectoryBundleError, match="hash mismatch"):
        verify_trajectory_bundle(output)


def test_verifier_rebuilds_trajectory_from_raw_runtime_events(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    browser_dir = run_dir / "browser" / "run-1"
    screenshot_dir = browser_dir / "screenshots"
    screenshot_dir.mkdir(parents=True)
    calls = [
        ("1", "browser_inspect", {}, {}),
        (
            "2",
            "browser_click",
            {"element_id": "change-link"},
            {"url_after": "http://test/ops/change"},
        ),
        ("3", "terminal", {"command": "grep -Fxq ..."}, {}),
        (
            "4",
            "browser_click",
            {"element_id": "asset-link"},
            {"url_after": "http://test/ops/asset-check"},
        ),
        ("5", "browser_inspect", {}, {"url": "http://test/ops/asset-check"}),
    ]
    events = []
    for call_id, name, arguments, extra_data in calls:
        events.extend(
            [
                _event("tool.call_started", call_id, name, arguments=arguments),
                _event(
                    "tool.call_completed",
                    call_id,
                    name,
                    data={"success": True, **extra_data},
                ),
            ]
        )
    (run_dir / "runtime_events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    (browser_dir / "browser_actions.jsonl").write_text('{"outcome":"success"}\n')
    (browser_dir / "browser_trace.zip").write_bytes(b"trace")
    (browser_dir / "video.webm").write_bytes(b"video")
    (screenshot_dir / "observe-0001.png").write_bytes(b"png")
    ticket = tmp_path / "ticket.json"
    _write_json(ticket, {"ticket_id": "OPS202608220001"})
    terminal = tmp_path / "terminal.json"
    _write_json(terminal, {"exit_code": 0, "stdout": "CONFIG_VERSION_OK\n"})
    final_state = tmp_path / "final-state.json"
    _write_json(
        final_state,
        {
            "fixture": {"before_version": "0.9.0", "after_version": "1.0.0"},
            "asset": {
                "hostname": "fixture-node-01",
                "status": "running",
                "version": "1.0.0",
            },
            "evidence_records": {"precheck": "WSO-before", "postcheck": "WSO-after"},
        },
    )
    output = tmp_path / "bundle"
    materialize_trajectory_bundle(
        run_dir=run_dir,
        output_dir=output,
        ticket_path=ticket,
        terminal_verification_path=terminal,
        final_state_path=final_state,
        repository_commits={"homemaster": "abc", "ant-design-pro": "def"},
    )

    rows = [
        json.loads(line)
        for line in (output / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    rows[0]["sop_stage"], rows[-1]["sop_stage"] = (
        rows[-1]["sop_stage"],
        rows[0]["sop_stage"],
    )
    (output / "trajectory.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    _rewrite_manifest_artifact(output, "trajectory.jsonl")

    with pytest.raises(TrajectoryBundleError, match="raw runtime events"):
        verify_trajectory_bundle(output)


def test_failed_final_verification_does_not_publish_bundle(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    browser_dir = run_dir / "browser" / "run-1"
    screenshot_dir = browser_dir / "screenshots"
    screenshot_dir.mkdir(parents=True)
    events = [
        _event("tool.call_started", "1", "browser_inspect"),
        _event("tool.call_completed", "1", "browser_inspect", data={"success": True}),
        _event(
            "tool.call_started",
            "2",
            "terminal",
            arguments={"command": "grep -Fxq ..."},
        ),
        _event("tool.call_completed", "2", "terminal", data={"success": True}),
    ]
    (run_dir / "runtime_events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    (browser_dir / "browser_actions.jsonl").write_text('{"outcome":"success"}\n')
    (browser_dir / "browser_trace.zip").write_bytes(b"trace")
    (browser_dir / "video.webm").write_bytes(b"video")
    (screenshot_dir / "observe-0001.png").write_bytes(b"png")
    ticket = tmp_path / "ticket.json"
    _write_json(ticket, {"ticket_id": "OPS202608220001"})
    terminal = tmp_path / "terminal.json"
    _write_json(terminal, {"exit_code": 0, "stdout": "CONFIG_VERSION_OK\n"})
    final_state = tmp_path / "final-state.json"
    _write_json(
        final_state,
        {
            "fixture": {"before_version": "0.9.0", "after_version": "1.0.0"},
            "asset": {
                "hostname": "fixture-node-01",
                "status": "running",
                "version": "1.0.0",
            },
            "evidence_records": {"precheck": "WSO-before", "postcheck": "WSO-after"},
        },
    )
    output = tmp_path / "bundle"

    with pytest.raises(TrajectoryBundleError, match="lacks required SOP stages"):
        materialize_trajectory_bundle(
            run_dir=run_dir,
            output_dir=output,
            ticket_path=ticket,
            terminal_verification_path=terminal,
            final_state_path=final_state,
            repository_commits={"homemaster": "abc", "ant-design-pro": "def"},
        )

    assert not output.exists()
    assert not list(tmp_path.glob(".bundle.tmp-*"))
