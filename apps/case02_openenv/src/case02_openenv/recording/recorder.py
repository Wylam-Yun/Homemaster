"""FFmpeg x11grab lifecycle with a real first-packet readiness gate."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from case02_openenv.artifacts import atomic_write_json
from case02_openenv.recording.verifier import VideoVerifier


class DemoRecorder:
    def __init__(
        self,
        *,
        run_id: str,
        run_root: Path,
        display: str,
        ffmpeg: str = "/usr/bin/ffmpeg",
        verifier: VideoVerifier | None = None,
        first_packet_timeout_s: float = 15.0,
    ) -> None:
        self.run_id = run_id
        self.run_root = run_root
        self.display = display
        self.ffmpeg = ffmpeg
        self.verifier = verifier or VideoVerifier(ffmpeg=ffmpeg)
        self.first_packet_timeout_s = first_packet_timeout_s
        self.video_dir = run_root / "video"
        self.part = self.video_dir / "demo.mp4.part"
        self.video = self.video_dir / "demo.mp4"
        self.progress = self.video_dir / "ffmpeg_progress.log"
        self.stderr_path = self.video_dir / "ffmpeg.stderr.log"
        self.process: subprocess.Popen[str] | None = None
        self.stderr_handle: Any = None
        self.first_packet: dict[str, Any] | None = None

    def start(self) -> dict[str, Any]:
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self.stderr_handle = self.stderr_path.open("w", encoding="utf-8")
        command = [
            self.ffmpeg,
            "-y",
            "-f",
            "x11grab",
            "-framerate",
            "15",
            "-video_size",
            "1920x1080",
            "-i",
            f"{self.display}.0",
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
            "-flush_packets",
            "1",
            "-progress",
            str(self.progress),
            "-nostats",
            "-f",
            "mp4",
            str(self.part),
        ]
        self.process = subprocess.Popen(
            command,
            env={**os.environ, "DISPLAY": self.display},
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=self.stderr_handle,
            text=True,
        )
        deadline = time.monotonic() + self.first_packet_timeout_s
        samples: list[int] = []
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self._close_stderr()
                raise RuntimeError(f"FFmpeg exited before readiness: {self.process.returncode}")
            samples.append(self.part.stat().st_size if self.part.exists() else 0)
            progress = self._last_progress()
            positive = [size for size in samples if size > 0]
            if progress and len(positive) >= 2 and max(positive) > min(positive):
                if progress.get("frame", 0) >= 1 and progress.get("total_size", 0) > 28:
                    self.first_packet = {
                        "progress": progress,
                        "size_samples": samples,
                        "command": command,
                    }
                    return {
                        "success": True,
                        "status": "recording",
                        "first_packet": self.first_packet,
                    }
            time.sleep(0.1)
        self.abort()
        raise TimeoutError("FFmpeg did not produce a frame and growing MP4")

    def status(self) -> dict[str, Any]:
        return {
            "success": True,
            "status": "recording" if self.process and self.process.poll() is None else "stopped",
            "first_packet": self.first_packet,
        }

    def stop(self) -> dict[str, Any]:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("recorder has not started")
        self.process.stdin.write("q\n")
        self.process.stdin.flush()
        return_code = self.process.wait(timeout=30)
        if self.stderr_handle:
            self._close_stderr()
        if return_code != 0:
            raise RuntimeError(f"FFmpeg shutdown returned {return_code}")
        os.replace(self.part, self.video)
        manifest = self.verifier.verify(self.video, self.run_id)
        manifest["ffmpeg_return_code"] = return_code
        manifest["first_packet"] = self.first_packet
        atomic_write_json(self.video_dir / "video_manifest.json", manifest)
        return {"success": True, "status": "verified", "manifest": manifest}

    def abort(self) -> int | None:
        if self.process is None:
            self._close_stderr()
            return None
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self._close_stderr()
        return self.process.returncode

    def _close_stderr(self) -> None:
        if self.stderr_handle is not None and not self.stderr_handle.closed:
            self.stderr_handle.close()

    def _last_progress(self) -> dict[str, Any] | None:
        if not self.progress.is_file():
            return None
        current: dict[str, str] = {}
        blocks: list[dict[str, str]] = []
        for line in self.progress.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if not separator:
                continue
            current[key] = value
            if key == "progress":
                blocks.append(current)
                current = {}
        if not blocks:
            return None
        block = blocks[-1]
        try:
            return {
                "frame": int(block["frame"]),
                "total_size": int(block["total_size"]),
                "out_time_ms": int(block["out_time_ms"]),
                "state": block["progress"],
            }
        except (KeyError, ValueError):
            return None
