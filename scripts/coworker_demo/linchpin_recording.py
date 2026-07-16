"""FFmpeg/ffprobe linchpin helpers and executable gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat


@dataclass(frozen=True)
class FfmpegProgress:
    frame: int
    total_size: int
    out_time_ms: int
    state: str


@dataclass(frozen=True)
class VideoProbe:
    codec_name: str
    width: int
    height: int
    pix_fmt: str
    duration_s: float
    frame_count: int


def _nonnegative_int(value: object, field: str) -> int:
    if not isinstance(value, str) or not value.isdecimal():
        raise ValueError(f"{field} must be an unsigned decimal string")
    parsed = int(value)
    if parsed < 0:
        raise ValueError(f"{field} must be nonnegative")
    return parsed


def parse_ffmpeg_progress(text: str) -> FfmpegProgress:
    """Parse the last complete block emitted by ``ffmpeg -progress``."""

    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key:
            raise ValueError("malformed FFmpeg progress line")
        current[key] = value
        if key == "progress":
            blocks.append(current)
            current = {}
    if current or not blocks:
        raise ValueError("FFmpeg progress has no complete terminal block")

    block = blocks[-1]
    required = {"frame", "total_size", "out_time_ms", "progress"}
    if not required.issubset(block):
        raise ValueError("FFmpeg progress block is missing required fields")
    state = block["progress"]
    if state not in {"continue", "end"}:
        raise ValueError("FFmpeg progress state is invalid")
    frame = _nonnegative_int(block["frame"], "frame")
    total_size = _nonnegative_int(block["total_size"], "total_size")
    out_time_ms = _nonnegative_int(block["out_time_ms"], "out_time_ms")
    if frame < 1 or total_size < 1 or out_time_ms < 1:
        raise ValueError("FFmpeg progress does not prove a written frame")
    return FfmpegProgress(
        frame=frame,
        total_size=total_size,
        out_time_ms=out_time_ms,
        state=state,
    )


def _video_stream(payload: dict[str, Any]) -> dict[str, Any]:
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise ValueError("ffprobe streams must be an array")
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    if len(video_streams) != 1 or not isinstance(video_streams[0], dict):
        raise ValueError("ffprobe must contain exactly one video stream")
    return video_streams[0]


def parse_ffprobe_video(text: str) -> VideoProbe:
    """Parse and enforce the fixed H.264 linchpin video contract."""

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("ffprobe output is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("ffprobe output must be an object")
    stream = _video_stream(payload)

    if stream.get("codec_name") != "h264":
        raise ValueError("video codec is not H.264")
    if stream.get("width") != 1920 or stream.get("height") != 1080:
        raise ValueError("video dimensions are not 1920x1080")
    if stream.get("pix_fmt") != "yuv420p":
        raise ValueError("video pixel format is not yuv420p")
    try:
        duration_s = float(stream["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("video duration is not numeric") from exc
    frame_count = _nonnegative_int(stream.get("nb_read_frames"), "nb_read_frames")
    if duration_s < 4.0 or frame_count < 1:
        raise ValueError("video is too short or contains no readable frames")

    return VideoProbe(
        codec_name="h264",
        width=1920,
        height=1080,
        pix_fmt="yuv420p",
        duration_s=duration_s,
        frame_count=frame_count,
    )


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _region_stats(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        region = rgb.crop((640, 0, 1920, 720))
        grayscale = region.convert("L")
        histogram = grayscale.histogram()
        total = region.width * region.height
        nonblack = sum(histogram[17:])
        return {
            "frame_width": rgb.width,
            "frame_height": rgb.height,
            "region": [640, 0, 1920, 720],
            "region_nonblack_pixels": nonblack,
            "region_nonblack_ratio": nonblack / total,
            "region_grayscale_variance": ImageStat.Stat(grayscale).var[0],
        }


def _changed_pixels(left: Path, right: Path) -> int:
    with Image.open(left) as left_image, Image.open(right) as right_image:
        left_region = left_image.convert("RGB").crop((640, 0, 1920, 720))
        right_region = right_image.convert("RGB").crop((640, 0, 1920, 720))
        difference = ImageChops.difference(left_region, right_region)
        histogram = difference.convert("L").histogram()
        return sum(histogram[1:])


def _read_progress(path: Path) -> FfmpegProgress:
    return parse_ffmpeg_progress(path.read_text(encoding="utf-8"))


def mp4_has_observed_growth(sizes: list[int]) -> bool:
    """Require a written header followed by a larger on-disk fragment."""

    positive_sizes = [size for size in sizes if size > 0]
    return len(positive_sizes) >= 2 and max(positive_sizes) > min(positive_sizes)


def _run_ffprobe(ffprobe: str, video: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,pix_fmt,duration,nb_read_frames",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(video),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def _extract_frame(
    ffmpeg: str,
    video: Path,
    timestamp_s: float,
    destination: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-ss",
            f"{timestamp_s:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-y",
            str(destination),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def run_gate(args: argparse.Namespace) -> int:
    artifact_dir = args.artifact_dir.resolve()
    artifact_dir.mkdir(parents=True, exist_ok=False)
    video = artifact_dir / "demo.mp4"
    progress_path = artifact_dir / "ffmpeg_progress.log"
    stderr_path = artifact_dir / "ffmpeg.stderr.log"
    ffmpeg_command = [
        args.ffmpeg_executable,
        "-y",
        "-f",
        "x11grab",
        "-framerate",
        "15",
        "-video_size",
        "1920x1080",
        "-i",
        f"{args.display}.0",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-g",
        "15",
        "-keyint_min",
        "15",
        "-sc_threshold",
        "0",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+frag_keyframe+empty_moov+default_base_moof",
        "-progress",
        str(progress_path),
        "-nostats",
        str(video),
    ]
    result: dict[str, Any] = {
        "schema_version": 1,
        "pass": False,
        "display": args.display,
        "expected_marker": args.expected_marker,
        "ffmpeg_command": ffmpeg_command,
    }
    process: subprocess.Popen[str] | None = None
    stderr_handle = stderr_path.open("w", encoding="utf-8")
    try:
        environment = os.environ.copy()
        environment["DISPLAY"] = args.display
        xdpyinfo = subprocess.run(
            ["/usr/bin/xdpyinfo"],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        xwininfo = subprocess.run(
            ["/usr/bin/xwininfo", "-root", "-tree"],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        (artifact_dir / "xwininfo.stdout.log").write_text(xwininfo.stdout, encoding="utf-8")
        if xdpyinfo.returncode != 0:
            raise RuntimeError(f"configured display is unavailable: {xdpyinfo.stderr.strip()}")
        if xwininfo.returncode != 0 or args.expected_marker not in xwininfo.stdout:
            raise RuntimeError("expected live browser marker is absent from the X11 window tree")

        process = subprocess.Popen(
            ffmpeg_command,
            env=environment,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=stderr_handle,
        )
        first_packet_deadline = time.monotonic() + args.first_packet_timeout_s
        size_samples: list[dict[str, Any]] = []
        first_progress: FfmpegProgress | None = None
        while time.monotonic() < first_packet_deadline:
            if process.poll() is not None:
                raise RuntimeError(f"FFmpeg exited before first packet: {process.returncode}")
            size = video.stat().st_size if video.exists() else 0
            size_samples.append({"monotonic_ns": time.monotonic_ns(), "size": size})
            observed_sizes = [sample["size"] for sample in size_samples]
            if progress_path.exists() and mp4_has_observed_growth(observed_sizes):
                try:
                    candidate = _read_progress(progress_path)
                except ValueError:
                    candidate = None
                if candidate is not None and candidate.frame >= 1 and candidate.total_size > 28:
                    first_progress = candidate
                    break
            time.sleep(0.1)
        if first_progress is None:
            raise TimeoutError("FFmpeg did not publish a frame and growing MP4 before timeout")
        first_packet_size = video.stat().st_size

        capture_deadline = time.monotonic() + args.duration_s
        while time.monotonic() < capture_deadline:
            if process.poll() is not None:
                raise RuntimeError(f"FFmpeg exited during capture: {process.returncode}")
            size_samples.append({"monotonic_ns": time.monotonic_ns(), "size": video.stat().st_size})
            time.sleep(0.25)
        if process.stdin is None:
            raise RuntimeError("FFmpeg stdin is unavailable for normal shutdown")
        process.stdin.write("q\n")
        process.stdin.flush()
        ffmpeg_returncode = process.wait(timeout=20)
        if ffmpeg_returncode != 0:
            raise RuntimeError(f"FFmpeg normal shutdown returned {ffmpeg_returncode}")
        final_progress = _read_progress(progress_path)
        final_size = video.stat().st_size
        if final_size <= first_packet_size:
            raise RuntimeError("fragmented MP4 did not grow after the first-packet gate")

        ffprobe = _run_ffprobe(args.ffprobe_executable, video)
        (artifact_dir / "ffprobe.json").write_text(ffprobe.stdout, encoding="utf-8")
        (artifact_dir / "ffprobe.stderr.log").write_text(ffprobe.stderr, encoding="utf-8")
        if ffprobe.returncode != 0:
            raise RuntimeError(f"ffprobe returned {ffprobe.returncode}")
        video_probe = parse_ffprobe_video(ffprobe.stdout)

        frames_dir = artifact_dir / "frames"
        frames_dir.mkdir()
        timestamps = {
            "first": 0.1,
            "middle": video_probe.duration_s / 2,
            "last": max(0.1, video_probe.duration_s - 0.2),
        }
        frame_paths: dict[str, Path] = {}
        frame_checks: dict[str, dict[str, Any]] = {}
        for name, timestamp_s in timestamps.items():
            frame_path = frames_dir / f"{name}.png"
            extraction = _extract_frame(
                args.ffmpeg_executable,
                video,
                timestamp_s,
                frame_path,
            )
            if extraction.returncode != 0 or not frame_path.is_file():
                raise RuntimeError(
                    f"{name} frame extraction failed: {extraction.returncode} {extraction.stderr}"
                )
            stats = _region_stats(frame_path)
            frame_paths[name] = frame_path
            frame_checks[name] = {
                "timestamp_s": timestamp_s,
                "extract_returncode": extraction.returncode,
                "sha256": _sha256(frame_path),
                "stats": stats,
                "pass": stats["frame_width"] == 1920
                and stats["frame_height"] == 1080
                and stats["region_nonblack_ratio"] > 0.25
                and stats["region_grayscale_variance"] > 100,
            }
        changed_pixels = {
            "first_middle": _changed_pixels(frame_paths["first"], frame_paths["middle"]),
            "middle_last": _changed_pixels(frame_paths["middle"], frame_paths["last"]),
        }
        motion_pass = max(changed_pixels.values()) > 500
        shutil.copy2(frame_paths["middle"], artifact_dir / "poster.png")
        checks = {
            "ffmpeg_returncode": ffmpeg_returncode == 0,
            "ffmpeg_progress_end": final_progress.state == "end",
            "fragmented_mp4_grew": final_size > first_packet_size > 0,
            "ffprobe_returncode": ffprobe.returncode == 0,
            "duration": video_probe.duration_s >= 4.0,
            "frame_count": video_probe.frame_count > 0,
            "per_frame_regions": all(item["pass"] for item in frame_checks.values()),
            "visible_change": motion_pass,
        }
        result.update(
            {
                "ffmpeg_pid": process.pid,
                "ffmpeg_returncode": ffmpeg_returncode,
                "first_progress": first_progress.__dict__,
                "final_progress": final_progress.__dict__,
                "size_samples": size_samples,
                "first_packet_size": first_packet_size,
                "final_size": final_size,
                "ffprobe_returncode": ffprobe.returncode,
                "video_probe": video_probe.__dict__,
                "video_path": str(video),
                "video_sha256": _sha256(video),
                "frame_checks": frame_checks,
                "changed_pixels": changed_pixels,
                "checks": checks,
                "pass": all(checks.values()),
            }
        )
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)
    finally:
        if process is not None and process.poll() is None:
            if process.stdin is not None:
                process.stdin.write("q\n")
                process.stdin.flush()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        stderr_handle.close()
        if process is not None:
            result.setdefault("ffmpeg_returncode", process.returncode)
        _atomic_json(artifact_dir / "video_manifest.json", result)
        print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["pass"] else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--display", required=True)
    parser.add_argument("--expected-marker", required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--ffmpeg-executable", default="/usr/bin/ffmpeg")
    parser.add_argument("--ffprobe-executable", default="/usr/bin/ffprobe")
    parser.add_argument("--duration-s", type=float, default=5.0)
    parser.add_argument("--first-packet-timeout-s", type=float, default=15.0)
    return parser


def main() -> int:
    return run_gate(_parser().parse_args())


if __name__ == "__main__":
    sys.exit(main())
