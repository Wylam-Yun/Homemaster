"""Generic current-frame screenshot tool."""

from __future__ import annotations

import base64
import hashlib
import inspect
import io
from collections.abc import Awaitable, Mapping
from typing import Protocol, runtime_checkable

from PIL import Image, UnidentifiedImageError

from homemaster.tools.contracts import (
    ResultImage,
    ResultProjection,
    ToolExecutionContext,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
)


@runtime_checkable
class ScreenshotSource(Protocol):
    """Borrowed environment capable of returning its current visual frame."""

    def screenshot(self) -> Awaitable[bytes]: ...


class ScreenshotTool:
    """Return one current PNG frame without affecting environment state."""

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        del arguments
        source = _screenshot_source(context.backend)
        if source is None:
            return _failure(
                "screenshot_unavailable",
                "the current environment does not provide a screenshot source",
                backend_attempted=False,
            )
        try:
            captured = source.screenshot()
            png = await captured if inspect.isawaitable(captured) else captured
        except Exception:
            return _failure(
                "screenshot_unavailable",
                "the current environment could not capture a screenshot",
                backend_attempted=True,
            )
        if not isinstance(png, bytes):
            return _failure(
                "invalid_screenshot",
                "the screenshot source did not return PNG bytes",
                backend_attempted=True,
            )
        try:
            pixel_sha256 = _validate_png(png)
        except ValueError:
            return _failure(
                "invalid_screenshot",
                "the screenshot source did not return a valid non-empty PNG",
                backend_attempted=True,
            )
        content_sha256 = hashlib.sha256(png).hexdigest()
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            images=(
                ResultImage(
                    media_type="image/png",
                    data_base64=base64.b64encode(png).decode("ascii"),
                    content_sha256=content_sha256,
                    pixel_sha256=pixel_sha256,
                ),
            ),
            backend_attempted=True,
            model_projection=ResultProjection.IMAGE_ONLY,
        )


def _screenshot_source(backend: object | None) -> ScreenshotSource | None:
    if backend is None:
        return None
    candidate = backend
    # Device connection handles deliberately expose the borrowed object this way.
    actual_backend = getattr(backend, "actual_backend", None)
    if actual_backend is not None:
        candidate = actual_backend
    method = getattr(candidate, "screenshot", None)
    if not callable(method):
        return None
    return candidate  # type: ignore[return-value]


def _validate_png(content: bytes) -> str:
    if not content:
        raise ValueError("empty image")
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format != "PNG" or image.width < 1 or image.height < 1:
                raise ValueError("not a non-empty PNG")
            image.load()
            pixels = image.tobytes()
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ValueError("invalid PNG") from exc
    return hashlib.sha256(pixels).hexdigest()


def _failure(
    code: str,
    message: str,
    *,
    backend_attempted: bool,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        status=ToolExecutionStatus.FAILURE,
        error=ToolExecutionError(code, message),
        backend_attempted=backend_attempted,
    )


__all__ = ["ScreenshotSource", "ScreenshotTool"]
