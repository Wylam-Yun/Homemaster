"""Provider-specific token estimation for context budgeting."""

from __future__ import annotations

import base64
import json
import math
import re
from typing import Protocol

from homemaster.agent.messages import Message
from homemaster.config import ProviderProfileConfig

# CJK/Hangul/Kana/Fullwidth codepoints price at roughly one token each in
# BPE vocabularies (hermes-agent calibration vs cl100k/o200k/Qwen2.5).
_CJK_DENSE_RE = re.compile(
    "[\u1100-\u11ff\u2e80-\u9fff\ua960-\ua97f\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]"
)
_CHARS_PER_TOKEN = 4


def estimate_text_tokens_rough(text: str) -> int:
    """Rough token estimate: token-dense CJK ~1 token/codepoint; everything
    else ceil(UTF-8 bytes / 4).

    Byte-counting (not chars) is the corrective for non-CJK, non-ASCII
    text: Cyrillic/Greek/Arabic are ~2 bytes/char so they count ~chars/2,
    matching real BPE cost where a plain chars/4 under-counts ~2x
    (hermes-agent's calibration: Russian 0.67->1.24, Arabic 0.53->0.96,
    Hindi 0.34->0.90 estimate/real). Lone surrogates in tool output must
    not turn an estimate into a raise — encode replace emits b"?" (1 byte),
    which prices them at ~1 token and never raises.
    """
    if not text:
        return 0
    if text.isascii():  # ASCII cannot contain token-dense CJK
        return (len(text) + 3) // _CHARS_PER_TOKEN
    stripped = _CJK_DENSE_RE.sub("", text)
    dense = len(text) - len(stripped)
    return dense + (len(stripped.encode("utf-8", "replace")) + 3) // _CHARS_PER_TOKEN


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
        return max(1, estimate_text_tokens_rough(text))

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
            # pi/openclaw count reasoning + tool-call wire fields. Strictly,
            # both in-tree formatters drop ThinkingBlock (OpenAI always;
            # Anthropic without signature) and the Anthropic wire drops the
            # tool-result name — counting them is a deliberate conservative
            # bias, not a wire claim: it keeps the estimate safe if a future
            # formatter echoes thinking back, and the wire fields (call
            # id/name/args) that do serialize dominate tool-heavy deltas.
            reasoning = getattr(message, "reasoning_content", None)
            if reasoning:
                total += self.estimate_text(reasoning)
            for call in getattr(message, "tool_calls", None) or ():
                total += self.estimate_text(call.id)
                total += self.estimate_text(call.name)
                total += self.estimate_json(call.arguments)
            name = getattr(message, "name", None)
            if name:
                total += self.estimate_text(name)
            call_id = getattr(message, "tool_call_id", None)
            if call_id:
                total += self.estimate_text(call_id)
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


class AnthropicTokenEstimator(BaseTokenEstimator):
    """Anthropic-wire-family estimator (``anthropic``/``minimax``)."""
    def estimate_image(self, *, base64_data: str, media_type: str) -> int:
        width, height = decode_image_dimensions(base64_data)
        return max(1, (width * height) // 1000)

    def estimate_json(self, value: object) -> int:
        # Anthropic-family tool_use.input is serialized server-side before
        # tokenization; if that serialization escapes CJK as \uXXXX the real
        # cost is ~1.5-2 tokens/codepoint rather than ~1. ensure_ascii=True
        # prices the escaped shape — bounded over-count if the wire keeps
        # raw CJK, never an under-count.
        return self.estimate_text(json.dumps(value, ensure_ascii=True, sort_keys=True))

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
    from homemaster.config.config import OPENAI_WIRE_FORMATS

    if provider.api_format in OPENAI_WIRE_FORMATS:
        return OpenAIChatTokenEstimator()
    return AnthropicTokenEstimator()


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
