from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from homemaster.tools.contracts import (
    PermissionSubject,
    ToolExecutionContext,
    ToolExecutionStatus,
)
from homemaster.tools.observe import ScreenshotTool


class _Source:
    def __init__(self, value: bytes) -> None:
        self.value = value
        self.calls = 0

    async def screenshot(self) -> bytes:
        self.calls += 1
        return self.value


def _png() -> bytes:
    image = Image.new("RGB", (3, 2), color=(17, 34, 51))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _context(backend: object | None) -> ToolExecutionContext:
    return ToolExecutionContext(
        session_id="session",
        run_id="run",
        turn_index=0,
        tool_call_id="call",
        internal_tool_id="core.observe.v1",
        permission_subject=PermissionSubject(subject_id="user", channel="test"),
        backend=backend,
        deadline=None,
        cancellation=None,
        domain_observer=None,
        working_directory=Path.cwd(),
    )


@pytest.mark.asyncio
async def test_observe_returns_exactly_one_png_image_block_and_no_text() -> None:
    png = _png()
    source = _Source(png)

    result = await ScreenshotTool().execute({}, _context(source))

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.text == ""
    assert result.data == {}
    assert len(result.images) == 1
    assert source.calls == 1
    message = result.to_message(tool_call_id="call", name="observe")
    assert len(message.content) == 1
    assert message.content[0].type == "image"
    assert base64.b64decode(message.content[0].source["data"]) == png


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [b"", b"not-a-png"])
async def test_observe_rejects_missing_or_invalid_png(value: bytes) -> None:
    result = await ScreenshotTool().execute({}, _context(_Source(value)))

    assert result.status is ToolExecutionStatus.FAILURE
    assert result.error is not None
    assert result.error.code == "invalid_screenshot"
    assert result.images == ()


@pytest.mark.asyncio
async def test_observe_without_a_screenshot_source_fails_before_backend_attempt() -> None:
    result = await ScreenshotTool().execute({}, _context(None))

    assert result.status is ToolExecutionStatus.FAILURE
    assert result.error is not None
    assert result.error.code == "screenshot_unavailable"
    assert result.backend_attempted is False
