"""Product-independent verification of one completed coworker run bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REQUIRED_MANIFEST_ARTIFACTS = (
    "input/item_change_ticket.json",
    "input/scenario.json",
    "input/dataset_manifest.json",
    "input/ground_truth_hashes.json",
    "environment/audit_events.jsonl",
    "environment/state_snapshots.jsonl",
    "environment/evaluator_inputs.json",
    "trajectory/raw_actions.jsonl",
    "trajectory/effective_trajectory.jsonl",
    "trajectory/trajectory_match.json",
    "scores/trajectory_score.json",
    "scores/result_score.json",
    "scores/summary.json",
    "video/demo.mp4",
    "video/poster.png",
    "video/video_manifest.json",
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _extract_frame(video: Path, timestamp: float) -> bytes:
    extraction = subprocess.run(
        [
            "/usr/bin/ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        capture_output=True,
        check=False,
    )
    if extraction.returncode != 0:
        raise RuntimeError(f"frame extraction failed: {extraction.stderr.decode().strip()}")
    return extraction.stdout


def _frame_stats(frame: bytes, width: int, height: int) -> tuple[dict[str, float], bytes]:
    expected = width * height * 3
    if len(frame) != expected:
        raise ValueError(f"raw frame size {len(frame)} != {expected}")
    grayscale = bytearray(width * height)
    nonblack = 0
    total = 0
    total_squared = 0
    for pixel, offset in enumerate(range(0, len(frame), 3)):
        value = (frame[offset] + frame[offset + 1] + frame[offset + 2]) // 3
        grayscale[pixel] = value
        nonblack += value >= 17
        total += value
        total_squared += value * value
    count = width * height
    mean = total / count
    return (
        {
            "nonblack_ratio": nonblack / count,
            "variance": total_squared / count - mean * mean,
        },
        bytes(grayscale),
    )


def _changed_pixels(left: bytes, right: bytes) -> int:
    if len(left) != len(right):
        raise ValueError("frame sizes differ")
    return sum(
        left_pixel != right_pixel for left_pixel, right_pixel in zip(left, right, strict=True)
    )


def verify(run_root: Path, data_root: Path) -> dict[str, Any]:
    run_root = run_root.resolve()
    failures: list[str] = []
    summary = _read_json(run_root / "scores/summary.json")
    scenario = summary["scenario_id"]
    dag = yaml.safe_load((data_root / "agent_trajectory_ground_truth.yaml").read_text())
    required = {
        node["node_id"] for node in dag["nodes"] if scenario in node["required_in_scenarios"]
    }
    raw = _read_jsonl(run_root / "trajectory/raw_actions.jsonl")
    known_evidence: set[str] = set()
    for event in raw:
        if event.get("source") == "decision":
            unknown = sorted(set(event.get("evidence_refs", [])).difference(known_evidence))
            if unknown:
                failures.append(f"unknown_evidence_ref:{event.get('event_id')}:{','.join(unknown)}")
        if event.get("status") in {"accepted", "succeeded"}:
            if event.get("event_id"):
                known_evidence.add(event["event_id"])
            if event.get("source") in {"backend", "browser", "terminal", "state"}:
                known_evidence.update(event.get("evidence_refs", []))
            if event.get("kind") == "automation_job_submitted":
                job_id = event.get("arguments", {}).get("job_id")
                if job_id:
                    known_evidence.add(f"job-{job_id}-accepted")
    observed = {
        event.get("node_id") for event in raw if event.get("status") in {"accepted", "succeeded"}
    }
    config_variants = {
        event.get("arguments", {}).get("check")
        for event in raw
        if event.get("node_id") == "PRE_CONFIG"
    }
    if not {"extension_config", "upstream_ready"}.issubset(config_variants):
        observed.discard("PRE_CONFIG")
    missing_nodes = sorted(required - observed)
    if missing_nodes:
        failures.append(f"missing_nodes:{','.join(missing_nodes)}")

    expected_nodes = 24 if scenario == "normal" else 22
    expected_results = 14 if scenario == "normal" else 11
    for key, expected in (
        ("required_nodes", expected_nodes),
        ("matched_nodes", expected_nodes),
        ("required_checkpoints", expected_results),
        ("passed_checkpoints", expected_results),
    ):
        if summary.get(key) != expected:
            failures.append(f"summary_{key}:{summary.get(key)}")
    for key in ("trajectory_score", "result_score", "overall_score"):
        if summary.get(key) != 100.0:
            failures.append(f"summary_{key}:{summary.get(key)}")
    if summary.get("formal_success") is not True:
        failures.append("formal_success_not_true")
    if summary.get("video_verification") != "passed":
        failures.append("video_not_passed")

    commands_path = run_root / "terminal/commands.jsonl"
    commands = _read_jsonl(commands_path) if commands_path.is_file() else []
    exits = [command.get("exit_code") for command in commands]
    if scenario == "normal" and exits != [0]:
        failures.append(f"normal_terminal_exits:{exits}")
    if scenario == "post_change_anomaly" and exits != [0, 1]:
        failures.append(f"anomaly_terminal_exits:{exits}")
    terminal_ids = [command.get("evidence_id") for command in commands]
    if len(terminal_ids) != len(set(terminal_ids)):
        failures.append("terminal_evidence_reused")

    state = _read_json(run_root / "environment/state.json")
    episode_file = (
        run_root
        / "environment/episode_root/service_layer/component/config/extension_item_mapping.json"
    )
    config = _read_json(episode_file)
    key = f"{state['variables']['TenantId']}:{state['variables']['ItemCode']}"
    if scenario == "normal" and key not in config:
        failures.append("normal_config_absent")
    if scenario == "post_change_anomaly" and key in config:
        failures.append("anomaly_config_present")

    manifest = _read_json(run_root / "run_manifest.json")
    manifest_artifacts = manifest.get("artifacts", {})
    for relative in REQUIRED_MANIFEST_ARTIFACTS:
        if relative not in manifest_artifacts:
            failures.append(f"missing_manifest_entry:{relative}")
    for relative, entry in manifest_artifacts.items():
        path = (run_root / relative).resolve()
        if run_root not in path.parents or not path.is_file():
            failures.append(f"manifest_missing:{relative}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != entry.get("sha256"):
            failures.append(f"manifest_hash:{relative}")
        elif entry.get("complete") is not True:
            failures.append(f"manifest_incomplete:{relative}")

    video = run_root / "video/demo.mp4"
    probe = subprocess.run(
        [
            "/usr/bin/ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,pix_fmt,duration,nb_read_frames",
            "-of",
            "json",
            str(video),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        failures.append(f"ffprobe_return:{probe.returncode}")
        stream = {}
    else:
        streams = json.loads(probe.stdout).get("streams", [])
        stream = streams[0] if len(streams) == 1 else {}
    expected_video = {
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "pix_fmt": "yuv420p",
    }
    for key_name, expected in expected_video.items():
        if stream.get(key_name) != expected:
            failures.append(f"video_{key_name}:{stream.get(key_name)}")
    if float(stream.get("duration", 0)) < 4 or int(stream.get("nb_read_frames", 0)) < 1:
        failures.append("video_duration_or_frames")
    video_manifest = _read_json(run_root / "video/video_manifest.json")
    if not video_manifest.get("verified"):
        failures.append("video_manifest_unverified")
    if video_manifest.get("ffmpeg_return_code") != 0:
        failures.append(f"ffmpeg_return:{video_manifest.get('ffmpeg_return_code')}")
    video_sha256 = hashlib.sha256(video.read_bytes()).hexdigest() if video.is_file() else None
    if video_manifest.get("sha256") != video_sha256:
        failures.append("video_manifest_sha256")
    if summary.get("video_manifest_sha256") != video_sha256:
        failures.append("summary_video_sha256")
    first_packet = video_manifest.get("first_packet") or {}
    progress = first_packet.get("progress") or {}
    positive_sizes = [size for size in first_packet.get("size_samples", []) if size > 0]
    if (
        progress.get("frame", 0) < 1
        or progress.get("total_size", 0) <= 28
        or len(set(positive_sizes)) < 2
    ):
        failures.append("first_packet_not_proven")

    try:
        duration = float(stream.get("duration", 0))
        frame_stats: dict[str, dict[str, float]] = {}
        grayscale: dict[str, bytes] = {}
        for name, timestamp in {
            "first": 0.2,
            "middle": duration / 2,
            "last": max(0.2, duration - 0.3),
        }.items():
            frame = _extract_frame(video, timestamp)
            frame_stats[name], grayscale[name] = _frame_stats(frame, 1920, 1080)
            if frame_stats[name]["nonblack_ratio"] < 0.05 or frame_stats[name]["variance"] < 5:
                failures.append(f"video_{name}_frame_blank")
        if _changed_pixels(grayscale["first"], grayscale["last"]) < 1000:
            failures.append("video_first_last_unchanged")
    except (OSError, RuntimeError, ValueError) as exc:
        failures.append(f"video_frame_verification:{type(exc).__name__}:{exc}")

    return {
        "schema_version": 1,
        "run_id": summary["run_id"],
        "scenario_id": scenario,
        "required_nodes": expected_nodes,
        "observed_required_nodes": len(required & observed),
        "required_checkpoints": expected_results,
        "terminal_exit_codes": exits,
        "video_sha256": video_sha256,
        "failures": failures,
        "pass": not failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--data-root", type=Path, default=Path("data/coworker_demo/case_02"))
    args = parser.parse_args()
    result = verify(args.run_root, args.data_root.resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
