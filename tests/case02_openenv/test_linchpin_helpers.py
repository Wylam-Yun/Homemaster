from __future__ import annotations

import json

import pytest

from scripts.coworker_demo.linchpin_browser_vnc import (
    ClickReceipt,
    parse_click_receipt,
    parse_rfb_banner,
)
from scripts.coworker_demo.linchpin_recording import (
    FfmpegProgress,
    VideoProbe,
    mp4_has_observed_growth,
    parse_ffmpeg_progress,
    parse_ffprobe_video,
)
from scripts.coworker_demo.linchpin_terminal import (
    build_bubblewrap_command,
    parse_exit_status,
)


def test_parse_playwright_click_receipt_requires_dom_and_backend_agreement() -> None:
    payload = {
        "action_id": "linchpin-click-001",
        "marker": "COWORKER-L1-7A3F",
        "backend_count": 1,
        "dom_count": 1,
        "page_url": "http://127.0.0.1:43127/",
    }

    assert parse_click_receipt(json.dumps(payload)) == ClickReceipt(**payload)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"action_id": "a", "marker": "m", "backend_count": 1, "dom_count": 0},
        {
            "action_id": "a",
            "marker": "m",
            "backend_count": 1,
            "dom_count": 1,
            "page_url": "file:///tmp/fake.html",
        },
    ],
)
def test_parse_playwright_click_receipt_rejects_incomplete_or_non_http_data(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        parse_click_receipt(json.dumps(payload))


@pytest.mark.parametrize("banner", [b"RFB 003.003\n", b"RFB 003.008\n"])
def test_parse_rfb_banner_accepts_complete_protocol_banner(banner: bytes) -> None:
    assert parse_rfb_banner(banner) == banner.decode("ascii").strip().removeprefix("RFB ")


@pytest.mark.parametrize(
    "banner",
    [b"", b"RFB 3.8\n", b"HTTP/1.1 200", b"RFB 003.008\r\n", b"RFB 004.001\n"],
)
def test_parse_rfb_banner_rejects_malformed_or_unsupported_versions(banner: bytes) -> None:
    with pytest.raises(ValueError):
        parse_rfb_banner(banner)


def test_parse_ffmpeg_progress_uses_last_complete_progress_block() -> None:
    text = """frame=1
fps=0.0
total_size=4096
out_time_ms=66666
progress=continue
frame=61
fps=15.1
total_size=262144
out_time_ms=4066666
progress=end
"""

    assert parse_ffmpeg_progress(text) == FfmpegProgress(
        frame=61,
        total_size=262144,
        out_time_ms=4066666,
        state="end",
    )


@pytest.mark.parametrize(
    "text",
    ["", "frame=1\nprogress=continue\n", "frame=x\ntotal_size=1\nout_time_ms=1\nprogress=end\n"],
)
def test_parse_ffmpeg_progress_rejects_incomplete_or_invalid_data(text: str) -> None:
    with pytest.raises(ValueError):
        parse_ffmpeg_progress(text)


def test_first_packet_gate_requires_observed_mp4_growth_beyond_header() -> None:
    assert not mp4_has_observed_growth([0, 28, 28, 28])
    assert not mp4_has_observed_growth([0, 0, 4096])
    assert mp4_has_observed_growth([0, 28, 28, 4096])


def test_parse_ffprobe_video_requires_numeric_h264_video_contract() -> None:
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "pix_fmt": "yuv420p",
                "duration": "4.133333",
                "nb_read_frames": "62",
            }
        ],
        "format": {"duration": "4.134000"},
    }

    assert parse_ffprobe_video(json.dumps(payload)) == VideoProbe(
        codec_name="h264",
        width=1920,
        height=1080,
        pix_fmt="yuv420p",
        duration_s=4.133333,
        frame_count=62,
    )


@pytest.mark.parametrize(
    "stream_patch",
    [
        {"codec_name": "vp9"},
        {"width": 1280},
        {"pix_fmt": "yuv444p"},
        {"duration": "N/A"},
        {"nb_read_frames": "N/A"},
        {"nb_read_frames": "0"},
    ],
)
def test_parse_ffprobe_video_rejects_contract_mismatch(stream_patch: dict[str, object]) -> None:
    stream = {
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "pix_fmt": "yuv420p",
        "duration": "4.1",
        "nb_read_frames": "61",
    }
    stream.update(stream_patch)

    with pytest.raises(ValueError):
        parse_ffprobe_video(json.dumps({"streams": [stream], "format": {"duration": "4.1"}}))


@pytest.mark.parametrize(("text", "expected"), [("0\n", 0), ("1\n", 1), ("127\n", 127)])
def test_parse_terminal_exit_status_accepts_one_decimal_line(text: str, expected: int) -> None:
    assert parse_exit_status(text) == expected


@pytest.mark.parametrize("text", ["", "0", "0\nignored\n", "-1\n", "256\n", "success\n"])
def test_parse_terminal_exit_status_rejects_ambiguous_data(text: str) -> None:
    with pytest.raises(ValueError):
        parse_exit_status(text)


def test_bubblewrap_mount_order_overlays_writable_opt_before_app_bind(tmp_path) -> None:
    episode_app = tmp_path / "episode" / "opt" / "app"
    command = build_bubblewrap_command(
        episode_app=episode_app,
        bash_executable="/usr/bin/bash",
        bubblewrap_executable="/usr/bin/bwrap",
    )

    read_only_root = command.index("--ro-bind")
    opt_overlay = command.index("--tmpfs", read_only_root)
    app_directory = command.index("--dir", opt_overlay)
    app_bind = command.index("--bind", app_directory)
    assert command[opt_overlay + 1] == "/opt"
    assert command[app_directory + 1] == "/opt/app"
    assert command[app_bind + 1 : app_bind + 3] == [str(episode_app), "/opt/app"]
