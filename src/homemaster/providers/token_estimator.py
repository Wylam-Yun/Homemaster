"""Provider-specific token estimation for context budgeting."""

from __future__ import annotations

import base64
import json
import math
from typing import Protocol

from homemaster.agent.messages import Message
from homemaster.config import ProviderProfileConfig


class TokenEstimator(Protocol):
    def estimate_text(self, text: str) -> int: ...

    def estimate_image(self, *, base64_data: str, media_type: str) -> int: ...

    def estimate_json(self, value: object) -> int: ...

    def estimate_messages(self, messages: list[Message]) -> int: ...

    def real_usage(self, usage: dict[str, int]) -> int: ...

    def supports_real_usage(self) -> bool: ...


class BaseTokenEstimator:
    """Shared estimator behavior with simple calibration support."""

    def __init__(self) -> None:
        self._calibration_ratio: float | None = None

    def estimate_text(self, text: str) -> int:
        cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
        non_cjk = max(0, len(text) - cjk)
        return max(1, math.ceil(cjk / 2) + math.ceil(non_cjk / 4))

    def estimate_image(self, *, base64_data: str, media_type: str) -> int:
        width, height = decode_image_dimensions(base64_data)
        return max(1, (width * height) // 750)

    def estimate_json(self, value: object) -> int:
        return self.estimate_text(json.dumps(value, ensure_ascii=False, sort_keys=True))

    def estimate_messages(self, messages: list[Message]) -> int:
        total = 0
        for message in messages:
            for block in message.content:
                if block.type == "text" and block.text:
                    total += self.estimate_text(block.text)
                elif block.type == "image" and isinstance(block.source, dict):
                    total += self.estimate_image(
                        base64_data=str(block.source.get("data", "")),
                        media_type=str(block.source.get("media_type", "image/png")),
                    )
        return total

    def real_usage(self, usage: dict[str, int]) -> int:
        return int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)

    def supports_real_usage(self) -> bool:
        return True

    def calibrate(self, estimated: int, real: int) -> None:
        if estimated <= 0 or real <= 0:
            return
        ratio = real / estimated
        self._calibration_ratio = (
            ratio
            if self._calibration_ratio is None
            else self._calibration_ratio * 0.7 + ratio * 0.3
        )

    def calibrated_estimate(self, raw_estimate: int) -> int:
        if self._calibration_ratio is None:
            return raw_estimate
        return max(1, int(raw_estimate * self._calibration_ratio))


class MimoTokenEstimator(BaseTokenEstimator):
    def estimate_image(self, *, base64_data: str, media_type: str) -> int:
        width, height = decode_image_dimensions(base64_data)
        return max(1, (width * height) // 1000)

    def real_usage(self, usage: dict[str, int]) -> int:
        return int(usage.get("input_tokens") or 0) + int(
            usage.get("cache_read_input_tokens") or 0
        )


class OpenAIChatTokenEstimator(BaseTokenEstimator):
    def estimate_image(self, *, base64_data: str, media_type: str) -> int:
        width, height = decode_image_dimensions(base64_data)
        tiles = max(1, math.ceil(width / 512) * math.ceil(height / 512))
        return 170 + 85 * tiles

    def real_usage(self, usage: dict[str, int]) -> int:
        return int(usage.get("prompt_tokens") or 0)


def make_default_estimator(provider: ProviderProfileConfig) -> TokenEstimator:
    if provider.api_format == "openai":
        return OpenAIChatTokenEstimator()
    return MimoTokenEstimator()


def decode_image_dimensions(base64_data: str) -> tuple[int, int]:
    header = base64.b64decode(base64_data[:512], validate=False)
    if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
        return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")
    if header.startswith(b"\xff\xd8"):
        return _decode_jpeg_dimensions(header)
    raise ValueError("unsupported image format")


def _decode_jpeg_dimensions(data: bytes) -> tuple[int, int]:
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        segment_length = int.from_bytes(data[index + 2 : index + 4], "big")
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB}:
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return width, height
        if segment_length <= 0:
            break
        index += 2 + segment_length
    raise ValueError("unsupported JPEG header")
