from __future__ import annotations

from pathlib import Path

from case02_openenv.recording.display import DisplayManager
from case02_openenv.recording.recorder import DemoRecorder


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
