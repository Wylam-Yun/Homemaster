"""Detached HomeMaster Cron scheduler process."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

from croniter import croniter

from homemaster.tools.runtime_services import HomeCronStore


async def run_scheduler(state_root: Path, *, poll_interval: float = 0.25) -> int:
    store = HomeCronStore(state_root / "cron")
    store.state_dir.mkdir(parents=True, exist_ok=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    store.pid_path.write_text(f"{os.getpid()}\n", encoding="ascii")
    try:
        while not stop.is_set():
            await _run_due_jobs(store)
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_interval)
            except TimeoutError:
                pass
    finally:
        try:
            if int(store.pid_path.read_text(encoding="ascii").strip()) == os.getpid():
                store.pid_path.unlink(missing_ok=True)
        except (FileNotFoundError, ValueError):
            pass
    return 0


async def _run_due_jobs(store: HomeCronStore) -> None:
    jobs = store.load()
    now = time.time()
    changed = False
    for job in jobs:
        if not job.get("enabled", True) or float(job.get("next_run", now + 1)) > now:
            continue
        command = job.get("command")
        if not isinstance(command, str) or not command:
            job["last_status"] = "unsupported_payload"
            job["last_return_code"] = None
        else:
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=str(Path(job.get("cwd") or Path.cwd()).expanduser().resolve()),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            output, _ = await process.communicate()
            job["last_return_code"] = process.returncode
            job["last_status"] = "completed" if process.returncode == 0 else "failed"
            log_path = store.state_dir / f"{job['name']}.log"
            log_path.write_bytes(output)
        job["last_run"] = datetime.now(UTC).isoformat()
        job["next_run"] = croniter(job["schedule"], now).get_next(float)
        changed = True
    if changed:
        store.save(jobs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", type=Path, default=Path("~/.homemaster").expanduser())
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run_scheduler(args.state_root.expanduser().resolve())))


if __name__ == "__main__":
    main()
