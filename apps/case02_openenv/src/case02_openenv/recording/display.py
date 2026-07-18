"""Dedicated loopback-only TigerVNC display and fixed companion windows."""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Any


class DisplayManager:
    def __init__(
        self,
        run_root: Path,
        *,
        display_min: int = 120,
        display_max: int = 159,
        tigervnc: str = "/usr/bin/Xtigervnc",
        chrome: str = "/usr/bin/google-chrome",
    ) -> None:
        self.run_root = run_root
        self.display_min = display_min
        self.display_max = display_max
        self.tigervnc = tigervnc
        self.chrome = chrome
        self.display_number: int | None = None
        self.vnc_port: int | None = None
        self.processes: dict[str, subprocess.Popen[Any]] = {}
        self.logs: list[Any] = []

    @property
    def display(self) -> str:
        if self.display_number is None:
            raise RuntimeError("display has not started")
        return f":{self.display_number}"

    def start(self, *, timeout_s: float = 20.0) -> dict[str, Any]:
        self.display_number = self._allocate_display()
        self.vnc_port = self._allocate_port()
        recording_dir = self.run_root / "video"
        recording_dir.mkdir(parents=True, exist_ok=True)
        stdout = (recording_dir / "xtigervnc.stdout.log").open("w", encoding="utf-8")
        stderr = (recording_dir / "xtigervnc.stderr.log").open("w", encoding="utf-8")
        self.logs.extend([stdout, stderr])
        command = [
            self.tigervnc,
            self.display,
            "-geometry",
            "1920x1080",
            "-depth",
            "24",
            "-rfbport",
            str(self.vnc_port),
            "-interface",
            "127.0.0.1",
            "-localhost",
            "yes",
            "-SecurityTypes",
            "None",
            "-ac",
            "-nolisten",
            "tcp",
            "-br",
        ]
        self.processes["tigervnc"] = subprocess.Popen(command, stdout=stdout, stderr=stderr)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            process = self.processes["tigervnc"]
            if process.poll() is not None:
                raise RuntimeError(f"Xtigervnc exited during startup: {process.returncode}")
            if Path(f"/tmp/.X11-unix/X{self.display_number}").exists():
                check = subprocess.run(
                    ["/usr/bin/xdpyinfo"],
                    env={**os.environ, "DISPLAY": self.display},
                    capture_output=True,
                    check=False,
                )
                if check.returncode == 0:
                    return {
                        "display": self.display,
                        "vnc_port": self.vnc_port,
                        "vnc_command": command,
                        "loopback_only": True,
                    }
            time.sleep(0.1)
        self.stop()
        raise TimeoutError("TigerVNC display did not become ready")

    def _observer_command(self, observer_url: str) -> list[str]:
        profile = self.run_root / "browser/observer-profile"
        profile.mkdir(parents=True, exist_ok=True)
        return [
            self.chrome,
            f"--app={observer_url}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--disable-dev-shm-usage",
            "--window-position=0,0",
            "--window-size=1920,1080",
        ]

    def start_companion_windows(self, *, observer_url: str) -> None:
        environment = {**os.environ, "DISPLAY": self.display}
        self.processes["observer"] = subprocess.Popen(
            self._observer_command(observer_url),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self) -> dict[str, Any]:
        observer = self.processes.get("observer")
        observer_was_alive = observer is not None and observer.poll() is None
        returns: dict[str, int | None] = {}
        for name in ("observer", "tigervnc"):
            process = self.processes.get(name)
            if process is None:
                continue
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            returns[name] = process.returncode
        for handle in self.logs:
            handle.close()
        return {
            "observer_was_alive": observer_was_alive,
            "return_codes": returns,
        }

    def _allocate_display(self) -> int:
        for number in range(self.display_min, self.display_max + 1):
            if (
                not Path(f"/tmp/.X11-unix/X{number}").exists()
                and not Path(f"/tmp/.X{number}-lock").exists()
            ):
                return number
        raise RuntimeError("no free display in configured range")

    @staticmethod
    def _allocate_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])
