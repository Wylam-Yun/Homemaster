"""Independent ffprobe and frame-region verification for delivered video."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat


class VideoVerifier:
    def __init__(
        self, *, ffmpeg: str = "/usr/bin/ffmpeg", ffprobe: str = "/usr/bin/ffprobe"
    ) -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    def verify(self, video: Path, run_id: str) -> dict[str, Any]:
        probe = subprocess.run(
            [
                self.ffprobe,
                "-v",
                "error",
                "-count_frames",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_type,codec_name,width,height,pix_fmt,duration,nb_read_frames",
                "-of",
                "json",
                str(video),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if probe.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {probe.stderr.strip()}")
        payload = json.loads(probe.stdout)
        streams = payload.get("streams", [])
        if len(streams) != 1:
            raise RuntimeError("video must contain exactly one stream")
        stream = streams[0]
        contract = {
            "codec": stream.get("codec_name") == "h264",
            "width": stream.get("width") == 1920,
            "height": stream.get("height") == 1080,
            "pixel_format": stream.get("pix_fmt") == "yuv420p",
            "duration": float(stream.get("duration", 0)) >= 4.0,
            "frames": int(stream.get("nb_read_frames", 0)) > 0,
        }
        if not all(contract.values()):
            raise RuntimeError(f"video contract failed: {contract}")
        duration = float(stream["duration"])
        frames_dir = video.parent / "extracted_frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        frames: dict[str, dict[str, Any]] = {}
        paths: dict[str, Path] = {}
        for name, timestamp in {
            "first": 0.2,
            "middle": duration / 2,
            "last": max(0.2, duration - 0.3),
        }.items():
            destination = frames_dir / f"{name}.png"
            extraction = subprocess.run(
                [
                    self.ffmpeg,
                    "-v",
                    "error",
                    "-ss",
                    f"{timestamp:.3f}",
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
            if extraction.returncode != 0 or not destination.is_file():
                raise RuntimeError(f"{name} frame extraction failed")
            paths[name] = destination
            frames[name] = self._frame_stats(destination)
            if (
                frames[name]["nonblack_ratio"] < 0.05
                or frames[name]["dark_ratio"] < 0.05
                or frames[name]["variance"] < 5
            ):
                raise RuntimeError(f"{name} frame is blank or lacks visible content")
        changed = self._changed_pixels(paths["first"], paths["last"])
        if changed < 1000:
            raise RuntimeError("video first and last frames do not show meaningful change")
        poster = video.parent / "poster.png"
        poster.write_bytes(paths["middle"].read_bytes())
        return {
            "schema_version": 1,
            "run_id": run_id,
            "path": str(video),
            "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
            "codec": "h264",
            "width": 1920,
            "height": 1080,
            "pix_fmt": "yuv420p",
            "duration_s": duration,
            "frame_count": int(stream["nb_read_frames"]),
            "contract_checks": contract,
            "frame_checks": frames,
            "first_last_changed_pixels": changed,
            "verified": True,
        }

    @staticmethod
    def _frame_stats(path: Path) -> dict[str, float]:
        with Image.open(path) as image:
            gray = image.convert("RGB").crop((0, 0, 1920, 1080)).convert("L")
            histogram = gray.histogram()
            nonblack = sum(histogram[17:])
            dark = sum(histogram[:64])
            return {
                "nonblack_ratio": nonblack / (gray.width * gray.height),
                "dark_ratio": dark / (gray.width * gray.height),
                "variance": ImageStat.Stat(gray).var[0],
            }

    @staticmethod
    def _changed_pixels(left: Path, right: Path) -> int:
        with Image.open(left) as left_image, Image.open(right) as right_image:
            difference = ImageChops.difference(
                left_image.convert("RGB"), right_image.convert("RGB")
            )
            return sum(difference.convert("L").histogram()[1:])
