"""Secret-safe readiness checks for a real coworker demo run."""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from homemaster.benchmarking.coworker_demo.config import load_coworker_config
from homemaster.benchmarking.coworker_demo.ticket_bundle import CaseRepository
from homemaster.config import load_config


def run_preflight(coworker_config: Path, provider_config: Path) -> dict[str, Any]:
    config = load_coworker_config(coworker_config)
    home = load_config(provider_config)
    provider = home.get_provider(home.runtime_defaults.default_provider_name, kind="chat")
    checks: dict[str, Any] = {}
    checks["provider"] = {
        "pass": bool(provider.base_url and provider.model and provider.api_keys),
        "name": provider.name,
        "model": provider.model,
        "api_format": provider.api_format,
        "key_count": len(provider.api_keys),
    }
    checks["config_mode"] = {
        "pass": provider_config.is_file() and provider_config.stat().st_mode & 0o077 == 0,
        "mode": oct(provider_config.stat().st_mode & 0o777),
    }
    executables = {
        "service_python": config.paths.service_python,
        "chrome": config.browser.chrome_executable,
        "tigervnc": config.display.tigervnc_executable,
        "xterm": config.display.xterm_executable,
        "ffmpeg": config.recording.ffmpeg_executable,
        "ffprobe": config.recording.ffprobe_executable,
        "tmux": config.terminal.tmux_executable,
        "bash": config.terminal.bash_executable,
        "bubblewrap": config.terminal.bubblewrap_executable,
    }
    checks["executables"] = {
        "pass": all(path.is_file() for path in executables.values()),
        "found": {name: path.is_file() for name, path in executables.items()},
    }
    encoder = subprocess.run(
        [str(config.recording.ffmpeg_executable), "-hide_banner", "-encoders"],
        text=True,
        capture_output=True,
        check=False,
    )
    checks["libx264"] = {
        "pass": encoder.returncode == 0 and "libx264" in encoder.stdout,
        "return_code": encoder.returncode,
    }
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((config.service.bind_host, config.service.port))
        port_free = True
    except OSError:
        port_free = False
    checks["service_port"] = {"pass": port_free, "port": config.service.port}
    bundle = CaseRepository(config.paths.data_root).resolve(
        config.paths.data_root / "test_set/item_change_ticket.json", "normal"
    )
    checks["bundle"] = {
        "pass": len(bundle.required_nodes) == 24,
        "locked_file_count": len(bundle.locked_hashes),
    }
    disk = shutil.disk_usage(config.paths.artifact_root.parent)
    checks["disk"] = {"pass": disk.free >= 2 * 1024**3, "free_bytes": disk.free}
    return {
        "schema_version": 1,
        "checks": checks,
        "pass": all(value["pass"] for value in checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coworker-config", type=Path, default=Path("config/coworker_demo.yaml"))
    parser.add_argument("--provider-config", type=Path, default=Path("config/homemaster.yaml"))
    args = parser.parse_args()
    result = run_preflight(args.coworker_config.resolve(), args.provider_config.resolve())
    print(json.dumps(result, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
