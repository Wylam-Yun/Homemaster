from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from homemaster.adapters import build_alfworld_profile
from homemaster.tools.contracts import PermissionSubject, ToolExecutionContext, ToolExecutionStatus


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


def _context(profile, backend: Backend) -> ToolExecutionContext:
    return ToolExecutionContext(
        session_id="session-1",
        run_id="run-1",
        turn_index=0,
        tool_call_id="observe-1",
        internal_tool_id="core.observe.v1",
        tool_view=profile.view,
        permission_subject=PermissionSubject(subject_id="user", channel="test"),
        backend=backend,
        deadline=None,
        cancellation=None,
        domain_observer=None,
        working_directory=Path.cwd(),
    )


@pytest.mark.asyncio
async def test_explicit_screenshot_is_image_only_and_creates_no_action_debt() -> None:
    profile = build_alfworld_profile()
    backend = Backend()
    observe = profile.view.lookup("observe").tool
    action = profile.view.lookup("robot_go_to").tool
    assert observe is not None and action is not None

    result = await observe.executor.execute({}, _context(profile, backend))

    assert result.status is ToolExecutionStatus.SUCCESS
    assert backend.screenshot_count == 1
    assert backend.state_sequence == 0
    assert backend.event_sequence == 0
    message = result.to_message(tool_call_id="observe-1", name="observe")
    assert len(message.content) == 1
    assert message.content[0].type == "image"
    assert message.content[0].metadata == {}
    assert action.definition.model_alias == "robot_go_to"
