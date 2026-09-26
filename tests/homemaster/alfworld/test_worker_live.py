from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from homemaster.alfworld.worker_client import AlfworldWorkerClient


@pytest.mark.live_alfworld
@pytest.mark.asyncio
async def test_isolated_worker_real_thor_lifecycle_and_actions(tmp_path: Path) -> None:
    repo = Path(os.environ.get("HOMEMASTER_LIVE_REPO", "/home/haodong2/weilin/red_bird/Homemaster"))
    asset_root = Path(
        os.environ.get("HOMEMASTER_LIVE_ALFWORLD_ROOT", "/home/haodong2/weilin/red_bird/alfworld")
    )
    python_executable = Path(
        os.environ.get(
            "HOMEMASTER_LIVE_ALFWORLD_PYTHON",
            str(repo / ".runtime" / "alfworld-venv" / "bin" / "python"),
        )
    )
    manifest = Path(
        os.environ.get(
            "HOMEMASTER_LIVE_TRIAL_MANIFEST",
            str(repo / ".runtime" / "single_mug_manifest.json"),
        )
    )
    config_path = asset_root / "configs" / "base_config.yaml"
    data_root = asset_root / "data"
    display = os.environ.get("HOMEMASTER_LIVE_DISPLAY", ":99")
    required = (repo, asset_root, python_executable, manifest, config_path, data_root)
    if not all(path.exists() for path in required):
        pytest.skip("configured live ALFWorld worker environment is unavailable")
    if (
        subprocess.run(
            ["xdpyinfo", "-display", display],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        != 0
    ):
        pytest.skip(f"display {display} is unavailable")

    log_path = tmp_path / "worker.stderr.log"
    client = AlfworldWorkerClient.start(
        python_executable=python_executable,
        asset_root=asset_root,
        data_root=data_root,
        config_path=config_path,
        trial_manifest=manifest,
        trial_index=0,
        env_type="AlfredThorEnv",
        split="valid_unseen",
        seed=42,
        allow_offscreen_object_navigation=False,
        display=display,
        frame_dir=tmp_path / "frames",
        log_path=log_path,
        startup_timeout_s=180,
        request_timeout_s=180,
    )
    manifest_entry = json.loads(manifest.read_text(encoding="utf-8"))["entries"][0]
    expected_identity = {
        key: manifest_entry[key]
        for key in (
            "trial_id",
            "trial_sha256",
            "expected_logical_scene",
            "goal_identity",
            "goal_fingerprint",
        )
    }
    reset_identity = client.runtime_identity
    for key, expected in expected_identity.items():
        assert reset_identity[key] == expected
    assert reset_identity["logical_scene"] == expected_identity["expected_logical_scene"]
    action_records: list[dict[str, object]] = []
    initial_state = client.raw_state
    screenshot_digest = ""
    close_receipt = None
    try:
        assert client.current_state.step_index == 0
        frame = await client.screenshot()
        screenshot_digest = hashlib.sha256(frame).hexdigest()
        with Image.open(io.BytesIO(frame)) as image:
            image.load()
            assert image.format == "PNG"
            assert image.width > 0 and image.height > 0

        before_navigation = client.raw_state
        navigation = client.go_to_target(
            "Mug", tool_name="robot_go_to", tool_args={"target": "Mug"}
        )
        assert navigation.success
        assert navigation.execution_feedback.state_read_status == "ok"
        assert navigation.backend_action_count == 1
        action_records.append(
            {
                "operation": "navigate",
                "before": before_navigation,
                "after": client.raw_state,
                "success": navigation.success,
            }
        )

        before_manipulation = client.raw_state
        manipulation = client.manipulate_with_thor(
            action="take",
            tool_name="robot_manipulate",
            tool_args={"object": "Mug"},
        )
        assert manipulation.success
        assert manipulation.execution_feedback.state_read_status == "ok"
        assert manipulation.backend_action_count == 1
        action_records.append(
            {
                "operation": "take",
                "before": before_manipulation,
                "after": client.raw_state,
                "success": manipulation.success,
            }
        )
        before_target_navigation = client.raw_state
        target_navigation = client.go_to_target(
            "Desk", tool_name="robot_go_to", tool_args={"target": "Desk"}
        )
        assert target_navigation.success
        action_records.append(
            {
                "operation": "navigate",
                "before": before_target_navigation,
                "after": client.raw_state,
                "success": target_navigation.success,
            }
        )
        before_put = client.raw_state
        put = client.manipulate_with_thor(
            action="put",
            tool_name="robot_manipulate",
            tool_args={"object": "Mug", "target_receptacle": "Desk"},
        )
        assert put.success
        assert client.current_state.won
        action_records.append(
            {
                "operation": "put",
                "before": before_put,
                "after": client.raw_state,
                "success": put.success,
            }
        )
    finally:
        close_receipt = client.close()

    assert client._process.poll() is not None
    stderr = log_path.read_text(encoding="utf-8", errors="replace")
    assert "Traceback (most recent call last)" not in stderr
    evidence_root = Path(
        os.environ.get(
            "HOMEMASTER_PHASE5_EVIDENCE_ROOT",
            str(repo / "plan" / "V3.5" / "evidence" / "phase-5"),
        )
    )
    evidence_dir = evidence_root / expected_identity["trial_id"]
    evidence_dir.mkdir(parents=True, exist_ok=True)
    persistent_stderr = evidence_dir / "worker.stderr.log"
    persistent_stderr.write_text(stderr, encoding="utf-8")
    evidence = {
        "schema": "homemaster-v35-phase5-worker-live-v1",
        "trial_id": expected_identity["trial_id"],
        "manifest_identity": expected_identity,
        "worker_identity": reset_identity,
        "request_history": list(client.request_history),
        "initial_state": initial_state,
        "actions": action_records,
        "screenshot": {"sha256": screenshot_digest, "mime_type": "image/png"},
        "terminal": {"won": client.raw_state.get("won"), "terminal_owner": "alfworld_thor"},
        "close": (
            {
                "operation": close_receipt.operation,
                "external_return_code": close_receipt.external_return_code,
                "backend_attempted": close_receipt.backend_attempted,
                "state": close_receipt.state,
                "evidence_ref": close_receipt.evidence_ref,
                "error": close_receipt.error,
            }
            if close_receipt is not None
            else None
        ),
        "worker_pid": client.worker_pid,
        "worker_exit_code": client._process.returncode,
        "stderr_path": str(persistent_stderr),
        "stderr": stderr,
        "cleanup": {"worker_exited": client._process.poll() is not None},
    }
    (evidence_dir / "episode.json").write_text(
        json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )
