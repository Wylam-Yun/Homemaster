from __future__ import annotations

import base64

from homemaster.agent.messages import ContentBlock, UserMessage
from homemaster.config import ProviderProfileConfig
from homemaster.providers.llm_client import LLMClient
from homemaster.providers.token_estimator import (
    MimoTokenEstimator,
    decode_image_dimensions,
)


def _png_base64(width: int, height: int) -> str:
    header = (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )
    return base64.b64encode(header).decode("ascii")


def _provider() -> ProviderProfileConfig:
    return ProviderProfileConfig(
        name="Mimo",
        kind="chat",
        api_format="anthropic",
        transport="anthropic_sdk",
        base_url="https://mimo.example/anthropic",
        model="mimo-v2.5-pro",
        api_keys=["secret-one"],
        context_window_tokens=1_000_000,
    )


def test_decode_png_dimensions_from_base64_header() -> None:
    assert decode_image_dimensions(_png_base64(128, 64)) == (128, 64)


def test_mimo_estimator_counts_images_and_cache_read_usage() -> None:
    estimator = MimoTokenEstimator()
    image = _png_base64(128, 128)
    message = UserMessage(
        content=[
            ContentBlock(text="水杯"),
            ContentBlock(
                type="image",
                source={"type": "base64", "media_type": "image/png", "data": image},
            ),
        ]
    )

    assert estimator.estimate_image(base64_data=image, media_type="image/png") == 16
    assert estimator.estimate_messages([message]) >= 17
    assert estimator.real_usage({"input_tokens": 66, "cache_read_input_tokens": 192}) == 258


def test_llm_client_exposes_provider_token_estimator() -> None:
    client = LLMClient(_provider())

    assert isinstance(client.token_estimator, MimoTokenEstimator)
