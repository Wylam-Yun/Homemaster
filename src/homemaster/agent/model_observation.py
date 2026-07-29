"""Model-observation barrier helpers for state-changing environment actions."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING

from PIL import Image, UnidentifiedImageError

from homemaster.agent.messages import ContentBlock, ToolCall, ToolResultMessage

if TYPE_CHECKING:
    from homemaster.tools.base import ToolRegistry

MAX_PROTOCOL_FAILURES = 3
MAX_OBSERVE_FAILURES = 3


@dataclass(frozen=True)
class ObservationImageEvidence:
    content_sha256: str
    pixel_sha256: str


def load_model_observation_prompt() -> str:
    prompt = (
        resources.files("homemaster")
        .joinpath("prompts/model_observation_required.txt")
        .read_text(encoding="utf-8")
        .strip()
    )
    if not prompt:
        raise RuntimeError("model observation prompt is empty")
    return prompt


def append_model_observation_prompt(system_prompt: str) -> str:
    prompt = load_model_observation_prompt()
    return f"{system_prompt.rstrip()}\n\n{prompt}" if system_prompt.strip() else prompt


def observation_tool_schema(tool_schemas: list[dict[str, object]]) -> list[dict[str, object]]:
    selected = [schema for schema in tool_schemas if schema.get("name") == "observe"]
    if len(selected) != 1:
        raise RuntimeError("model observation barrier requires exactly one observe tool")
    return selected


def action_requires_model_observation(
    registry: ToolRegistry | None,
    tool_name: str,
) -> bool:
    tool = registry.get(tool_name) if registry is not None else None
    return bool(tool is not None and tool.requires_model_observation)


def observation_batch_error_results(
    tool_calls: list[ToolCall],
    *,
    code: str,
    message: str,
) -> list[ToolResultMessage]:
    return [
        ToolResultMessage(
            tool_call_id=call.id,
            name=call.name,
            content=[ContentBlock(text=message)],
            is_error=True,
            data={
                "status": "invalid",
                "error_code": code,
                "backend_attempted": False,
            },
        )
        for call in tool_calls
    ]


def validate_observation_result(result: ToolResultMessage) -> ObservationImageEvidence:
    if result.is_error:
        raise ValueError("observe returned an error")
    images = [block for block in result.content if block.type == "image"]
    if len(images) != 1:
        raise ValueError("observe must return exactly one image")
    source = images[0].source
    if not isinstance(source, dict):
        raise ValueError("observe image source is missing")
    if source.get("type") != "base64" or source.get("media_type") != "image/png":
        raise ValueError("observe image must be a base64 PNG")
    encoded = source.get("data")
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("observe image data is empty")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("observe image base64 is invalid") from exc
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format != "PNG" or image.width < 1 or image.height < 1:
                raise ValueError("observe image is not a non-empty PNG")
            image.load()
            pixels = image.tobytes()
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ValueError("observe image is invalid") from exc
    return ObservationImageEvidence(
        content_sha256=hashlib.sha256(content).hexdigest(),
        pixel_sha256=hashlib.sha256(pixels).hexdigest(),
    )


__all__ = [
    "MAX_OBSERVE_FAILURES",
    "MAX_PROTOCOL_FAILURES",
    "ObservationImageEvidence",
    "action_requires_model_observation",
    "append_model_observation_prompt",
    "observation_batch_error_results",
    "observation_tool_schema",
    "validate_observation_result",
]
