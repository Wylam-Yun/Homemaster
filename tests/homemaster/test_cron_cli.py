"""Process-level black-box gate for the HomeMaster Cron CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _run(cli: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(cli), "cron", *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_cron_cli_starts_executes_due_job_reports_status_and_stops(tmp_path: Path) -> None:
    cli = Path(sys.executable).with_name("homemaster")
    assert cli.is_file()
    state_root = tmp_path / "home"
    cron_dir = state_root / "cron"
    cron_dir.mkdir(parents=True)
    terminal = tmp_path / "scheduler-terminal.txt"
    registry = cron_dir / "cron_jobs.json"
    registry.write_text(
        json.dumps(
            [
                {
                    "name": "due-job",
                    "schedule": "* * * * *",
                    "command": f"printf scheduler-verified > {terminal}",
                    "cwd": str(tmp_path),
                    "enabled": True,
                    "next_run": 0,
                }
            ]
        ),
        encoding="utf-8",
    )

    started = _run(cli, "start", "--state-root", str(state_root))
    assert started.returncode == 0, started.stderr
    pid = int(started.stdout.strip().split("pid=", 1)[1])
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not terminal.exists():
            time.sleep(0.02)
        status = _run(cli, "status", "--state-root", str(state_root))

        assert status.returncode == 0
        assert status.stdout.strip() == f"running pid={pid}"
        assert terminal.read_text(encoding="utf-8") == "scheduler-verified"
        completed = json.loads(registry.read_text(encoding="utf-8"))[0]
        assert completed["last_return_code"] == 0
        assert completed["last_status"] == "completed"
    finally:
        stopped = _run(cli, "stop", "--state-root", str(state_root))
        assert stopped.returncode == 0, stopped.stderr

    after = _run(cli, "status", "--state-root", str(state_root))
    assert after.returncode == 1
    assert after.stdout.strip() == "stopped"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError(f"Cron scheduler pid={pid} still exists after stop")
