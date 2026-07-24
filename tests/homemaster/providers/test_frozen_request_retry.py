from __future__ import annotations

import base64
import hashlib

from homemaster.agent.messages import ContentBlock, UserMessage
from homemaster.providers.llm_client import _attempt_record
from homemaster.providers.transports.anthropic import AnthropicTransport


def test_provider_attempt_tracks_generic_screenshot_bytes_without_observation_binding() -> None:
    png = b"same-current-frame"
    encoded = base64.b64encode(png).decode("ascii")
    block = ContentBlock(
        type="image",
        source={"type": "base64", "media_type": "image/png", "data": encoded},
        metadata={"content_sha256": hashlib.sha256(png).hexdigest()},
    )

    attempt = _attempt_record(
        messages=[UserMessage(content=[block])],
        request_body={
            "messages": [
                {"content": [{"type": "image", "source": {"type": "base64", "data": encoded}}]}
            ]
        },
        model_attempt_id="attempt-1",
        request_sha256=hashlib.sha256(b"request").hexdigest(),
        stripped_images=False,
        response_completed=True,
        error=None,
    )

    assert attempt.outbound_images[0].content_sha256 == hashlib.sha256(png).hexdigest()


def test_provider_attempt_omits_images_dropped_by_transport() -> None:
    first = b"older-frame"
    second = b"current-frame"
    first_encoded = base64.b64encode(first).decode("ascii")
    second_encoded = base64.b64encode(second).decode("ascii")
    messages = [
        UserMessage(
            content=[
                ContentBlock(
                    type="image",
                    source={"type": "base64", "media_type": "image/png", "data": first_encoded},
                )
            ]
        ),
        UserMessage(
            content=[
                ContentBlock(
                    type="image",
                    source={"type": "base64", "media_type": "image/png", "data": second_encoded},
                )
            ]
        ),
    ]

    request_body = AnthropicTransport().build_create_kwargs(
        model="test-model",
        messages=messages,
        tools=None,
    )
    attempt = _attempt_record(
        messages=messages,
        request_body=request_body,
        model_attempt_id="attempt-2",
        request_sha256=hashlib.sha256(b"request-2").hexdigest(),
        stripped_images=False,
        response_completed=True,
        error=None,
    )

    assert [image.content_sha256 for image in attempt.outbound_images] == [
        hashlib.sha256(second).hexdigest()
    ]
    assert attempt.stripped_images is True
