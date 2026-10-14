"""End-to-end client tests against a real fake-worker subprocess."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from homemaster.robocasa.worker_client import RoboCasaWorkerClient, RoboCasaWorkerError

FAKE_WORKER = Path(__file__).with_name("fake_worker.py")


def _spawn(tmp_path: Path, success_after: int = 2) -> RoboCasaWorkerClient:
    workdir = tmp_path / "artifacts"
    # The fake worker takes the success threshold as argv[1]; wrap it in a
    # shim since the client spawn helper does not pass extra args.
    shim = tmp_path / "shim.py"
    shim.write_text(
        "import runpy, sys\n"
        f"sys.argv = [{str(FAKE_WORKER)!r}, {success_after!r}]\n"
        f"runpy.run_path({str(FAKE_WORKER)!r}, run_name='__main__')\n",
        encoding="utf-8",
    )
    return RoboCasaWorkerClient.start(
        python_executable=Path(sys.executable),
        worker_source=shim,
        artifact_root=workdir,
        log_path=tmp_path / "worker.stderr.log",
        startup_timeout_s=30.0,
        request_timeout_s=15.0,
    )


def test_full_cell_lifecycle(tmp_path: Path) -> None:
    client = _spawn(tmp_path)
    try:
        start = client.start_runtime(
            rpent_root=tmp_path,
            task_name="OpenDrawer",
            split="target",
            seed=1,
            cuda_device="2",
            hi_res=False,
            workdir=tmp_path / "w",
        )
        assert start["task_language"] == "Task: OpenDrawer"
        assert client.runtime_started is True

        obs = client.observe(cameras=["agentview"])
        assert obs["state"]["task_name"] == "OpenDrawer"
        assert obs["state"]["success"] is False
        assert client.current_state.won is False

        step = client.primitive("skill")
        assert step["primitive"] == "skill"
        assert step["errored"] is False
        assert client.current_state.step_index == 2

        check = client.check_success()
        # fake worker flips success after 2 mutating ops (skill + observe counted
        # only mutating ops; observe doesn't count, so after 2 skills)
        assert isinstance(check["success"], bool)

        client.primitive("skill")
        assert client.current_state.success is True
        assert client.check_success()["success"] is True

        reconf = client.reconfigure(task_name="CloseFridge", split="target", seed=3)
        assert reconf["task_language"] == "Task: CloseFridge"
    finally:
        result = client.close()
    assert result["closed"] is True
    assert client.worker_exit_code == 0


def test_screenshot_returns_verified_png(tmp_path: Path) -> None:
    import asyncio

    workdir = tmp_path / "artifacts"
    client = _spawn(tmp_path)
    try:
        # production: worker workdir == client artifact_root
        client.start_runtime(
            rpent_root=tmp_path,
            task_name="OpenDrawer",
            split="target",
            seed=1,
            cuda_device=None,
            hi_res=False,
            workdir=workdir,
        )
        png = asyncio.run(client.screenshot())
        assert png.startswith(b"\x89PNG")
    finally:
        client.close()


def test_worker_error_raises(tmp_path: Path) -> None:
    client = _spawn(tmp_path)
    try:
        # reconfigure before start → worker reports an error frame
        with pytest.raises(RoboCasaWorkerError):
            client.reconfigure(task_name="X", split="target", seed=0)
    finally:
        client.close()


def test_terminate_kills_process(tmp_path: Path) -> None:
    client = _spawn(tmp_path)
    client.terminate()
    assert client.worker_exit_code is not None
