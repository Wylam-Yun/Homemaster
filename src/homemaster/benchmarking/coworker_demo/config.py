"""Independent configuration loader for coworker demo runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ServiceSettings:
    bind_host: str
    port: int
    public_base_url: str
    startup_timeout_s: float
    request_timeout_s: float


@dataclass(frozen=True)
class PathSettings:
    data_root: Path
    artifact_root: Path
    service_python: Path


@dataclass(frozen=True)
class BrowserSettings:
    chrome_executable: Path
    action_timeout_s: float
    viewport_width: int
    viewport_height: int
    window_x: int
    window_y: int


@dataclass(frozen=True)
class DisplaySettings:
    tigervnc_executable: Path
    xterm_executable: Path
    display_min: int
    display_max: int
    width: int
    height: int
    depth: int
    localhost_only: bool


@dataclass(frozen=True)
class RecordingSettings:
    enabled: bool
    ffmpeg_executable: Path
    ffprobe_executable: Path
    frame_rate: int
    crf: int
    preset: str
    first_packet_timeout_s: float
    final_score_hold_s: float


@dataclass(frozen=True)
class TerminalSettings:
    tmux_executable: Path
    bash_executable: Path
    bubblewrap_executable: Path
    command_timeout_s: float


@dataclass(frozen=True)
class RuntimeSettings:
    max_tool_iterations: int
    max_browser_actions: int
    max_terminal_actions: int
    max_wall_time_s: float


@dataclass(frozen=True)
class CoworkerConfig:
    source_path: Path
    service: ServiceSettings
    paths: PathSettings
    browser: BrowserSettings
    display: DisplaySettings
    recording: RecordingSettings
    terminal: TerminalSettings
    runtime: RuntimeSettings


def load_coworker_config(path: Path | str = Path("config/coworker_demo.yaml")) -> CoworkerConfig:
    source = Path(path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"coworker config not found: {source}")
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("coworker config schema_version must be 1")
    project_root = source.parent.parent

    def absolute(value: Any) -> Path:
        candidate = Path(str(value)).expanduser()
        return (
            candidate.absolute()
            if candidate.is_absolute()
            else (project_root / candidate).absolute()
        )

    return CoworkerConfig(
        source_path=source,
        service=ServiceSettings(**raw["service"]),
        paths=PathSettings(
            data_root=absolute(raw["paths"]["data_root"]),
            artifact_root=absolute(raw["paths"]["artifact_root"]),
            service_python=absolute(raw["paths"]["service_python"]),
        ),
        browser=BrowserSettings(
            chrome_executable=absolute(raw["browser"]["chrome_executable"]),
            **{key: value for key, value in raw["browser"].items() if key != "chrome_executable"},
        ),
        display=DisplaySettings(
            tigervnc_executable=absolute(raw["display"]["tigervnc_executable"]),
            xterm_executable=absolute(raw["display"]["xterm_executable"]),
            **{
                key: value
                for key, value in raw["display"].items()
                if key not in {"tigervnc_executable", "xterm_executable"}
            },
        ),
        recording=RecordingSettings(
            ffmpeg_executable=absolute(raw["recording"]["ffmpeg_executable"]),
            ffprobe_executable=absolute(raw["recording"]["ffprobe_executable"]),
            **{
                key: value
                for key, value in raw["recording"].items()
                if key not in {"ffmpeg_executable", "ffprobe_executable"}
            },
        ),
        terminal=TerminalSettings(
            tmux_executable=absolute(raw["terminal"]["tmux_executable"]),
            bash_executable=absolute(raw["terminal"]["bash_executable"]),
            bubblewrap_executable=absolute(raw["terminal"]["bubblewrap_executable"]),
            command_timeout_s=float(raw["terminal"]["command_timeout_s"]),
        ),
        runtime=RuntimeSettings(**raw["runtime"]),
    )
