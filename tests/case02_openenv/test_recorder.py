from __future__ import annotations

import json
from pathlib import Path

from case02_openenv.recording.display import DisplayManager
from case02_openenv.recording.recorder import DemoRecorder
from case02_openenv.recording.verifier import VideoVerifier
from PIL import Image


def test_recorder_contract_uses_fragmented_h264_x11grab(tmp_path: Path) -> None:
    recorder = DemoRecorder(run_id="run", run_root=tmp_path, display=":144")
    assert recorder.display == ":144"
    assert recorder.part.name == "demo.mp4.part"
    assert recorder.video.name == "demo.mp4"
    source = Path("apps/case02_openenv/src/case02_openenv/recording/recorder.py").read_text(
        encoding="utf-8"
    )
    for argument in ('"-g"', '"-keyint_min"', '"-sc_threshold"', '"-flush_packets"'):
        assert argument in source


def test_display_allocator_skips_live_socket(tmp_path: Path, monkeypatch) -> None:
    manager = DisplayManager(tmp_path, display_min=120, display_max=121)
    original_exists = Path.exists

    def fake_exists(path: Path) -> bool:
        if str(path) in {"/tmp/.X11-unix/X120", "/tmp/.X120-lock"}:
            return True
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", fake_exists)
    assert manager._allocate_display() == 121


def test_executive_observer_command_is_full_screen_and_has_no_xterm(
    tmp_path: Path,
) -> None:
    manager = DisplayManager(tmp_path)

    command = manager._observer_command("http://127.0.0.1:8765/observer/run")

    assert "--window-position=0,0" in command
    assert "--window-size=1920,1080" in command
    for argument in (
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-sync",
        "--metrics-recording-only",
        "--password-store=basic",
        "--disable-features=OptimizationHints,MediaRouter,Translate",
    ):
        assert argument in command
    assert all("xterm" not in part for part in command)


def test_display_stop_reports_observer_was_alive(tmp_path: Path) -> None:
    manager = DisplayManager(tmp_path)

    class FakeProcess:
        returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = -15

        def wait(self, timeout: float) -> int | None:
            return self.returncode

    manager.processes["observer"] = FakeProcess()  # type: ignore[assignment]

    result = manager.stop()

    assert result["observer_was_alive"] is True
    assert result["return_codes"] == {"observer": -15}


def test_observer_readiness_waits_for_visible_content(tmp_path: Path, monkeypatch) -> None:
    manager = DisplayManager(tmp_path)
    manager.display_number = 120

    class FakeProcess:
        def poll(self) -> None:
            return None

    manager.processes["observer"] = FakeProcess()  # type: ignore[assignment]
    visible = iter([False, False, True])
    monkeypatch.setattr(manager, "_display_has_visible_content", lambda: next(visible))
    monkeypatch.setattr("case02_openenv.recording.display.time.sleep", lambda _delay: None)

    manager._wait_for_observer_ready(timeout_s=1)


def test_observer_readiness_allows_slow_fresh_profile_startup(tmp_path: Path) -> None:
    source = Path("apps/case02_openenv/src/case02_openenv/recording/display.py").read_text(
        encoding="utf-8"
    )

    assert "timeout_s: float = 30.0" in source


def test_observer_readiness_rejects_a_white_loading_screen(tmp_path: Path, monkeypatch) -> None:
    manager = DisplayManager(tmp_path)
    manager.display_number = 120

    monkeypatch.setattr(
        "case02_openenv.recording.display.ImageGrab.grab",
        lambda **_kwargs: Image.new("RGB", (20, 10), "white"),
    )

    assert manager._display_has_visible_content() is False


def test_observer_readiness_accepts_contrasting_dark_content(tmp_path: Path, monkeypatch) -> None:
    manager = DisplayManager(tmp_path)
    manager.display_number = 120
    rendered = Image.new("RGB", (20, 10), "white")
    for x in range(10):
        for y in range(10):
            rendered.putpixel((x, y), (20, 20, 20))

    monkeypatch.setattr(
        "case02_openenv.recording.display.ImageGrab.grab",
        lambda **_kwargs: rendered.copy(),
    )

    assert manager._display_has_visible_content() is True


def test_video_frame_stats_include_dark_pixel_ratio(tmp_path: Path) -> None:
    frame = Image.new("RGB", (1920, 1080), "white")
    frame.paste("black", (0, 0, 960, 1080))
    path = tmp_path / "frame.png"
    frame.save(path)

    assert VideoVerifier._frame_stats(path) == {
        "nonblack_ratio": 0.5,
        "dark_ratio": 0.5,
        "variance": 16256.25,
    }


def test_named_frames_use_persisted_monotonic_offsets_and_settle_margin(tmp_path: Path) -> None:
    recorder = DemoRecorder(
        run_id="run",
        run_root=tmp_path,
        display=":144",
        ui_settle_margin_s=0.35,
    )
    events = [
        {
            "event_id": "event-first",
            "sequence": 1,
            "event_type": "tool.call_started",
            "timestamp": "2026-07-19T08:00:00Z",
            "monotonic_offset_s": 1.0,
        },
        {
            "event_id": "event-open",
            "sequence": 2,
            "event_type": "tool.call_failed",
            "timestamp": "2026-07-19T08:00:01Z",
            "monotonic_offset_s": 2.0,
            "incident_delta": {"status": "open"},
        },
        {
            "event_id": "event-recovered",
            "sequence": 3,
            "event_type": "tool.call_completed",
            "timestamp": "2026-07-19T08:00:02Z",
            "monotonic_offset_s": 3.0,
            "incident_delta": {"status": "resolved"},
            "result": {"caused_by_current_change": True},
        },
        {
            "event_id": "event-terminal",
            "sequence": 4,
            "event_type": "runtime.turn_completed",
            "timestamp": "2026-07-19T08:00:03Z",
            "monotonic_offset_s": 4.0,
            "stage": "terminal",
        },
    ]
    path = tmp_path / "presentation/events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    requests = recorder._named_frame_requests()

    assert [request["name"] for request in requests] == [
        "first_model_action",
        "incident_open_2",
        "incident_resolved_3",
        "causal_alarm_3",
        "terminal_outcome",
    ]
    assert requests[0]["timestamp_s"] == 1.35
    assert requests[0]["calculated_offset_s"] == 1.35
    assert requests[0]["ffmpeg_basis"] == "recording_monotonic_origin"
    assert requests[-1]["source_event_id"] == "event-terminal"
    assert all(request["ui_settle_margin_s"] == 0.35 for request in requests)


def test_observer_region_stats_are_independent_from_full_frame(tmp_path: Path) -> None:
    frame = Image.new("RGB", (1920, 1080), "black")
    frame.paste("white", (1320, 96, 1620, 996))
    path = tmp_path / "observer-frame.png"
    frame.save(path)

    stats = VideoVerifier._region_stats(path, (1320, 96, 1920, 996))

    assert stats["nonblack_ratio"] == 0.5
    assert stats["dark_ratio"] == 0.5
    assert stats["variance"] == 16256.25
