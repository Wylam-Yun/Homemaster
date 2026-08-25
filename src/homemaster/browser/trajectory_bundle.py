"""Materialize and verify private deterministic browser-run evidence bundles."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_STAGE_BY_ROUTE = {
    "/ops/alarm-query": ("check_before_change", "OPS-SOP-STEP-001"),
    "/ops/change": ("change_implement", "OPS-SOP-STEP-004"),
    "/ops/asset-check": ("change_verified", "OPS-SOP-STEP-005"),
}
_FRAMEWORK_TOOLS = {"load_skill", "read_file", "task_planner", "task_progress_check"}


class TrajectoryBundleError(RuntimeError):
    pass


def materialize_trajectory_bundle(
    *,
    run_dir: Path,
    output_dir: Path,
    ticket_path: Path,
    terminal_verification_path: Path,
    final_state_path: Path,
    repository_commits: Mapping[str, str],
) -> Path:
    run_dir = Path(run_dir).resolve(strict=True)
    ticket_path = Path(ticket_path).resolve(strict=True)
    output_dir = Path(output_dir).absolute()
    if output_dir.exists():
        raise TrajectoryBundleError(f"trajectory output already exists: {output_dir}")
    runtime_path = _required_file(run_dir / "runtime_events.jsonl")
    action_path = _only(run_dir.glob("browser/*/browser_actions.jsonl"), "browser actions")
    trace_path = _only(run_dir.glob("browser/*/browser_trace.zip"), "Playwright trace")
    video_path = _only(run_dir.glob("browser/*/*.webm"), "browser video")
    screenshot_paths = sorted(run_dir.glob("browser/*/screenshots/*.png"))
    if not screenshot_paths:
        raise TrajectoryBundleError("browser run has no persisted observe screenshots")

    runtime_events = _read_jsonl(runtime_path)
    trajectory = _build_trajectory(runtime_events)
    terminal = _read_json(_required_file(terminal_verification_path))
    final_state = _read_json(_required_file(final_state_path))
    _validate_terminal_and_state(terminal, final_state)

    output_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output_dir.parent, 0o700)
    temporary = output_dir.with_name(f".{output_dir.name}.tmp-{uuid.uuid4().hex}")
    temporary.mkdir(mode=0o700)
    try:
        _copy_private(runtime_path, temporary / "runtime_events.jsonl")
        _copy_private(action_path, temporary / "browser_actions.jsonl")
        _copy_private(trace_path, temporary / "playwright_trace.zip")
        _copy_private(video_path, temporary / f"browser_video{video_path.suffix}")
        screenshots_dir = temporary / "screenshots"
        screenshots_dir.mkdir(mode=0o700)
        for screenshot in screenshot_paths:
            _copy_private(screenshot, screenshots_dir / screenshot.name)
        _write_json(temporary / "terminal_verification.json", terminal)
        _write_json(temporary / "final_state.json", final_state)
        _write_jsonl(temporary / "trajectory.jsonl", trajectory)
        _write_private(temporary / "trajectory.md", _render_markdown(trajectory))

        artifacts = {
            path.relative_to(temporary).as_posix(): {
                "sha256": _sha256(path),
                "byte_count": path.stat().st_size,
            }
            for path in sorted(temporary.rglob("*"))
            if path.is_file()
        }
        manifest = {
            "schema_version": "browser-deterministic-run-v1",
            "created_at": datetime.now(UTC).isoformat(),
            "status": "succeeded",
            "ticket": {
                "path": str(ticket_path),
                "sha256": _sha256(ticket_path),
            },
            "repository_commits": dict(repository_commits),
            "tool_call_count": len(trajectory),
            "artifacts": artifacts,
        }
        _write_json(temporary / "manifest.json", manifest)
        verify_trajectory_bundle(temporary)
        temporary.rename(output_dir)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return output_dir


def verify_trajectory_bundle(bundle_dir: Path) -> None:
    bundle_dir = Path(bundle_dir).resolve(strict=True)
    manifest = _read_json(_required_file(bundle_dir / "manifest.json"))
    if manifest.get("schema_version") != "browser-deterministic-run-v1":
        raise TrajectoryBundleError("unsupported trajectory manifest schema")
    if bundle_dir.stat().st_mode & 0o077:
        raise TrajectoryBundleError("trajectory directory is not private")
    for directory in (path for path in bundle_dir.rglob("*") if path.is_dir()):
        if directory.stat().st_mode & 0o077:
            raise TrajectoryBundleError(f"trajectory subdirectory is not private: {directory}")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping) or not artifacts:
        raise TrajectoryBundleError("trajectory manifest has no artifact hashes")
    for relative, expected in artifacts.items():
        path = _required_file(bundle_dir / str(relative))
        if path.stat().st_mode & 0o077:
            raise TrajectoryBundleError(f"trajectory artifact is not private: {relative}")
        if not isinstance(expected, Mapping) or _sha256(path) != expected.get("sha256"):
            raise TrajectoryBundleError(f"trajectory artifact hash mismatch: {relative}")
        if path.stat().st_size != expected.get("byte_count"):
            raise TrajectoryBundleError(f"trajectory artifact size mismatch: {relative}")

    trajectory = _read_jsonl(_required_file(bundle_dir / "trajectory.jsonl"))
    rebuilt_trajectory = _build_trajectory(
        _read_jsonl(_required_file(bundle_dir / "runtime_events.jsonl"))
    )
    if trajectory != rebuilt_trajectory:
        raise TrajectoryBundleError("trajectory does not match raw runtime events")
    if len(trajectory) != manifest.get("tool_call_count"):
        raise TrajectoryBundleError("trajectory tool count mismatch")
    for index, item in enumerate(trajectory, start=1):
        if item.get("sequence") != index or not item.get("tool_call_id"):
            raise TrajectoryBundleError("trajectory sequence is not continuous")
        if item.get("status") != "success" or item.get("result") is None:
            raise TrajectoryBundleError("trajectory contains a failed or incomplete tool call")
    stages = {item.get("sop_stage") for item in trajectory}
    required = {"check_before_change", "change_implement", "change_verified"}
    if not required <= stages:
        raise TrajectoryBundleError(f"trajectory lacks required SOP stages: {required - stages}")
    if not any(item.get("tool_name") == "terminal" for item in trajectory):
        raise TrajectoryBundleError("trajectory lacks terminal verification")
    _validate_terminal_and_state(
        _read_json(bundle_dir / "terminal_verification.json"),
        _read_json(bundle_dir / "final_state.json"),
    )


def _build_trajectory(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    started: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    current_stage, current_step = _STAGE_BY_ROUTE["/ops/alarm-query"]
    for event in events:
        event_type = event.get("type")
        call_id = event.get("tool_call_id")
        if event_type == "tool.call_started":
            if not isinstance(call_id, str) or not call_id or call_id in started:
                raise TrajectoryBundleError("invalid or duplicate tool start event")
            tool_name = str(event.get("name") or "")
            payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
            arguments = payload.get("arguments")
            arguments = dict(arguments) if isinstance(arguments, Mapping) else {}
            if tool_name == "browser_navigate":
                route = _route_from_url(str(arguments.get("url") or ""))
                if route is not None:
                    current_stage, current_step = route
            stage, step = (
                ("framework", None)
                if tool_name in _FRAMEWORK_TOOLS
                else (current_stage, current_step)
            )
            if tool_name == "terminal":
                stage, step = _STAGE_BY_ROUTE["/ops/change"]
            started[call_id] = {
                "tool_name": tool_name,
                "arguments": arguments,
                "timestamp": event.get("timestamp"),
                "sop_stage": stage,
                "sop_step_id": step,
            }
        elif event_type in {"tool.call_completed", "tool.call_failed"}:
            if not isinstance(call_id, str) or call_id not in started:
                if _is_protocol_blocked_completion(event):
                    continue
                raise TrajectoryBundleError("tool terminal event has no matching start")
            start = started.pop(call_id)
            payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
            result = payload.get("data", payload.get("result"))
            rows.append(
                {
                    "sequence": len(rows) + 1,
                    "tool_call_id": call_id,
                    **start,
                    "result": result,
                    "status": "failure" if event_type.endswith("failed") else "success",
                    "duration_ms": event.get("duration_ms"),
                    "receipt_refs": _receipt_refs(payload),
                }
            )
            route = _route_from_tool_result(payload)
            if route is not None:
                current_stage, current_step = route
    if started:
        raise TrajectoryBundleError(f"tool calls have no terminal result: {sorted(started)}")
    if not rows:
        raise TrajectoryBundleError("runtime trace contains no complete tool calls")
    return rows


def _is_protocol_blocked_completion(event: Mapping[str, Any]) -> bool:
    if event.get("type") != "tool.call_completed":
        return False
    payload = event.get("payload")
    if not isinstance(payload, Mapping) or payload.get("is_error") is not False:
        return False
    data = payload.get("data")
    return (
        isinstance(data, Mapping)
        and data.get("status") == "protocol_blocked"
        and data.get("backend_attempted") is False
    )


def _validate_terminal_and_state(
    terminal: Mapping[str, Any], final_state: Mapping[str, Any]
) -> None:
    if terminal.get("exit_code") != 0 or terminal.get("stdout") != "CONFIG_VERSION_OK\n":
        raise TrajectoryBundleError("terminal verification is not exact")
    fixture = final_state.get("fixture")
    asset = final_state.get("asset")
    evidence = final_state.get("evidence_records")
    if not isinstance(fixture, Mapping) or (
        fixture.get("before_version"),
        fixture.get("after_version"),
    ) != ("0.9.0", "1.0.0"):
        raise TrajectoryBundleError("fixture transition is not 0.9.0 -> 1.0.0")
    if not isinstance(asset, Mapping) or (
        asset.get("hostname"),
        asset.get("status"),
        asset.get("version"),
    ) != ("fixture-node-01", "running", "1.0.0"):
        raise TrajectoryBundleError("asset state does not read the updated fixture")
    if not isinstance(evidence, Mapping) or not all(
        str(evidence.get(key, "")).startswith("WSO-") for key in ("precheck", "postcheck")
    ):
        raise TrajectoryBundleError("precheck and postcheck evidence records are required")


def _route_from_url(url: str) -> tuple[str, str] | None:
    for route, value in _STAGE_BY_ROUTE.items():
        if route in url:
            return value
    return None


def _route_from_tool_result(payload: Mapping[str, Any]) -> tuple[str, str] | None:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return None
    candidates = [data.get("url_after"), data.get("url")]
    next_snapshot = data.get("next_snapshot")
    if isinstance(next_snapshot, Mapping):
        candidates.append(next_snapshot.get("url"))
    for candidate in candidates:
        if isinstance(candidate, str) and (route := _route_from_url(candidate)) is not None:
            return route
    return None


def _receipt_refs(payload: Mapping[str, Any]) -> list[str]:
    refs = payload.get("evidence_refs")
    if isinstance(refs, list):
        return [str(value) for value in refs]
    data = payload.get("data")
    if isinstance(data, Mapping) and data.get("evidence_ref"):
        return [str(data["evidence_ref"])]
    return []


def _render_markdown(trajectory: list[dict[str, Any]]) -> str:
    lines = [
        "# Deterministic Browser Tool Trajectory",
        "",
        "| # | SOP stage | SOP step | Tool | Status | Duration ms |",
        "|---:|---|---|---|---|---:|",
    ]
    for item in trajectory:
        lines.append(
            f"| {item['sequence']} | {item['sop_stage']} | "
            f"{item['sop_step_id'] or '-'} | `{item['tool_name']}` | "
            f"{item['status']} | {item['duration_ms'] or 0:.1f} |"
        )
    return "\n".join(lines) + "\n"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TrajectoryBundleError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise TrajectoryBundleError(f"JSONL row is not an object at {path}:{line_number}")
        rows.append(value)
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TrajectoryBundleError(f"JSON artifact is not an object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    _write_private(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    _write_private(
        path,
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
    )


def _write_private(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o600)


def _copy_private(source: Path, target: Path) -> None:
    shutil.copyfile(source, target)
    os.chmod(target, 0o600)


def _only(paths: Any, label: str) -> Path:
    values = list(paths)
    if len(values) != 1:
        raise TrajectoryBundleError(f"expected exactly one {label}, found {len(values)}")
    return _required_file(values[0])


def _required_file(path: Path) -> Path:
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.stat().st_size == 0:
        raise TrajectoryBundleError(f"required artifact is missing or empty: {path}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "TrajectoryBundleError",
    "materialize_trajectory_bundle",
    "verify_trajectory_bundle",
]
