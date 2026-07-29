from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from homemaster.application.resources import RunResourceScope
from homemaster.config import AlfworldGatewayConfig
from homemaster.gateway.alfworld import create_alfworld_gateway_binding


@pytest.mark.live_alfworld
@pytest.mark.asyncio
async def test_isolated_http_environment_real_action_image_and_cleanup(
    tmp_path: Path,
) -> None:
    repo = Path(
        os.environ.get(
            "HOMEMASTER_LIVE_REPO",
            "/home/haodong2/weilin/red_bird/Homemaster",
        )
    )
    asset_root = Path(
        os.environ.get(
            "HOMEMASTER_LIVE_ALFWORLD_ROOT",
            "/home/haodong2/weilin/red_bird/alfworld",
        )
    )
    python_executable = Path(
        os.environ.get(
            "HOMEMASTER_LIVE_ALFWORLD_PYTHON",
            "/data0/yuqiao/envs/hm_alfworld/bin/python",
        )
    )
    required = (
        repo,
        asset_root / "data" / "json_2.1.1",
        asset_root / "configs" / "base_config.yaml",
        python_executable,
        repo / "config" / "alfworld_v18_regression_trials.json",
    )
    if not all(path.exists() for path in required):
        pytest.skip("configured live ALFWorld environment is unavailable")

    display = os.environ.get("HOMEMASTER_LIVE_DISPLAY", ":107")
    assert not _display_ready(display)
    scope = RunResourceScope()
    binding = None
    try:
        binding, _owner = await create_alfworld_gateway_binding(
            AlfworldGatewayConfig(
                asset_root=asset_root,
                data_root=asset_root / "data",
                config_path=asset_root / "configs" / "base_config.yaml",
                python_executable=python_executable,
                trial_manifest=repo / "config" / "alfworld_v18_regression_trials.json",
                trial_index=3,
                display=display,
                manage_xvfb=True,
                allow_offscreen_object_navigation=False,
            ),
            run_dir=tmp_path / "live-run",
            resource_scope=scope,
        )
        initial = binding.adapter.current_state
        assert initial.episode_id == (
            "valid_unseen/pick_and_place_simple-SaltShaker-None-Drawer-10"
        )
        artifact_root = tmp_path / "live-run" / "alfworld"
        initial_event = _latest_event(artifact_root)
        metadata_objects = initial_event["raw_metadata_payload"]["objects"]
        salt_shaker = _first_object(metadata_objects, "SaltShaker")
        drawer = _first_object(metadata_objects, "Drawer")
        assert salt_shaker["visible"] is False
        assert salt_shaker["receptacle"] is False
        assert drawer["visible"] is False
        assert drawer["receptacle"] is True
        action_events_before = len(list((artifact_root / "events").glob("*.json")))

        rejected = binding.adapter.go_to_target(
            "SaltShaker",
            tool_name="robot_go_to",
            tool_args={"target": "SaltShaker"},
        )
        assert rejected.success is False
        assert rejected.failure_reason == "target_not_visible"
        assert rejected.backend_action_count == 0
        assert rejected.state.step_index == initial.step_index
        assert len(list((artifact_root / "events").glob("*.json"))) == action_events_before

        navigated = binding.adapter.go_to_target(
            "Drawer",
            tool_name="robot_go_to",
            tool_args={"target": "Drawer"},
        )
        assert navigated.backend_action_count == 1
        assert navigated.success is True
        assert navigated.state.step_index >= initial.step_index
        assert len(list((artifact_root / "events").glob("*.json"))) == (action_events_before + 1)
        navigation_event = _latest_event(artifact_root)
        snapshot = json.loads((artifact_root / "oracle-pose-snapshot.json").read_text())
        snapshot_entry = next(
            item for item in snapshot["entries"] if item["exact_object_id"] == drawer["objectId"]
        )
        assert navigation_event["returned_action"] == "TeleportFull"
        assert navigation_event["action_success"] is True
        assert drawer["objectId"] in navigation_event["strict_visible_exact_ids"]
        assert navigation_event["returned_pose"] == snapshot_entry["pose"]

        png = await binding.adapter.screenshot()
        content_sha256 = hashlib.sha256(png).hexdigest()
        with Image.open(io.BytesIO(png)) as image:
            image.load()
            assert image.size == (300, 300)
            pixel_sha256 = hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest()
        assert len(png) > 10_000
        assert len(content_sha256) == len(pixel_sha256) == 64
        assert binding.adapter.runtime_identity["ai2thor_version"] == "2.1.0"
        assert binding.adapter.runtime_identity["allow_offscreen_object_navigation"] is False
        assert str(binding.adapter.runtime_identity["alfworld_origin"]).startswith(
            str(python_executable.parents[1])
        )
    finally:
        await scope.aclose()

    assert binding is not None
    assert not _pid_exists(binding.adapter.worker_pid)
    assert not _display_ready(display)


def _display_ready(display: str) -> bool:
    result = subprocess.run(
        ["xdpyinfo", "-display", display],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _latest_event(artifact_root: Path) -> dict[str, object]:
    event_path = sorted((artifact_root / "events").glob("*.json"))[-1]
    value = json.loads(event_path.read_text())
    assert isinstance(value, dict)
    return value


def _first_object(objects: object, object_type: str) -> dict[str, object]:
    assert isinstance(objects, list)
    matches = sorted(
        (
            item
            for item in objects
            if isinstance(item, dict) and item.get("objectType") == object_type
        ),
        key=lambda item: str(item["objectId"]),
    )
    assert matches
    return matches[0]


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
