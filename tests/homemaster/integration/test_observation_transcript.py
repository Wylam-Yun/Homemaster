from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from homemaster.adapters import build_universal_tool_registry
from homemaster.tools.contracts import ToolExecutionStatus
from tests.homemaster.tools.universal_harness import execute


class Backend:
    backend_id = "alfworld-backend"
    generation = 1

    def __init__(self) -> None:
        self.state_sequence = 0
        self.event_sequence = 0
        self.screenshot_count = 0

    async def screenshot(self) -> bytes:
        self.screenshot_count += 1
        image = Image.new("RGB", (4, 3), color=(18, 52, 86))
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def advance(self) -> None:
        self.state_sequence += 1
        self.event_sequence += 1


@pytest.mark.asyncio
async def test_explicit_screenshot_is_image_only_and_creates_no_action_debt() -> None:
    registry = build_universal_tool_registry()
    backend = Backend()
    observe = registry.get("observe")
    action = registry.get("robot_go_to")
    assert observe is not None and action is not None

    result = await execute(
        registry,
        Path.cwd(),
        "observe",
        {},
        capabilities=("device.read",),
        backend=backend,
        call_id="observe-1",
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert backend.screenshot_count == 1
    assert backend.state_sequence == 0
    assert backend.event_sequence == 0
    assert result.text == ""
    assert len(result.data["images"]) == 1
    assert result.data["images"][0]["media_type"] == "image/png"
    assert action.name == "robot_go_to"
