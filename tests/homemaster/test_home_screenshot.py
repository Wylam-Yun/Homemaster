from __future__ import annotations

import io

import pytest
from PIL import Image, ImageGrab

from homemaster.cli.composition import HomeCliBackend


@pytest.mark.asyncio
async def test_home_backend_encodes_the_current_display_as_png(monkeypatch) -> None:
    calls: list[dict[str, str]] = []

    def grab(**kwargs):
        calls.append(kwargs)
        return Image.new("RGB", (3, 2), color=(11, 22, 33))

    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.setattr(ImageGrab, "grab", grab)
    backend = HomeCliBackend(world_path=None, memory_path=None)

    screenshot = await backend.screenshot()

    with Image.open(io.BytesIO(screenshot)) as image:
        assert image.format == "PNG"
        assert image.size == (3, 2)
        assert image.getpixel((0, 0)) == (11, 22, 33)
    assert calls == [{"xdisplay": ":99"}]
